"""Step 5: turn jaw clenches and blinks into INPUT EVENTS.

This is the first script here that produces something you can actually drive a UI
with. It calibrates to *you*, then prints events as they happen:

    CLENCH        a short jaw clench            -> "pick this tile"
    LONG_CLENCH   a clench held past --long-ms  -> "call for help"
    BLINK         one blink
    DOUBLE_BLINK  two blinks inside --double-ms -> "go back"

    python clench_detect.py                 # calibrate, then detect
    python clench_detect.py --load          # reuse the last calibration
    python clench_detect.py --synthetic --no-clench-cal   # smoke test, no hardware

Why calibration is not optional
-------------------------------
Clench strength in microvolts depends on your jaw, your skin moisture, and exactly
how the ear-tips are sitting *today*. A threshold that works now will be wrong
tomorrow, or after you take the band off and put it back on. So we measure two
things every session: how quiet you are at rest, and how loud a real clench is.

How detection works
-------------------
Jaw clench is EMG (muscle), not EEG: a burst of FAST activity, ~20-110 Hz, loudest
on the ear electrodes TP9/TP10. We bandpass to that range, take a short rolling RMS
("envelope"), and watch for it to cross a threshold.

Blink is the opposite: a big SLOW deflection, ~1-10 Hz, on the forehead electrodes
AF7/AF8 (they sit right above your eyes). Same envelope trick, different band and
different channels, so the two detectors barely interfere.
"""

import json
import pathlib
import sys
import time
from collections import deque

import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowPresets
from brainflow.data_filter import DataFilter, FilterTypes, NoiseTypes

from config import board_label, build_parser, eeg_channels_and_names, get_board

CALIBRATION_FILE = pathlib.Path(__file__).parent / "calibration.json"

# --- tuning knobs you probably will not need to touch ----------------------
EMG_BAND = (20.0, 110.0)    # Hz: jaw muscle. Above EEG, below the Nyquist limit.
BLINK_BAND = (1.0, 10.0)    # Hz: eyelid movement artifact.
WINDOW_SECONDS = 1.0        # how much signal each filter pass sees
ENVELOPE_SECONDS = 0.20     # how much of the window's tail becomes the RMS value
EDGE_SECONDS = 0.02         # trimmed off the very end: filter edge wobble
TICK_SECONDS = 0.05         # detector runs at 20 Hz
RELEASE_FRACTION = 0.6      # hysteresis: must fall to 60% of threshold to release
MIN_EVENT_MS = 80           # shorter than this is a twitch, not a deliberate clench
REFRACTORY_MS = 250         # ignore re-triggers this soon after an event

CR = "\r"                   # keeps the live meter redrawing on one line


# ============================================================ signal processing

def envelope(window, fs, band):
    """One number: how much energy is in `band` right now, in uV RMS.

    `window` is a single channel's recent samples. We filter a whole second so the
    filter has room to settle, then measure only the last ~200 ms, minus a sliver
    at the very end where zero-phase filtering always wobbles.
    """
    signal = np.ascontiguousarray(window, dtype=np.float64)
    DataFilter.perform_bandpass(signal, fs, band[0], band[1], 4,
                                FilterTypes.BUTTERWORTH_ZERO_PHASE, 0.0)
    DataFilter.remove_environmental_noise(signal, fs, NoiseTypes.FIFTY_AND_SIXTY)

    edge = int(fs * EDGE_SECONDS)
    tail = int(fs * ENVELOPE_SECONDS)
    segment = signal[-(tail + edge):-edge] if edge else signal[-tail:]
    if segment.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(segment ** 2)))


def read_levels(board, rows, fs, window_samples):
    """Current (emg, blink) envelope levels, or None if the buffer is still filling.

    EMG takes the max across the ear channels: you might clench harder on one side,
    and either side is a valid "yes". Blink takes the max across the forehead pair.
    """
    data = board.get_current_board_data(window_samples,
                                        preset=BrainFlowPresets.DEFAULT_PRESET)
    if data.shape[1] < window_samples:
        return None
    emg = max(envelope(data[r], fs, EMG_BAND) for r in rows["emg"])
    blink = max(envelope(data[r], fs, BLINK_BAND) for r in rows["blink"])
    return emg, blink


# =================================================================== calibration

def wait_for_enter(prompt):
    """input() that survives a piped/non-interactive stdin (used by the smoke test)."""
    try:
        input(prompt)
    except EOFError:
        print("  (non-interactive stdin, continuing)")


