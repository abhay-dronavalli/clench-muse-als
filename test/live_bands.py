"""Step 2: live band powers + mindfulness score.

Every 0.5 s, look at the most recent 2 s of EEG and print:
  - average power in delta / theta / alpha / beta / gamma across the electrodes
  - BrainFlow's mindfulness score (0..1) from those band features

    python live_bands.py --synthetic
    python live_bands.py                 # real Muse 2; Ctrl-C to stop

Demo: close your eyes for ~15 s and watch alpha climb.
"""

import sys
import time

import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowPresets
from brainflow.data_filter import DataFilter
from brainflow.ml_model import (
    BrainFlowClassifiers,
    BrainFlowMetrics,
    BrainFlowModelParams,
    MLModel,
)

from config import board_label, build_parser, eeg_channels_and_names, get_board

BAND_NAMES = ["delta", "theta", "alpha", "beta", "gamma"]
WINDOW_SECONDS = 2.0     # how much history each estimate uses
UPDATE_SECONDS = 0.5     # how often we print


def main() -> int:
    parser = build_parser("Print live EEG band powers and a mindfulness score.")
    parser.add_argument("--window", type=float, default=WINDOW_SECONDS,
                       help=f"analysis window in seconds (default {WINDOW_SECONDS})")
    parser.add_argument("--interval", type=float, default=UPDATE_SECONDS,
                       help=f"seconds between updates (default {UPDATE_SECONDS})")
    args = parser.parse_args()

    board = get_board(args)
    fs = BoardShim.get_sampling_rate(board.board_id, BrainFlowPresets.DEFAULT_PRESET)
    channels, names = eeg_channels_and_names(board.board_id)
    window_samples = int(fs * args.window)

    # BrainFlow ships a pre-trained mindfulness model; prepare() loads it once.
    model = MLModel(BrainFlowModelParams(
        BrainFlowMetrics.MINDFULNESS, BrainFlowClassifiers.DEFAULT_CLASSIFIER))

    print(f"{board_label(board)}: EEG {names} @ {fs} Hz, "
          f"{args.window:.0f} s window, update every {args.interval:.1f} s")
    board.prepare_session()
    try:
        model.prepare()
        board.start_stream()

        # Let the ring buffer fill before the first estimate, otherwise the FFT
        # gets a stub of data and the numbers jump around.
        print(f"Filling the {args.window:.0f} s buffer ...")
        time.sleep(args.window + 0.2)

        header = "  ".join(f"{b:>7}" for b in BAND_NAMES)
        print(f"\n{'time':>6}  {header}   mindful")
        print("-" * (8 + len(header) + 11))

        started = time.monotonic()
        while True:
            # get_current_board_data PEEKS at the newest N samples and leaves the
            # buffer alone -- exactly what a sliding window wants.
            data = board.get_current_board_data(window_samples,
                                               preset=BrainFlowPresets.DEFAULT_PRESET)
            if data.shape[1] < window_samples // 2:
                time.sleep(args.interval)
                continue

            # apply_filter=True lets BrainFlow detrend + bandpass before the FFT.
            # Returns (means, stdevs), each 5 long, in delta..gamma order.
            means, stdevs = DataFilter.get_avg_band_powers(data, channels, fs, True)

            # The mindfulness model wants the 10 band features concatenated.
            features = np.concatenate((means, stdevs))
            mindfulness = model.predict(features)
            # predict() returns an array for some metrics; normalise to a float.
            score = float(np.ravel(mindfulness)[0])

            elapsed = time.monotonic() - started
            row = "  ".join(f"{v:7.3f}" for v in means)
            print(f"{elapsed:6.1f}  {row}   {score:6.2f}   {bar(means)}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        # try/finally so Ctrl-C still frees the headband and the model.
        # Each step is guarded: if we failed early, some of these were never started.
        for cleanup in (board.stop_stream, board.release_session, model.release):
            try:
                cleanup()
            except Exception:
                pass
        print("Session released.")
    return 0


def bar(means: np.ndarray) -> str:
    """Tiny ASCII bar for whichever band is strongest -- easy to read at a glance."""
    strongest = int(np.argmax(means))
    return f"{BAND_NAMES[strongest]} up"


if __name__ == "__main__":
    sys.exit(main())
