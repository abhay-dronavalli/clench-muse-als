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


def samples(emg, blink=10, hold=1, duration=2):
    return [(i*.05, cd.Levels(emg, blink, blink, hold, hold))
            for i in range(round(duration/.05))]


def calibration_run(monkeypatch, tmp_path, phases, **options):
    from brainflow.board_shim import BoardIds
    monkeypatch.setattr(cd, "HERE", tmp_path)
    phases = iter(phases)
    monkeypatch.setattr(cd, "collect", lambda *a, **k: next(phases))
    board = SimpleNamespace(board_id=BoardIds.MUSE_2_BOARD)
    path = cd.calibration_file(board, "regression")
    path.write_text('{"previous": "keep me"}')
    ui = QuietUI()
    result = cd.calibrate(board, {}, 256, 256, args(**options), ui)
    return result, path, ui


def test_empty_rest_cannot_replace_saved_calibration(monkeypatch, tmp_path):
    result, path, ui = calibration_run(monkeypatch, tmp_path, [[]])
    assert result is None
    assert path.read_text() == '{"previous": "keep me"}'
    assert any("retry" in s.lower() for s in ui.logs)


def test_noise_dominated_clenches_cannot_replace_profile(monkeypatch, tmp_path):
    rest = [(i*.05, cd.Levels(100 + (i % 5)*25, 10, 10, 1, 1)) for i in range(200)]
    weak = samples(210)
    phases = [rest, weak, weak, weak]
    result, path, ui = calibration_run(monkeypatch, tmp_path, phases)
    assert result is None
    assert path.read_text() == '{"previous": "keep me"}'


@pytest.mark.parametrize("scale", [.25, 1, 10])
def test_calibration_uses_sustained_clenches_and_disables_bad_eyes(monkeypatch, tmp_path, scale):
    rest = [(i*.05, cd.Levels((5 + (i % 5)*.1)*scale, 10, 10, 1, 1))
            for i in range(200)]
    clench = samples(25*scale)
    clench[10] = (.5, cd.Levels(1000*scale, 10, 10, 1, 1))
    phases = [rest, clench, clench, clench, samples(5, duration=6),
              samples(5), samples(5), samples(5)]
    result, path, ui = calibration_run(monkeypatch, tmp_path, phases)
    assert result["emg_threshold"] < 15*scale  # a spike cannot set the threshold
    assert result["emg_threshold"] >= result["emg_rest"] + 6*result["emg_sigma"]
    assert result["blink_enabled"] is False
    assert result["hold_threshold"] is None
    rec = cd.recognizer_from_calibration(result, args())
    events = []
    for i in range(100):
        lv = cd.Levels(5*scale, 100 if i == 5 else 10, 100 if i == 5 else 10)
        events.extend(rec.update(lv, i*.05))
    assert events == []


def test_hold_calibration_replays_final_threshold_and_requested_duration(monkeypatch, tmp_path):
    # Broad 300 ms low plateau with a brief peak: peak-based threshold must
    # not enable a 600 ms hold that none of the samples can produce.
    hold = samples(5, hold=1)
    for i in range(8, 14):
        hold[i] = (i*.05, cd.Levels(5, 10, 10, 20, 20))
    result, _, _ = calibration_run(monkeypatch, tmp_path,
        [samples(5, duration=10)] + [samples(30)]*3 + [samples(5, duration=6)] + [hold]*3,
        long_blink_ms=600)
    assert result["long_blink_ms"] == 600
    assert result["hold_threshold"] is None


def test_good_eye_holds_are_enabled(monkeypatch, tmp_path):
    hold = samples(5, hold=1)
    for i in range(8, 30):
        hold[i] = (i*.05, cd.Levels(5, 10, 10, 30, 30))
    blinks = ordinary_blinks()
    result, _, _ = calibration_run(monkeypatch, tmp_path,
        [samples(5, duration=10)] + [samples(30)]*3 + [blinks] + [hold]*3)
    assert result["hold_threshold"] is not None
    assert result["hold_method"] == "raw_deflection_v2"


def ordinary_blinks(hold_level=1):
    blinks = samples(5, duration=6)
    for start in (10, 40, 70):
        for i in range(start, start+14):
            blinks[i] = (i*.05, cd.Levels(5, 100, 100, hold_level, hold_level))
    return blinks


