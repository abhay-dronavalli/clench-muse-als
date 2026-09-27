"""Simulated history: 4 weeks of plausible use in which clench strength slowly declines and refusals
slowly rise. Every row is marked simulated = true. It is an illustration for the demo, not data about
a real person.

    python -m insights.seed             load 28 days ending yesterday (refuses if simulated rows exist)
    python -m insights.seed --reset     delete the simulated rows first (asks; --yes skips)

Deterministic: a fixed random seed, and every value depends only on the day's index, so two runs on
the same date write identical rows.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from insights import config, db

SEED = 20260927
DAYS = 28
WAKE_HOUR, SLEEP_HOUR = 8, 20          # the headband is worn 08:00-20:00 local
THRESHOLD_UV = 35.0                    # one calibration for the whole period
SCAN_MS = 1000

# Start -> end of the 4 weeks. Modest on purpose.
STRENGTH = (0.72, 0.55)                # median CLENCH strength (0..1 of the calibrated range)
MARGIN = (1.95, 1.35)                  # median peak EMG / threshold at a clench
REFUSAL = (0.04, 0.12)                 # share of headband gestures the core refuses
DROPOUTS_PER_DAY = (1.0, 4.0)          # brief headband contact losses
REASON_WEIGHTS = {"disconnected": 0.35, "blocked": 0.30, "stale": 0.25, "clock_skew": 0.10}


def lerp(pair: tuple[float, float], p: float) -> float:
    return pair[0] + (pair[1] - pair[0]) * p


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class Day:
    gestures: list[tuple[Any, ...]] = field(default_factory=list)
    messages: list[tuple[Any, ...]] = field(default_factory=list)
    body: list[tuple[Any, ...]] = field(default_factory=list)
    clench_peaks: dict[int, float] = field(default_factory=dict)  # second of day -> margin
    dropouts: list[tuple[int, int]] = field(default_factory=list)  # (start s, end s) of day
    blocked: list[tuple[int, int]] = field(default_factory=list)


def day_events(index: int, n_days: int, day: date, tz: ZoneInfo) -> Day:
    """Gestures, messages and body state for one day. Seeded by the day's index only."""
    rng = random.Random(SEED * 100 + index)
    p = index / max(n_days - 1, 1)
    start = datetime(day.year, day.month, day.day, WAKE_HOUR, tzinfo=tz)
    span = (SLEEP_HOUR - WAKE_HOUR) * 3600
    out = Day()

    def at(sec: float) -> datetime:
        return start + timedelta(seconds=sec)

    refusal = lerp(REFUSAL, p)
    reasons, weights = zip(*REASON_WEIGHTS.items())

    def gesture(sec: float, kind: str, accepted: bool, reason: str | None = None) -> None:
        strength = peak = duration = None
        if kind == "CLENCH":
            strength = round(clip(rng.gauss(lerp(STRENGTH, p), 0.08), 0.05, 1.0), 3)
            peak = round(max(1.02, rng.gauss(lerp(MARGIN, p), 0.15)), 3)
            out.clench_peaks[int(sec)] = peak
        elif kind == "LONG_CLENCH":
            duration = round(rng.uniform(2.5, 3.2), 2)
        out.gestures.append((at(sec), True, kind, "muse", accepted, reason, strength, duration, peak))

    def attempt(sec: float, kind: str) -> float:
        """One intended gesture: refused tries are retried a couple of seconds later."""
        while rng.random() < refusal:
            gesture(sec, kind, False, rng.choices(reasons, weights)[0])
            sec += rng.uniform(1.5, 3.5)
        gesture(sec, kind, True)
        return sec

    n_messages = rng.randint(25, 40) - (4 if day.weekday() >= 5 else 0)
    for msg_sec in sorted(rng.uniform(600, span - 60) for _ in range(n_messages)):
        selections = rng.choices([2, 3, 4, 5], [0.35, 0.35, 0.2, 0.1])[0]
        scan_steps = sum(rng.randint(0, 3) for _ in range(selections - 1))
        sec = msg_sec
        first = None
        for i in range(selections):
            sec += rng.uniform(1.2, 2.8) + (scan_steps / selections)
            done = attempt(sec, "CLENCH")
            first = first if first is not None else done
            sec = done
            if i == 1 and rng.random() < 0.3:  # a back-and-forth
                sec = attempt(sec + rng.uniform(1, 2), "DOUBLE_BLINK")
        confirm = sec + rng.uniform(0.3, 0.8)
        day1_sel = selections + rng.randint(1, 3)
        day1_steps = scan_steps + rng.randint(2, 6)
        out.messages.append((at(confirm), True, selections, scan_steps, day1_sel, day1_steps, SCAN_MS,
                             float(scan_steps), float(day1_steps), round(confirm - first, 2)))
    if rng.random() < 0.25:
        attempt(rng.uniform(0, span), "LONG_CLENCH")
    for _ in range(rng.randint(0, 2)):  # a gesture or two while the caregiver had input paused
        gesture(rng.uniform(0, span), "CLENCH", False, "paused")

    for _ in range(max(0, round(rng.gauss(lerp(DROPOUTS_PER_DAY, p), 0.8)))):
        s = rng.randint(0, span - 200)
        out.dropouts.append((s, s + rng.randint(20, 120)))
    for _ in range(rng.randint(5, 9) + round(6 * p)):
        s = rng.randint(0, span - 20)
        out.blocked.append((s, s + rng.randint(2, 10)))

    for k in range(0, span, 300):
        bpm = rng.gauss(76 + 3 * p, 4)
        level = "elevated" if bpm > 86 else "calm" if bpm < 68 else "normal"
        out.body.append((at(k), True, level, round(bpm, 1), round(abs(rng.gauss(0.2, 0.1)), 2),
                         rng.random() < 0.03))
    out.gestures.sort(key=lambda r: r[0])
    return out


