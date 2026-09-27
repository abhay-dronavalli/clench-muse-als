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


# --- detection while paused (so the input log is never silently empty) ---------


def test_a_paused_detector_still_reports_a_clench_for_the_core_to_refuse():
    detector = ClenchInput(profile())
    detector.update(5.0, 0.0, enabled=False)
    detector.update(5.0, 0.7, enabled=False)
    detector.update(30.0, 1.0, enabled=False)
    events = detector.update(5.0, 1.25, enabled=False)
    assert [e["type"] for e in events] == ["CLENCH"]


def test_a_clench_held_across_enable_never_fires():
    """Enabling mid-clench must not select: the switch change re-arms the detector."""
    detector = ClenchInput(profile())
    detector.update(5.0, 0.0, enabled=False)
    detector.update(5.0, 0.7, enabled=False)
    detector.update(30.0, 1.0, enabled=False)            # clench starts while paused
    assert detector.update(30.0, 1.1, enabled=True) == []  # caregiver enables mid-clench
    assert detector.update(5.0, 1.3, enabled=True) == []   # release: not armed, nothing fires


def test_a_crossing_during_head_motion_never_fires_after_it_stops():
    detector = armed_detector()
    detector.update(30.0, 1.0, enabled=True, blocked="Head moving")
    assert detector.update(5.0, 1.2, enabled=True) == []
