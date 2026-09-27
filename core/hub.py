"""WebSocket hub: keeps track of connected clients and delivers Core -> Board messages (and SETTINGS
to everyone, METRICS, SHORTCUT_DEBUG and INPUT_EVENT to consoles and the dev panel).

Each client has its own queue and writer task, so messages always arrive in the order the session
emitted them, and a slow or dead client never blocks the session.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import WebSocket

from core.contracts import (ActionResult, DesktopInput, InputEvent, Message, Metrics, Screen, Settings, ShortcutDebug,
                            TypeText)

log = logging.getLogger("clench.hub")

Role = Literal["board", "console", "input", "sensor", "desktop"]


class Client:
    def __init__(self, ws: WebSocket, role: Role) -> None:
        self.ws = ws
        self.role = role
        self.queue: asyncio.Queue[Message] = asyncio.Queue()

    async def pump(self) -> None:
        """Send queued messages until the socket fails or the task is cancelled."""
        while True:
            msg = await self.queue.get()
            try:
                await self.ws.send_text(msg.model_dump_json())
            except Exception as e:  # closed socket; the reader loop cleans up
                log.info("%s send failed: %s", self.role, e)
                return


class Hub:
    def __init__(self) -> None:
        self._clients: set[Client] = set()

    def add(self, client: Client) -> None:
        self._clients.add(client)
        log.info("%s connected (%d clients)", client.role, len(self._clients))

    def remove(self, client: Client) -> None:
        self._clients.discard(client)
        log.info("%s disconnected (%d clients)", client.role, len(self._clients))

    def broadcast(self, msg: Message) -> None:
        """Send to every board, and to every console so the caregiver screen can mirror. SETTINGS
        also goes to input clients, so the dev panel shows the real values. METRICS and
        SHORTCUT_DEBUG go to consoles and input clients (the dev panel) only: the patient's board has
        no use for them. INPUT_EVENT goes the same way; the board's own input log opens a console
        socket for it, exactly as the board's Muse panel already does. The desktop agent gets
        DESKTOP_INPUT and TYPE_TEXT (it alone), SETTINGS, SCREEN (to draw the help countdown) and
        ACTION_RESULT."""
        for c in self._clients:
            if isinstance(msg, (DesktopInput, TypeText)):
                wanted = c.role == "desktop"
            elif isinstance(msg, (Metrics, ShortcutDebug, InputEvent)):
                wanted = c.role in ("console", "input")
            elif c.role == "desktop":
                wanted = isinstance(msg, (Settings, Screen, ActionResult))
            else:
                wanted = c.role in ("board", "console") or isinstance(msg, Settings)
            if wanted:
                c.queue.put_nowait(msg)

    def send_to(self, client: Client, msg: Message) -> None:
        client.queue.put_nowait(msg)

    def count(self, role: Role) -> int:
        return sum(1 for c in self._clients if c.role == role)