def signal_rows(index: int, day: date, tz: ZoneInfo, events: Day, hz: int) -> Iterator[tuple[Any, ...]]:
    """Signal samples for one day. Rest EMG with noise, the clench peaks at the clench times,
    and nothing but `connected = false` during a dropout."""
    rng = random.Random(SEED * 1000 + index)
    start = datetime(day.year, day.month, day.day, WAKE_HOUR, tzinfo=tz)
    span = (SLEEP_HOUR - WAKE_HOUR) * 3600
    down = {s for a, b in events.dropouts for s in range(a, b)}
    blocked = {s for a, b in events.blocked for s in range(a, b)}
    step = 1 / hz
    for k in range(span * hz):
        sec = k * step
        t = start + timedelta(seconds=sec)
        s = int(sec)
        if s in down:
            yield (t, True, [], None, None, None, False, False)
            continue
        if s in events.clench_peaks and k % hz == 0:
            emg = events.clench_peaks[s] * THRESHOLD_UV
        else:
            emg = clip(rng.gauss(11.0, 2.5) + 1.5 * math.sin(sec / 900), 3.0, THRESHOLD_UV * 0.8)
        emg = round(emg, 2)
        ch = [round(emg * rng.uniform(0.9, 1.1), 2), round(rng.gauss(9, 1.5), 2),
              round(rng.gauss(9, 1.5), 2), round(emg * rng.uniform(0.9, 1.1), 2)]
        yield (t, True, ch, emg, THRESHOLD_UV, round(emg / THRESHOLD_UV, 4), True, s in blocked)


def days_ending_yesterday(n: int, tz: ZoneInfo) -> list[date]:
    yesterday = datetime.now(tz).date() - timedelta(days=1)
    return [yesterday - timedelta(days=n - 1 - i) for i in range(n)]


def columnstore_job(conn: psycopg.Connection) -> int | None:
    row = conn.execute("SELECT job_id FROM timescaledb_information.jobs "
                       "WHERE hypertable_name = 'signal' AND proc_name = 'policy_compression'").fetchone()
    return row[0] if row else None


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m insights.seed")
    p.add_argument("--reset", action="store_true", help="delete existing simulated rows first")
    p.add_argument("--yes", action="store_true", help="do not ask before --reset")
    p.add_argument("--days", type=int, default=DAYS)
    p.add_argument("--signal-hz", type=int, default=1, help="simulated signal samples per second (default 1)")
    args = p.parse_args(argv)
    tz = ZoneInfo(config.LOCAL_TZ)
    t0 = time.monotonic()

    with db.connect(db.require_url()) as conn:
        db.apply_schema(conn)
        existing = sum(c["simulated"] for c in db.row_counts(conn).values())
        if existing and not args.reset:
            sys.exit(f"{existing:,} simulated rows already exist; pass --reset to replace them.")
        # Pause the columnstore policy while deleting and backfilling, so it and the load never
        # wait on each other (a run already in progress is waited out by execute_retrying).
        job = columnstore_job(conn)
        if job is not None:
            conn.execute("SELECT alter_job(%s, scheduled => false)", (job,))
        try:
            if args.reset and existing:
                if not args.yes and input(f"Delete {existing:,} simulated rows (live rows are kept)? [y/N] ").lower() != "y":
                    sys.exit("nothing changed")
                # Deleting from compressed signal chunks decompresses them; lift the per-statement
                # guard (100k rows by default) for this session only.
                conn.execute("SET timescaledb.max_tuples_decompressed_per_dml_transaction = 0")
                for t in db.TABLES:
                    db.execute_retrying(conn, f"DELETE FROM {t} WHERE simulated")
                print(f"deleted {existing:,} simulated rows")

            counts = dict.fromkeys(db.TABLES, 0)
            for i, day in enumerate(days_ending_yesterday(args.days, tz)):
                ev = day_events(i, args.days, day, tz)
                with conn.transaction():
                    counts["gestures"] += db.copy_rows(conn, "gestures", ev.gestures)
                    counts["messages"] += db.copy_rows(conn, "messages", ev.messages)
                    counts["body_state"] += db.copy_rows(conn, "body_state", ev.body)
                    counts["signal"] += db.copy_rows(conn, "signal", signal_rows(i, day, tz, ev, args.signal_hz))
                print(f"  {day}  {len(ev.messages):>3} messages  {len(ev.gestures):>4} gestures", flush=True)
            print("loaded " + ", ".join(f"{t} {n:,}" for t, n in counts.items()))
            db.refresh_all(conn)
            print("continuous aggregates refreshed")
            n = db.compress_signal(conn, "1 day")
            print(f"converted {n} signal chunks to the columnstore")
        finally:
            if job is not None:
                conn.execute("SELECT alter_job(%s, scheduled => true)", (job,))
        db.print_stats(conn)
    print(f"done in {time.monotonic() - t0:.0f} s")


if __name__ == "__main__":
    main()
