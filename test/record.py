"""Step 3: record a session to CSV.

Saves one CSV per preset (EEG, IMU, PPG) into recordings/, named with a
timestamp so runs never overwrite each other.

    python record.py --synthetic --seconds 5
    python record.py --seconds 60 --label eyes-closed

The CSVs are raw BrainFlow matrices written with DataFilter.write_file:
one ROW per channel is NOT the layout on disk -- write_file transposes, so each
line of the file is one sample and each column is a channel. Read them back with
DataFilter.read_file(path), which returns the original channels x samples array.
"""

import datetime as dt
import pathlib
import sys
import time

from brainflow.board_shim import BoardShim
from brainflow.data_filter import DataFilter

from config import (
    available_presets,
    board_label,
    build_parser,
    eeg_channels_and_names,
    enable_ppg,
    get_board,
    preset_name,
)

RECORDINGS = pathlib.Path(__file__).parent / "recordings"


def main() -> int:
    parser = build_parser("Record N seconds from the board to CSV files.")
    parser.add_argument("--seconds", type=float, default=60.0,
                       help="how long to record (default 60)")
    parser.add_argument("--label", default=None,
                       help="optional tag in the filename, e.g. --label jaw-clench")
    args = parser.parse_args()

    RECORDINGS.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = f"_{args.label}" if args.label else ""

    board = get_board(args)
    print(f"Recording {args.seconds:.0f} s from {board_label(board)} ...")
    board.prepare_session()

    written: list[pathlib.Path] = []
    try:
        enable_ppg(board)
        board.start_stream()

        # Simple countdown so you know how long to hold still / keep blinking.
        started = time.monotonic()
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= args.seconds:
                break
            print(f"\r  {elapsed:5.1f} / {args.seconds:.0f} s", end="", flush=True)
            time.sleep(min(0.5, args.seconds - elapsed))
        print(f"\r  {args.seconds:5.1f} / {args.seconds:.0f} s")

        board.stop_stream()

        for preset in available_presets(board.board_id):
            data = board.get_board_data(preset=preset)
            name = preset_name(preset).replace("_PRESET", "").lower()  # default / auxiliary / ancillary
            path = RECORDINGS / f"{stamp}{tag}_{name}.csv"
            if not data.size:
                print(f"  {preset_name(preset):<18} no data, skipped")
                continue
            # 'w' truncates; use 'a' to append to an existing file.
            DataFilter.write_file(data, str(path), "w")
            fs = BoardShim.get_sampling_rate(board.board_id, preset)
            print(f"  {preset_name(preset):<18} {data.shape[1]:>6} samples @ {fs:>3} Hz "
                  f"-> {path.name}")
            written.append(path)
    except KeyboardInterrupt:
        print("\n  interrupted -- stopping early")
    finally:
        board.release_session()

    if not written:
        print("\nNothing was written. Run check_connection.py first.")
        return 1

    channels, names = eeg_channels_and_names(board.board_id)
    print(f"\nEEG rows inside the *_default.csv columns: {channels} -> {names}")
    print(f"Saved {len(written)} file(s) in {RECORDINGS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
