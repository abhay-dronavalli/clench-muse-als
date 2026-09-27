"""Guided raw recording: the script saves cue windows alongside the raw signal.

`record.py` just dumps N seconds and leaves you to remember what happened.
This one walks you through a fixed protocol -- rest, three kinds of clench,
blinks, then talking and chewing as noise -- printing a big cue and beeping at
every rep, and writes the timestamps it cued next to the CSVs. Detection can
then be tuned offline against real signal with no headband attached:

    python record_protocol.py --profile taher            # the real thing
    python record_protocol.py --synthetic --speed 6      # 30 s dress rehearsal
    python record_protocol.py --dry-run                  # prompts only, no board

While it runs:  X = the rep I just cued did NOT happen (drops that label)
                M = mark this moment (something twitched, the band slipped)
                Q = stop early and keep what has been recorded

Outputs in recordings/, all sharing one <stamp>_<label> prefix:
    *_default.csv      EEG @ 256 Hz -- the only file the detector reads
    *_auxiliary.csv    accelerometer + gyro
    *_ancillary.csv    PPG
    *_labels.json      every phase and rep, in seconds from the first EEG sample
    *_manifest.json    the same truth as an evaluate_detection.py manifest

Timestamps come from BrainFlow's own timestamp row, not from our clock, so they
line up with the samples even if the stream started late.
"""

import datetime as dt
import json
import pathlib
import shutil
import sys
import time

from brainflow.board_shim import BoardShim, BrainFlowPresets
from brainflow.data_filter import DataFilter

from config import (
    available_presets,
    board_label,
    build_parser,
    eeg_channels_and_names,
    enable_ppg,
    get_board,
    prepare_or_explain,
    preset_name,
)

HERE = pathlib.Path(__file__).parent
RECORDINGS = HERE / "recordings"

# Seconds of filter and envelope lag to leave at the end of every truth window.
# read_levels looks back over a 1 s window and reports an RMS tail, so a gesture
# is still visible for a moment after the jaw has let go.
RELEASE_MARGIN = 0.6

# The protocol, exactly as ordered for this recording. Edit this list to change
# it; the cues, the labels and the manifest all follow from it.
#   kind="rest"  -> one quiet block, also used as the calibration baseline
#   kind="reps"  -> `reps` cued holds of `hold` s, `gap` s apart
#   kind="noise" -> one block of deliberate artifact, no gesture labels
PROTOCOL = [
    dict(kind="rest", name="rest", seconds=30.0, baseline=True,
         cue="REST -- sit still, eyes open, jaw loose",
         detail="Natural blinks are fine. Do not talk, chew or swallow hard."),
    dict(kind="reps", name="quick clenches", reps=5, hold=0.4, gap=4.0,
         event="CLENCH", cue="CLENCH",
         detail="A short, comfortable bite -- not your hardest. The strength you "
                "could repeat all day."),
    dict(kind="reps", name="1 s clenches", reps=5, hold=1.0, gap=4.0,
         event="CLENCH", cue="CLENCH and HOLD",
         detail="Hold that same comfortable bite until the bar empties."),
    dict(kind="reps", name="3 s clenches", reps=2, hold=3.0, gap=5.0,
         event="LONG_CLENCH", cue="CLENCH and HOLD LONG",
         detail="Same strength, just keep holding. This is the call-for-help hold."),
    dict(kind="reps", name="blinks", reps=5, hold=0.25, gap=3.0,
         event="BLINK", cue="BLINK",
         detail="One deliberate blink per cue, both eyes, then open fully again."),
    dict(kind="noise", name="talking", seconds=20.0,
         cue="TALK -- read something out loud",
         detail="Normal voice, keep going for the whole block. Jaw otherwise relaxed."),
    dict(kind="noise", name="chewing / yawning", seconds=20.0,
         cue="CHEW and YAWN",
         detail="Chew as if eating, and fit in a couple of real yawns."),
]

