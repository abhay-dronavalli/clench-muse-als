import numpy as np
import pytest

import compare_clenches as c


def test_two_ears_do_not_double_count_one_release():
    events = c.merge_offsets([1.12, 2.1, 1.1, 2.11])
    assert [e["t"] for e in events] == [1.1, 2.1]


def test_preprocessing_does_not_modify_raw_recording():
    raw = np.random.default_rng(43).normal(size=(4, 2560))
    before = raw.copy()
    processed = c.temporal_signals(raw, 256)
    assert processed.shape == (2, 2560)
    assert np.all(np.isfinite(processed))
    np.testing.assert_array_equal(raw, before)


@pytest.mark.parametrize("scale", [1., .2])
@pytest.mark.parametrize("method", ["default", "hodges-bui", "solnik"])
def test_actual_biosppy_emits_one_release_per_synthetic_clench(scale, method):
    pytest.importorskip("biosppy")
    case = c.base.synthetic_case("probe", scale=scale)
    events = c.biosppy_candidates(case["raw"], case["fs"], case["baseline"], method)
    result = c.base.assess(events, case["actions"], {"CLENCH"}, score_start=10)
    assert result["per_gesture"]["CLENCH"] == dict(asked=3, hits=3, misses=0, false_triggers=0)


def test_errors_cannot_be_ranked_as_successful_zero_false_triggers(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("probe failure")
    monkeypatch.setattr(c, "biosppy_candidates", fail)
    monkeypatch.setattr(c, "nk_candidates", fail)
    monkeypatch.setattr(c.base, "current_events", fail)
    monkeypatch.setattr(c.base, "neurokit_clenches", fail)
    results = c.benchmark(c.base.synthetic_case("probe"))
    assert all(row["status"] == "error" and "per_gesture" not in row for row in results.values())
