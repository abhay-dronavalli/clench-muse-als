"""Drill scoring, queue timing and release behavior without Tk or a headband."""
import pytest

from station_activities import DrillState, LONG_BLINK, CLENCH


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def prompt(require_neutral=False, rounds=2):
    clock = Clock()
    drill = DrillState(rounds=rounds, inputs=(LONG_BLINK,), clock=clock,
                       require_neutral=require_neutral)
    clock.now += 2
    if require_neutral:
        drill.on_detector_state(True, clock.now-.7)
        drill.on_detector_state(True)
    drill.update_frame(0)
    assert drill.current.want == LONG_BLINK
    return drill, clock


def test_long_blink_during_eye_prompt_is_a_hit():
    drill, clock = prompt()
    clock.now += .7
    drill.on_gesture(LONG_BLINK, "held 400 ms")
    assert drill.tally()[LONG_BLINK]["hit"] == 1
    assert drill.timeline[-1]["disposition"] == "hit"
    assert drill.rounds[0].latency_ms == pytest.approx(700)


def test_held_eyes_cannot_start_another_eye_prompt():
    drill, clock = prompt(require_neutral=True)
    clock.now += .7
    drill.on_gesture(LONG_BLINK, "")
    clock.now += 3
    drill.on_detector_state(False)
    drill.update_frame(0)
    assert drill.current is None
    assert drill.tally()[LONG_BLINK]["miss"] == 0
    drill.on_detector_state(True)
    clock.now += .7
    drill.on_detector_state(True)
    drill.update_frame(0)
    assert drill.current.want == LONG_BLINK


def test_stale_signal_cannot_start_prompt():
    clock = Clock()
    drill = DrillState(clock=clock, require_neutral=True)
    drill.on_detector_state(True)
    clock.now += 2
    drill.update_frame(0)
    assert drill.current is None


def test_logged_late_long_blink_does_not_turn_miss_into_hit():
    drill, clock = prompt(rounds=1)
    clock.now += 4.1
    drill.on_gesture(LONG_BLINK, "late")
    assert drill.tally()[LONG_BLINK]["miss"] == 1
    assert drill.stray == 1
    assert "outside" in drill.feedback


def test_on_time_detection_delivered_late_corrects_ui_timeout():
    drill, clock = prompt(rounds=1)
    detected_at = clock.now + .7
    clock.now += 4.1
    drill.update_frame(0)
    assert drill.tally()[LONG_BLINK]["miss"] == 1
    drill.on_gesture(LONG_BLINK, "queued while UI stalled", detected_at)
    assert drill.tally()[LONG_BLINK]["hit"] == 1
    assert drill.tally()[LONG_BLINK]["miss"] == 0
    assert drill.rounds[0].latency_ms == pytest.approx(700)


def test_wrong_input_is_visible_and_later_hold_is_not_a_second_hit():
    drill, clock = prompt()
    drill.inputs.append(CLENCH)
    clock.now += .2
    drill.on_gesture(CLENCH, "")
    clock.now += .5
    drill.on_gesture(LONG_BLINK, "")
    assert drill.tally()[LONG_BLINK]["wrong"] == 1
    assert drill.tally()[LONG_BLINK]["hit"] == 0
    assert drill.stray == 1
    assert [e["disposition"] for e in drill.report()["timeline"]] == [
        "wrong", "outside active prompt or duplicate"]
