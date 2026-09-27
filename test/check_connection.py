"""Step 1: is the headband actually talking to us?

Connects, streams for 5 seconds, then reports what arrived on each preset.
Run this before anything else -- if it fails, nothing downstream will work.

    python check_connection.py --synthetic      # no hardware needed
    python check_connection.py                 # real Muse 2
"""

import sys
import time

import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowPresets

from config import (
    available_presets,
    board_label,
    build_parser,
    eeg_channels_and_names,
    enable_ppg,
    get_board,
    preset_name,
    warn_about_platform,
)

TROUBLESHOOTING = """
TROUBLESHOOTING
  1. Power-cycle the Muse: hold the button until it turns off, wait 3 s, turn it on.
     A slow-breathing white LED means "awake and advertising"; solid means connected.
  2. Close every other app that could hold it: the Muse phone app, muselsl,
     Petal / Mind Monitor, another copy of this script, an old Python shell.
  3. Name the device explicitly:  python check_connection.py --name Muse-XXXX
     (the XXXX is printed inside the battery compartment / shown by a BLE scanner)
  4. Check Bluetooth permissions and radio:
     - Windows: Bluetooth on, build 19041+.
     - macOS:   System Settings > Privacy & Security > Bluetooth > allow your terminal.
     - Linux:   libdbus-1-dev installed, BrainFlow built with BLE.
  5. Sit within a metre or two of the laptop for the first connection.
  6. Still stuck? Prove the code is fine with:  python check_connection.py --synthetic
     and re-run the real test with --debug to see the native BLE log.
"""

STREAM_SECONDS = 5


def main() -> int:
    parser = build_parser("Connect to the board, stream for 5 s, report what arrived.")
    parser.add_argument("--seconds", type=float, default=STREAM_SECONDS,
                       help=f"how long to stream (default {STREAM_SECONDS})")
    args = parser.parse_args()

    warn_about_platform()
    board = get_board(args)
    print(f"Connecting to {board_label(board)} ...")

    try:
        board.prepare_session()
    except Exception as exc:
        print(f"\nFAIL: could not prepare the session.\n  {type(exc).__name__}: {exc}")
        print(TROUBLESHOOTING)
        return 1

    try:
        # PPG lives on the ancillary preset and is off until we ask for it.
        enable_ppg(board)

        board.start_stream()
        print(f"Streaming for {args.seconds:.0f} s -- sit still...")
        time.sleep(args.seconds)
        board.stop_stream()

        presets = available_presets(board.board_id)
        # One get_board_data() per preset; each empties that preset's ring buffer.
        collected = {p: board.get_board_data(preset=p) for p in presets}

        print()
        ok = report(board, collected)
    finally:
        # Always hand the Bluetooth connection back, even on Ctrl-C or a crash.
        board.release_session()
        print("\nSession released.")

    if ok:
        print("\nPASS: data is flowing. Next:  python live_bands.py"
              + (" --synthetic" if args.synthetic else ""))
        return 0
    print("\nFAIL: connected, but the data looks wrong (see above).")
    print(TROUBLESHOOTING)
    return 1


def report(board: BoardShim, collected: dict) -> bool:
    """Print per-preset sample counts and per-EEG-channel stats. True if it looks sane."""
    board_id = board.board_id
    ok = True

    for preset, data in collected.items():
        fs = BoardShim.get_sampling_rate(board_id, preset)
        n_samples = data.shape[1] if data.size else 0
        print(f"{preset_name(preset):<18} {n_samples:>6} samples  "
              f"({data.shape[0]} rows, nominal {fs} Hz)")
        if preset == BrainFlowPresets.ANCILLARY_PRESET and n_samples == 0:
            print("                   (no PPG -- fine on synthetic, check p50 on a real Muse)")
        elif n_samples == 0:
            ok = False
            print("                   !! nothing arrived on this preset")

    eeg = collected.get(BrainFlowPresets.DEFAULT_PRESET)
    if eeg is None or not eeg.size:
        print("\n!! no EEG at all -- the headband is not sending.")
        return False

    channels, names = eeg_channels_and_names(board_id)
    fs = BoardShim.get_sampling_rate(board_id, BrainFlowPresets.DEFAULT_PRESET)
    print(f"\nEEG @ {fs} Hz, {eeg.shape[1]} samples "
          f"({eeg.shape[1] / fs:.1f} s of data)\n")
    print(f"  {'channel':<8} {'row':>3}  {'mean (uV)':>12} {'std (uV)':>11}   fit")
    for row, name in zip(channels, names):
        signal = eeg[row]
        mean, std = float(np.mean(signal)), float(np.std(signal))
        # Rough rule of thumb for dry electrodes: a few uV is suspiciously flat,
        # hundreds of uV is a loose or dry contact picking up movement/mains.
        if std < 1.0:
            fit = "FLAT?"
        elif std > 200.0:
            fit = "NOISY?"
        else:
            fit = "ok"
        print(f"  {name:<8} {row:>3}  {mean:>12.2f} {std:>11.2f}   {fit}")

    # Sanity check against the wall clock: a healthy link gives ~fs samples/second.
    if eeg.shape[1] < fs * 1.0:
        print("\n!! far fewer EEG samples than expected -- flaky link.")
        ok = False

    print("\n  FLAT?  = electrode not making contact (wet the forehead, move hair)")
    print("  NOISY? = loose electrode or mains pickup (sit still, unplug the charger)")
    return ok


if __name__ == "__main__":
    sys.exit(main())
