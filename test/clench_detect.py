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
from collections import deque, namedtuple

import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowPresets
from brainflow.data_filter import DataFilter, FilterTypes, NoiseTypes

from config import (board_label, build_parser, eeg_channels_and_names, get_board,
                    prepare_or_explain)

HERE = pathlib.Path(__file__).parent


def calibration_file(board, profile="default"):
    """Where one person's calibration for one board lives.

    Profiles let several people share the headband: each gets their own file, so
    calibrating for your friend never touches yours. Synthetic runs get a
    separate suffix as well -- a smoke test once silently overwrote a real
    calibration, which then loaded back with meaningless thresholds.
    """
    from brainflow.board_shim import BoardIds
    suffix = ".synthetic" if board.board_id == BoardIds.SYNTHETIC_BOARD else ""
    return HERE / f"calibration.{profile}{suffix}.json"


def list_profiles():
    """Every profile name that has a saved calibration, real or synthetic."""
    names = set()
    for path in HERE.glob("calibration.*.json"):
        stem = path.name[len("calibration."):-len(".json")]
        if stem.endswith(".synthetic"):
            stem = stem[:-len(".synthetic")]
        if stem:
            names.add(stem)
    return sorted(names)


def load_calibration(board, profile="default"):
    """Read a profile's calibration, or None if it is missing or from another board."""
    path = calibration_file(board, profile)
    if not path.exists():
        known = list_profiles()
        print(f"  no saved calibration for profile '{profile}'"
              + (f" (known profiles: {', '.join(known)})" if known else ""))
        return None
    data = json.loads(path.read_text())
    if data.get("blink_method") != "p2p_coincidence":
        print(f"  ignoring {path.name}: recorded with the old blink measurement.")
        print("  Blinks are now measured peak-to-peak with both forehead channels")
        print("  agreeing, so the old numbers are in different units. Recalibrate.")
        return None
    if data.get("board") != board_label(board):
        print(f"  ignoring {path.name}: recorded on {data.get('board')}, "
              f"this is {board_label(board)}")
        return None
    return data


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

BLINK_P2P_SECONDS = 0.30    # a blink swoop fits comfortably inside 300 ms
BLINK_COINCIDENCE_MS = 60   # both forehead channels must spike this close together
BLINK_REFRACTORY_MS = 200   # minimum gap between two counted blinks
BLINK_SIGMA_FLOOR = 4.0     # threshold never sits below rest + 4 sigma
FOREHEAD_NOISY_UV = 120.0   # resting peak-to-peak above this means poor contact

CR = "\r"                   # keeps the live meter redrawing on one line
LINE_WIDTH = 120            # event lines pad to this so they fully erase the
                            # meter line underneath; a fixed 20 left fragments.


# ============================================================ signal processing

def _filtered(window, fs, band, notch):
    """Copy one channel, clean it, and band-limit it.

    Order matters. The mains hum sits at 50/60 Hz, which is INSIDE the 20-110 Hz
    EMG band, so it has to come out BEFORE the bandpass -- notching afterwards
    leaves the hum sitting in the number we measure. A venue has far more
    electrical hum than a bedroom, so this is not a theoretical concern.
    """
    signal = np.ascontiguousarray(window, dtype=np.float64)
    if notch:
        DataFilter.remove_environmental_noise(signal, fs, NoiseTypes.FIFTY_AND_SIXTY)
    DataFilter.perform_bandpass(signal, fs, band[0], band[1], 4,
                                FilterTypes.BUTTERWORTH_ZERO_PHASE, 0.0)
    return signal


def _tail(signal, fs, seconds):
    """The most recent `seconds` of a filtered window, minus the wobbly last sliver."""
    edge = int(fs * EDGE_SECONDS)
    length = int(fs * seconds)
    return signal[-(length + edge):-edge] if edge else signal[-length:]


def envelope(window, fs, band=EMG_BAND):
    """How much muscle energy is in the band right now, in uV RMS.

    RMS over a short tail is the right measure for a clench: it is a sustained
    buzz, so "how loud, on average, just now" is exactly the question.
    """
    segment = _tail(_filtered(window, fs, band, notch=True), fs, ENVELOPE_SECONDS)
    if segment.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(segment ** 2)))


