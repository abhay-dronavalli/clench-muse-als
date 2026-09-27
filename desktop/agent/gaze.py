"""Gaze on the desktop: samples, the smoothing filters, freshness, and the look-back trail. Pure.

Smoothing is Savitzky-Golay, then One Euro. Raw webcam gaze jitters by tens of pixels from frame to
frame, and One Euro alone reads that jitter as speed and lets it through. Savitzky-Golay fits a
low-order polynomial to the last few hundred milliseconds and takes its value: jitter averages out,
while a real jump (a saccade) keeps its shape better than under a moving average. One Euro then
steadies what is left while the eyes rest.

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


class SavitzkyGolay:
    """Causal Savitzky-Golay smoothing over time, for one 2D point stream.

    Keeps the samples of the last `window_s` seconds, fits x(t) and y(t) with a polynomial of
    `order` (least squares, so an uneven frame rate is fine: Eyedid runs at 15 to 30 fps), and
    returns the fit `lag_s` before the newest sample. Evaluating a little behind the newest sample
    smooths much more than evaluating at it (a fit is least certain at its end) for a small delay.
    A gap longer than RESET_S starts over, as One Euro does.
    """

    def __init__(self, window_s: float = 0.5, order: int = 2, lag_s: float = 0.08) -> None:
        if order not in (1, 2, 3):
            raise ValueError("order must be 1, 2 or 3")
        self.window_s, self.order, self.lag_s = window_s, order, lag_s
        self.reset()

    def reset(self) -> None:
        self._pts: deque[tuple[float, float, float]] = deque()

    def __call__(self, x: float, y: float, t: float) -> tuple[float, float]:
        if self._pts and t - self._pts[-1][0] > RESET_S:
            self.reset()
        self._pts.append((t, x, y))
        while self._pts[0][0] < t - self.window_s:
            self._pts.popleft()
        n = len(self._pts)
        order = min(self.order, n - 2)  # need more points than coefficients, or it just repeats them
        if order < 1:
            return x, y
        at = t - min(self.lag_s, t - self._pts[0][0])  # never evaluate before the oldest sample
        return _fit_at(self._pts, at, order, 1), _fit_at(self._pts, at, order, 2)


def _fit_at(pts: "deque[tuple[float, float, float]]", at: float, order: int, col: int) -> float:
    """Least-squares polynomial of `order` through (t, pts[col]), evaluated at `at`. Time is taken
    relative to `at`, so the answer is the constant term."""
    k = order + 1
    moments = [0.0] * (2 * k - 1)  # sum of dt^j
    rhs = [0.0] * k  # sum of value * dt^j
    for p in pts:
        dt = p[0] - at
        v = p[col]
        power = 1.0
        for j in range(2 * k - 1):
            moments[j] += power
            if j < k:
                rhs[j] += v * power
            power *= dt
    a = [[moments[i + j] for j in range(k)] + [rhs[i]] for i in range(k)]
    for c in range(k):  # Gaussian elimination with partial pivoting
        pivot = max(range(c, k), key=lambda r: abs(a[r][c]))
        if abs(a[pivot][c]) < 1e-12:
            return pts[-1][col]  # all samples at one instant: nothing to fit
        a[c], a[pivot] = a[pivot], a[c]
        for r in range(k):
            if r != c:
                f = a[r][c] / a[c][c]
                for j in range(c, k + 1):
                    a[r][j] -= f * a[c][j]
    return a[0][k] / a[0][0]


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

    def __init__(self, width: float, height: float, left: float = 0, top: float = 0,
                 sg: SavitzkyGolay | None = None, one_euro: bool = True) -> None:
        self.left, self.top, self.width, self.height = left, top, width, height
        self.sg = sg  # None = no Savitzky-Golay
        self.filter = OneEuro(width, height) if one_euro else None
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
        if self.sg is not None:
            x, y = self.sg(x, y, s.t)
        if self.filter is not None:
            x, y = self.filter(x, y, s.t)
        self.point = (x, y)
        self.seen_at = s.t

    def fresh(self, now: float) -> bool:
        return self.point is not None and now - self.seen_at <= STALE_S

    def tracker_alive(self, now: float) -> bool:
        return now - self.last_sample_at <= STALE_S
