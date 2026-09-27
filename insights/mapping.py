"""Turn core console messages into database rows. Pure: no I/O, so every rule here is unit-tested.

Privacy is enforced here, by building rows from an explicit list of fields: METRICS `text`, SIGNAL
`profile` and every free-text reason are never copied into a row.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Exact refusal strings from core/main.py refuse_reason(). Anything else is either the sensor's own
# `blocked` explanation (free text, stored only as the code "blocked") or "other".
REASON_CODES: dict[str, str] = {
    "Muse input is paused": "paused",
    "no patient board is open": "no_board",
    "the headband is not connected": "disconnected",
    "the signal is stale (no telemetry for 2 s)": "stale",
    "the gesture is older than a second (clock skew?)": "clock_skew",
}

# How far before a CLENCH to look for its EMG peak. The sensor sends CLENCH on release, stamped at
# release time, and SIGNAL at about 4 Hz, so the peak is in the second or so before it.
PEAK_WINDOW_S = 1.5
# A composition that takes longer than this was almost certainly abandoned and resumed; its
# wall-clock time says nothing about effort, so compose_s is left empty.
MAX_COMPOSE_S = 600.0

GESTURE_KINDS = frozenset({"CLENCH", "LONG_CLENCH", "DOUBLE_BLINK"})
LEVELS = frozenset({"calm", "normal", "elevated"})


def ts(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


def _finite(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if math.isfinite(x) else None


def reason_code(reason: str | None, blocked_text: str | None = None) -> str | None:
    if reason is None:
        return None
    if reason in REASON_CODES:
        return REASON_CODES[reason]
    if blocked_text is not None and reason == blocked_text:
        return "blocked"
    return "other"


@dataclass
class Row:
    table: str
    values: dict[str, Any]


class Tracker:
    """Keeps the little bit of state needed to derive rows: the last SETTINGS scan speed, recent
    SIGNAL samples (for a clench's peak margin), and when the current message started."""

    def __init__(self, simulated: bool = False) -> None:
        self.simulated = simulated
        self.scan_ms: int | None = None
        self.blocked_text: str | None = None
        self.last_signal_t: float | None = None
        self.recent: deque[tuple[float, float]] = deque()  # (t, emg/threshold)
        self.compose_start: float | None = None

    def handle(self, msg: dict[str, Any], received_at: float) -> list[Row]:
        kind = msg.get("type")
        if kind == "SETTINGS":
            scan_ms = msg.get("scan_ms")
            if isinstance(scan_ms, int) and scan_ms > 0:
                self.scan_ms = scan_ms
            return []
        if kind == "SIGNAL":
            return self._signal(msg)
        if kind == "INPUT_EVENT":
            return self._input_event(msg)
        if kind == "METRICS":
            return self._metrics(msg, received_at)
        if kind == "STATE":
            return self._state(msg)
        return []

    def _signal(self, msg: dict[str, Any]) -> list[Row]:
        t = _finite(msg.get("t"))
        if t is None:
            return []
        blocked = msg.get("blocked")
        self.blocked_text = blocked if isinstance(blocked, str) and blocked else None
        # The core replays its cached SIGNAL to every new console, so a reconnect repeats the last
        # sample. Keep time strictly moving forward.
        if self.last_signal_t is not None and t <= self.last_signal_t:
            return []
        self.last_signal_t = t
        emg = _finite(msg.get("emg"))
        threshold = _finite(msg.get("threshold"))
        margin = emg / threshold if emg is not None and threshold else None
        if margin is not None:
            self.recent.append((t, margin))
        while self.recent and self.recent[0][0] < t - 2 * PEAK_WINDOW_S:
            self.recent.popleft()
        ch = msg.get("ch")
        ch_vals = [_finite(c) for c in ch] if isinstance(ch, list) else None
        if ch_vals is not None and any(v is None for v in ch_vals):
            ch_vals = None
        connected = msg.get("connected")
        return [Row("signal", {
            "time": ts(t), "simulated": self.simulated, "ch": ch_vals, "emg": emg,
            "threshold": threshold, "margin": margin,
            "connected": connected if isinstance(connected, bool) else None,
            "blocked": self.blocked_text is not None,
        })]

    def peak_margin(self, t: float) -> float | None:
        window = [m for (st, m) in self.recent if t - PEAK_WINDOW_S <= st <= t]
        return max(window) if window else None

    def _input_event(self, msg: dict[str, Any]) -> list[Row]:
        t = _finite(msg.get("t"))
        kind = msg.get("kind")
        source = msg.get("source")
        accepted = msg.get("accepted")
        if t is None or kind not in GESTURE_KINDS or source not in ("muse", "dev") or not isinstance(accepted, bool):
            return []
        strength = _finite(msg.get("strength")) if kind == "CLENCH" else None
        duration = _finite(msg.get("duration")) if kind == "LONG_CLENCH" else None
        peak = self.peak_margin(t) if kind == "CLENCH" and source == "muse" else None
        if accepted and kind == "CLENCH" and self.compose_start is None:
            self.compose_start = t
        return [Row("gestures", {
            "time": ts(t), "simulated": self.simulated, "kind": kind, "source": source,
            "accepted": accepted,
            "reason": None if accepted else reason_code(msg.get("reason"), self.blocked_text) or "other",
            "strength": strength, "duration": duration, "peak_margin": peak,
        })]

    def _metrics(self, msg: dict[str, Any], received_at: float) -> list[Row]:
        # METRICS has no timestamp: it is sent right after the confirm clench, so receipt time is it.
        fields = ("selections", "scan_steps", "day1_selections", "day1_scan_steps")
        vals = {f: msg.get(f) for f in fields}
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in vals.values()):
            return []
        compose = None
        if self.compose_start is not None:
            elapsed = received_at - self.compose_start
            if 0 <= elapsed <= MAX_COMPOSE_S:
                compose = elapsed
        self.compose_start = None
        per_step = self.scan_ms / 1000 if self.scan_ms else None
        return [Row("messages", {
            "time": ts(received_at), "simulated": self.simulated, **vals,
            "scan_ms": self.scan_ms,
            "wait_s": vals["scan_steps"] * per_step if per_step is not None else None,
            "day1_wait_s": vals["day1_scan_steps"] * per_step if per_step is not None else None,
            "compose_s": compose,
        })]

    def _state(self, msg: dict[str, Any]) -> list[Row]:
        t = _finite(msg.get("t"))
        level = msg.get("level")
        if t is None or level not in LEVELS:
            return []
        eyes = msg.get("eyes_closed")
        return [Row("body_state", {
            "time": ts(t), "simulated": self.simulated, "level": level,
            "bpm": _finite(msg.get("hr")), "motion": _finite(msg.get("motion")),
            "eyes_closed": eyes if isinstance(eyes, bool) else None,
        })]


COLUMNS: dict[str, tuple[str, ...]] = {
    "signal": ("time", "simulated", "ch", "emg", "threshold", "margin", "connected", "blocked"),
    "gestures": ("time", "simulated", "kind", "source", "accepted", "reason", "strength", "duration", "peak_margin"),
    "messages": ("time", "simulated", "selections", "scan_steps", "day1_selections", "day1_scan_steps",
                 "scan_ms", "wait_s", "day1_wait_s", "compose_s"),
    "body_state": ("time", "simulated", "level", "bpm", "motion", "eyes_closed"),
}