def collect(board, rows, fs, window_samples, seconds, label):
    """Sample both envelopes for `seconds`, showing a countdown. Returns (emg, blink)."""
    emg_values, blink_values = [], []
    started = time.monotonic()
    while True:
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        levels = read_levels(board, rows, fs, window_samples)
        if levels:
            emg_values.append(levels[0])
            blink_values.append(levels[1])
            print(f"{CR}  {label}  {remaining:4.1f}s   emg {levels[0]:7.1f} uV   "
                  f"blink {levels[1]:7.1f} uV ", end="", flush=True)
        time.sleep(TICK_SECONDS)
    print()
    return emg_values, blink_values


def robust_baseline(values):
    """Median and MAD-based sigma. Median/MAD ignore the odd stray spike; mean/std
    would be dragged upward by an accidental swallow or twitch during calibration."""
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        return 0.0, 1.0
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    sigma = mad * 1.4826  # MAD -> standard-deviation-equivalent for normal noise
    return median, max(sigma, 1e-6)


def calibrate(board, rows, fs, window_samples, args):
    """Measure rest, then (optionally) measure real clenches and blinks.

    Two thresholds come out of this:
      - with active calibration: sit 30% of the way from rest up to your real peak.
        This is far more reliable than a pure noise multiple, because it knows how
        much headroom you actually have.
      - without it: rest + k*sigma, a plain statistical outlier test.
    """
    print("\n--- CALIBRATION ---")
    print("1) REST: sit still, jaw relaxed and slightly open, try not to blink.")
    wait_for_enter("   Press Enter when ready...")
    rest_emg, rest_blink = collect(board, rows, fs, window_samples,
                                   args.baseline_seconds, "resting")

    emg_rest, emg_sigma = robust_baseline(rest_emg)
    blink_rest, blink_sigma = robust_baseline(rest_blink)
    print(f"   rest:  emg {emg_rest:.1f} +/- {emg_sigma:.1f} uV    "
          f"blink {blink_rest:.1f} +/- {blink_sigma:.1f} uV")

    # Statistical fallback thresholds, used as-is if we skip the active phase.
    emg_threshold = emg_rest + args.k * emg_sigma
    blink_threshold = blink_rest + args.k * blink_sigma
    emg_peak = blink_peak = None

    if not args.no_clench_cal:
        print("\n2) CLENCH: clench your jaw HARD for the whole 2 seconds, three times.")
        peaks = []
        for rep in range(1, 4):
            wait_for_enter(f"   Press Enter, then clench for 2 s  (rep {rep}/3)...")
            values, _ = collect(board, rows, fs, window_samples, 2.0, f"clench {rep}")
            if values:
                peaks.append(float(np.max(values)))
                print(f"   peak {peaks[-1]:.1f} uV")
        if peaks:
            emg_peak = float(np.median(peaks))  # median of 3: one bad rep cannot skew it
            if emg_peak > emg_rest * 1.5:
                # 30% of the way up. Low enough that a gentle clench still fires,
                # high enough that chewing-adjacent noise does not.
                emg_threshold = emg_rest + 0.30 * (emg_peak - emg_rest)
            else:
                print("   !! clench barely rose above rest -- check the ear-tips.")
                print("      Falling back to the statistical threshold.")

        print("\n3) BLINK: blink hard, once a second, for 5 seconds.")
        wait_for_enter("   Press Enter when ready...")
        _, blink_values = collect(board, rows, fs, window_samples, 5.0, "blinking")
        if blink_values:
            blink_peak = float(np.percentile(blink_values, 90))
            if blink_peak > blink_rest * 1.5:
                blink_threshold = blink_rest + 0.30 * (blink_peak - blink_rest)
            else:
                print("   !! blinks barely rose above rest. Using the statistical threshold.")

    calibration = {
        "board": board_label(board),
        "fs": fs,
        "emg_rest": emg_rest, "emg_sigma": emg_sigma,
        "emg_peak": emg_peak, "emg_threshold": emg_threshold,
        "blink_rest": blink_rest, "blink_sigma": blink_sigma,
        "blink_peak": blink_peak, "blink_threshold": blink_threshold,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    CALIBRATION_FILE.write_text(json.dumps(calibration, indent=2))
    print(f"\nSaved to {CALIBRATION_FILE.name}. Reuse it with --load "
          "(only valid while the band stays on your head).")
    return calibration


def describe(calibration):
    print("\n--- THRESHOLDS ---")
    for kind in ("emg", "blink"):
        rest = calibration[f"{kind}_rest"]
        peak = calibration[f"{kind}_peak"]
        threshold = calibration[f"{kind}_threshold"]
        measured = f", your peak {peak:.0f}" if peak else ""
        headroom = f" ({peak / threshold:.1f}x headroom)" if peak and threshold else ""
        label = "clench (EMG, ears)" if kind == "emg" else "blink (forehead)"
        print(f"  {label:<20} rest {rest:6.1f} -> fires at {threshold:6.1f} uV"
              f"{measured}{headroom}")


# ==================================================================== detection

class EdgeDetector:
    """Threshold crossing with hysteresis, a minimum duration and a refractory gap.

    Hysteresis (rise at threshold, fall at 60% of it) stops a signal hovering right
    at the line from machine-gunning events. The refractory gap stops the tail of
    one clench from being read as the start of the next.
    """

    def __init__(self, threshold, min_ms, refractory_ms):
        self.threshold = threshold
        self.release = threshold * RELEASE_FRACTION
        self.min_ms = min_ms
        self.refractory_ms = refractory_ms
        self.active = False
        self.started_at = 0.0
        self.last_event_at = -1e9
        self.peak = 0.0

    def update(self, level, now):
        """Feed one sample. Returns ("rise"|"fall", duration_ms, peak) at edges."""
        if not self.active:
            if level > self.threshold and (now - self.last_event_at) * 1000 > self.refractory_ms:
                self.active = True
                self.started_at = now
                self.peak = level
                return ("rise", 0.0, level)
        else:
            self.peak = max(self.peak, level)
            if level < self.release:
                self.active = False
                self.last_event_at = now
                duration_ms = (now - self.started_at) * 1000
                peak = self.peak
                self.peak = 0.0
                if duration_ms >= self.min_ms:
                    return ("fall", duration_ms, peak)
        return None

    def held_ms(self, now):
        return (now - self.started_at) * 1000 if self.active else 0.0


class GestureRecognizer:
    """Turns the two envelope streams into named gestures.

    Kept separate from the board loop on purpose: it is pure arithmetic over
    (emg_level, blink_level, now), so test_gestures.py can drive it with made-up
    numbers and prove CLENCH / LONG_CLENCH / DOUBLE_BLINK fire correctly without
    anyone having to put the headband on.
    """

    def __init__(self, emg_threshold, blink_threshold, long_ms=1500, double_ms=700):
        self.clench = EdgeDetector(emg_threshold, MIN_EVENT_MS, REFRACTORY_MS)
        self.blink = EdgeDetector(blink_threshold, 40, 150)
        self.long_ms = long_ms
        self.double_ms = double_ms
        self.long_fired = False      # one held clench emits LONG_CLENCH only once
        self.pending_blink = None    # a blink waiting to see if a second follows
        self.counts = {"CLENCH": 0, "LONG_CLENCH": 0, "BLINK": 0, "DOUBLE_BLINK": 0}

    def update(self, emg_level, blink_level, now):
        """Feed one tick. Returns a list of (NAME, detail) fired on this tick."""
        events = []

        # --- jaw ---
        edge = self.clench.update(emg_level, now)
        if edge and edge[0] == "rise":
            self.long_fired = False
        elif edge and edge[0] == "fall" and not self.long_fired:
            events.append(("CLENCH", f"{edge[1]:5.0f} ms   peak {edge[2]:6.1f} uV"))
        # Fire the long clench while it is still held, not on release: someone
        # calling for help should get feedback at the 1.5 s mark, not afterwards.
        if (self.clench.active and not self.long_fired
                and self.clench.held_ms(now) >= self.long_ms):
            events.append(("LONG_CLENCH", f"held {self.long_ms} ms   -- HELP"))
            self.long_fired = True

        # --- eyes ---
        edge = self.blink.update(blink_level, now)
        if edge and edge[0] == "fall":
            gap_ms = (now - self.pending_blink) * 1000 if self.pending_blink else None
            if gap_ms is not None and gap_ms <= self.double_ms:
                events.append(("DOUBLE_BLINK", f"gap {gap_ms:4.0f} ms  -- BACK"))
                self.pending_blink = None
            else:
                self.pending_blink = now
        # A lone blink only becomes a BLINK once its partner window has expired.
        if (self.pending_blink is not None
                and (now - self.pending_blink) * 1000 > self.double_ms):
            events.append(("BLINK", ""))
            self.pending_blink = None

        for name, _ in events:
            self.counts[name] += 1
        return events


def detect_loop(board, rows, fs, window_samples, calibration, args):
    """The input loop: read envelopes, recognise gestures, print them."""
    recognizer = GestureRecognizer(calibration["emg_threshold"],
                                   calibration["blink_threshold"],
                                   args.long_ms, args.double_ms)
    recent = deque(maxlen=6)

    print("\n--- LISTENING ---  clench = CLENCH, hold = LONG_CLENCH, "
          "two blinks = DOUBLE_BLINK.   Ctrl-C to stop.\n")
    started = time.monotonic()

    while True:
        now = time.monotonic()
        levels = read_levels(board, rows, fs, window_samples)
        if levels is None:
            time.sleep(TICK_SECONDS)
            continue
        emg_level, blink_level = levels

        for name, detail in recognizer.update(emg_level, blink_level, now):
            print(f"{CR}  [{now - started:6.1f}s]  {name:<13} {detail}" + " " * 20)
            recent.append(name)

        # Live meter: bar fills as you approach the threshold, | marks the line.
        print(f"{CR}  {meter(emg_level, recognizer.clench.threshold)} emg {emg_level:6.1f}   "
              f"{meter(blink_level, recognizer.blink.threshold)} blink {blink_level:6.1f}   "
              f"{' '.join(list(recent)[-3:]):<40}", end="", flush=True)
        time.sleep(TICK_SECONDS)


def meter(level, threshold, width=14):
    """ASCII bar where the threshold sits at 2/3 of the width."""
    scale = threshold * 1.5 if threshold > 0 else 1.0
    filled = min(width, int(width * level / scale))
    mark = int(width * 2 / 3)
    cells = ["#" if i < filled else "-" for i in range(width)]
    if mark < width:
        cells[mark] = "|" if filled <= mark else "#"
    return "[" + "".join(cells) + "]"


# ========================================================================= main

def main():
    parser = build_parser("Detect jaw clenches and blinks as input events.")
    parser.add_argument("--load", action="store_true",
                        help="reuse calibration.json instead of recalibrating")
    parser.add_argument("--no-clench-cal", action="store_true",
                        help="skip the active clench/blink phase, use rest + k*sigma only")
    parser.add_argument("--baseline-seconds", type=float, default=10.0,
                        help="length of the resting measurement (default 10)")
    parser.add_argument("--k", type=float, default=6.0,
                        help="sigma multiple for the fallback threshold (default 6)")
    parser.add_argument("--long-ms", type=int, default=1500,
                        help="hold this long for LONG_CLENCH (default 1500)")
    parser.add_argument("--double-ms", type=int, default=700,
                        help="two blinks within this gap are a DOUBLE_BLINK (default 700)")
    args = parser.parse_args()

    board = get_board(args)
    fs = BoardShim.get_sampling_rate(board.board_id, BrainFlowPresets.DEFAULT_PRESET)
    channels, names = eeg_channels_and_names(board.board_id)
    window_samples = int(fs * WINDOW_SECONDS)

    # channels/names are in order TP9, AF7, AF8, TP10 on the Muse 2:
    # ears first and last (muscle), forehead in the middle (eyes).
    rows = {"emg": [channels[0], channels[3]], "blink": [channels[1], channels[2]]}
    print(f"{board_label(board)} @ {fs} Hz")
    print(f"  clench channels: {names[0]}, {names[3]}   "
          f"blink channels: {names[1]}, {names[2]}")

    board.prepare_session()
    try:
        board.start_stream()
        time.sleep(WINDOW_SECONDS + 0.3)  # let the window fill before we measure

        if args.load and CALIBRATION_FILE.exists():
            calibration = json.loads(CALIBRATION_FILE.read_text())
            print(f"\nLoaded calibration from {calibration['saved_at']}.")
            print("  (Re-run without --load if you took the band off since then.)")
        else:
            if args.load:
                print("\nNo calibration.json yet -- calibrating now.")
            calibration = calibrate(board, rows, fs, window_samples, args)

        describe(calibration)
        detect_loop(board, rows, fs, window_samples, calibration, args)
    except KeyboardInterrupt:
        print("\n\nStopped.")
    finally:
        for cleanup in (board.stop_stream, board.release_session):
            try:
                cleanup()
            except Exception:
                pass
        print("Session released.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
