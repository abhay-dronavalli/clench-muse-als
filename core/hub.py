"""WebSocket hub: keeps track of connected boards and consoles and delivers Core -> Board messages.

Each client has its own queue and writer task, so messages always arrive in the order the session
emitted them, and a slow or dead client never blocks the session.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import WebSocket

from core.contracts import Message

log = logging.getLogger("clench.hub")

Role = Literal["board", "console", "input"]


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
        """Send to every board, and to every console so the caregiver screen can mirror."""
        for c in self._clients:
            if c.role in ("board", "console"):
                c.queue.put_nowait(msg)

    def send_to(self, client: Client, msg: Message) -> None:
        client.queue.put_nowait(msg)

    def count(self, role: Role) -> int:
        return sum(1 for c in self._clients if c.role == role)
