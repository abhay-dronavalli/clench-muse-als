"""What the insights page shows. Every query reads a continuous aggregate, never a raw table.

Ability charts use headband gestures only (source = 'muse'): the keyboard stand-in always reports
full strength and is never refused, so counting it would make the person look stronger than they are.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from insights import config, rules

TZ = config.LOCAL_TZ
REASONS = ("disconnected", "stale", "blocked", "clock_skew", "paused", "no_board", "other")
# A day whose average threshold moved more than this from the day before is marked as a
# recalibration: strength is measured against the calibration, so a reset hides a decline.
RECALIBRATION_CHANGE = 0.05


def today_local(now: datetime | None = None) -> date:
    return (now or datetime.now(ZoneInfo(TZ))).astimezone(ZoneInfo(TZ)).date()


def _rows(conn: psycopg.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def _ratio(num: Any, den: Any) -> float | None:
    return float(num) / float(den) if num is not None and den else None


def gestures_daily(conn: psycopg.Connection, days: int) -> list[dict[str, Any]]:
    rows = _rows(conn, f"""
        SELECT (bucket AT TIME ZONE %s)::date AS day, simulated, gestures, clenches, refused,
               {', '.join(f'refused_{r}' for r in REASONS)}, median_strength, median_margin
        FROM gestures_daily
        WHERE source = 'muse' AND bucket >= now() - make_interval(days => %s)
        ORDER BY bucket, simulated""", (TZ, days))
    out = []
    for r in rows:
        out.append({
            "day": r["day"].isoformat(), "simulated": r["simulated"],
            "gestures": r["gestures"], "clenches": r["clenches"], "refused": r["refused"],
            "refusal_rate": _ratio(r["refused"], r["gestures"]),
            "refusal_by_reason": {k: _ratio(r[f"refused_{k}"], r["gestures"]) for k in REASONS},
            "median_strength": r["median_strength"], "median_margin": r["median_margin"],
        })
    return out


def messages_daily(conn: psycopg.Connection, days: int) -> list[dict[str, Any]]:
    rows = _rows(conn, """
        SELECT (bucket AT TIME ZONE %s)::date AS day, simulated, messages, clenches, day1_clenches,
               wait_s, wait_n, compose_s, compose_n
        FROM messages_daily
        WHERE bucket >= now() - make_interval(days => %s)
        ORDER BY bucket, simulated""", (TZ, days))
    return [{
        "day": r["day"].isoformat(), "simulated": r["simulated"], "messages": r["messages"],
        "clenches_per_message": _ratio(r["clenches"], r["messages"]),
        "day1_clenches_per_message": _ratio(r["day1_clenches"], r["messages"]),
        "wait_s_per_message": _ratio(r["wait_s"], r["wait_n"]),
        "compose_s_per_message": _ratio(r["compose_s"], r["compose_n"]),
    } for r in rows]


def signal_daily(conn: psycopg.Connection, days: int) -> list[dict[str, Any]]:
    rows = _rows(conn, """
        SELECT (bucket AT TIME ZONE %s)::date AS day, simulated, samples, connected_samples,
               threshold_sum, threshold_n
        FROM signal_daily
        WHERE bucket >= now() - make_interval(days => %s)
        ORDER BY bucket, simulated""", (TZ, days))
    out: list[dict[str, Any]] = []
    prev: dict[bool, float] = {}
    for r in rows:
        avg = _ratio(r["threshold_sum"], r["threshold_n"])
        before = prev.get(r["simulated"])
        recal = avg is not None and before is not None and abs(avg - before) > RECALIBRATION_CHANGE * before
        if avg is not None:
            prev[r["simulated"]] = avg
        out.append({"day": r["day"].isoformat(), "simulated": r["simulated"],
                    "connected_share": _ratio(r["connected_samples"], r["samples"]),
                    "threshold": avg, "recalibrated": recal})
    return out


def live_today(conn: psycopg.Connection) -> dict[str, Any]:
    """Today's live totals from the hourly aggregates (real-time, so the last hour is included)."""
    g = _rows(conn, """
        SELECT source, sum(gestures) AS gestures, sum(clenches) AS clenches, sum(refused) AS refused
        FROM gestures_hourly
        WHERE NOT simulated AND bucket >= date_trunc('day', now() AT TIME ZONE %s) AT TIME ZONE %s
        GROUP BY source""", (TZ, TZ))
    m = _rows(conn, """
        SELECT sum(messages) AS messages, sum(clenches) AS clenches
        FROM messages_hourly
        WHERE NOT simulated AND bucket >= date_trunc('day', now() AT TIME ZONE %s) AT TIME ZONE %s""",
              (TZ, TZ))
    by_source = {r["source"]: r for r in g}
    muse, dev = by_source.get("muse", {}), by_source.get("dev", {})
    return {
        "headband_gestures": int(muse.get("gestures") or 0),
        "headband_clenches": int(muse.get("clenches") or 0),
        "headband_refused": int(muse.get("refused") or 0),
        "keyboard_gestures": int(dev.get("gestures") or 0),
        "messages": int(m[0]["messages"] or 0) if m else 0,
    }


def suggestion(conn: psycopg.Connection, cfg: config.Config, today: date | None = None) -> rules.Suggestion:
    rows = _rows(conn, """
        SELECT (bucket AT TIME ZONE %s)::date AS day, simulated, median_margin
        FROM gestures_daily
        WHERE source = 'muse' AND bucket >= now() - make_interval(days => %s)""",
                 (TZ, cfg.decline_window_days + 2))
    points = rules.merge_days((r["day"], r["simulated"], r["median_margin"]) for r in rows)
    return rules.decline(points, today or today_local(), drop=cfg.decline_drop,
                         window_days=cfg.decline_window_days, min_days=cfg.decline_min_days)
