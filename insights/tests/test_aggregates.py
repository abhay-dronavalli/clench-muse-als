"""Continuous aggregate math, on fixtures in a throwaway schema (see conftest.pg).

Fixtures sit at local noon a few days back, so they are inside every query window, and use values
whose median, rate and per-message averages are easy to check by hand.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from insights import config, db, queries
from insights.app import build_payload

TZ = ZoneInfo(config.LOCAL_TZ)
TODAY = queries.today_local()
D3 = TODAY - timedelta(days=3)
D2 = TODAY - timedelta(days=2)


def at(day, hour=12, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def g(ts, *, kind="CLENCH", source="muse", accepted=True, reason=None, strength=None, peak=None, sim=False):
    return {"time": ts, "simulated": sim, "kind": kind, "source": source, "accepted": accepted,
            "reason": reason, "strength": strength, "duration": 2.6 if kind == "LONG_CLENCH" else None,
            "peak_margin": peak}


def m(ts, sel, *, compose=None, sim=False):
    return {"time": ts, "simulated": sim, "selections": sel, "scan_steps": 2, "day1_selections": sel + 3,
            "day1_scan_steps": 6, "scan_ms": 1000, "wait_s": 2.0, "day1_wait_s": 6.0, "compose_s": compose}


def load(conn, gestures=(), messages=(), signal=()):
    db.insert_rows(conn, "gestures", list(gestures))
    db.insert_rows(conn, "messages", list(messages))
    db.insert_rows(conn, "signal", list(signal))
    db.refresh_all(conn)


def day(rows, d, simulated=False):
    [r] = [r for r in rows if r["day"] == d.isoformat() and r["simulated"] == simulated]
    return r


def test_daily_median_strength_and_margin_are_exact(pg):
    load(pg, [g(at(D3, 9), strength=0.2, peak=1.1), g(at(D3, 10), strength=0.4, peak=1.3),
              g(at(D3, 15), strength=0.9, peak=2.0),
              g(at(D2, 9), strength=0.2, peak=1.2), g(at(D2, 11), strength=0.4, peak=1.4),
              g(at(D2, 13), strength=0.6, peak=1.6), g(at(D2, 16), strength=0.8, peak=1.8)])
    rows = queries.gestures_daily(pg, 10)
    assert day(rows, D3)["median_strength"] == pytest.approx(0.4)
    assert day(rows, D3)["median_margin"] == pytest.approx(1.3)
    assert day(rows, D2)["median_strength"] == pytest.approx(0.5)  # even count: midpoint
    assert day(rows, D2)["median_margin"] == pytest.approx(1.5)


def test_hourly_median_is_per_hour(pg):
    load(pg, [g(at(D3, 9, 5), strength=0.2), g(at(D3, 9, 50), strength=0.6), g(at(D3, 10, 5), strength=0.9)])
    rows = pg.execute("SELECT bucket, median_strength FROM gestures_hourly WHERE source = 'muse' "
                      "ORDER BY bucket").fetchall()
    assert [round(r[1], 3) for r in rows] == [0.4, 0.9]


def test_keyboard_gestures_do_not_count_toward_ability(pg):
    load(pg, [g(at(D3), strength=0.3, peak=1.2), g(at(D3, 13), source="dev", strength=1.0)])
    r = day(queries.gestures_daily(pg, 10), D3)
    assert r["median_strength"] == pytest.approx(0.3) and r["gestures"] == 1


def test_refusal_rate_by_reason(pg):
    ok = [g(at(D3, 9, i), strength=0.5) for i in range(6)]
    refused = [g(at(D3, 10), accepted=False, reason="disconnected", strength=0.5),
               g(at(D3, 10, 5), accepted=False, reason="disconnected", strength=0.5),
               g(at(D3, 11), kind="DOUBLE_BLINK", accepted=False, reason="stale"),
               g(at(D3, 12), kind="LONG_CLENCH", accepted=False, reason="blocked")]
    load(pg, ok + refused)
    r = day(queries.gestures_daily(pg, 10), D3)
    assert r["gestures"] == 10 and r["refused"] == 4
    assert r["refusal_rate"] == pytest.approx(0.4)
    assert r["refusal_by_reason"]["disconnected"] == pytest.approx(0.2)
    assert r["refusal_by_reason"]["stale"] == pytest.approx(0.1)
    assert r["refusal_by_reason"]["blocked"] == pytest.approx(0.1)
    assert r["refusal_by_reason"]["paused"] == 0


def test_days_follow_local_midnight_not_utc(pg):
    # 23:30 and 00:30 local are the same UTC day in New York summer time, but different local days.
    load(pg, [g(at(D3, 23, 30), strength=0.2), g(at(D2, 0, 30), strength=0.8)])
    rows = queries.gestures_daily(pg, 10)
    assert day(rows, D3)["median_strength"] == pytest.approx(0.2)
    assert day(rows, D2)["median_strength"] == pytest.approx(0.8)


def test_clenches_and_seconds_per_message_roll_up_hourly_to_daily(pg):
    load(pg, messages=[m(at(D3, 9), 2, compose=6.0), m(at(D3, 9, 30), 4, compose=10.0),
                       m(at(D3, 14), 3, compose=None)])
    r = day(queries.messages_daily(pg, 10), D3)
    assert r["messages"] == 3
    assert r["clenches_per_message"] == pytest.approx(3.0)
    assert r["day1_clenches_per_message"] == pytest.approx(6.0)
    assert r["compose_s_per_message"] == pytest.approx(8.0)  # the message without a time is left out
    assert r["wait_s_per_message"] == pytest.approx(2.0)
    hourly = pg.execute("SELECT sum(messages), sum(clenches) FROM messages_hourly").fetchone()
    assert tuple(hourly) == (3, 9)


def test_simulated_and_live_never_mix(pg):
    load(pg, [g(at(D3), strength=0.9, sim=True), g(at(D3, 13), strength=0.1)],
         [m(at(D3), 5, sim=True), m(at(D3, 13), 2)])
    rows = queries.gestures_daily(pg, 10)
    assert day(rows, D3, simulated=True)["median_strength"] == pytest.approx(0.9)
    assert day(rows, D3, simulated=False)["median_strength"] == pytest.approx(0.1)
    msgs = queries.messages_daily(pg, 10)
    assert day(msgs, D3, simulated=True)["clenches_per_message"] == 5
    assert day(msgs, D3, simulated=False)["clenches_per_message"] == 2


def test_real_time_aggregation_shows_rows_inserted_after_the_last_refresh(pg):
    load(pg)
    db.insert_rows(pg, "gestures", [g(datetime.now(TZ) - timedelta(seconds=30), strength=0.5)])
    today = queries.live_today(pg)
    assert today["headband_clenches"] == 1


def test_signal_daily_rolls_up_and_marks_a_recalibration(pg):
    def s(ts, threshold, connected=True):
        return {"time": ts, "simulated": False, "ch": [1.0, 2.0, 3.0, 4.0], "emg": 10.0, "threshold": threshold,
                "margin": 10.0 / threshold, "connected": connected, "blocked": False}
    load(pg, signal=[s(at(D3, 9), 35.0), s(at(D3, 10), 35.0), s(at(D3, 11), 35.0, connected=False),
                     s(at(D2, 9), 28.0)])
    rows = queries.signal_daily(pg, 10)
    assert day(rows, D3)["connected_share"] == pytest.approx(2 / 3)
    assert not day(rows, D3)["recalibrated"]
    assert day(rows, D2)["recalibrated"]


def test_suggestion_reads_the_daily_aggregate(pg):
    rows = []
    for i in range(14):
        d = TODAY - timedelta(days=13 - i)
        for k, off in enumerate((-0.1, 0.0, 0.1)):
            rows.append(g(at(d, 10 + k), strength=0.6, peak=1.9 - 0.04 * i + off, sim=True))
    load(pg, rows)
    cfg = config.load({"INSIGHTS_DECLINE_DROP": "0.15"})
    s = queries.suggestion(pg, cfg, TODAY)
    assert s.show and s.uses_simulated
    assert s.drop == pytest.approx(0.04 * 11, abs=1e-4)


def test_page_payload_splits_sources_on_one_day_axis(pg):
    load(pg, [g(at(D3), strength=0.7, sim=True), g(at(D2), strength=0.5)])
    cfg = config.load({})
    p = build_payload(queries.gestures_daily(pg, 8), queries.messages_daily(pg, 8), queries.signal_daily(pg, 8),
                      queries.live_today(pg), queries.suggestion(pg, cfg, TODAY), TODAY, 7)
    assert len(p["days"]) == 7 and p["days"][-1] == TODAY.isoformat()
    i3, i2 = p["days"].index(D3.isoformat()), p["days"].index(D2.isoformat())
    assert p["strength"]["simulated"][i3] == pytest.approx(0.7) and p["strength"]["live"][i3] is None
    assert p["strength"]["live"][i2] == pytest.approx(0.5) and p["strength"]["simulated"][i2] is None
