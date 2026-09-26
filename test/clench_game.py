"""Step 6: a tiny game that proves clench-as-a-button actually works.

A cursor scans left to right across a row of cells. One cell is the target.
Clench your jaw when the cursor is on it. That is the whole game -- and it is
exactly the interaction a scanning communication board uses, so if this feels
good, the real board will too.

    python clench_game.py              # calibrate, then play
    python clench_game.py --load       # reuse calibration.json, straight to the game
    python clench_game.py --synthetic  # runs, but nothing can clench: every round times out

What it measures
----------------
Hits and misses tell you whether the threshold is right. Reaction time (from the
moment the cursor lands on the target to the moment CLENCH fires) tells you whether
the *scan speed* is right -- it includes your reaction plus the detector's own lag,
which is roughly one 200 ms envelope window. If your mean reaction is 500 ms, a
900 ms dwell per cell is comfortable; if it is 900 ms, slow the scan down.
"""

import random
import sys
import time

from brainflow.board_shim import BoardShim, BrainFlowPresets

from clench_detect import (
    load_calibration,
    CR,
    GestureRecognizer,
    WINDOW_SECONDS,
    calibrate,
    describe,
    meter,
    read_levels,
)
from config import (board_label, build_parser, eeg_channels_and_names, get_board,
                    prepare_or_explain)

import json

TICK_SECONDS = 0.05      # 20 Hz, same as the detector
FEEDBACK_SECONDS = 0.8   # how long HIT / MISS stays on screen between rounds


def render(cells, cursor, target, emg_level, threshold):
    """One line: the scanning row plus a live clench meter."""
    row = []
    for i in range(cells):
        if i == cursor:
            row.append("[*]" if i == target else "[ ]")
        else:
            row.append(" * " if i == target else " . ")
    return f"  {''.join(row)}   {meter(emg_level, threshold)} {emg_level:6.1f} uV"


def play(board, rows, fs, window_samples, calibration, args):
    """Run the rounds. Returns a list of (outcome, reaction_ms) per round."""
    # emit_start=True: act on the RISING edge. Waiting for the release adds the
    # whole length of your clench to the lag, which reads as being exactly one
    # cell late on every round.
    recognizer = GestureRecognizer(calibration["emg_threshold"],
                                   calibration["blink_threshold"],
                                   args.long_ms, args.double_ms,
                                   emit_start=True)
    threshold = calibration["emg_threshold"]
    results = []

    print("\n--- GAME ---")
    print(f"  {args.cells} cells, cursor moves every {args.scan_ms} ms, "
          f"{args.rounds} rounds.")
    print("  [*] means the cursor is ON the target -- clench there.")
    print(f"  A round times out after {args.max_sweeps} full sweeps.  Ctrl-C to quit.\n")
    time.sleep(1.5)

    for round_number in range(1, args.rounds + 1):
        target = random.randrange(args.cells)
        cursor = 0
        sweeps = 0
        entered_target_at = None    # when the cursor last landed on the target
        cell_started = time.monotonic()
        outcome, reaction_ms = None, None

        while outcome is None:
            now = time.monotonic()

            # --- advance the cursor on its own clock ---
            if (now - cell_started) * 1000 >= args.scan_ms:
                cursor += 1
                cell_started = now
                if cursor >= args.cells:
                    cursor = 0
                    sweeps += 1
                    if sweeps >= args.max_sweeps:
                        outcome, reaction_ms = "TIMEOUT", None
                        break
                entered_target_at = now if cursor == target else None

            # --- read the headband ---
            levels = read_levels(board, rows, fs, window_samples)
            if levels is None:
                time.sleep(TICK_SECONDS)
                continue
            emg_level, blink_level = levels

            for name, _detail in recognizer.update(emg_level, blink_level, now):
                if name != "CLENCH_START":
                    continue  # blinks, and the later release-edge CLENCH, are ignored
                if cursor == target:
                    outcome = "HIT"
                    if entered_target_at is not None:
                        reaction_ms = (now - entered_target_at) * 1000
                else:
                    outcome = "MISS"
                break

            print(CR + render(args.cells, cursor, target, emg_level, threshold),
                  end="", flush=True)
            time.sleep(TICK_SECONDS)

        # --- round over: show the result on its own line ---
        marks = {"HIT": "HIT ", "MISS": "MISS", "TIMEOUT": "----"}[outcome]
        detail = f"  reaction {reaction_ms:4.0f} ms" if reaction_ms is not None else ""
        if outcome == "MISS":
            detail = f"  (cursor was on cell {cursor + 1}, target was {target + 1})"
        if outcome == "TIMEOUT":
            detail = "  (no clench detected)"
        print(CR + f"  round {round_number:2d}/{args.rounds}   {marks}{detail}".ljust(120))
        results.append((outcome, reaction_ms))
        time.sleep(FEEDBACK_SECONDS)

    return results


