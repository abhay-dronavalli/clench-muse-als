"""The agent's two outside connections, each on its own thread, handing everything to the Qt thread
through queues:

- CoreLink: the Core's /ws/desktop (SETTINGS, SCREEN, ACTION_RESULT, DESKTOP_INPUT in; SETTINGS and
  the stand-in's gestures out). Reconnects every 2 s; the Core may restart at any time.
- Gaze sources: EyedidSource runs `desktop.eyedid.worker` as a child process (so an SDK crash never
  takes the agent down) and restarts it if it dies; MouseSource uses the mouse pointer as the gaze,
  for working without a tracker or a calibration.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from desktop.agent.gaze import Sample, parse_worker_line, sample_from

log = logging.getLogger("clench.desktop")
ROOT = Path(__file__).resolve().parents[2]


class CoreLink(threading.Thread):
    def __init__(self, url: str, inbox: "queue.Queue[tuple[str, Any]]") -> None:
        super().__init__(name="core-link", daemon=True)
        self.url = url
        self.inbox = inbox  # ("core", message dict) and ("core_status", bool)
        self._ws: Any = None
        self._lock = threading.Lock()
        self.connected = False

    def send(self, msg: dict[str, Any]) -> bool:
        with self._lock:
            ws = self._ws
        if ws is None:
            return False
        try:
            ws.send(json.dumps(msg))
            return True
        except Exception:
            return False

    def run(self) -> None:
        from websockets.sync.client import connect

        while True:
            try:
                with connect(self.url, open_timeout=3, close_timeout=1) as ws:
                    with self._lock:
                        self._ws = ws
                    self.connected = True
                    self.inbox.put(("core_status", True))
                    for raw in ws:
                        try:
                            self.inbox.put(("core", json.loads(raw)))
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                log.debug("core link: %s", e)
            with self._lock:
                self._ws = None
            if self.connected:
                self.connected = False
                self.inbox.put(("core_status", False))
            time.sleep(2.0)


class MouseSource(threading.Thread):
    """The mouse pointer as the gaze, 60 times a second (development, demos without a tracker)."""

    kind = "mouse"

    def __init__(self, inbox: "queue.Queue[tuple[str, Any]]") -> None:
        super().__init__(name="mouse-gaze", daemon=True)
        self.inbox = inbox
        self.status = "running"

    def command(self, _msg: dict[str, Any]) -> bool:
        return False  # nothing to calibrate

    def run(self) -> None:
        from desktop.agent.winput import cursor

        while True:
            x, y = cursor()
            self.inbox.put(("gaze", Sample(time.time(), float(x), float(y), "MOUSE")))
            time.sleep(1 / 60)


class EyedidSource(threading.Thread):
    """Supervises the Eyedid worker process. Gaze lines become Samples; everything else ("status",
    "calib_*") goes to the Qt thread as ("eyedid", message)."""

    kind = "eyedid"

    def __init__(self, inbox: "queue.Queue[tuple[str, Any]]", screen_arg: str, env: dict[str, str],
                 camera: int = 0, face_cm: int = 50) -> None:
        super().__init__(name="eyedid", daemon=True)
        self.inbox = inbox
        self.args = [sys.executable, "-m", "desktop.eyedid.worker", "--screen", screen_arg,
                     "--camera", str(camera), "--face-cm", str(face_cm)]
        self.env = {**os.environ, **env}
        self.proc: subprocess.Popen[str] | None = None
        self.status = "starting"
        self.log: deque[str] = deque(maxlen=80)
        self._lock = threading.Lock()
        self.stopping = False

    def command(self, msg: dict[str, Any]) -> bool:
        with self._lock:
            proc = self.proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            return True
        except OSError:
            return False

    def stop(self) -> None:
        self.stopping = True
        self.command({"cmd": "quit"})
        with self._lock:
            proc = self.proc
        if proc is not None:
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def run(self) -> None:
        failures = 0
        while not self.stopping:
            proc = subprocess.Popen(self.args, cwd=ROOT, env=self.env, text=True, encoding="utf-8",
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with self._lock:
                self.proc = proc
            threading.Thread(target=self._drain, args=(proc,), daemon=True).start()
            running = False
            assert proc.stdout is not None
            for line in proc.stdout:
                msg = parse_worker_line(line)
                if msg is None:
                    continue
                if msg["type"] == "gaze":
                    self.inbox.put(("gaze", sample_from(msg)))
                    continue
                if msg["type"] == "status":
                    self.status = msg.get("state", "?")
                    running = running or self.status == "running"
                    if self.status == "error":
                        self.log.append(f"error: {msg.get('detail')}")
                self.inbox.put(("eyedid", msg))
            proc.wait()
            if self.stopping:
                return
            failures = 0 if running else failures + 1
            # A refused key or a missing SDK will not fix itself: stop retrying after 3 tries.
            if failures >= 3:
                self.inbox.put(("eyedid", {"type": "status", "state": "error",
                                           "detail": "Eyedid stopped: " + (self.log[-1] if self.log else "unknown")}))
                return
            self.inbox.put(("eyedid", {"type": "status", "state": "starting", "detail": "restarting"}))
            time.sleep(3.0)

    def _drain(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                self.log.append(line)
