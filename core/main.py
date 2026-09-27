"""Core Server (PRD A3.2): FastAPI app with the three WebSocket routes.

Run:  uv run uvicorn core.main:app --reload --port 8000

  /ws/board    patient board: READY, RESET, AUDIO_DONE, POINT, FACE_OK in; SETTINGS, SCREEN, CONFIRM, SPEAK,
               PLAY_AUDIO, ACTION_RESULT out
  /ws/console  caregiver console: SETTINGS in; SETTINGS plus a mirror of what the board gets out
  /ws/input    sensor service or web dev panel: CLENCH, DOUBLE_BLINK, LONG_CLENCH, STATE, SIGNAL,
               POINT, SETTINGS, RESET in; SETTINGS out

Every client gets the current SETTINGS as soon as it connects, and again after every change.
When the last board disconnects the session hears FACE_OK false (Auto falls back to scan in 3 s).

  /audio/<sha256>.mp3  cached ElevenLabs audio named in PLAY_AUDIO
  /api/head-range      GET / PUT the calibrated head range (HeadRange) in the database profile
  /api/sensor          GET status; POST start / stop the Muse Sensor Service subprocess

Every incoming message is parsed with parse_message. Invalid ones are logged and ignored; a bad
message never closes the connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from functools import partial

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ValidationError

from core.actions import build_registry
from core.clock import AsyncioScheduler, Scheduler
from core.computer.service import Computer
from core.config import dry_run_enabled, load_env, prewarm_enabled
from core.contracts import (Clench, DoubleBlink, FaceOk, HeadRange, InputEvent, Lang, LongClench,
                            Message, Ready, Signal, Settings, parse_message)
from core.db import DB_PATH, Db
from core.hub import Client, Hub, Role
from core.menu import Menu, load_menu
from core.pointer import DEFAULT_SCAN_MS
from core.profile import Profile, load_profile
from core.rank.jev import JevRanker, build_jev
from core.sensor_service import SensorService
from core.session import Session, voice_lines
from core.suggest import build_provider
from core.suggest.provider import LLMProvider
from core.suggest.service import Suggester
from core.voice import AUDIO_DIR, AUDIO_NAME, TTS, AudioCache, Voice, build_tts

logging.basicConfig(format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logging.getLogger("clench").setLevel(logging.INFO)
log = logging.getLogger("clench.core")

# Which message types each route accepts. Anything else is logged and ignored.
ACCEPTS: dict[Role, frozenset[str]] = {
    "board": frozenset({"READY", "RESET", "AUDIO_DONE", "POINT", "FACE_OK", "COMPUTER_POINT"}),
    "console": frozenset({"SETTINGS"}),
    "input": frozenset({"CLENCH", "DOUBLE_BLINK", "LONG_CLENCH", "STATE", "SIGNAL", "POINT", "SETTINGS", "RESET"}),
    "sensor": frozenset({'CLENCH', 'LONG_CLENCH', 'DOUBLE_BLINK', 'SIGNAL'}),
}

# How long the headband may be missing before Muse input is paused. A Bluetooth reconnect takes a
# few seconds and the command gate below already refuses anything that arrives meanwhile, so pausing
# on the first dropped sample only flapped the switch between Paused and Ready every few seconds.
MUSE_LOSS_GRACE_S = 10.0
GESTURES = ('CLENCH', 'LONG_CLENCH', 'DOUBLE_BLINK')


class SensorStart(BaseModel):
    """POST /api/sensor/start body: which calibration profile to run, and against what."""

    profile: str = "taher"
    source: str = "muse"
    blink: str = "auto"  # auto / on = DOUBLE_BLINK from MNE; off = none


def input_event(msg: Message, source: str, reason: str | None) -> InputEvent:
    """Describe one gesture for the board's input log, accepted or not."""
    return InputEvent(
        t=getattr(msg, "t", time.time()),
        kind=msg.type,  # type: ignore[arg-type]  (callers pass a gesture only)
        source=source,  # type: ignore[arg-type]
        accepted=reason is None,
        reason=reason,
        strength=msg.strength if isinstance(msg, Clench) else None,
        duration=msg.duration if isinstance(msg, LongClench) else None,
    )