def peak_to_peak(window, fs, band=BLINK_BAND):
    """How big the biggest swing was in the recent window, in uV.

    A blink is one quick swoop, not a sustained buzz, so RMS is the wrong tool:
    averaging over a window mixes the swoop with the quiet either side of it and
    makes the blink look smaller than it is. Peak-to-peak (highest minus lowest)
    measures the shape of the event itself.
    """
    segment = _tail(_filtered(window, fs, band, notch=False), fs, BLINK_P2P_SECONDS)
    if segment.size == 0:
        return 0.0
    return float(np.max(segment) - np.min(segment))


class Levels(namedtuple("Levels", "emg blink_left blink_right")):
    """One tick of measurements: clench loudness, and both forehead channels.

    Both forehead channels are kept separate on purpose. A real blink moves both
    eyelids, so it shows on AF7 AND AF8 at the same moment; a bad electrode or a
    stray movement usually shows on only one. Keeping them apart is what lets the
    detector demand agreement.
    """

    @property
    def blink(self):
        """The conservative blink size: whichever forehead channel saw less."""
        return min(self.blink_left, self.blink_right)


def read_levels(board, rows, fs, window_samples):
    """Measure the current tick, or None while the buffer is still filling.

    EMG takes the max across the ear channels: you might clench harder on one
    side, and either side is a valid "yes".
    """
    data = board.get_current_board_data(window_samples,
                                        preset=BrainFlowPresets.DEFAULT_PRESET)
    if data.shape[1] < window_samples:
        return None
    emg = max(envelope(data[r], fs) for r in rows["emg"])
    left, right = (peak_to_peak(data[r], fs) for r in rows["blink"])
    return Levels(emg, left, right)


# =================================================================== calibration

def wait_for_enter(prompt):
    """input() that survives a piped/non-interactive stdin (used by the smoke test)."""
    try:
        input(prompt)
    except EOFError:
        print("  (non-interactive stdin, continuing)")


def collect(board, rows, fs, window_samples, seconds, label):
    """Sample for `seconds` with a countdown. Returns a list of (timestamp, Levels)."""
    samples = []
    started = time.monotonic()
    while True:
        now = time.monotonic()
        remaining = seconds - (now - started)
        if remaining <= 0:
            break
        levels = read_levels(board, rows, fs, window_samples)
        if levels:
            samples.append((now, levels))
            print(f"{CR}  {label}  {remaining:4.1f}s   emg {levels.emg:7.1f} uV   "
                  f"blink L{levels.blink_left:6.0f} R{levels.blink_right:6.0f} uV ",
                  end="", flush=True)
        time.sleep(TICK_SECONDS)
    print()
    return samples