# A focused comparison for blinks missed or mistaken for clenches. Keep the
# standard protocol above intact so existing recordings stay comparable.
BLINK_PROTOCOL = [
    PROTOCOL[0],
    dict(kind="reps", name="gentle blinks", reps=6, hold=.25, gap=3.,
         release_margin=1.5,
         event="BLINK", cue="BLINK gently",
         detail="One comfortable blink, jaw relaxed. Open fully afterwards."),
    dict(kind="reps", name="firm blinks", reps=6, hold=.25, gap=3.,
         release_margin=1.5,
         event="BLINK", cue="BLINK firmly",
         detail="One firmer blink like the ones that misfire. Do not bite or force it."),
    dict(kind="reps", name="short clenches", reps=6, hold=.5, gap=4.,
         event="CLENCH", cue="CLENCH briefly",
         detail="Comfortable bite, eyes open; release at the second beep."),
    dict(kind="noise", name="head movement", seconds=15.,
         cue="Small comfortable head turns and nods",
         detail="Jaw loose. No deliberate blinks or clenches. M marks a band slip."),
    dict(kind="noise", name="final rest", seconds=15.,
         cue="REST -- eyes open, jaw loose",
         detail="Sit still. An observer can note any spontaneous blinks with M."),
]


# ----------------------------------------------------------------- terminal UI

def beep(enabled, frequency=880, ms=90):
    """Short tone so you can keep your eyes closed and still follow the cues."""
    if not enabled:
        return
    try:
        import winsound
        winsound.Beep(frequency, ms)
    except Exception:
        print("\a", end="", flush=True)


def banner(text, detail=""):
    line = "=" * max(len(text) + 4, 60)
    print("\n" + line + "\n  " + text + "\n" + line)
    if detail:
        print(f"  {detail}")


def poll_keys():
    """Keys pressed since the last call, lowercased. Empty list where unsupported."""
    try:
        import msvcrt
    except ImportError:
        return []
    keys = []
    while msvcrt.kbhit():
        char = msvcrt.getwch()
        if char:
            keys.append(char.lower())
    return keys


class Aborted(Exception):
    """Q was pressed: stop cueing, but keep the samples already collected."""


def countdown(seconds, label, session, bar=False):
    """Sleep `seconds` showing a clock, while watching for X / M / Q.

    Returns the keys seen, so the caller decides what X means at that point.
    """
    seen = []
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        if elapsed >= seconds:
            break
        left = seconds - elapsed
        meter = ""
        if bar:
            filled = int(24 * (1 - left / seconds))
            meter = "[" + "#" * filled + "-" * (24 - filled) + "]"
        print(f"\r  {label} {left:5.1f}s {meter}   ", end="", flush=True)
        for key in poll_keys():
            seen.append(key)
            if key == "q":
                raise Aborted()
            if key == "m":
                session.mark("manual marker")
        time.sleep(min(0.05, left))
    done_bar = "[" + "#" * 24 + "]" if bar else ""
    print(f"\r  {label}   0.0s {done_bar}   ")
    return seen


# --------------------------------------------------------------- the recording

class Session:
    """Collects cue times as unix seconds; converts to sample time at the end."""

    def __init__(self):
        self.phases = []
        self.actions = []
        self.markers = []
        self.baseline = None

    def mark(self, note):
        self.markers.append(dict(t_unix=time.time(), note=note))
        print(f"\n  marker noted ({note})")

    def phase(self, spec, start_unix, end_unix):
        self.phases.append(dict(name=spec["name"], kind=spec["kind"],
                                t_start_unix=start_unix, t_end_unix=end_unix))

    def action(self, spec, index, start_unix, hold, margin=RELEASE_MARGIN):
        self.actions.append(dict(event=spec["event"], phase=spec["name"], rep=index,
                                 t_start_unix=start_unix,
                                 t_end_unix=start_unix + hold + margin,
                                 release_margin_seconds=margin,
                                 happened=True))


def run_protocol(session, sound, speed, protocol=PROTOCOL):
    """Cue every phase. `speed` divides all durations, for dress rehearsals."""
    for spec in protocol:
        banner(spec["name"].upper(), spec.get("detail", ""))
        phase_start = time.time()

        if spec["kind"] in ("rest", "noise"):
            print(f"  {spec['cue']}")
            beep(sound, 660, 120)
            countdown(spec["seconds"] / speed, spec["cue"].split(" --")[0],
                      session, bar=True)
            phase_end = time.time()
            if spec.get("baseline"):
                # Trim both ends: the first seconds are a person still settling.
                # Never more than a sixth of the block, so a sped-up rehearsal
                # cannot trim the baseline inside out.
                trim = min(2.0, spec["seconds"] / speed / 6)
                session.baseline = (phase_start + trim, phase_end - trim)
        else:
            hold = spec["hold"] / speed
            print(f"  {spec['reps']} x {spec['hold']:.2g} s, one every "
                  f"{spec['gap']:.2g} s. Wait for the cue.")
            for index in range(1, spec["reps"] + 1):
                countdown(spec["gap"] / speed, f"rep {index}/{spec['reps']} in", session)
                beep(sound, 1180, 80)
                print(f"  >>> {spec['cue']} <<<")
                session.action(spec, index, time.time(), hold,
                               spec.get("release_margin", RELEASE_MARGIN) / speed)
                keys = countdown(hold, "HOLD", session, bar=True)
                beep(sound, 520, 80)
                print("  release")
                # X during or just after the hold retracts the label: that is how
                # a rep you did not manage stops counting as a detection miss.
                keys += countdown(0.8 / speed, "relax", session)
                if "x" in keys:
                    session.actions[-1]["happened"] = False
                    print(f"  rep {index} marked as NOT DONE (label dropped)")
            phase_end = time.time()

        session.phase(spec, phase_start, phase_end)


