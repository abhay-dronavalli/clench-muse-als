"""Compare actual library detectors on the same raw four-channel signals.

Offline MNE/NeuroKit peak times are NOT live response latency. Synthetic cases
are engineering probes, not evidence of human accuracy or generalization.
See LIBRARY_COMPARISON.md for installation, capabilities and limitations.
"""
import argparse
import importlib.metadata
import json
import logging
import subprocess
import sys
import time
import warnings
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy import signal

import clench_detect as cd

HERE = Path(__file__).parent
VTUBER_REVISION = "1d6453280c6547ceb6545bc58db60bd255bb3b25"
DEFAULT_VTUBER = HERE / "recordings" / "muse-vtuber-evaluation"
CHANNELS = ["TP9", "AF7", "AF8", "TP10"]
CAPABILITIES = {
    "current": {"CLENCH", "LONG_CLENCH", "BLINK", "DOUBLE_BLINK", "LONG_BLINK"},
    "mne": {"BLINK"},
    "neurokit-blink": {"BLINK"},
    "neurokit-blink-inverted": {"BLINK"},
    "neurokit-emg": {"CLENCH"},
    "vtuber": {"BLINK", "DOUBLE_BLINK", "CLENCH"},
    "vtuber-64": {"BLINK", "DOUBLE_BLINK", "CLENCH"},
}


def measured_levels(raw, fs):
    rows = {"emg": [0, 3], "blink": [1, 2]}
    for t in np.arange(1, raw.shape[1]/fs, cd.TICK_SECONDS):
        end = round(t*fs)
        board = SimpleNamespace(get_current_board_data=lambda *a, **k: raw[:, end-fs:end].copy())
        yield float(t), cd.read_levels(board, rows, fs, fs)


def current_events(raw, fs, baseline, profile):
    if profile is None:
        raise ValueError("Current detector requires the matching calibration profile")
    if profile.get("hold_threshold") and profile.get("hold_method") != "raw_deflection_v2":
        raise ValueError("Old hold calibration: recalibrate; do not compare incompatible units")
    if profile.get("fs", fs) != fs:
        raise ValueError("Profile sampling rate does not match recording")
    args = SimpleNamespace(long_ms=1500, double_ms=700,
                           long_blink_ms=profile.get("long_blink_ms", 400))
    recognizer = cd.recognizer_from_calibration(profile, args)
    return [dict(t=t, event=name) for t, lv in measured_levels(raw, fs)
            for name, _ in recognizer.update(lv, t)]


def mne_events(raw, fs, baseline, profile):
    import mne
    # BrainFlow is microvolts; MNE RawArray expects volts.
    data = mne.io.RawArray(raw * 1e-6, mne.create_info(CHANNELS, fs, "eeg"), verbose=False)
    events = mne.preprocessing.find_eog_events(data, ch_name=["AF7", "AF8"], verbose=False)
    return [dict(t=float(sample/fs), event="BLINK") for sample in events[:, 0]]


def neurokit_blinks(raw, fs, baseline, profile, polarity=1):
    import neurokit2 as nk
    # The library takes one EOG channel: averaging the two eyes is adapter policy.
    cleaned = nk.eog_clean(polarity*np.mean(raw[1:3], axis=0), sampling_rate=fs, method="neurokit")
    _, eye_info = nk.eog_peaks(cleaned, sampling_rate=fs, method="neurokit")
    return [dict(t=float(i/fs), event="BLINK") for i in eye_info["EOG_Blinks"]]


def neurokit_clenches(raw, fs, baseline, profile):
    import neurokit2 as nk
    # NK 0.2.13 emg_amplitude silently assumes 1000 Hz internally; its public
    # API has no sampling-rate argument. Do not apply that envelope at 256 Hz.
    # Supply a documented custom RMS envelope to NK's public activation API.
    sos = signal.butter(4, [20, min(110, fs*.45)], btype="bandpass", fs=fs, output="sos")
    ears = signal.sosfiltfilt(sos, raw[[0, 3]], axis=-1)
    kernel = np.ones(round(.2*fs))/round(.2*fs)
    envelope = np.max(np.sqrt(np.stack([np.convolve(ch*ch, kernel, "same") for ch in ears])), axis=0)
    start, end = (round(t*fs) for t in baseline)
    rest, sigma = cd.robust_baseline(envelope[start:end])
    _, info = nk.emg_activation(emg_amplitude=envelope, sampling_rate=fs,
                                method="threshold", threshold=rest+6*sigma,
                                duration_min=round(.08*fs))
    # Score release, like the current recognizer. Offline filtering can make
    # apparent onsets precede the signal; neither timestamp measures live latency.
    return [dict(t=float(i/fs), event="CLENCH") for i in info["EMG_Offsets"]]


