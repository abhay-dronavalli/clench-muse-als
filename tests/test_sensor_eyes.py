"""DOUBLE_BLINK from MNE in the Sensor Service (sensor/detect/eyes.py), and the demo source's eyes."""

import numpy as np
import pytest

from sensor.detect.eyes import MNEEyes
from sensor.detect.mne_blinks import SCAN_SECONDS, WINDOW_SECONDS, BlinkGroups, RollingMNEBlinks


class FakeWorker:
    """Stands in for MNEBlinkWorker: the test decides what each finished scan found."""

    def __init__(self, fs, since):
        self.submitted = []
        self.results = []
        self.closed = False

    def submit(self, eyes, end_time):
        self.submitted.append(end_time)

    def drain(self):
        out, self.results = self.results, []
        return out

    def close(self):
        self.closed = True


def make_eyes(since=100.0):
    return MNEEyes(256, since, worker_factory=FakeWorker)


def test_two_peaks_within_700_ms_send_one_double_blink():
    eyes = make_eyes()
    eyes.worker.results = [([101.0, 101.4], 101.5, None)]
    commands, _ = eyes.poll(102.0)
    assert commands == [{"type": "DOUBLE_BLINK", "t": 102.0}]


def test_a_single_blink_sends_nothing():
    eyes = make_eyes()
    eyes.worker.results = [([101.0], 101.5, None)]
    assert eyes.poll(101.6)[0] == []
    eyes.worker.results = [([], 102.5, None)]  # the second blink never came
    assert eyes.poll(102.6)[0] == []


def test_two_blinks_too_far_apart_are_not_a_double_blink():
    eyes = make_eyes()
    eyes.worker.results = [([101.0, 102.0], 102.5, None)]
    assert eyes.poll(103.0)[0] == []


def test_double_blink_is_stamped_when_sent_not_at_the_peak():
    """MNE commits a peak at least half a second late; the Core refuses gestures over 1 s old."""
    eyes = make_eyes()
    eyes.worker.results = [([100.2, 100.5], 100.6, None)]
    (command,), _ = eyes.poll(101.4)
    assert command["t"] == 101.4


def test_windows_are_submitted_every_scan_period():
    eyes = make_eyes()
    assert eyes.due(100.0)
    eyes.submit(np.zeros((2, 10)), 100.0, 100.0)
    assert not eyes.due(100.0 + SCAN_SECONDS / 2)
    assert eyes.due(100.0 + SCAN_SECONDS)
    assert eyes.worker.submitted == [100.0]
    assert eyes.window_samples == round(WINDOW_SECONDS * 256)


def test_a_missing_window_is_not_submitted():
    """Buffer still filling, or a timestamp gap: skip it, as the bench does."""
    eyes = make_eyes()
    eyes.submit(None, float("nan"), 100.0)
    assert eyes.worker.submitted == []


def test_an_mne_failure_stops_blinks_and_says_so():
    """MNE missing or crashing must never take clenches down with it."""
    eyes = make_eyes()
    eyes.worker.results = [([], 100.0, "ModuleNotFoundError: No module named 'mne'")]
    commands, notes = eyes.poll(101.0)
    assert commands == []
    assert eyes.failed and not eyes.due(200.0)
    assert any("Clenches still work" in note for note in notes)


def test_ready_is_announced_once():
    eyes = make_eyes()
    eyes.worker.results = [([], 100.5, None)]
    _, notes = eyes.poll(101.0)
    assert any("ready" in note for note in notes)
    eyes.worker.results = [([], 100.75, None)]
    assert eyes.poll(101.2)[1] == []


# --- the demo source through the real MNE path -----------------------------------


def test_the_demo_source_produces_exactly_one_double_blink_per_cycle(monkeypatch):
    """Runs real MNE on the demo's synthetic AF7/AF8, scanning every 0.25 s like the service."""
    pytest.importorskip("mne")
    import types

    import sensor.sources.muse as muse

    clock = {"t": 1000.0}
    monkeypatch.setattr(muse, "time", types.SimpleNamespace(time=lambda: clock["t"]))
    source = muse.DemoSource()
    source.open()
    scanner = RollingMNEBlinks(256, since=1000.0)
    groups = BlinkGroups(700)
    doubles = []
    for step in range(int(31 / SCAN_SECONDS)):
        clock["t"] = 1000.0 + step * SCAN_SECONDS
        eyes, end = source.eyes(round(WINDOW_SECONDS * 256))
        peaks, through = scanner.scan(eyes, end)
        doubles += [peak - 1000 for name, _, peak in groups.advance(peaks, through) if name == "DOUBLE_BLINK"]
    assert doubles == [pytest.approx(27.4, abs=0.05)]


def test_a_slow_first_scan_does_not_flip_ready_off_and_on():
    """MNE's first real scan is slow; a two-second lag must not be reported as blinks stopping."""
    eyes = make_eyes()
    eyes.worker.results = [([], 100.5, None)]
    eyes.poll(101.0)
    assert eyes.poll(103.0)[1] == []  # 2.5 s since the last finished scan: still ready
    assert eyes.ready


def test_a_real_stall_is_reported():
    eyes = make_eyes()
    eyes.worker.results = [([], 100.5, None)]
    eyes.poll(101.0)
    _, notes = eyes.poll(110.0)
    assert any("paused" in note for note in notes)
