"""Core Server (PRD A3.2): FastAPI app with the three WebSocket routes.

Run:  uv run uvicorn core.main:app --reload --port 8000

  /ws/board    patient board: READY, AUDIO_DONE, POINT, FACE_OK in; SCREEN, CONFIRM, SPEAK,
               ACTION_RESULT out
  /ws/console  caregiver console: SETTINGS in; mirror of what the board gets out
  /ws/input    sensor service or web dev panel: CLENCH, DOUBLE_BLINK, LONG_CLENCH, STATE, SIGNAL,
               POINT, SETTINGS in; nothing out

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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from core.actions import build_registry
from core.clock import AsyncioScheduler, Scheduler
from core.config import dry_run_enabled, load_env
from core.contracts import Lang, Message, Ready, parse_message
from core.db import DB_PATH, Db
from core.hub import Client, Hub, Role
from core.menu import Menu, load_menu
from core.pointer import DEFAULT_SCAN_MS
from core.profile import Profile, load_profile
from core.session import Session

logging.basicConfig(format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logging.getLogger("clench").setLevel(logging.INFO)
log = logging.getLogger("clench.core")

# Which message types each route accepts. Anything else is logged and ignored.
ACCEPTS: dict[Role, frozenset[str]] = {
    "board": frozenset({"READY", "AUDIO_DONE", "POINT", "FACE_OK"}),
    "console": frozenset({"SETTINGS"}),
    "input": frozenset({"CLENCH", "DOUBLE_BLINK", "LONG_CLENCH", "STATE", "SIGNAL", "POINT", "SETTINGS"}),
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
) -> FastAPI:
    """Build the app. Defaults are safe for tests: an in-memory database and no keys, so actions
    only dry-run. The real app (bottom of this file) passes the database file and .env."""
    menu = menu or load_menu()  # fails loudly at startup on a bad tree
    profile = profile or load_profile(menu.contacts)
    env = env if env is not None else {}
    dry_run = dry_run_enabled(env)
    hub = Hub()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Db(db_path)
        db.sync_profile(profile.name, profile.lang, menu.contacts.values())
        session = Session(
            menu,
            hub.broadcast,
            scheduler or AsyncioScheduler(),
            profile=profile,
            actions=build_registry(hub.broadcast, env, dry_run=dry_run),
            db=db,
            scan_ms=scan_ms,
            lang=lang,
        )
        app.state.session = session
        session.start()
        log.info("core ready: scanning home, %d ms per tile, lang %s, patient %s", scan_ms, session.lang, profile.name)
        if dry_run:
            log.warning("ACTIONS_DRY_RUN is on: messages and calls are only logged (set it to false in .env)")
        else:
            log.warning("ACTIONS_DRY_RUN is off: confirmed messages and calls are REALLY sent")
        yield
        session.stop()
        db.close()

    app = FastAPI(title="Clench Core", lifespan=lifespan)
    app.state.hub = hub

    async def serve(ws: WebSocket, role: Role) -> None:
        await ws.accept()
        client = Client(ws, role)
        hub.add(client)
        writer = asyncio.create_task(client.pump())
        session: Session = app.state.session
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

    @app.websocket("/ws/board")
    async def ws_board(ws: WebSocket) -> None:
        await serve(ws, "board")

    @app.websocket("/ws/console")
    async def ws_console(ws: WebSocket) -> None:
        await serve(ws, "console")

    @app.websocket("/ws/input")
    async def ws_input(ws: WebSocket) -> None:
        await serve(ws, "input")

    @app.get("/health")
    async def health() -> dict[str, object]:
        session: Session = app.state.session
        return {
            "ok": True,
            "state": session.state.value,
            "lang": session.lang,
            "dry_run": dry_run,
            "boards": hub.count("board"),
            "consoles": hub.count("console"),
            "inputs": hub.count("input"),
        }

    return app


app = create_app(db_path=DB_PATH, env=load_env())
