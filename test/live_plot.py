"""Live matplotlib scope for the four EEG channels.

Bandpassed 1-40 Hz with the mains hum (50 and 60 Hz) notched out, so blinks and
jaw clenches show up as obvious spikes instead of being buried in noise.

    python live_plot.py --synthetic
    python live_plot.py                       # real Muse 2
    python live_plot.py --save scope.png      # headless: grab one frame and exit

Close the window (or Ctrl-C) to stop.
"""

import sys
import time

import matplotlib
import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowPresets
from brainflow.data_filter import DataFilter, FilterTypes, NoiseTypes

from config import board_label, build_parser, eeg_channels_and_names, get_board

WINDOW_SECONDS = 5.0
REFRESH_SECONDS = 0.05


def main() -> int:
    parser = build_parser("Live plot of the four EEG channels.")
    parser.add_argument("--window", type=float, default=WINDOW_SECONDS,
                       help=f"seconds of history on screen (default {WINDOW_SECONDS})")
    parser.add_argument("--save", default=None, metavar="PNG",
                       help="save a single frame to this path and exit (no window)")
    args = parser.parse_args()

    if args.save:
        # Agg draws to a file without needing a display -- handy over SSH / in CI.
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # imported after use() so the backend sticks

    board = get_board(args)
    fs = BoardShim.get_sampling_rate(board.board_id, BrainFlowPresets.DEFAULT_PRESET)
    channels, names = eeg_channels_and_names(board.board_id)
    window_samples = int(fs * args.window)
    t_axis = np.arange(window_samples) / fs - args.window  # -5 s .. 0 s (now)

    fig, axes = plt.subplots(len(channels), 1, sharex=True, figsize=(10, 7))
    fig.suptitle(f"{board_label(board)} EEG -- 1-40 Hz bandpass, 50/60 Hz notch")
    lines = []
    for ax, name in zip(axes, names):
        (line,) = ax.plot(t_axis, np.zeros(window_samples), linewidth=0.8)
        ax.set_ylabel(f"{name}\n(uV)")
        ax.grid(alpha=0.3)
        lines.append(line)
    axes[-1].set_xlabel("seconds (0 = now)")
    fig.tight_layout()

    board.prepare_session()
    try:
        board.start_stream()
        print(f"Filling the {args.window:.0f} s buffer ...")
        time.sleep(min(args.window, 2.0) + 0.2)

        if args.save:
            draw_frame(board, channels, lines, axes, fs, window_samples)
            fig.savefig(args.save, dpi=110)
            print(f"Saved one frame to {args.save}")
            return 0

        plt.ion()
        plt.show(block=False)
        print("Plotting. Blink hard or clench your jaw to see the spikes. Ctrl-C to stop.")
        while plt.fignum_exists(fig.number):
            draw_frame(board, channels, lines, axes, fs, window_samples)
            # pause() both redraws and services the GUI event loop.
            plt.pause(REFRESH_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        for cleanup in (board.stop_stream, board.release_session):
            try:
                cleanup()
            except Exception:
                pass
        print("Session released.")
    return 0


def draw_frame(board, channels, lines, axes, fs, window_samples) -> None:
    """Pull the newest window, filter each channel in place, update the lines."""
    data = board.get_current_board_data(window_samples,
                                       preset=BrainFlowPresets.DEFAULT_PRESET)
    if data.shape[1] < 16:
        return

    for row, line, ax in zip(channels, lines, axes):
        # BrainFlow filters modify the array in place, so copy first: the raw
        # buffer is shared with the other channels' views.
        signal = np.ascontiguousarray(data[row], dtype=np.float64)
        DataFilter.perform_bandpass(signal, fs, 1.0, 40.0, 4,
                                   FilterTypes.BUTTERWORTH_ZERO_PHASE, 0.0)
        # One call kills both 50 Hz and 60 Hz mains hum, whichever country we're in.
        DataFilter.remove_environmental_noise(signal, fs, NoiseTypes.FIFTY_AND_SIXTY)

        # Right-align: the newest sample sits at x = 0 even while the buffer fills.
        padded = np.full(window_samples, np.nan)
        padded[window_samples - signal.size:] = signal
        line.set_ydata(padded)

        finite = signal[np.isfinite(signal)]
        if finite.size:
            span = max(float(np.max(np.abs(finite))) * 1.2, 5.0)
            ax.set_ylim(-span, span)


if __name__ == "__main__":
    sys.exit(main())
