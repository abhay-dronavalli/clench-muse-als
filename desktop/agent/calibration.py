"""Eye calibration on the desktop: the flow and where it is saved.

Five dots, as on the tablet and the board (decisions 18, 19): the SDK names each point, the overlay
draws a dot there, and after SETTLE_S (the eyes land and steady) the agent tells the SDK to collect.
The SDK reports progress, then the calibration data, which the SDK already uses and the agent saves
in `data/desktop/eyedid.<person>.json` (git-ignored), with the screen it was made on. A calibration
for another screen size is not loaded: it belongs to that camera and screen.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from desktop.eyedid.convert import Display

DIR = Path(__file__).resolve().parents[2] / "data" / "desktop"
PERSON = re.compile(r"[A-Za-z0-9_-]{1,32}")
SETTLE_S = 1.0


def path_for(person: str, directory: Path = DIR) -> Path:
    if not PERSON.fullmatch(person):
        raise ValueError(f"person name {person!r}: letters, digits, - and _ only")
    return directory / f"eyedid.{person}.json"


def screen_key(d: Display) -> list[float]:
    return [d.width_px, d.height_px, d.width_mm, d.height_mm]


def save(person: str, display: Display, data: list[float], directory: Path = DIR) -> Path:
    path = path_for(person, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"screen": screen_key(display), "data": data}), encoding="utf-8")
    return path


def load(person: str, display: Display, directory: Path = DIR) -> list[float] | None:
    try:
        saved = json.loads(path_for(person, directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if saved.get("screen") != screen_key(display) or not saved.get("data"):
        return None
    return [float(v) for v in saved["data"]]


class Flow:
    """One calibration at a time. `command` sends to the worker; `later(seconds, fn)` runs fn on the
    agent's thread after a delay (a Qt timer in the agent, a list in the tests)."""

    def __init__(self, command: Callable[[dict[str, Any]], bool], later: Callable[[float, Callable[[], None]], None],
                 on_end: Callable[[str], None]) -> None:
        self.command = command
        self.later = later
        self.on_end = on_end  # "done" | "cancelled" | "failed"
        self.running = False
        self.point: tuple[float, float] | None = None
        self.progress = 0.0
        self.collecting = False
        self._token = 0

    def start(self, roi: tuple[float, float, float, float]) -> bool:
        if not self.command({"cmd": "calibrate", "points": 5, "roi": list(roi)}):
            self.on_end("failed")
            return False
        self.running, self.point, self.progress, self.collecting = True, None, 0.0, False
        return True

    def cancel(self) -> None:
        if self.running:
            self.command({"cmd": "stop_calibration"})
            self._finish("cancelled")

    def on_worker(self, msg: dict[str, Any]) -> list[float] | None:
        """A calib_* line from the worker. Returns the calibration data when it is done."""
        if not self.running:
            return None
        kind = msg.get("type")
        if kind == "calib_point":
            self.point, self.progress, self.collecting = (float(msg["x"]), float(msg["y"])), 0.0, False
            self._token += 1
            token = self._token
            self.later(SETTLE_S, lambda: self._collect(token))
        elif kind == "calib_progress":
            self.progress = float(msg.get("p", 0.0))
        elif kind == "calib_done":
            self._finish("done")
            return [float(v) for v in msg.get("data") or []]
        elif kind == "calib_cancel":
            self._finish("cancelled")
        return None

    def _collect(self, token: int) -> None:
        if self.running and token == self._token:
            self.collecting = True
            self.command({"cmd": "collect"})

    def _finish(self, how: str) -> None:
        self.running, self.point, self.collecting = False, None, False
        self._token += 1
        self.on_end(how)