def summarise(results, args):
    hits = [r for r in results if r[0] == "HIT"]
    misses = [r for r in results if r[0] == "MISS"]
    timeouts = [r for r in results if r[0] == "TIMEOUT"]
    reactions = [r[1] for r in hits if r[1] is not None]

    print("\n--- RESULTS ---")
    print(f"  hits      {len(hits):2d}/{len(results)}")
    print(f"  misses    {len(misses):2d}   (clenched on the wrong cell)")
    print(f"  timeouts  {len(timeouts):2d}   (no clench detected at all)")
    if reactions:
        mean = sum(reactions) / len(reactions)
        print(f"  reaction  mean {mean:.0f} ms, best {min(reactions):.0f} ms, "
              f"worst {max(reactions):.0f} ms")
        print()
        # The advice that actually matters: dwell time versus your reaction time.
        if mean > args.scan_ms * 0.8:
            print(f"  Your reaction is close to the {args.scan_ms} ms dwell -- "
                  f"try --scan-ms {int(args.scan_ms * 1.5)}")
        elif mean < args.scan_ms * 0.4:
            print(f"  You are well inside the {args.scan_ms} ms dwell -- "
                  f"try --scan-ms {int(args.scan_ms * 0.7)} to move faster")
        else:
            print(f"  {args.scan_ms} ms per cell suits you well.")
    if timeouts:
        print("\n  Timeouts mean the threshold is too high: clench harder, or "
              "recalibrate without --load.")
    if misses:
        print("  Misses are usually late clenches -- slow the scan down before "
              "blaming the detector.")


def main():
    parser = build_parser("Scan-and-clench game: prove clench works as a button.")
    parser.add_argument("--load", action="store_true",
                        help="reuse calibration.json instead of recalibrating")
    parser.add_argument("--no-clench-cal", action="store_true",
                        help="skip the active clench/blink calibration phase")
    parser.add_argument("--baseline-seconds", type=float, default=10.0,
                        help="length of the resting measurement (default 10)")
    parser.add_argument("--k", type=float, default=6.0,
                        help="sigma multiple for the fallback threshold (default 6)")
    parser.add_argument("--long-ms", type=int, default=1500,
                        help="hold this long for LONG_CLENCH (default 1500)")
    parser.add_argument("--double-ms", type=int, default=700,
                        help="blink pairing window (unused here, kept for the detector)")
    parser.add_argument("--rounds", type=int, default=10, help="rounds to play (default 10)")
    parser.add_argument("--cells", type=int, default=6, help="cells in the row (default 6)")
    parser.add_argument("--scan-ms", type=int, default=900,
                        help="how long the cursor sits on each cell (default 900)")
    parser.add_argument("--max-sweeps", type=int, default=3,
                        help="sweeps before a round times out (default 3)")
    args = parser.parse_args()

    board = get_board(args)
    fs = BoardShim.get_sampling_rate(board.board_id, BrainFlowPresets.DEFAULT_PRESET)
    channels, _names = eeg_channels_and_names(board.board_id)
    window_samples = int(fs * WINDOW_SECONDS)
    rows = {"emg": [channels[0], channels[3]], "blink": [channels[1], channels[2]]}

    print(f"{board_label(board)} @ {fs} Hz")
    if not prepare_or_explain(board):
        return 1
    try:
        board.start_stream()
        time.sleep(WINDOW_SECONDS + 0.3)

        calibration = load_calibration(board, args.profile) if args.load else None
        if calibration:
            print(f"Loaded calibration from {calibration['saved_at']}.")
        else:
            if args.load:
                print("No usable saved calibration -- calibrating now.")
            calibration = calibrate(board, rows, fs, window_samples, args)
        describe(calibration)

        results = play(board, rows, fs, window_samples, calibration, args)
        summarise(results, args)
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
