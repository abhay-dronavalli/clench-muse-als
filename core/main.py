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

Every incoming message is parsed with parse_message. Invalid ones are logged and ignored; a bad
message never closes the connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from functools import partial

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import ValidationError

from core.actions import build_registry
from core.clock import AsyncioScheduler, Scheduler
from core.computer.service import Computer
from core.config import dry_run_enabled, load_env, prewarm_enabled
from core.contracts import FaceOk, HeadRange, Lang, Message, Ready, parse_message
from core.db import DB_PATH, Db
from core.hub import Client, Hub, Role
from core.menu import Menu, load_menu
from core.pointer import DEFAULT_SCAN_MS
from core.profile import Profile, load_profile
from core.rank.jev import JevRanker, build_jev
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
    "board": frozenset({"READY", "RESET", "AUDIO_DONE", "POINT", "FACE_OK"}),
    "console": frozenset({"SETTINGS"}),
    "input": frozenset({"CLENCH", "DOUBLE_BLINK", "LONG_CLENCH", "STATE", "SIGNAL", "POINT", "SETTINGS", "RESET"}),
}


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

    @app.get("/computer/start", response_class=HTMLResponse)
    async def computer_start() -> str:
        return (Path(__file__).parent / "computer" / "start.html").read_text(encoding="utf-8")

    async def serve(ws: WebSocket, role: Role) -> None:
        await ws.accept()
        client = Client(ws, role)
        hub.add(client)
        writer = asyncio.create_task(client.pump())
        session: Session = app.state.session
        hub.send_to(client, session.settings())
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
                    if isinstance(msg, Ready):
                        view = session.current_view()
                        if view is not None:
                            hub.send_to(client, view)
                    else:
                        session.handle(msg)
                except Exception:
                    log.exception("error handling %s from %s", msg.type, role)
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(client)
            writer.cancel()
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
        }

    return app


def contact_names(menu: Menu) -> dict[Lang, tuple[str, ...]]:
    """The contacts' first names in each language, for the AI (PRD D14: names only)."""
    return {
        lang: tuple(c.label(lang).split()[0] for c in menu.contacts.values())
        for lang in ("en", "es")
    }


app = create_app(db_path=DB_PATH, env=load_env())