def vtuber_events(raw, fs, baseline, profile, source=DEFAULT_VTUBER, chunk=4):
    if fs != 256:
        raise ValueError("Upstream blink implementation hard-codes 256 Hz; use a 256 Hz recording")
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != VTUBER_REVISION:
        raise ValueError(f"Expected muse-vtuber {VTUBER_REVISION}, got {revision}")
    sys.path.insert(0, str(source / "src"))
    from muse_vtuber.pipeline.blink import BlinkDetector
    from muse_vtuber.pipeline.clench import ClenchDetector, ClenchResult
    from muse_vtuber.pipeline.types import PipelineFrame
    blink, jaw = BlinkDetector(), ClenchDetector(sample_rate=fs)
    blink.guard_speech = False  # matches upstream create_pipeline
    events, was_clenching = [], False
    for end in range(chunk, raw.shape[1]+1, chunk):
        now = end/fs
        frame = PipelineFrame(eeg=raw[:, end-chunk:end].copy(), imu=None, timestamp=now)
        blink.process(frame)
        jaw.process(frame)
        clenching = frame.get(ClenchResult).jaw_clench
        if clenching and not was_clenching:
            events.append(dict(t=now, event="CLENCH"))
        was_clenching = clenching
        for event in frame.events:
            if event.kind == "blink":
                name = "DOUBLE_BLINK" if event.metadata.get("type") == "double" else "BLINK"
                events.append(dict(t=now, event=name))
    return events


def assess(events, actions, capabilities, score_start=0):
    """One-to-one matching; unsupported actions are reported, never counted hits.

    Primitive events inside unsupported same-family actions are unscored (e.g.
    BLINK during LONG_BLINK). Wrong-family emissions still count as false triggers.
    """
    expected = [a for a in actions if a["event"] in capabilities]
    unsupported = [a for a in actions if a["event"] not in capabilities]
    events = [e for e in events if e["t"] >= score_start]
    used, matches, excluded = set(), [], []
    per_gesture = {name: dict(asked=0, hits=0, misses=0, false_triggers=0)
                   for name in sorted(capabilities)}
    for action in expected:
        row = per_gesture[action["event"]]
        row["asked"] += 1
        match = next((i for i, e in enumerate(events) if i not in used
                      and e["event"] == action["event"]
                      and action["start"] <= e["t"] <= action["end"]), None)
        row["misses" if match is None else "hits"] += 1
        if match is not None:
            used.add(match)
        matches.append(dict(**action, hit=match is not None,
                            detected_at=None if match is None else events[match]["t"]))
    for i, event in enumerate(events):
        if i not in used and event["event"] in per_gesture:
            family = "BLINK" if "BLINK" in event["event"] else "CLENCH"
            if not any(family in a["event"] and a["start"] <= event["t"] <= a["end"] for a in unsupported):
                per_gesture[event["event"]]["false_triggers"] += 1
            else:
                excluded.append(event)
    eye_actions = [a for a in actions if "BLINK" in a["event"]]
    cross = sum(e["event"] in ("CLENCH", "LONG_CLENCH") and
                any(a["start"] <= e["t"] <= a["end"] for a in eye_actions) for e in events)
    return dict(per_gesture=per_gesture, unsupported_actions=len(unsupported),
                clench_during_eye_actions=cross, action_results=matches,
                unscored_same_family_events=excluded, events=events)


