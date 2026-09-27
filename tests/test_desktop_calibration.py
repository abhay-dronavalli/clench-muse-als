"""Desktop agent: eye calibration flow and storage, and the F8 / F9 keyboard stand-in. Pure."""

import pytest

from desktop.agent import calibration
from desktop.agent.calibration import SETTLE_S, Flow
from desktop.agent.hotkeys import Key, StandIn
from desktop.eyedid.convert import Display

LAPTOP = Display(1920, 1080, 344.0, 193.0)


class Harness:
    def __init__(self, accept=True):
        self.sent, self.timers, self.ended = [], [], []
        self.accept = accept
        self.flow = Flow(self.command, lambda s, fn: self.timers.append((s, fn)), self.ended.append)

    def command(self, msg):
        self.sent.append(msg)
        return self.accept

    def fire(self):
        timers, self.timers = self.timers, []
        for _, fn in timers:
            fn()


def test_each_dot_settles_before_samples_are_collected():
    h = Harness()
    assert h.flow.start((0, 0, 1920, 1080))
    assert h.sent == [{"cmd": "calibrate", "points": 5, "roi": [0, 0, 1920, 1080]}]
    h.flow.on_worker({"type": "calib_point", "x": 100.0, "y": 200.0})
    assert h.flow.point == (100.0, 200.0) and not h.flow.collecting
    assert h.timers[0][0] == SETTLE_S
    h.fire()
    assert h.sent[-1] == {"cmd": "collect"} and h.flow.collecting
    h.flow.on_worker({"type": "calib_progress", "p": 0.5})
    assert h.flow.progress == 0.5
    assert h.flow.on_worker({"type": "calib_done", "data": [1, 2.5]}) == [1.0, 2.5]
    assert h.ended == ["done"] and not h.flow.running


def test_a_settle_timer_from_an_earlier_dot_does_nothing():
    h = Harness()
    h.flow.start((0, 0, 1920, 1080))
    h.flow.on_worker({"type": "calib_point", "x": 1.0, "y": 1.0})
    h.flow.on_worker({"type": "calib_point", "x": 2.0, "y": 2.0})  # the SDK moved on
    h.fire()
    assert h.sent.count({"cmd": "collect"}) == 1


def test_cancel_stops_the_sdk_and_a_dead_worker_fails_at_once():
    h = Harness()
    h.flow.start((0, 0, 1920, 1080))
    h.flow.cancel()
    assert h.sent[-1] == {"cmd": "stop_calibration"} and h.ended == ["cancelled"]
    dead = Harness(accept=False)
    assert not dead.flow.start((0, 0, 1920, 1080)) and dead.ended == ["failed"]


def test_the_calibration_area_keeps_every_dot_on_the_screen():
    left, top, right, bottom = calibration.area(LAPTOP)
    # 14 mm in from each edge: about 78 px on this 1920 x 1080, 344 x 193 mm laptop
    assert (left, top) == pytest.approx((78.1, 78.3), abs=0.1)
    assert (1920 - right, 1080 - bottom) == pytest.approx((left, top))
    ring = 12 * LAPTOP.px_per_mm  # the biggest thing drawn around a dot (the settle ring)
    assert left > ring and top > ring


def test_calibrations_are_kept_per_person_and_screen(tmp_path):
    calibration.save("taher", LAPTOP, [0.1, 0.2], tmp_path)
    assert calibration.load("taher", LAPTOP, tmp_path) == [0.1, 0.2]
    assert calibration.load("luis", LAPTOP, tmp_path) is None
    other = Display(2560, 1440, 344.0, 193.0)
    assert calibration.load("taher", other, tmp_path) is None  # another screen: calibrate again
    with pytest.raises(ValueError):
        calibration.path_for("../etc", tmp_path)


def test_f8_tap_is_a_clench_timed_at_the_press():
    sent = []
    s = StandIn(sent.append, long_ms=2500)
    s.key(Key("F8", True, False, 10.0))
    s.key(Key("F8", True, False, 10.1))  # auto-repeat
    s.tick(10.3)
    s.key(Key("F8", False, False, 10.4))
    assert sent == [{"type": "CLENCH", "t": 10.0, "strength": 1.0}]


def test_f8_held_is_one_long_clench_while_still_held_and_no_clench_after():
    sent = []
    s = StandIn(sent.append, long_ms=2500)
    s.key(Key("F8", True, False, 10.0))
    s.tick(12.4)
    assert sent == []
    s.tick(12.5)
    s.tick(13.0)
    s.key(Key("F8", False, False, 13.2))
    assert sent == [{"type": "LONG_CLENCH", "t": 12.5, "duration": 2.5}]


def test_f9_is_a_double_blink_and_ctrl_f8_is_not_a_clench():
    sent = []
    s = StandIn(sent.append)
    s.key(Key("F9", True, False, 5.0))
    s.key(Key("F8", True, True, 6.0))
    s.key(Key("F8", False, True, 6.1))
    assert sent == [{"type": "DOUBLE_BLINK", "t": 5.0}]