def test_eye_hold_calibration_rejects_ordinary_blink_confusion(monkeypatch, tmp_path):
    hold = samples(5, hold=1)
    for i in range(8, 30):
        hold[i] = (i*.05, cd.Levels(5, 10, 10, 30, 30))
    result, _, ui = calibration_run(monkeypatch, tmp_path,
        [samples(5, duration=10)] + [samples(30)]*3 + [ordinary_blinks(30)] + [hold]*3)
    assert result["hold_threshold"] is None
    assert any("ordinary blink" in line.lower() for line in ui.logs)


@pytest.mark.parametrize("fault", ["flat", "noise", "nan", "clip"])
def test_rest_contact_gate_names_bad_channel(fault):
    raw = np.random.default_rng(4).normal(0, 10, (4, 256))
    if fault == "flat":
        raw[3] = 0
    elif fault == "noise":
        raw[3] *= 50
    elif fault == "nan":
        raw[3, 50] = np.nan
    else:
        raw[3, :100] = 1000
    issues = cd.contact_issues(raw, {"emg": [0, 3], "blink": [1, 2]})
    assert len(issues) == 1
    assert "TP10" in issues[0]


def test_clean_rest_and_sampling_coverage():
    raw = np.random.default_rng(4).normal(0, 10, (4, 256))
    assert cd.contact_issues(raw, {"emg": [0, 3], "blink": [1, 2]}) == []
    assert cd.valid_collection(samples(5, duration=10), 10)
    assert not cd.valid_collection(samples(5, duration=1), 10)
    assert not cd.valid_collection([(0, cd.Levels(5, 5, 5))]*200, 10)


def test_profile_selection_isolated_from_other_users_and_synthetic(tmp_path, monkeypatch):
    import json
    from brainflow.board_shim import BoardIds
    monkeypatch.setattr(cd, "HERE", tmp_path)
    board = SimpleNamespace(board_id=BoardIds.MUSE_2_BOARD)
    synthetic = SimpleNamespace(board_id=BoardIds.SYNTHETIC_BOARD)
    for name, threshold in [("a", 20), ("b", 30)]:
        cd.calibration_file(board, name).write_text(json.dumps(dict(
            board="MUSE_2_BOARD", fs=256, blink_method="p2p_coincidence",
            emg_threshold=threshold, emg_rest=5, blink_threshold=40, blink_rest=5)))
    assert cd.load_calibration(board, "a", QuietUI())["emg_threshold"] == 20
    assert cd.load_calibration(board, "b", QuietUI())["emg_threshold"] == 30
    assert cd.load_calibration(synthetic, "a", QuietUI()) is None
    with pytest.raises(ValueError):
        cd.calibration_file(board, "../a")


def test_malformed_profile_is_rejected(tmp_path, monkeypatch):
    from brainflow.board_shim import BoardIds
    monkeypatch.setattr(cd, "HERE", tmp_path)
    board = SimpleNamespace(board_id=BoardIds.MUSE_2_BOARD)
    cd.calibration_file(board, "bad").write_text('{"emg_threshold": NaN}')
    assert cd.load_calibration(board, "bad", QuietUI()) is None


@pytest.mark.parametrize("scale", [.2, 5])
def test_csv_replay_reports_hits_and_per_channel_comparison(tmp_path, scale):
    import json
    from brainflow.board_shim import BoardShim, BoardIds
    from brainflow.data_filter import DataFilter
    from evaluate_detection import evaluate
    from config import eeg_channels_and_names
    board_id = BoardIds.MUSE_2_BOARD.value
    fs = BoardShim.get_sampling_rate(board_id)
    rows, names = eeg_channels_and_names(board_id)
    t = np.arange(7*fs)/fs
    raw = np.zeros((BoardShim.get_num_rows(board_id), len(t)))
    raw[rows] = np.random.default_rng(9).normal(0, .2*scale, (4, len(t)))
    raw[rows[0]] += ((t >= 2) & (t < 2.5))*15*scale*np.sin(2*np.pi*75*t)
    DataFilter.write_file(raw, str(tmp_path / "synthetic.csv"), "w")
    c = dict(fs=fs, emg_rest=.2*scale, emg_threshold=3*scale,
             blink_rest=.1*scale, blink_threshold=10*scale)
    (tmp_path / "profile.json").write_text(json.dumps(c))
    result = evaluate(dict(recording="synthetic.csv", profile="profile.json",
        board_id=board_id, baseline=[1, 1.8],
        actions=[dict(start=2, end=4, event="CLENCH")]), tmp_path)
    assert (result["hits"], result["misses"], result["false_triggers"]) == (1, 0, 0)
    assert set(result["channels"]) == set(names)
    assert result["channels"]["TP9"]["peak_to_rest_ratio"] > 20