def total_seconds(speed, protocol=PROTOCOL):
    total = 0.0
    for spec in protocol:
        if spec["kind"] in ("rest", "noise"):
            total += spec["seconds"]
        else:
            total += spec["reps"] * (spec["gap"] + spec["hold"] + 0.8)
    return total / speed


# -------------------------------------------------------------------- outputs

def write_outputs(board, session, stamp, tag, label, profile, speed, aborted):
    """Save one CSV per preset, plus the labels and the evaluator manifest."""
    board_id = board.board_id
    fs = BoardShim.get_sampling_rate(board_id, BrainFlowPresets.DEFAULT_PRESET)
    eeg = board.get_board_data(preset=BrainFlowPresets.DEFAULT_PRESET)
    if not eeg.size:
        print("\nNo EEG arrived. Nothing to label -- run check_connection.py.")
        return None

    # Sample time zero is the first EEG sample, which is what the replay reader
    # sees as t=0. Every cue is shifted onto that clock.
    timestamp_row = BoardShim.get_timestamp_channel(board_id,
                                                    BrainFlowPresets.DEFAULT_PRESET)
    t0 = float(eeg[timestamp_row][0])
    duration = float(eeg[timestamp_row][-1]) - t0

    def rel(t_unix):
        return round(t_unix - t0, 3)

    written = {}
    for preset in available_presets(board_id):
        data = (eeg if preset == BrainFlowPresets.DEFAULT_PRESET
                else board.get_board_data(preset=preset))
        name = preset_name(preset).replace("_PRESET", "").lower()
        path = RECORDINGS / f"{stamp}{tag}_{name}.csv"
        if not data.size:
            print(f"  {preset_name(preset):<18} no data, skipped")
            continue
        DataFilter.write_file(data, str(path), "w")
        print(f"  {preset_name(preset):<18} {data.shape[1]:>6} samples @ "
              f"{BoardShim.get_sampling_rate(board_id, preset):>3} Hz -> {path.name}")
        written[name] = path

    channels, names = eeg_channels_and_names(board_id)
    actions = [dict(start=rel(a["t_start_unix"]), end=rel(a["t_end_unix"]),
                    event=a["event"], phase=a["phase"], rep=a["rep"],
                    release_margin_seconds=a["release_margin_seconds"])
               for a in session.actions if a["happened"]]
    dropped = [dict(phase=a["phase"], rep=a["rep"], cued_at=rel(a["t_start_unix"]))
               for a in session.actions if not a["happened"]]
    baseline = ([rel(session.baseline[0]), rel(session.baseline[1])]
                if session.baseline else None)
    labels = dict(
        recording=written["default"].name,
        board=board_label(board), board_id=int(board_id), fs=fs,
        recorded_at=dt.datetime.now().isoformat(timespec="seconds"),
        label=label, profile=profile, speed=speed, aborted=aborted,
        duration_seconds=round(duration, 2),
        eeg_columns=dict(zip(names, channels)),
        files={kind: path.name for kind, path in written.items()},
        baseline=baseline,
        phases=[dict(name=p["name"], kind=p["kind"],
                     start=rel(p["t_start_unix"]), end=rel(p["t_end_unix"]))
                for p in session.phases],
        actions=actions,
        not_done=dropped,
        markers=[dict(t=rel(m["t_unix"]), note=m["note"]) for m in session.markers],
        release_margin_seconds=RELEASE_MARGIN,
        note="Times are seconds from the first EEG sample. These are cued windows "
             "corrected only where X was pressed, so they record intent: check a "
             "surprising miss against the waveform before trusting the label.",
    )
    labels_path = RECORDINGS / f"{stamp}{tag}_labels.json"
    labels_path.write_text(json.dumps(labels, indent=2) + "\n")

    # The evaluator wants its own flatter shape, with the profile beside it.
    suffix = ".synthetic" if board_label(board) == "SYNTHETIC_BOARD" else ""
    profile_src = HERE / f"calibration.{profile}{suffix}.json"
    profile_snapshot = RECORDINGS / f"{stamp}{tag}_calibration.json"
    if profile_src.exists():
        shutil.copy2(profile_src, profile_snapshot)
    else:
        print(f"\n  ! no {profile_src.name} to copy: calibrate this profile, then "
              f"copy its JSON to {profile_snapshot.name} before evaluating.")
    manifest = [dict(recording=written["default"].name, profile=profile_snapshot.name,
                     board_id=int(board_id), baseline=baseline or [2, 28],
                     actions=actions)]
    manifest_path = RECORDINGS / f"{stamp}{tag}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"\n  labels   -> {labels_path.name}")
    print(f"  manifest -> {manifest_path.name}")
    print("\nTimestamps (seconds from the first EEG sample):")
    for phase in labels["phases"]:
        print(f"  {phase['start']:7.2f} - {phase['end']:7.2f}  {phase['name']}")
        for action in actions:
            if action["phase"] == phase["name"]:
                print(f"      {action['start']:7.2f} - {action['end']:7.2f}  "
                      f"{action['event']:<12} rep {action['rep']}")
        for miss in dropped:
            if miss["phase"] == phase["name"]:
                print(f"      {miss['cued_at']:7.2f}            NOT DONE     "
                      f"rep {miss['rep']}")
    for marker in labels["markers"]:
        print(f"      {marker['t']:7.2f}            marker       {marker['note']}")
    return labels_path


