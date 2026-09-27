from datetime import date, timedelta

from insights.rules import MESSAGE, DayPoint, decline, merge_days

TODAY = date(2026, 9, 27)


def series(values, simulated=True, end=TODAY):
    """values[-1] lands on `end`."""
    n = len(values)
    return [DayPoint(end - timedelta(days=n - 1 - i), v, simulated) for i, v in enumerate(values)]


def test_steady_decline_shows_the_card():
    pts = series([1.9, 1.88, 1.86, 1.84, 1.8, 1.78, 1.75, 1.72, 1.7, 1.68, 1.66, 1.64, 1.62, 1.6])
    s = decline(pts, TODAY, drop=0.15)
    assert s.show and s.message == MESSAGE
    assert s.start == 1.88 and s.end == 1.62
    assert round(s.drop, 2) == 0.26
    assert s.uses_simulated


def test_small_change_does_not_show_it():
    pts = series([1.9, 1.85, 1.9, 1.88, 1.86, 1.9, 1.87, 1.85, 1.84, 1.86, 1.83, 1.82, 1.84, 1.85])
    s = decline(pts, TODAY, drop=0.15)
    assert not s.show and s.message is None


def test_a_drop_exactly_at_the_threshold_does_not_show_it():
    pts = series([2.0, 2.0, 2.0, 1.9, 1.9, 1.9, 1.85, 1.85, 1.85])
    assert not decline(pts, TODAY, drop=0.15, min_days=7).show


def test_one_bad_day_at_the_end_does_not_trigger():
    pts = series([1.9] * 13 + [1.2])
    assert not decline(pts, TODAY, drop=0.15).show


def test_improvement_never_triggers():
    pts = series([1.4 + 0.04 * i for i in range(14)])
    s = decline(pts, TODAY, drop=0.15)
    assert not s.show and s.drop < 0


def test_too_few_days_says_so():
    s = decline(series([1.9, 1.5, 1.2, 1.0, 0.9]), TODAY, drop=0.15, min_days=7)
    assert not s.show and "only 5 days" in s.reason


def test_days_outside_the_window_are_ignored():
    old = series([3.0] * 10, end=TODAY - timedelta(days=20))
    recent = series([1.5] * 10)
    assert not decline(old + recent, TODAY, drop=0.15).show


def test_live_day_replaces_a_simulated_day():
    d = TODAY - timedelta(days=1)
    pts = merge_days([(d, True, 1.9), (d, False, 1.1), (TODAY, True, None)])
    assert pts == [DayPoint(d, 1.1, False)]


def test_live_only_window_is_not_flagged_as_simulated():
    pts = series([1.9] * 5 + [1.5] * 5, simulated=False)
    s = decline(pts, TODAY, drop=0.15)
    assert s.show and not s.uses_simulated
