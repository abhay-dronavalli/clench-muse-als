"""The clench-decline suggestion. Pure: takes daily medians, returns whether to show the card.

A suggestion only. Nothing here (or anywhere in insights/) changes a Clench setting.

Rule: over the last `window_days` days that have data, compare the median of the first `edge_days`
daily median margins with the median of the last `edge_days`. If it fell by more than `drop`
(in units of the threshold, so 0.15 = 15% of the threshold), show the card. Comparing a few days at
each end rather than fitting a slope keeps one unusually good or bad day from deciding it.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

MESSAGE = "Clench signal is weakening. Consider recalibrating or switching to eyebrow raises."
EDGE_DAYS = 3


@dataclass(frozen=True)
class DayPoint:
    day: date
    margin: float
    simulated: bool


@dataclass(frozen=True)
class Suggestion:
    show: bool
    message: str | None
    reason: str
    start: float | None = None
    end: float | None = None
    drop: float | None = None
    days_used: int = 0
    uses_simulated: bool = False


def merge_days(rows: Iterable[tuple[date, bool, float | None]]) -> list[DayPoint]:
    """One point per day. A day with live data uses it; a day with only simulated data uses that.
    Days without a median margin are skipped."""
    live: dict[date, float] = {}
    sim: dict[date, float] = {}
    for day, simulated, margin in rows:
        if margin is None:
            continue
        (sim if simulated else live)[day] = float(margin)
    out = [DayPoint(d, live[d], False) for d in live]
    out += [DayPoint(d, m, True) for d, m in sim.items() if d not in live]
    return sorted(out, key=lambda p: p.day)


def decline(points: Iterable[DayPoint], today: date, *, drop: float, window_days: int = 14,
            min_days: int = 7, edge_days: int = EDGE_DAYS) -> Suggestion:
    first_day = today - timedelta(days=window_days - 1)
    window = sorted((p for p in points if first_day <= p.day <= today), key=lambda p: p.day)
    if len(window) < max(min_days, 2 * edge_days):
        return Suggestion(False, None, f"only {len(window)} days with clench data in the last "
                          f"{window_days} (need {max(min_days, 2 * edge_days)})", days_used=len(window))
    start = statistics.median(p.margin for p in window[:edge_days])
    end = statistics.median(p.margin for p in window[-edge_days:])
    fell = start - end
    uses_sim = any(p.simulated for p in window)
    common = dict(start=start, end=end, drop=fell, days_used=len(window), uses_simulated=uses_sim)
    if fell > drop:
        return Suggestion(True, MESSAGE, f"median margin fell {fell:.2f} (from {start:.2f}x to "
                          f"{end:.2f}x threshold), more than {drop:.2f}", **common)
    return Suggestion(False, None, f"median margin changed {-fell:+.2f} (from {start:.2f}x to "
                      f"{end:.2f}x threshold); card shows above a {drop:.2f} drop", **common)
