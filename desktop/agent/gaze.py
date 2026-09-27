"""Gaze on the desktop: samples, the One Euro filter, freshness, and the look-back trail. Pure.

Points are screen pixels (physical, the agent is per-monitor DPI aware); time is seconds on the
Unix clock, the same clock the headband's gestures carry, so a CLENCH at `t` can look back at the
gaze just before it. The filter works in fractions of the screen so its parameters match the
board's (web/src/facetrack/oneEuro.ts: min cutoff 1 Hz, beta 10, reset after 250 ms).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

STALE_S = 0.5  # no seen sample for this long = the eyes are not detected (the board's GAZE_STALE_MS)
RESET_S = 0.25  # a gap longer than this starts the filter over


@dataclass(frozen=True)
class Sample:
    t: float
    x: float | None  # None when the eyes are not found this frame
    y: float | None
    state: str = "SUCCESS"  # Eyedid's tracking state, or "MOUSE" / "CALIBRATING"

    @property
    def found(self) -> bool:
        return self.x is not None and self.y is not None


def parse_worker_line(line: str) -> dict[str, Any] | None:
    """One stdout line from desktop.eyedid.worker, or None for anything that is not a protocol message
    (the SDK and its libraries also print)."""
    import json

    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return None
    return msg if isinstance(msg, dict) and isinstance(msg.get("type"), str) else None


def sample_from(msg: dict[str, Any]) -> Sample:
    """A worker "gaze" message as a Sample. A point off the screen by more than a screen is noise."""
    x, y = msg.get("x"), msg.get("y")
    ok = msg.get("state") == "SUCCESS" and isinstance(x, (int, float)) and isinstance(y, (int, float))
    return Sample(float(msg["t"]), float(x) if ok else None, float(y) if ok else None, str(msg.get("state")))


class _Axis:
    def __init__(self) -> None:
        self.x: float | None = None
        self.dx = 0.0

    def filter(self, value: float, dt: float, min_cutoff: float, beta: float, d_cutoff: float) -> float:
        if self.x is None or dt <= 0:
            self.x, self.dx = value, 0.0
            return value
        self.dx += _alpha(d_cutoff, dt) * ((value - self.x) / dt - self.dx)
        self.x += _alpha(min_cutoff + beta * abs(self.dx), dt) * (value - self.x)
        return self.x


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1 / (2 * math.pi * cutoff)
    return 1 / (1 + tau / dt)


class OneEuro:
    """2D One Euro filter over screen fractions (so the board's parameters apply unchanged)."""

    def __init__(self, width: float, height: float, min_cutoff: float = 1.0, beta: float = 10.0,
                 d_cutoff: float = 1.0) -> None:
        self.w, self.h = width, height
        self.params = (min_cutoff, beta, d_cutoff)
        self.reset()

    def reset(self) -> None:
        self._x, self._y = _Axis(), _Axis()
        self._t: float | None = None

    def __call__(self, x: float, y: float, t: float) -> tuple[float, float]:
        if self._t is not None and t - self._t > RESET_S:
            self.reset()
        dt = 0.0 if self._t is None else t - self._t
        self._t = t
        fx = self._x.filter(x / self.w, dt, *self.params)
        fy = self._y.filter(y / self.h, dt, *self.params)
        return fx * self.w, fy * self.h


T = TypeVar("T")


class Trail(Generic[T]):
    """What was true when: `at(t)` is the last value set at or before `t` (the clench look-back)."""

    def __init__(self, keep_s: float = 2.0) -> None:
        self.keep_s = keep_s
        self._items: deque[tuple[float, T]] = deque()

    def add(self, t: float, value: T) -> None:
        self._items.append((t, value))
        while self._items and self._items[0][0] < t - self.keep_s:
            self._items.popleft()

    def at(self, t: float) -> T | None:
        found: T | None = None
        for when, value in self._items:
            if when > t:
                break
            found = value
        if found is None and self._items:
            return self._items[0][1]  # older than what is kept: the oldest is the best guess
        return found

    def latest(self) -> T | None:
        return self._items[-1][1] if self._items else None


class Gaze:
    """The current gaze: filtered point, when the eyes were last seen, and the last raw state."""

    def __init__(self, width: float, height: float, left: float = 0, top: float = 0) -> None:
        self.left, self.top, self.width, self.height = left, top, width, height
        self.filter = OneEuro(width, height)
        self.point: tuple[float, float] | None = None
        self.seen_at = float("-inf")
        self.state = "none"
        self.last_sample_at = float("-inf")

    def feed(self, s: Sample) -> None:
        self.last_sample_at = s.t
        self.state = s.state
        if not s.found:
            return
        assert s.x is not None and s.y is not None
        # Keep the point on the screen: the eyes on the bezel still mean the nearest edge.
        x = min(max(s.x, self.left), self.left + self.width - 1)
        y = min(max(s.y, self.top), self.top + self.height - 1)
        self.point = self.filter(x, y, s.t)
        self.seen_at = s.t

    def fresh(self, now: float) -> bool:
        return self.point is not None and now - self.seen_at <= STALE_S

    def tracker_alive(self, now: float) -> bool:
        return now - self.last_sample_at <= STALE_S
