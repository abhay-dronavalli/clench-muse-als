"""Run explicitly: python -m pytest test/test_detection.py (needs test/requirements)."""
from types import SimpleNamespace

import numpy as np
import pytest

import clench_detect as cd


class QuietUI(cd.ConsoleUI):
    def __init__(self):
        self.events = []
        self.logs = []
        self.done = False

    def log(self, message=""):
        self.logs.append(message)

    def event(self, name, detail, elapsed):
        self.events.append(name)

    def tick(self, *args):
        pass

    def should_stop(self):
        return self.done

    def wait(self, prompt):
        pass


def args(**kwargs):
    return SimpleNamespace(**dict(dict(long_ms=1500, double_ms=700,
        long_blink_ms=400, baseline_seconds=10, k=6, profile="regression",
        no_clench_cal=False, blink_only=False), **kwargs))


@pytest.mark.parametrize("measure", [cd.envelope, cd.peak_to_peak, cd.deflection])
def test_filters_leave_raw_buffer_unchanged(measure):
    raw = np.random.default_rng(7).normal(size=256)
    before = raw.copy()
    measure(raw, 256)
    np.testing.assert_array_equal(raw, before)


def test_hold_frontend_receives_raw_signal():
    raw = np.zeros((4, 256))
    raw[1:3, 100:] = 100
    board = SimpleNamespace(get_current_board_data=lambda *a, **k: raw.copy())
    levels = cd.read_levels(board, {"emg": [0, 3], "blink": [1, 2]}, 256, 256)
    assert levels.hold_left == pytest.approx(cd.deflection(raw[1].copy(), 256))


@pytest.mark.parametrize("scale", [0.25, 1, 10])
def test_live_loop_releases_weak_clench_at_persons_rest(monkeypatch, scale):
    # Same rest/threshold relationship as the saved weak-clench profile.
    profile = dict(emg_rest=6.37*scale, emg_threshold=8.81*scale,
                   blink_rest=12*scale, blink_threshold=40*scale)
    ui = QuietUI()
    clock = iter(np.arange(0, 6, 0.05))
    ticks = iter([cd.Levels(v*scale, 12*scale, 12*scale)
                  for v in [6.37]*10 + [14.51]*6 + [6.37]*50])
    def read(*a):
        lv = next(ticks, None)
        if lv is None:
            ui.done = True
        return lv
    monkeypatch.setattr(cd, "read_levels", read)
    monkeypatch.setattr(cd.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cd.time, "sleep", lambda _: None)
    cd.detect_loop(None, None, 256, 256, profile, args(), ui)
    assert ui.events == ["CLENCH"]


def test_existing_gesture_suite():
    import test_gestures
    assert test_gestures.main() == 0


@pytest.mark.parametrize("scale", [0.2, 1, 5])
@pytest.mark.parametrize("duration,expected", [(0.5, "CLENCH"), (2.0, "LONG_CLENCH")])
def test_raw_emg_through_filters_across_signal_scales(scale, duration, expected):
    fs = 256
    t = np.arange(7*fs)/fs
    raw = np.random.default_rng(42).normal(0, .2*scale, (4, len(t)))
    raw[0] += ((t >= 2) & (t < 2+duration))*15*scale*np.sin(2*np.pi*75*t)
    profile = dict(emg_rest=.2*scale, emg_threshold=3*scale,
                   blink_rest=.1*scale, blink_threshold=10*scale)
    recognizer = cd.recognizer_from_calibration(profile, args())
    events = []
    for now in np.arange(1, 7, .05):
        end = round(now*fs)
        board = SimpleNamespace(get_current_board_data=lambda *a, **k:
                                raw[:, end-fs:end].copy())
        lv = cd.read_levels(board, {"emg": [0, 3], "blink": [1, 2]}, fs, fs)
        events.extend(name for name, _ in recognizer.update(lv, now))
    assert events == [expected]


def test_replay_scoring_counts_duplicates_wrong_inputs_and_misses():
    from evaluate_detection import score
    events = [dict(t=1.1, event="CLENCH"), dict(t=1.2, event="CLENCH"),
              dict(t=3, event="LONG_CLENCH"), dict(t=4, event="BLINK")]
    actions = [dict(start=1, end=2, event="CLENCH"),
               dict(start=3, end=4, event="LONG_BLINK")]
    assert score(events, actions) == dict(hits=1, misses=1, false_triggers=2)


def test_legacy_hold_profile_requires_recalibration(tmp_path, monkeypatch):
    import json
    from brainflow.board_shim import BoardIds
    monkeypatch.setattr(cd, "HERE", tmp_path)
    board = SimpleNamespace(board_id=BoardIds.MUSE_2_BOARD)
    c = dict(board="MUSE_2_BOARD", fs=256, blink_method="p2p_coincidence",
             emg_threshold=30, emg_rest=5, blink_threshold=40, blink_rest=5,
             hold_threshold=10)
    cd.calibration_file(board, "a").write_text(json.dumps(c))
    loaded = cd.load_calibration(board, "a", QuietUI())
    assert loaded["hold_threshold"] is None
    assert loaded["emg_threshold"] == 30
    assert json.loads(cd.calibration_file(board, "a").read_text()) == c
