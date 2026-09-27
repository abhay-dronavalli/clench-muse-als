"""The agent's gaze for the board page: ws://127.0.0.1:8766 (docs/desktop-control.md, chunk 5).

One camera owner. While the agent runs, its Eyedid worker has the webcam, so the board must not
start Eyedid web or its head tracker. The board connects here instead and feeds the agent's
(smoothed) gaze into its gaze slot, as the tablet shell does. Gaze stays on this laptop; it never
goes to the Core.

Only the board's own pages may connect (Origin check): any web page open in the browser could
otherwise read where the person looks. Local programs send no Origin and are allowed (they could
read the screen anyway).

Messages, one JSON object each:
  {"type": "hello", "screen": [width_px, height_px], "version": 1}               on connect
  {"type": "gaze", "t": s, "x": px | null, "y": px | null, "found": bool, "state": str}   ~30 per s
x, y are physical screen pixels of the primary screen; the page turns them into its own window's
fractions (web/src/facetrack/desktopAgent.ts).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any

log = logging.getLogger("clench.desktop.bridge")

PORT = 8766
ORIGINS = [f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (5173, 5174, 5175)]


def gaze_message(t: float, point: tuple[float, float] | None, found: bool, state: str) -> dict[str, Any]:
    ok = found and point is not None
    return {"type": "gaze", "t": round(t, 3), "x": round(point[0], 1) if ok and point else None,
            "y": round(point[1], 1) if ok and point else None, "found": ok, "state": state}


class GazeBridge(threading.Thread):
    def __init__(self, screen: tuple[int, int], port: int = PORT, extra_origins: list[str] | None = None) -> None:
        super().__init__(name="gaze-bridge", daemon=True)
        self.screen = screen
        self.port = port
        self.origins: list[str | None] = [*ORIGINS, *(extra_origins or []), None]
        self._clients: set[Any] = set()
        self._lock = threading.Lock()
        self._out: queue.Queue[str] = queue.Queue(maxsize=4)
        self.error: str | None = None

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._clients)

    def publish(self, msg: dict[str, Any]) -> None:
        """From the Qt thread; never blocks. A slow page loses old frames, never gets them late."""
        if not self.clients:
            return
        text = json.dumps(msg, separators=(",", ":"))
        try:
            self._out.put_nowait(text)
        except queue.Full:
            try:
                self._out.get_nowait()
            except queue.Empty:
                pass
            self._out.put_nowait(text)

    def run(self) -> None:
        from websockets.sync.server import serve

        threading.Thread(target=self._send_loop, name="gaze-bridge-send", daemon=True).start()
        try:
            with serve(self._serve_client, "127.0.0.1", self.port, origins=self.origins) as server:
                log.info("gaze bridge for the board on ws://127.0.0.1:%d", self.port)
                server.serve_forever()
        except OSError as e:  # another agent already runs, or the port is taken
            self.error = f"gaze bridge could not listen on {self.port}: {e}"
            log.warning(self.error)

    def _serve_client(self, ws: Any) -> None:
        ws.send(json.dumps({"type": "hello", "screen": list(self.screen), "version": 1}))
        with self._lock:
            self._clients.add(ws)
        log.info("board connected to the gaze bridge (%d)", self.clients)
        try:
            for _ in ws:  # the page sends nothing we need; this just waits for it to close
                pass
        except Exception:
            pass
        finally:
            with self._lock:
                self._clients.discard(ws)
            log.info("board left the gaze bridge (%d)", self.clients)

    def _send_loop(self) -> None:
        while True:
            text = self._out.get()
            with self._lock:
                clients = list(self._clients)
            for ws in clients:
                try:
                    ws.send(text)
                except Exception:
                    with self._lock:
                        self._clients.discard(ws)
