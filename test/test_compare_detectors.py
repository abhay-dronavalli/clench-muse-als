"""Evaluation safeguards plus real optional-library probes; no headband needed."""
import numpy as np
import pytest

import compare_detectors as c


def test_matching_counts_duplicates_and_blink_clench_confusion():
    actions = [dict(start=1, end=2, event="BLINK", phase="firm")]
    events = [dict(t=1.2, event="BLINK"), dict(t=1.3, event="BLINK"),
              dict(t=1.4, event="CLENCH")]
    result = c.assess(events, actions, {"BLINK", "CLENCH"})
    assert result["per_gesture"]["BLINK"] == dict(asked=1, hits=1, misses=0, false_triggers=1)
    assert result["per_gesture"]["CLENCH"]["false_triggers"] == 1
    assert result["clench_during_eye_actions"] == 1
    assert result["action_results"][0]["phase"] == "firm"


def test_unsupported_eyes_do_not_hide_false_clenches():
    result = c.assess([dict(t=1.2, event="CLENCH")],
                     [dict(start=1, end=2, event="BLINK")], {"CLENCH"})
    assert result["unsupported_actions"] == 1
    assert result["per_gesture"]["CLENCH"]["false_triggers"] == 1
    assert result["clench_during_eye_actions"] == 1


def test_unsupported_hold_is_unscored_not_a_success():
    result = c.assess([dict(t=1.2, event="BLINK")],
                     [dict(start=1, end=2, event="LONG_BLINK")], {"BLINK"})
    assert result["per_gesture"]["BLINK"] == dict(asked=0, hits=0, misses=0, false_triggers=0)
    assert len(result["unscored_same_family_events"]) == 1


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_invalid_signal_rejected_before_any_library_runs(bad):
    raw = np.zeros((4, 2560))
    raw[1, 10] = bad
    with pytest.raises(ValueError, match="finite EEG"):
        c.compare(raw, 256, [2, 5], [], None)


def test_overlapping_truth_is_rejected():
    actions = [dict(start=3, end=5, event="BLINK"), dict(start=4, end=6, event="CLENCH")]
    with pytest.raises(ValueError, match="overlap"):
        c.compare(np.zeros((4, 2560)), 256, [1, 2], actions, None)


def test_adapter_failure_is_not_reported_as_zero_misses(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("probe failed")
    for name in ("current_events", "mne_events", "neurokit_blinks", "neurokit_clenches", "vtuber_events"):
        monkeypatch.setattr(c, name, fail)
    report = c.compare(np.zeros((4, 2560)), 256, [1, 2], [], None)
    for name in c.CAPABILITIES:
        assert report[name]["status"] == "error"
        assert "per_gesture" not in report[name]


def test_current_replay_does_not_modify_input():
    case = c.synthetic_case("probe", scale=.2)
    before = case["raw"].copy()
    events = c.current_events(case["raw"], case["fs"], case["baseline"], case["profile"])
    np.testing.assert_array_equal(case["raw"], before)
    result = c.assess(events, case["actions"], c.CAPABILITIES["current"], 10)
    assert result["per_gesture"]["CLENCH"]["hits"] == 3


def test_channel_metrics_preserve_scale_independent_ratios():
    case = c.synthetic_case("probe")
    def measure(raw):
        return c.channel_metrics(raw, case["fs"], case["baseline"], case["actions"])
    full, weak = measure(case["raw"]), measure(case["raw"]*.2)
    assert weak["TP9"]["raw_baseline_std_uv"] == pytest.approx(full["TP9"]["raw_baseline_std_uv"]*.2)
    assert weak["TP9"]["muscle"]["peak_to_baseline_ratio"] == pytest.approx(full["TP9"]["muscle"]["peak_to_baseline_ratio"])


@pytest.mark.parametrize("scale", [1, .2])
def test_real_mne_blinks_across_amplitudes(scale):
    pytest.importorskip("mne")
    case = c.synthetic_case("probe", scale=scale)
    events = c.mne_events(case["raw"], 256, case["baseline"], None)
    result = c.assess(events, case["actions"], {"BLINK"}, 10)
    assert result["per_gesture"]["BLINK"]["hits"] == 4
    assert result["per_gesture"]["BLINK"]["false_triggers"] == 0


def test_real_neurokit_with_explicit_polarity():
    pytest.importorskip("neurokit2")
    case = c.synthetic_case("probe")
    events = c.neurokit_blinks(case["raw"], 256, case["baseline"], None, polarity=-1)
    assert c.assess(events, case["actions"], {"BLINK"}, 10)["per_gesture"]["BLINK"]["hits"] == 4


@pytest.mark.parametrize("scale", [1, .2])
def test_real_neurokit_emg_reports_eye_artifact_as_false_clench(scale):
    pytest.importorskip("neurokit2")
    case = c.synthetic_case("probe", scale=scale, blink_ear_burst=80)
    events = c.neurokit_clenches(case["raw"], 256, case["baseline"], None)
    result = c.assess(events, case["actions"], {"CLENCH"}, 10)
    assert result["per_gesture"]["CLENCH"]["hits"] == 3
    assert result["per_gesture"]["CLENCH"]["false_triggers"] == 4
    assert result["clench_during_eye_actions"] == 4


def test_upstream_adapter_runs_real_source_when_available():
    if not c.DEFAULT_VTUBER.exists():
        pytest.skip("Optional pinned muse-vtuber checkout is not installed")
    case = c.synthetic_case("probe")
    events = c.vtuber_events(case["raw"], 256, case["baseline"], None)
    assert c.assess(events, case["actions"], c.CAPABILITIES["vtuber"], 10)["per_gesture"]["BLINK"]["hits"] == 4
