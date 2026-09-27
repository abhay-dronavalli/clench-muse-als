import asyncio
import json

import pytest

from sensor.detect.input import ClenchInput
from sensor.main import run_connection
from sensor.sources.muse import Sample


def profile():
    return {"emg_rest": 5.0, "emg_threshold": 20.0, "emg_peak": 55.0}


def armed_detector(long_ms=2500):
    detector = ClenchInput(profile(), long_ms=long_ms)
    # Enabling a sensor while the jaw is already active must not fire.
    assert detector.update(5.0, 0.0, enabled=True) == []
    assert detector.update(5.0, 0.7, enabled=True) == []
    return detector


def test_short_clench_emits_on_release_with_strength():
    detector = armed_detector()
    assert detector.update(30.0, 1.0, enabled=True) == []
    events = detector.update(5.0, 1.25, enabled=True)
    assert events == [{"type": "CLENCH", "t": 1.25, "strength": 0.5}]


def test_long_clench_emits_help_once_and_suppresses_release():
    detector = armed_detector(long_ms=1000)
    assert detector.update(30.0, 1.0, enabled=True) == []
    events = detector.update(30.0, 2.05, enabled=True)
    assert events[0]["type"] == "LONG_CLENCH"
    assert events[0]["duration"] >= 1.0
    assert detector.update(5.0, 2.2, enabled=True) == []


def test_disabled_or_blocked_input_resets_and_rearms():
    detector = armed_detector()
    detector.update(30.0, 1.0, enabled=True)
    assert detector.update(30.0, 1.1, enabled=False) == []
    assert detector.update(5.0, 1.2, enabled=True) == []
    assert detector.update(5.0, 1.9, enabled=True) == []
    detector.update(30.0, 2.0, enabled=True)
    assert detector.update(30.0, 2.1, enabled=True, blocked="Head moving") == []


class StopRun(Exception):
    pass


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, raw):
        message = json.loads(raw)
        self.messages.append(message)
        if message.get('connected') is True:
            raise StopRun

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Future()
        raise StopAsyncIteration


class FakeSource:
    def __init__(self, should_fail):
        self.should_fail = should_fail
        self.closed = False

    def open(self):
        if self.should_fail:
            raise RuntimeError('Muse unavailable')

    def close(self):
        self.closed = True

    def read(self):
        return Sample(5.0, [2.0, 2.0, 2.0, 2.0])


def test_failed_muse_open_recreates_source_before_retry():
    sources = []

    def factory():
        source = FakeSource(should_fail=not sources)
        sources.append(source)
        return source

    ws = FakeWebSocket()
    with pytest.raises(StopRun):
        asyncio.run(run_connection(ws, factory, profile(), 'taher', retry_delay=0))

    assert len(sources) == 2
    assert sources[0].closed is True
    assert sources[1].closed is True


# --- double blink (back) -------------------------------------------------------


def blink_profile(**over):
    return {**profile(), "blink_rest": 10.0, "blink_threshold": 60.0,
            "blink_enabled": True, **over}


def blink_detector(**over):
    detector = ClenchInput(blink_profile(**over))
    # Arm the jaw first: nothing is accepted until the jaw has been quiet.
    detector.update(5.0, 0.0, enabled=True, blink=(4.0, 4.0))
    detector.update(5.0, 0.7, enabled=True, blink=(4.0, 4.0))
    return detector


def swoop(detector, t, level=(120.0, 120.0)):
    """One blink: both forehead channels rise together, then fall back."""
    events = detector.update(5.0, t, enabled=True, blink=level)
    events += detector.update(5.0, t + 0.05, enabled=True, blink=(4.0, 4.0))
    return events


def test_two_blinks_close_together_emit_double_blink():
    detector = blink_detector()
    assert swoop(detector, 1.0) == []  # one blink alone means nothing
    events = swoop(detector, 1.4)
    assert [e["type"] for e in events] == ["DOUBLE_BLINK"]


def test_one_blink_alone_never_emits():
    detector = blink_detector()
    assert swoop(detector, 1.0) == []
    # The second blink comes too late to pair (DOUBLE_BLINK_MS is 700 ms).
    assert swoop(detector, 2.5) == []


def test_a_single_channel_spike_is_not_a_blink():
    """A loose electrode or a chewing burst moves one side; a blink moves both."""
    detector = blink_detector()
    assert swoop(detector, 1.0, level=(120.0, 4.0)) == []
    assert swoop(detector, 1.4, level=(120.0, 4.0)) == []


def test_blink_is_off_when_the_eye_calibration_failed():
    """The bench writes blink_enabled=False when it could not separate a blink from rest."""
    detector = blink_detector(blink_enabled=False)
    assert detector.blink_enabled is False
    assert swoop(detector, 1.0) == []
    assert swoop(detector, 1.4) == []


def test_blink_is_off_without_a_calibrated_threshold():
    detector = ClenchInput({**profile(), "blink_enabled": True})
    assert detector.blink_enabled is False


def test_forcing_blink_on_overrides_a_failed_calibration():
    assert ClenchInput(blink_profile(blink_enabled=False), blink=True).blink_enabled is True


def test_poor_forehead_contact_does_not_block_the_jaw():
    """Bad eye contact must never cost the patient a pick, or the help hold."""
    detector = blink_detector()
    assert detector.update(30.0, 1.0, enabled=True, blink=None) == []
    events = detector.update(5.0, 1.25, enabled=True, blink=None)
    assert [e["type"] for e in events] == ["CLENCH"]


def test_a_clench_wins_the_tick_over_a_blink():
    """Clenching pulls the brow, so one tick must never send both commands."""
    detector = blink_detector()
    swoop(detector, 1.0)                                              # first blink: now pending
    detector.update(30.0, 1.1, enabled=True, blink=(4.0, 4.0))        # jaw rises
    # The jaw releases on the same tick the second blink lands: CLENCH only, no DOUBLE_BLINK.
    events = detector.update(5.0, 1.2, enabled=True, blink=(120.0, 120.0))
    assert [e["type"] for e in events] == ["CLENCH"]
