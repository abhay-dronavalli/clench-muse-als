"""Offline evaluation of the actual front end and recognizer; no headband required.

--profiles prints saved summaries (these are NOT labeled detection accuracy).
--manifest describes raw BrainFlow CSVs, matching calibration, and truth windows.
See INVESTIGATION.md for the recording protocol and manifest schema.
"""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from brainflow.board_shim import BoardShim
from brainflow.data_filter import DataFilter

import clench_detect as cd
from config import eeg_channels_and_names


def score(events, actions):
    """One event per truth window; extras and wrong gestures are false triggers.

    Events and truth windows must use the same emission semantics (CLENCH on
    release, CLENCH_START on onset). Unlabeled spontaneous BLINK is excluded.
    """
    used = set()
    hits = 0
    for action in actions:
        match = next((i for i, e in enumerate(events) if i not in used
                      and e["event"] == action["event"]
                      and action["start"] <= e["t"] <= action["end"]), None)
        if match is not None:
            used.add(match)
            hits += 1
    expected = {a["event"] for a in actions}
    false = sum(i not in used and (e["event"] != "BLINK" or "BLINK" in expected)
                for i, e in enumerate(events))
    return dict(hits=hits, misses=len(actions)-hits, false_triggers=false)


def profile_summary(path):
    c = json.loads(path.read_text())
    return dict(profile=path.stem.removeprefix("calibration."),
        emg_rest=c["emg_rest"], emg_noise_sigma=c["emg_sigma"],
        emg_peak=c.get("emg_peak"), emg_threshold=c["emg_threshold"],
        threshold_above_rest_sigma=(c["emg_threshold"]-c["emg_rest"])/c["emg_sigma"],
        af7_rest_p2p=c.get("blink_rest_left"), af8_rest_p2p=c.get("blink_rest_right"),
        blink_separation=c.get("blink_separation"),
        hold_durations_ms=c.get("hold_durations_ms"),
        legacy_release_below_rest=c["emg_threshold"]*.6 <= c["emg_rest"])


def evaluate(entry, root):
    c = json.loads((root / entry["profile"]).read_text())
    if c.get("hold_threshold") and c.get("hold_method") != "raw_deflection_v2":
        raise ValueError("Old hold profile used the buggy filter; recalibrate before replay")
    board_id = entry["board_id"]
    fs = BoardShim.get_sampling_rate(board_id)
    if c["fs"] != fs:
        raise ValueError("Recording and calibration sampling rates differ")
    data = DataFilter.read_file(str(root / entry["recording"]))
    channels, names = eeg_channels_and_names(board_id)
    rows = {"emg": [channels[0], channels[3]], "blink": [channels[1], channels[2]]}
    window = int(fs * cd.WINDOW_SECONDS)
    if data.shape[1] < window:
        raise ValueError("Recording is shorter than the filter window")
    duration = data.shape[1]/fs
    previous_end = cd.WINDOW_SECONDS
    for action in sorted(entry["actions"], key=lambda a: a["start"]):
        if not (previous_end <= action["start"] < action["end"] <= duration):
            raise ValueError("Truth windows must not overlap or extend outside the usable recording")
        previous_end = action["end"]
    timings = SimpleNamespace(long_ms=entry.get("long_ms", 1500),
        double_ms=entry.get("double_ms", 700),
        long_blink_ms=entry.get("long_blink_ms", c.get("long_blink_ms", 400)))
    if hasattr(cd, "recognizer_from_calibration"):
        recognizer = cd.recognizer_from_calibration(c, timings)
    else:
        recognizer = cd.GestureRecognizer(c["emg_threshold"], c["blink_threshold"],
            timings.long_ms, timings.double_ms, hold_threshold=c.get("hold_threshold"),
            long_blink_ms=timings.long_blink_ms)
    events, measured = [], []
    # Choose sample boundaries from time, avoiding a 13/256 vs 20 Hz clock drift.
    for t in np.arange(cd.WINDOW_SECONDS, data.shape[1]/fs, cd.TICK_SECONDS):
        end = round(t*fs)
        raw = data[:, end-window:end]
        board = SimpleNamespace(get_current_board_data=lambda *a, **k: raw.copy())
        lv = cd.read_levels(board, rows, fs, window)
        events.extend(dict(t=round(float(t), 3), event=name)
                      for name, _ in recognizer.update(lv, float(t)))
        measured.append((float(t), [cd.envelope(raw[r].copy(), fs) for r in channels]))
    baseline = entry["baseline"]
    quiet = np.array([v for t, v in measured if baseline[0] <= t <= baseline[1]])
    if not len(quiet):
        raise ValueError("Baseline interval contains no complete signal windows")
    clenches = [a for a in entry["actions"] if a["event"] in ("CLENCH", "LONG_CLENCH")]
    metrics = {}
    for i, name in enumerate(names):
        rest, sigma = cd.robust_baseline(quiet[:, i])
        peaks = [max((v[i] for t, v in measured if a["start"] <= t <= a["end"]),
                     default=0) for a in clenches]
        peak = float(np.median(peaks)) if peaks else None
        metrics[name] = dict(rest_emg_rms_uv=rest, noise_mad_sigma_uv=sigma,
            median_clench_peak_rms_uv=peak,
            peak_to_rest_ratio=peak/rest if peak is not None and rest else None,
            separation_sigma=(peak-rest)/sigma if peak is not None else None)
    return dict(recording=entry["recording"], **score(events, entry["actions"]),
                channels=metrics, events=events)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--profiles", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {}
    if args.profiles:
        result["profiles"] = [profile_summary(p) for p in sorted(cd.HERE.glob("calibration.*.json"))]
    if args.manifest:
        manifest = json.loads(args.manifest.read_text())
        result["recordings"] = [evaluate(e, args.manifest.parent) for e in manifest]
    if not result:
        parser.error("Choose --profiles and/or --manifest")
    rendered = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
