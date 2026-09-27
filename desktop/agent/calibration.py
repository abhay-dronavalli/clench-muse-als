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
# The Windows SDK puts the calibration dots ON the corners of the area it is given (checked: the full
# screen gave (0, 0) and (1920, 0), dots mostly off the screen). Inset the area so a dot and its 12 mm
# settle ring always show, while the corners stay near the edges the person will look at.
INSET_MM = 14.0


def area(display: Display) -> tuple[float, float, float, float]:
    """The calibration area in screen pixels: the screen, INSET_MM in from every edge."""
    dx = INSET_MM * display.width_px / display.width_mm
    dy = INSET_MM * display.height_px / display.height_mm
    return (display.left + dx, display.top + dy,
            display.left + display.width_px - dx, display.top + display.height_px - dy)


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


CHECK_SETTLE_S = 0.8  # the board's gazeCheck.ts: the eyes land and steady...
CHECK_SAMPLE_S = 1.5  # ...then this long of samples
CHECK_MIN_SAMPLES = 10
CHECK_RADIUS_MM = 15.0  # the median gaze must land this close to the dot (the snap radius)


def check_points(display: Display) -> list[tuple[float, float]]:
    """Where the check dot may go: the calibration area's 3 x 3 grid without its centre (the centre
    is the easiest place to be right by luck)."""
    left, top, right, bottom = area(display)
    xs = (left, (left + right) / 2, right)
    ys = (top, (top + bottom) / 2, bottom)
    return [(x, y) for y in ys for x in xs if (x, y) != (xs[1], ys[1])]


class Check:
    """One dot to check a loaded calibration, as the board and the tablet do (decisions 18, 19).
    Shows like a calibration dot (`point`, `collecting`, `progress`), so the overlay draws either."""

    def __init__(self, point: tuple[float, float], px_per_mm: float, now: float) -> None:
        self.point: tuple[float, float] | None = point
        self.radius = CHECK_RADIUS_MM * px_per_mm
        self.started = now
        self.samples: list[tuple[float, float]] = []
        self.running = True
        self.collecting = False
        self.progress = 0.0
        self.passed: bool | None = None

    def feed(self, now: float, gaze: tuple[float, float] | None) -> bool | None:
        """Every frame. Returns the verdict (True = the calibration still holds) once, then None."""
        if not self.running:
            return None
        elapsed = now - self.started
        self.collecting = elapsed >= CHECK_SETTLE_S
        if not self.collecting:
            return None
        if gaze is not None:
            self.samples.append(gaze)
        self.progress = min(1.0, (elapsed - CHECK_SETTLE_S) / CHECK_SAMPLE_S)
        if self.progress < 1.0:
            return None
        self.running = False
        self.passed = verdict(self.samples, self.point or (0.0, 0.0), self.radius)
        return self.passed

    def cancel(self) -> None:
        self.running = False


def verdict(samples: list[tuple[float, float]], dot: tuple[float, float], radius: float) -> bool:
    """The median gaze lands within `radius` of the dot, with enough samples to say so."""
    if len(samples) < CHECK_MIN_SAMPLES:
        return False
    xs = sorted(x for x, _ in samples)
    ys = sorted(y for _, y in samples)
    mx, my = xs[len(xs) // 2], ys[len(ys) // 2]
    return ((mx - dot[0]) ** 2 + (my - dot[1]) ** 2) ** 0.5 <= radius