def refuse_reason(session: "Session", hub: Hub, state: object, msg: Message) -> str | None:
    """Why the Core must ignore this headband gesture, or None to act on it.

    The emergency path depends on none of this beyond the headband being alive: LONG_CLENCH is
    refused for exactly the same reasons as any other gesture, never for an extra one.
    """
    signal: Signal = state.sensor_signal  # type: ignore[attr-defined]
    if not session.muse_enabled:
        return 'Muse input is paused'
    if not hub.count('board'):
        return 'no patient board is open'
    if not signal.connected:
        return 'the headband is not connected'
    if signal.blocked:
        return signal.blocked
    if time.monotonic()-state.sensor_seen > 2:  # type: ignore[attr-defined]
        return 'the signal is stale (no telemetry for 2 s)'
    if not 0 <= time.time()-msg.t <= 1:
        return 'the gesture is older than a second (clock skew?)'
    return None


def decode(text: str, role: Role) -> Message | None:
    """Parse one incoming frame. Returns None (and logs why) if it is not a valid message for this route."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("%s sent invalid JSON, ignored: %.200s", role, text)
        return None
    if not isinstance(data, dict):
        log.warning("%s sent a non-object, ignored: %.200s", role, text)
        return None
    try:
        msg = parse_message(data)
    except ValidationError as e:
        log.warning("%s sent an invalid message, ignored: %.200s (%d errors: %s)",
                    role, text, e.error_count(), e.errors(include_url=False, include_input=False))
        return None
    if msg.type not in ACCEPTS[role]:
        log.warning("%s is not accepted on /ws/%s, ignored", msg.type, role)
        return None
    return msg


def create_app(
    *,
    menu: Menu | None = None,
    profile: Profile | None = None,
    scheduler: Scheduler | None = None,
    scan_ms: int = DEFAULT_SCAN_MS,
    lang: Lang | None = None,
    db_path: str | Path = ":memory:",
    env: Mapping[str, str] | None = None,
    audio_dir: Path = AUDIO_DIR,
    tts: TTS | None = None,
    provider: LLMProvider | None = None,
    jev: JevRanker | None = None,
) -> FastAPI:
    """Build the app. Defaults are safe for tests: an in-memory database and no keys, so actions
    only dry-run, speech uses the browser voice and there is no AI (fixed phrases). The real app
    (bottom of this file) passes the database file and .env. Tests pass `provider` (FakeProvider)."""
    menu = menu or load_menu()  # fails loudly at startup on a bad tree
    profile = profile or load_profile(menu.contacts)
    env = env if env is not None else {}
    dry_run = dry_run_enabled(env)
    hub = Hub()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Db(db_path)
        db.sync_profile(profile.name, profile.lang, menu.contacts.values())
        app.state.db = db
        voice = Voice(hub.broadcast, tts=tts or build_tts(env), cache=AudioCache(audio_dir, db))
        app.state.voice = voice
        llm, off_reason = (provider, "") if provider is not None else build_provider(env)
        suggester = Suggester(
            llm,
            patient_name=profile.name,
            contacts=contact_names(menu),
            history=db,
            off_reason=off_reason,
        )
        app.state.suggester = suggester
        ranker_jev, jev_off = (jev, "") if jev is not None else build_jev(env)
        app.state.jev = ranker_jev
        session = Session(
            menu,
            hub.broadcast,
            scheduler or AsyncioScheduler(),
            profile=profile,
            actions=build_registry(voice, env, dry_run=dry_run),
            voice=voice,
            suggester=suggester,
            db=db,
            scan_ms=scan_ms,
            lang=lang,
            jev=ranker_jev,
            computer_factory=partial(Computer, start_url=env.get("COMPUTER_START_URL", "http://127.0.0.1:8000/computer/start")),
        )
        app.state.session = session
        session.start()
        log.info("core ready: scanning home, %d ms per tile, lang %s, patient %s", scan_ms, session.lang, profile.name)
        if dry_run:
            log.warning("ACTIONS_DRY_RUN is on: messages and calls are only logged (set it to false in .env)")
        else:
            log.warning("ACTIONS_DRY_RUN is off: confirmed messages and calls are REALLY sent")
        log.info("voice: %s", voice.describe())
        log.info("AI: %s", suggester.describe())
        log.info("ranking: %s, %s", "learning on" if session.learning else "Day 1 mode (learning off)",
                 ranker_jev.describe() if ranker_jev else f"no Jev ({jev_off}): history and time of day only")
        prewarm: asyncio.Task[object] | None = None
        if voice.tts.voice_id is not None and prewarm_enabled(env):
            # Background only: the board works (browser speech for anything not cached) meanwhile.
            prewarm = asyncio.create_task(voice.prewarm(voice_lines(menu, profile)))
        yield
        app.state.sensor_service.stop()
        if prewarm is not None:
            prewarm.cancel()
        await session.computer.aclose()
        session.stop()
        await suggester.aclose()
        if ranker_jev is not None:
            await ranker_jev.aclose()
        await voice.aclose()
        db.close()

    app = FastAPI(title="Clench Core", lifespan=lifespan)
    app.state.hub = hub
    app.state.sensor_signal = Signal(t=time.time(), ch=[], connected=False, blocked='Muse service not connected')
    app.state.sensor_seen = 0.0
    app.state.sensor_service = SensorService()
    app.state.headband_lost_at: float | None = None

    @app.get("/computer/start", response_class=HTMLResponse)
    async def computer_start() -> str:
        return (Path(__file__).parent / "computer" / "start.html").read_text(encoding="utf-8")

    async def serve(ws: WebSocket, role: Role) -> None:
        if role == 'sensor' and hub.count('sensor'):
            await ws.close(code=1008, reason='A Muse service is already connected')
            return
        await ws.accept()
        client = Client(ws, role)
        hub.add(client)
        writer = asyncio.create_task(client.pump())
        session: Session = app.state.session
        hub.send_to(client, session.settings())
        if role in ('board', 'console'):
            # Sent after READY for boards to preserve the existing startup ordering.
            if role == 'console':
                hub.send_to(client, app.state.sensor_signal)
        try:
            while True:
                event = await ws.receive()
                if event["type"] == "websocket.disconnect":
                    break
                text = event.get("text")
                if text is None:
                    log.warning("%s sent a binary frame, ignored", role)
                    continue
                msg = decode(text, role)
                if msg is None:
                    continue
                try:
                    if role == 'sensor':
                        if isinstance(msg, Signal):
                            app.state.sensor_signal = msg
                            app.state.sensor_seen = time.monotonic()
                            hub.broadcast(msg)
                            if msg.connected:
                                app.state.headband_lost_at = None
                            else:
                                # Pause only once the loss lasts: a reconnect must not flap the switch.
                                if app.state.headband_lost_at is None:
                                    app.state.headband_lost_at = time.monotonic()
                                lost_for = time.monotonic()-app.state.headband_lost_at
                                if lost_for >= MUSE_LOSS_GRACE_S and session.muse_enabled:
                                    log.info('headband missing for %.0f s: pausing Muse input', lost_for)
                                    session.handle(Settings(**dict(session.settings().model_dump(), muse_enabled=False)))
                            continue
                        reason = refuse_reason(session, hub, app.state, msg)
                        hub.broadcast(input_event(msg, 'muse', reason))
                        if reason is not None:
                            log.info('Muse %s suppressed: %s', msg.type, reason)
                            continue
                    if role == 'input' and msg.type in GESTURES:
                        hub.broadcast(input_event(msg, 'dev', None))
                    if isinstance(msg, Ready):
                        view = session.current_view()
                        if view is not None:
                            hub.send_to(client, view)
                        if session.computer.active:
                            hub.send_to(client, session.computer.view())
                    else:
                        session.handle(msg)
                except Exception:
                    log.exception("error handling %s from %s", msg.type, role)
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(client)
            writer.cancel()
            if role == 'sensor':
                app.state.sensor_signal = Signal(t=time.time(), ch=[], connected=False,
                    profile=app.state.sensor_signal.profile, blocked='Muse service disconnected')
                hub.broadcast(app.state.sensor_signal)
                session.handle(Settings(**dict(session.settings().model_dump(), muse_enabled=False)))
            if role == 'board' and hub.count('board') == 0 and session.muse_enabled:
                session.handle(Settings(**dict(session.settings().model_dump(), muse_enabled=False)))
            if role == "board" and hub.count("board") == 0 and session.face_ok:
                log.info("last board disconnected: no webcam face any more")
                session.handle(FaceOk(ok=False))

    @app.websocket("/ws/board")
    async def ws_board(ws: WebSocket) -> None:
        await serve(ws, "board")

    @app.websocket("/ws/console")
    async def ws_console(ws: WebSocket) -> None:
        await serve(ws, "console")

    @app.websocket("/ws/input")
    async def ws_input(ws: WebSocket) -> None:
        await serve(ws, "input")

    @app.websocket('/ws/sensor')
    async def ws_sensor(ws: WebSocket) -> None:
        await serve(ws, 'sensor')

    @app.get("/audio/{name}")
    async def audio(name: str) -> FileResponse:
        path = audio_dir / name
        if not AUDIO_NAME.match(name) or not path.is_file():
            raise HTTPException(status_code=404)
        # The name is a hash of the text and voice, so the file never changes.
        return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/api/head-range")
    async def get_head_range() -> HeadRange | None:
        saved = app.state.db.head_range()
        if saved is None:
            return None
        try:
            return HeadRange.model_validate(saved)
        except ValidationError:
            log.warning("saved head range is invalid, ignored (calibrate again): %s", saved)
            return None

    @app.put("/api/head-range")
    async def put_head_range(head_range: HeadRange) -> HeadRange:
        app.state.db.set_head_range(head_range.model_dump())
        log.info("head range saved: %s", head_range.model_dump())
        return head_range

    @app.get("/api/sensor")
    async def get_sensor() -> dict[str, object]:
        return app.state.sensor_service.status()

    @app.post("/api/sensor/start")
    async def start_sensor(body: SensorStart, request: Request) -> dict[str, object]:
        """Launch the Sensor Service so the console can connect the headband itself."""
        server = request.scope.get("server") or ("127.0.0.1", 8000)
        # The subprocess dials this Core back, so the URL must name the port we are bound to,
        # not the web dev server's port that proxied the request here.
        url = f"ws://127.0.0.1:{server[1]}/ws/sensor"
        try:
            return app.state.sensor_service.start(url, body.profile, body.source, body.blink)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/sensor/stop")
    async def stop_sensor() -> dict[str, object]:
        status = app.state.sensor_service.stop()
        # Losing the headband must never leave the board accepting stale clenches.
        session: Session = app.state.session
        if session.muse_enabled:
            session.handle(Settings(**dict(session.settings().model_dump(), muse_enabled=False)))
        return status

    @app.get("/health")
    async def health() -> dict[str, object]:
        session: Session = app.state.session
        voice: Voice = app.state.voice
        suggester: Suggester = app.state.suggester
        return {
            "ok": True,
            "state": session.state.value,
            "lang": session.lang,
            "dry_run": dry_run,
            "voice": voice.describe(),
            "voice_paused": voice.breaker.is_open,
            "voice_chars_sent": voice.chars_sent,
            "speak_picks": session.speak_picks,
            "ai": suggester.describe(),
            "ai_calls": suggester.calls,
            "learning": session.learning,
            "pointing_mode": session.pointing_mode,
            "pointer": session.pointer.source,
            "face_ok": session.face_ok,
            "jev": app.state.jev.describe() if app.state.jev else "off",
            "boards": hub.count("board"),
            "consoles": hub.count("console"),
            "inputs": hub.count("input"),
            "sensor_service": "running" if app.state.sensor_service.running() else "stopped",
            "muse_enabled": session.muse_enabled,
        }

    return app


def contact_names(menu: Menu) -> dict[Lang, tuple[str, ...]]:
    """The contacts' first names in each language, for the AI (PRD D14: names only)."""
    return {
        lang: tuple(c.label(lang).split()[0] for c in menu.contacts.values())
        for lang in ("en", "es")
    }


app = create_app(db_path=DB_PATH, env=load_env())