def dry_run(sound, speed, protocol=PROTOCOL):
    """Practise the cues with no board and no files."""
    session = Session()
    banner("DRY RUN -- nothing is being recorded")
    try:
        run_protocol(session, sound, speed, protocol)
    except Aborted:
        print("\n  stopped early")
    print(f"\nCued {len(session.actions)} actions across {len(session.phases)} "
          "phases. No files written.")
    return 0


def main():
    parser = build_parser("Record the labelled clench/blink protocol to CSV.")
    parser.add_argument("--label", default=None,
                        help="tag in the filenames (default: the profile name)")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="divide every duration, for a quick rehearsal (e.g. 6)")
    parser.add_argument("--no-sound", action="store_true", help="no beeps")
    parser.add_argument("--dry-run", action="store_true",
                        help="walk through the cues without touching the headband")
    parser.add_argument("--protocol", choices=["standard", "blink-comparison"],
                        default="standard", help="choose the actions to record")
    args = parser.parse_args()

    sound = not args.no_sound
    speed = max(args.speed, 1.0)
    protocol = BLINK_PROTOCOL if args.protocol == "blink-comparison" else PROTOCOL
    minutes, seconds = divmod(total_seconds(speed, protocol), 60)

    print(__doc__.split("Outputs")[0].rstrip())
    print(f"Protocol: {len(protocol)} phases, about {int(minutes)}m {int(seconds):02d}s"
          + (f" (sped up x{speed:g})" if speed != 1 else ""))
    if args.dry_run:
        return dry_run(sound, speed, protocol)

    RECORDINGS.mkdir(exist_ok=True)
    label = args.label or args.profile
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = f"_{label}" if label else ""

    board = get_board(args)
    print(f"\nBoard: {board_label(board)}   profile: {args.profile}")
    print("Wear the band the way you calibrated it, and do not adjust it now.")
    input("Press Enter when you are sitting comfortably and ready to start... ")

    if not prepare_or_explain(board):
        return 1

    aborted = False
    session = Session()
    try:
        enable_ppg(board)
        board.start_stream()
        # A few seconds of settling before the first label: the BLE stream often
        # stutters on its first packets.
        countdown(3.0, "starting in", session)
        try:
            run_protocol(session, sound, speed, protocol)
        except Aborted:
            aborted = True
            print("\n  Q pressed: stopping the protocol, keeping what was recorded")
        beep(sound, 1320, 200)
        print("\nDone. Stopping the stream...")
        board.stop_stream()
        written = write_outputs(board, session, stamp, tag, label, args.profile,
                                speed, aborted)
    except KeyboardInterrupt:
        print("\n  Ctrl+C -- this recording is lost; press Q instead to keep one.")
        return 1
    finally:
        board.release_session()

    if written is None:
        return 1
    print(f"\nSend the whole {stamp}{tag}_* set (CSV + labels + manifest). Then:")
    print("  python evaluate_detection.py --manifest "
          f"recordings/{stamp}{tag}_manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