def channel_metrics(raw, fs, baseline, actions):
    """Offline descriptive features, not an estimate of detector accuracy."""
    first, last = (round(t*fs) for t in baseline)
    result = {}
    for i, name in enumerate(CHANNELS):
        bands = {}
        for kind, low, high, family in (("eye", 1, 10, "BLINK"), ("muscle", 20, 110, "CLENCH")):
            sos = signal.butter(4, [low, min(high, .45*fs)], btype="bandpass", fs=fs, output="sos")
            filtered = signal.sosfiltfilt(sos, raw[i])
            n = round(.2*fs)
            rms = np.sqrt(np.convolve(filtered**2, np.ones(n)/n, "same"))
            noise = float(np.sqrt(np.mean(filtered[first:last]**2)))
            peaks = [float(np.max(rms[round(a["start"]*fs):round(a["end"]*fs)]))
                     for a in actions if family in a["event"]]
            peak = float(np.median(peaks)) if peaks else None
            bands[kind] = dict(baseline_rms_uv=noise, median_action_peak_rms_uv=peak,
                peak_to_baseline_ratio=peak/noise if peak is not None and noise > 0 else None)
        result[name] = dict(raw_baseline_std_uv=float(np.std(raw[i, first:last])), **bands)
    return result


def compare(raw, fs, baseline, actions, profile, source=DEFAULT_VTUBER, score_start=0):
    raw = np.asarray(raw, dtype=float)
    if raw.ndim != 2 or raw.shape[0] != 4 or not np.all(np.isfinite(raw)):
        raise ValueError("Need finite EEG array in TP9, AF7, AF8, TP10 order")
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError("Sampling rate must be positive and finite")
    if not (0 <= baseline[0] < baseline[1] <= raw.shape[1]/fs):
        raise ValueError("Invalid baseline interval")
    if any(not (0 <= a["start"] < a["end"] <= raw.shape[1]/fs) for a in actions):
        raise ValueError("Action outside recording")
    ordered = sorted(actions, key=lambda a: a["start"])
    if any(a["end"] > b["start"] for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Action windows must not overlap")
    candidates = dict(current=current_events, mne=mne_events,
        **{"neurokit-blink": neurokit_blinks,
           "neurokit-blink-inverted": lambda *a: neurokit_blinks(*a, polarity=-1),
           "neurokit-emg": neurokit_clenches},
        vtuber=lambda *a: vtuber_events(*a, source=source),
        **{"vtuber-64": lambda *a: vtuber_events(*a, source=source, chunk=64)})
    results = {}
    for name, adapter in candidates.items():
        started = time.perf_counter()
        with warnings.catch_warnings(record=True) as captured:
            try:
                events = adapter(raw, fs, baseline, profile)
                results[name] = dict(status="ok", **assess(events, actions, CAPABILITIES[name], score_start))
            except Exception as exc:
                # Errors must never masquerade as zero misses / a successful run.
                results[name] = dict(status="error", error=f"{type(exc).__name__}: {exc}")
        results[name]["compute_seconds"] = round(time.perf_counter()-started, 3)
        results[name]["warnings"] = sorted({str(w.message) for w in captured})
    results["libmuse"] = dict(status="unavailable", reason="Licensed SDK not installed; current artifact API unverified")
    return results


def synthetic_case(name, scale=1, noise=2, blink_ear_burst=0, weak_eye=1, polarity=-1):
    """Deterministic engineering fixture; parameters are NOT physiological models."""
    fs = 256
    rng = np.random.default_rng(241)
    t = np.arange(50*fs)/fs
    raw = rng.normal(0, noise*scale, (4, len(t)))
    actions = []
    for center in (12, 16, 20, 24):
        pulse = polarity*100*scale*np.exp(-.5*((t-center)/.045)**2)
        raw[1] += pulse
        raw[2] += weak_eye*pulse
        burst = blink_ear_burst*scale*((t >= center-.04) & (t < center+.14))*np.sin(2*np.pi*35*t)
        raw[0] += burst
        raw[3] += burst
        actions.append(dict(start=center-.2, end=center+1.5, event="BLINK"))
    for start in (30, 34, 38):
        burst = ((t >= start) & (t < start+.5))*scale*(60*np.sin(2*np.pi*35*t)+40*np.sin(2*np.pi*75*t))
        raw[0] += burst
        raw[3] += .8*burst
        actions.append(dict(start=start, end=start+1.5, event="CLENCH"))
    # Separate calibration examples: no test-action samples used to choose thresholds.
    training = np.random.default_rng(987).normal(0, noise*scale, (4, 6*fs))
    ct = np.arange(6*fs)/fs
    training[0] += ((ct >= 3) & (ct < 5))*scale*(60*np.sin(2*np.pi*35*ct)+40*np.sin(2*np.pi*75*ct))
    training[3] += .8*((ct >= 3) & (ct < 5))*scale*(60*np.sin(2*np.pi*35*ct)+40*np.sin(2*np.pi*75*ct))
    measured = list(measured_levels(training, fs))
    quiet = [lv for tm, lv in measured if tm < 2.5]
    rest, sigma = cd.robust_baseline([lv.emg for lv in quiet])
    peak = float(np.percentile([lv.emg for tm, lv in measured if 3.5 <= tm <= 5], 75))
    b_rest, b_sigma = cd.robust_baseline([max(lv.blink_left, lv.blink_right) for lv in quiet])
    profile = dict(fs=fs, emg_rest=rest, emg_threshold=max(rest+6*sigma, rest+.3*(peak-rest)),
        blink_rest=b_rest, blink_threshold=b_rest+4*b_sigma, hold_threshold=None)
    return dict(name=name, raw=raw, fs=fs, baseline=[2, 9], actions=actions,
                profile=profile, score_start=10)


def load_manifest(path):
    from brainflow.board_shim import BoardShim
    from brainflow.data_filter import DataFilter
    from config import eeg_channels_and_names
    for entry in json.loads(path.read_text()):
        fs = BoardShim.get_sampling_rate(entry["board_id"])
        channels, names = eeg_channels_and_names(entry["board_id"])
        if names != CHANNELS:
            raise ValueError(f"Expected Muse channel mapping, got {names}")
        data = DataFilter.read_file(str(path.parent / entry["recording"]))
        raw = data[channels]
        stamps = data[BoardShim.get_timestamp_channel(entry["board_id"])]
        # Cues use wall time; adapters use sample time. Refuse misleading scores
        # when packet loss / clock jumps make those clocks disagree materially.
        if not np.all(np.isfinite(stamps)) or np.max(np.abs(stamps-stamps[0]-np.arange(len(stamps))/fs)) > .1:
            raise ValueError("EEG timestamps deviate from sample time by >100 ms; repair timing before scoring")
        profile_path = path.parent / entry["profile"]
        profile = json.loads(profile_path.read_text()) if profile_path.exists() else None
        yield dict(name=entry["recording"], raw=raw, fs=fs, profile=profile,
                   baseline=entry["baseline"], actions=entry["actions"],
                   score_start=entry.get("score_start", entry["baseline"][1]))


def main():
    parser = argparse.ArgumentParser(__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--synthetic", action="store_true")
    choice.add_argument("--manifest", type=Path)
    parser.add_argument("--vtuber-source", type=Path, default=DEFAULT_VTUBER)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.getLogger("blink_detector").setLevel(logging.ERROR)
    versions = {}
    for name in ("mne", "neurokit2", "brainflow", "numpy", "scipy"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not installed"
    cases = ([synthetic_case("reference"), synthetic_case("low-amplitude", scale=.2),
              synthetic_case("noisy", noise=8), synthetic_case("weak-AF8", weak_eye=.1),
              synthetic_case("blink-with-ear-burst", blink_ear_burst=80),
              synthetic_case("positive-blink", polarity=1)] if args.synthetic
             else list(load_manifest(args.manifest)))
    report = dict(synthetic=bool(args.synthetic), versions=versions, vtuber_revision=VTUBER_REVISION,
        note="MNE/NK are offline; timestamps are not live latency. No human accuracy claim.", cases=[])
    for case in cases:
        name = case.pop("name")
        print(f"Evaluating {name}...", flush=True)
        results = compare(**case, source=args.vtuber_source)
        report["cases"].append(dict(name=name, results=results, calibration_profile=case["profile"],
            baseline=case["baseline"], score_start=case.get("score_start", 0),
            duration_seconds=case["raw"].shape[1]/case["fs"],
            channels=channel_metrics(case["raw"], case["fs"], case["baseline"], case["actions"])))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    for case in report["cases"]:
        for name, result in case["results"].items():
            if result["status"] != "ok":
                print(case["name"], name, result["status"], result.get("error", result.get("reason")))
                continue
            print(case["name"], name, result["per_gesture"], "clenches during eyes:", result["clench_during_eye_actions"])
    print(f"Full report: {args.output}")


if __name__ == "__main__":
    main()
