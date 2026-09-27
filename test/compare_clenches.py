"""Clench candidate benchmark: fixed adapter policies, no per-file hand tuning.

Generic EMG libraries report muscle activity, not semantic jaw gestures. These
offline experiments do not establish streaming latency or cross-user accuracy.
"""
import argparse
import importlib.metadata
import json
import time
import warnings
from pathlib import Path

import numpy as np
from scipy import signal

import compare_detectors as base
import clench_detect as cd


def temporal_signals(raw, fs):
    """Shared custom preprocessing; not the libraries' default 100 Hz highpass."""
    sos = signal.butter(4, [20, min(110, .45*fs)], fs=fs, btype="bandpass", output="sos")
    ears = signal.sosfiltfilt(sos, raw[[0, 3]], axis=-1)
    for frequency in (50, 60):
        if frequency < fs/2:
            b, a = signal.iirnotch(frequency, 30, fs)
            ears = signal.filtfilt(b, a, ears, axis=-1)
    return ears


def merge_offsets(events, tolerance=.2):
    """Either ear can activate; coalesce near-simultaneous release events.

    This is explicit adapter policy, not a library-provided clench classifier.
    Compare before/after lists in the report when auditing a candidate.
    """
    merged = []
    for value in sorted(events):
        if not merged or value-merged[-1] > tolerance:
            merged.append(float(value))
    return [dict(t=t, event="CLENCH") for t in merged]


def nk_candidates(raw, fs, baseline, method):
    import neurokit2 as nk
    ears = temporal_signals(raw, fs)
    offsets = []
    for ear in ears:
        if method == "mixture":
            n = round(.2*fs)
            envelope = np.sqrt(np.convolve(ear**2, np.ones(n)/n, "same"))
            _, info = nk.emg_activation(emg_amplitude=envelope, sampling_rate=fs,
                method="mixture", duration_min=round(.08*fs))
        else:
            _, info = nk.emg_activation(emg_cleaned=ear, sampling_rate=fs,
                method="biosppy", duration_min=round(.08*fs))
        offsets.extend(info["EMG_Offsets"]/fs)
    return merge_offsets(offsets)


def biosppy_candidates(raw, fs, baseline, method):
    from biosppy.signals import emg, tools
    ears = temporal_signals(raw, fs)
    offsets = []
    first, last = (round(t*fs) for t in baseline)
    for ear in ears:
        if method == "default":
            # Explicitly reconstruct the documented default threshold so we can
            # identify whether the first returned transition is onset or offset.
            smooth = tools.smoother(signal=np.abs(ear), kernel="boxzen",
                size=int(.05*fs), mirror=True)[0]
            threshold = 1.2*np.mean(np.abs(smooth))+2*np.std(np.abs(smooth), ddof=1)
            result = emg.find_onsets(signal=ear, sampling_rate=fs, threshold=threshold)
            initially_active = smooth[0] > threshold
        elif method == "hodges-bui":
            result = emg.hodges_bui_onset_detector(signal=ear, rest=ear[first:last],
                sampling_rate=fs, size=round(.05*fs), threshold=6)
            initially_active = result["processed"][0] >= 6
        else:
            # Feed energy-domain baseline statistics because this method's test
            # function is TKEO energy; raw-amplitude statistics have other units.
            quiet = ear[first:last]
            quiet = quiet-np.mean(quiet)
            energy = np.abs(quiet[1:-1]**2-quiet[:-2]*quiet[2:])
            result = emg.solnik_onset_detector(signal=ear,
                rest=dict(mean=float(np.mean(energy)), std_dev=float(np.std(energy, ddof=1))),
                sampling_rate=fs, threshold=6, active_state_duration=round(.08*fs))
            initially_active = False  # upstream Solnik always initializes inactive
        # BioSPPy's 'onsets' contains BOTH transitions. Never count releases as
        # additional clenches. Handle initial activity for threshold methods.
        boundaries = np.asarray(result["onsets"], dtype=int)
        offsets.extend(boundaries[0 if initially_active else 1::2]/fs)
    return merge_offsets(offsets)


def benchmark(case):
    raw, fs, baseline, profile = (case[k] for k in ("raw", "fs", "baseline", "profile"))
    adapters = {
        "current": lambda: base.current_events(raw, fs, baseline, profile),
        "nk-baseline-threshold": lambda: base.neurokit_clenches(raw, fs, baseline, profile),
        "nk-mixture": lambda: nk_candidates(raw, fs, baseline, "mixture"),
        "nk-biosppy": lambda: nk_candidates(raw, fs, baseline, "biosppy"),
        "biosppy-default": lambda: biosppy_candidates(raw, fs, baseline, "default"),
        "biosppy-hodges-bui": lambda: biosppy_candidates(raw, fs, baseline, "hodges-bui"),
        "biosppy-solnik-energy-baseline": lambda: biosppy_candidates(raw, fs, baseline, "solnik"),
    }
    results = {}
    for name, run in adapters.items():
        started = time.perf_counter()
        with warnings.catch_warnings(record=True) as captured:
            try:
                events = [e for e in run() if e["event"] in ("CLENCH", "LONG_CLENCH")]
                results[name] = dict(status="ok", **base.assess(events,
                    case["actions"], {"CLENCH", "LONG_CLENCH"} if name == "current" else {"CLENCH"},
                    case.get("score_start", 0)))
            except Exception as exc:
                results[name] = dict(status="error", error=f"{type(exc).__name__}: {exc}")
        results[name]["warnings"] = sorted({str(w.message) for w in captured})
        results[name]["compute_seconds"] = round(time.perf_counter()-started, 3)
    return results


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.manifest and not args.synthetic:
        parser.error("Choose --manifest and/or --synthetic")
    cases = list(base.load_manifest(args.manifest)) if args.manifest else []
    if args.synthetic:
        cases += [base.synthetic_case("synthetic-reference"),
            base.synthetic_case("synthetic-small", scale=.2),
            base.synthetic_case("synthetic-noisy", noise=8),
            base.synthetic_case("synthetic-blink-contamination", blink_ear_burst=80)]
    report = dict(note="Exploratory offline muscle-activity candidates. Whole-file fits are not calibration-only or streaming validation.",
        versions={name:importlib.metadata.version(name) for name in ("biosppy", "neurokit2", "numpy", "scipy")}, cases=[])
    for case in cases:
        print("Evaluating", case["name"], flush=True)
        results = benchmark(case)
        report["cases"].append(dict(name=case["name"], results=results))
        for name, row in results.items():
            print(name, row.get("per_gesture", row.get("error")), "clenches during blinks", row.get("clench_during_eye_actions"), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