def blink_sizes(samples, provisional_threshold):
    """Pull individual blinks out of a recorded stretch, newest measure first.

    Walks the recording looking for stretches where BOTH channels are above a
    provisional threshold, and records the size of each such stretch. That gives
    one number per blink instead of one number for the whole phase -- which
    matters, because the threshold wants to sit under the SMALLEST blink, and an
    average over the whole phase cannot tell you what the smallest one was.
    """
    sizes = []
    in_blink = False
    peak = 0.0
    for _now, levels in samples:
        both_up = (levels.blink_left > provisional_threshold
                   and levels.blink_right > provisional_threshold)
        if both_up:
            in_blink = True
            peak = max(peak, levels.blink)
        elif in_blink:
            sizes.append(peak)
            in_blink = False
            peak = 0.0
    if in_blink:
        sizes.append(peak)
    return sizes


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
    """Measure rest, then real clenches, then real blinks.

    Clench threshold sits 30% of the way from your resting floor up to your
    measured peak. Blink threshold sits halfway between the resting noise
    (rest + 4 sigma) and the SMALLEST blink you produced -- under your weakest
    blink so none get missed, but clear of the noise.
    """
    print("")
    print("--- CALIBRATION ---")
    print("1) REST: sit still, jaw relaxed and slightly open.")
    print("   Stare at one fixed spot and do not talk. Eye movement shows up on")
    print("   the forehead sensors and would inflate the blink noise floor.")
    wait_for_enter("   Press Enter when ready...")
    rest = collect(board, rows, fs, window_samples, args.baseline_seconds, "resting")

    emg_rest, emg_sigma = robust_baseline([lv.emg for _t, lv in rest])
    # The blink noise floor is the WORSE of the two forehead channels: one
    # threshold serves both, so it has to clear the noisier one.
    blink_rest, blink_sigma = robust_baseline(
        [max(lv.blink_left, lv.blink_right) for _t, lv in rest])
    left_rest = float(np.median([lv.blink_left for _t, lv in rest])) if rest else 0.0
    right_rest = float(np.median([lv.blink_right for _t, lv in rest])) if rest else 0.0

    print(f"   rest:  clench {emg_rest:.1f} +/- {emg_sigma:.1f} uV")
    print(f"          forehead peak-to-peak  AF7 {left_rest:.0f}  AF8 {right_rest:.0f} uV")

    # --- contact check on the forehead pair -------------------------------
    for label, value in (("AF7", left_rest), ("AF8", right_rest)):
        if value > FOREHEAD_NOISY_UV:
            print(f"   !! {label} is noisy at rest ({value:.0f} uV peak-to-peak).")
            print("      That is poor skin contact. Wipe the forehead, sit the band")
            print("      snug just above the eyebrows, and clear any hair underneath.")

    emg_threshold = emg_rest + args.k * emg_sigma
    blink_floor = blink_rest + BLINK_SIGMA_FLOOR * blink_sigma
    blink_threshold = blink_floor
    emg_peak = blink_peak = None
    emg_trials, blink_trials = [], []
    separation = None

    # --blink-only: keep the clench numbers from the saved profile and redo just
    # the blink phase. Only valid while the band has not moved since that run --
    # the clench thresholds were measured against that exact electrode placement.
    reuse = None
    if getattr(args, "blink_only", False):
        reuse = load_calibration(board, getattr(args, "profile", "default"))
        if not reuse or reuse.get("emg_peak") is None:
            print("")
            print("   !! --blink-only needs an existing profile with clench data.")
            print("      Run a full calibration first.")
            return None
        emg_rest = reuse["emg_rest"]
        emg_sigma = reuse["emg_sigma"]
        emg_peak = reuse["emg_peak"]
        emg_threshold = reuse["emg_threshold"]
        emg_trials = reuse.get("emg_trials", [])
        print("")
        print(f"2) CLENCH: skipped, reusing {reuse['saved_at']} "
              f"(peak {emg_peak:.0f} uV, fires at {emg_threshold:.1f} uV)")

    if not args.no_clench_cal and reuse is None:
        # ---------------- clench ----------------
        print("")
        print("2) CLENCH: clench your jaw HARD for the whole 2 seconds, three times.")
        for rep in range(1, 4):
            wait_for_enter(f"   Press Enter, then clench for 2 s  (rep {rep}/3)...")
            samples = collect(board, rows, fs, window_samples, 2.0, f"clench {rep}")
            if samples:
                emg_trials.append(float(max(lv.emg for _t, lv in samples)))
                print(f"   peak {emg_trials[-1]:.1f} uV")
        if emg_trials:
            emg_peak = float(np.median(emg_trials))  # one bad rep cannot skew it
            if emg_peak > emg_rest * 1.5:
                emg_threshold = emg_rest + 0.30 * (emg_peak - emg_rest)
            elif emg_peak > emg_rest:
                print("   !! clench barely rose above rest -- check the ear-tips.")
                print("      Falling back to the statistical threshold.")
            if emg_peak < emg_rest * 4:
                print(f"   !! WEAK CLENCH: peak {emg_peak:.0f} uV is only "
                      f"{emg_peak / emg_rest:.1f}x your resting {emg_rest:.0f} uV.")
                print("      Redo this: clench HARD the instant the countdown starts,")
                print("      and hold it for the whole 2 seconds. Check the ear-tips too.")


    if not args.no_clench_cal:
        # ---------------- blink ----------------
        print("")
        print("3) BLINK: blink hard and deliberately, once a second, for 6 seconds.")
        print("   Separate, distinct blinks -- not fluttering.")
        wait_for_enter("   Press Enter when ready...")
        samples = collect(board, rows, fs, window_samples, 6.0, "blinking")
        blink_trials = blink_sizes(samples, blink_floor)

        if len(blink_trials) >= 2:
            blink_peak = float(min(blink_trials))   # the SMALLEST blink, on purpose
            separation = blink_peak / blink_floor if blink_floor else 0.0
            sizes = ", ".join(f"{v:.0f}" for v in blink_trials)
            print(f"   {len(blink_trials)} blinks detected: {sizes} uV")
            print(f"   smallest {blink_peak:.0f} uV vs noise floor {blink_floor:.0f} uV"
                  f"  ({separation:.1f}x separation)")
            # Halfway between the noise floor and the weakest blink.
            blink_threshold = blink_floor + 0.5 * (blink_peak - blink_floor)
            if separation < 2.0:
                print("   !! WEAK SEPARATION: blinks are close to the noise.")
                print("      Improve forehead contact and redo, or fall back to")
                print("      double-clench for BACK instead of double-blink.")
        else:
            print(f"   !! only {len(blink_trials)} blink(s) detected -- cannot set a")
            print("      blink threshold this way. Using the statistical floor, which")
            print("      is far less reliable. Check forehead contact and redo.")

    calibration = {
        "board": board_label(board),
        "fs": fs,
        "blink_method": "p2p_coincidence",   # guards against loading old profiles
        "emg_rest": emg_rest, "emg_sigma": emg_sigma,
        "emg_peak": emg_peak, "emg_threshold": emg_threshold,
        "emg_trials": emg_trials,
        "blink_rest": blink_rest, "blink_sigma": blink_sigma,
        "blink_floor": blink_floor,
        "blink_peak": blink_peak, "blink_threshold": blink_threshold,
        "blink_trials": blink_trials,
        "blink_separation": separation,
        "blink_rest_left": left_rest, "blink_rest_right": right_rest,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = calibration_file(board, getattr(args, "profile", "default"))
    path.write_text(json.dumps(calibration, indent=2))
    print("")
    print(f"Saved to {path.name}. Reuse it with --load "
          "(only valid while the band stays on your head).")
    return calibration


def describe(calibration):
    print("\n--- THRESHOLDS ---")
    for kind in ("emg", "blink"):
        rest = calibration[f"{kind}_rest"]
        peak = calibration[f"{kind}_peak"]
        threshold = calibration[f"{kind}_threshold"]
        word = "your peak" if kind == "emg" else "smallest blink"
        measured = f", {word} {peak:.0f}" if peak else ""
        headroom = f" ({peak / threshold:.1f}x headroom)" if peak and threshold else ""
        label = ("clench (EMG, ears)" if kind == "emg"
                 else "blink (p2p, forehead)")
        print(f"  {label:<22} rest {rest:6.1f} -> fires at {threshold:6.1f} uV"
              f"{measured}{headroom}")
    separation = calibration.get("blink_separation")
    if separation:
        verdict = "good" if separation >= 3 else ("usable" if separation >= 2 else "WEAK")
        print(f"  blink separation       {separation:.1f}x above the noise floor "
              f"({verdict})")


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


class BlinkDetector:
    """Fires only when BOTH forehead channels spike at the same moment.

    One detector per eye. A real blink moves both eyelids together, so AF7 and
    AF8 cross their threshold within a few tens of milliseconds of each other.
    A loose electrode, a head turn or a stray bit of interference almost always
    hits one channel, or hits both but far apart in time -- so requiring
    agreement throws away most false blinks for free.

    Note on resolution: the detector ticks at 20 Hz, so two channels crossing on
    the same tick read as 0 ms apart and one tick apart reads as 50 ms. The
    60 ms window therefore means "same tick, or one tick apart".
    """

    def __init__(self, threshold, coincidence_ms=BLINK_COINCIDENCE_MS,
                 refractory_ms=BLINK_REFRACTORY_MS):
        self.threshold = threshold
        self.left = EdgeDetector(threshold, 0, refractory_ms)
        self.right = EdgeDetector(threshold, 0, refractory_ms)
        self.coincidence_ms = coincidence_ms
        self.refractory_ms = refractory_ms
        self.rose_left = None
        self.rose_right = None
        self.last_blink_at = -1e9

    def update(self, levels, now):
        """Feed one tick. Returns the two channels' gap in ms if a blink fired."""
        edge_left = self.left.update(levels.blink_left, now)
        edge_right = self.right.update(levels.blink_right, now)
        if edge_left and edge_left[0] == "rise":
            self.rose_left = now
        if edge_right and edge_right[0] == "rise":
            self.rose_right = now

        # Forget a rise once it is too old to pair with anything. Without this a
        # channel stuck above threshold would sit there waiting to pair with an
        # unrelated spike minutes later.
        window = self.coincidence_ms / 1000.0
        if self.rose_left is not None and now - self.rose_left > window:
            self.rose_left = None
        if self.rose_right is not None and now - self.rose_right > window:
            self.rose_right = None

        if self.rose_left is not None and self.rose_right is not None:
            gap_ms = abs(self.rose_left - self.rose_right) * 1000
            if (now - self.last_blink_at) * 1000 > self.refractory_ms:
                self.last_blink_at = now
                self.rose_left = self.rose_right = None
                return gap_ms
        return None


class GestureRecognizer:
    """Turns the two envelope streams into named gestures.

    Kept separate from the board loop on purpose: it is pure arithmetic over
    a Levels tick and a timestamp, so test_gestures.py can drive it with made-up
    numbers and prove CLENCH / LONG_CLENCH / DOUBLE_BLINK fire correctly without
    anyone having to put the headband on.
    """

    def __init__(self, emg_threshold, blink_threshold, long_ms=1500, double_ms=700,
                 emit_start=False):
        self.clench = EdgeDetector(emg_threshold, MIN_EVENT_MS, REFRACTORY_MS)
        self.blink = BlinkDetector(blink_threshold)
        self.long_ms = long_ms
        self.double_ms = double_ms
        # emit_start: fire CLENCH_START the instant the envelope crosses the
        # threshold, instead of waiting for the release. Anything interactive --
        # a game, a scanning board -- must use this: waiting for release adds the
        # whole length of the clench to the latency, which reads as "one cell late".
        self.emit_start = emit_start
        self.long_fired = False      # one held clench emits LONG_CLENCH only once
        self.pending_blink = None    # a blink waiting to see if a second follows
        self.counts = {"CLENCH": 0, "CLENCH_START": 0, "LONG_CLENCH": 0,
                       "BLINK": 0, "DOUBLE_BLINK": 0}

    def update(self, levels, now):
        """Feed one Levels tick. Returns a list of (NAME, detail) fired on it."""
        events = []

        # --- jaw ---
        edge = self.clench.update(levels.emg, now)
        if edge and edge[0] == "rise":
            self.long_fired = False
            if self.emit_start:
                events.append(("CLENCH_START", f"peak so far {edge[2]:6.1f} uV"))
        elif edge and edge[0] == "fall" and not self.long_fired:
            events.append(("CLENCH", f"{edge[1]:5.0f} ms   peak {edge[2]:6.1f} uV"))
        # Fire the long clench while it is still held, not on release: someone
        # calling for help should get feedback at the 1.5 s mark, not afterwards.
        if (self.clench.active and not self.long_fired
                and self.clench.held_ms(now) >= self.long_ms):
            events.append(("LONG_CLENCH", f"held {self.long_ms} ms   -- HELP"))
            self.long_fired = True

        # --- eyes ---
        # A blink only exists if both forehead channels agreed; `coincidence` is
        # how far apart they were, which is worth showing while tuning.
        coincidence = self.blink.update(levels, now)
        if coincidence is not None:
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
        for name, detail in recognizer.update(levels, now):
            line = f"  [{now - started:6.1f}s]  {name:<13} {detail}"
            print(CR + line.ljust(LINE_WIDTH))
            recent.append(name)

        # Live meter: bar fills as you approach the threshold, | marks the line.
        status = (f"  {meter(levels.emg, recognizer.clench.threshold)} emg {levels.emg:6.1f}   "
                  f"{meter(levels.blink, recognizer.blink.threshold)} blink "
                  f"L{levels.blink_left:5.0f} R{levels.blink_right:5.0f}   "
                  f"{' '.join(list(recent)[-3:])}")
        print(CR + status.ljust(LINE_WIDTH), end="", flush=True)
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
    parser.add_argument("--blink-only", action="store_true",
                        help="redo only the rest + blink phases, keeping the clench "
                             "numbers from the saved profile. Only valid if the band "
                             "has not moved since that calibration.")
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

    if not prepare_or_explain(board):
        return 1
    try:
        board.start_stream()
        time.sleep(WINDOW_SECONDS + 0.3)  # let the window fill before we measure

        # --blink-only always recalibrates; it reuses the clench numbers inside
        # calibrate() rather than loading the whole profile.
        calibration = (load_calibration(board, args.profile)
                       if args.load and not args.blink_only else None)
        if calibration:
            print("")
            print(f"Loaded calibration from {calibration['saved_at']}.")
            print("  (Re-run without --load if you took the band off since then.)")
        else:
            if args.load and not args.blink_only:
                print("")
                print("No usable saved calibration -- calibrating now.")
            calibration = calibrate(board, rows, fs, window_samples, args)
        if calibration is None:
            return 1

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
