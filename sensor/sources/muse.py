"""Headless BrainFlow acquisition. No Tkinter, MNE, or UI imports."""
import math
import time
from dataclasses import dataclass

import numpy as np
from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams, BrainFlowPresets

from sensor.detect.clench import envelope, peak_to_peak


# How noisy the forehead channels may be at rest before blinks are untrustworthy. A loose
# AF7/AF8 swings far more than a blink does, so pairing them would fire on contact noise.
FOREHEAD_NOISY_UV = 120.


@dataclass
class Sample:
    level: float
    channels: list[float]
    blocked: str | None = None
    # Forehead peak-to-peak, in uV. None when the contact is too poor to read blinks: the jaw
    # still works, so bad eye contact must never block a clench (or the help hold).
    blink: tuple[float, float] | None = None


class MuseSource:
    def __init__(self, name=None, motion_limit=30.):
        params = BrainFlowInputParams()
        if name:
            params.serial_number = name
        BoardShim.disable_board_logger()
        self.board = BoardShim(BoardIds.MUSE_2_BOARD, params)
        self.motion_limit = motion_limit  # degrees/s, not an EEG amplitude threshold

    def open(self):
        try:
            self.board.prepare_session()
            self.board.start_stream()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.board.is_prepared():
            try:
                self.board.stop_stream()
            finally:
                self.board.release_session()

    def read(self):
        data = self.board.get_current_board_data(256)
        if data.shape[1] < 256:
            return Sample(0., [], 'Waiting for EEG samples')
        stamps = data[BoardShim.get_timestamp_channel(self.board.board_id)]
        if not np.isfinite(stamps).all() or not 0 <= time.time()-stamps[-1] < 1:
            raise ConnectionError('EEG stream stopped')
        if np.max(np.abs(stamps-stamps[-1]-(np.arange(256)-255)/256)) > .1:
            return Sample(0., [], 'EEG samples interrupted; waiting for continuous data')
        rows = BoardShim.get_eeg_channels(self.board.board_id)[:4]
        raw = data[rows]
        for row, label in ((0, 'TP9'), (3, 'TP10')):
            values = raw[row]
            if (not np.isfinite(values).all() or np.std(values) < 1 or
                    max(np.mean(values == values.min()), np.mean(values == values.max())) > .2):
                return Sample(0., [], f'{label} poor contact — adjust band')
        # Suppress the entire gesture during motion and rearm after quiet; a
        # motion-induced threshold crossing cannot fire later on release.
        preset = BrainFlowPresets.AUXILIARY_PRESET
        imu = self.board.get_current_board_data(16, preset=preset)
        if imu.shape[1] < 8:
            return Sample(0., [], 'Waiting for motion sensor')
        it = imu[BoardShim.get_timestamp_channel(self.board.board_id, preset)]
        gyro = imu[BoardShim.get_gyro_channels(self.board.board_id, preset)]
        if not np.isfinite(gyro).all() or not 0 <= time.time()-it[-1] < 1:
            return Sample(0., [], 'Motion sensor unavailable')
        level = max(envelope(raw[0], 256), envelope(raw[3], 256))
        channels = [float(np.std(ch)) for ch in raw]
        if not np.isfinite(channels).all():
            channels = []
        blocked = 'Head moving — hold still to clench' if np.max(np.linalg.norm(gyro, axis=0)) > self.motion_limit else None
        # AF7 and AF8 sit above the eyes. Read them even while the jaw is blocked by motion:
        # the caller decides what a blocked sample means, and only it knows the profile.
        blink = None
        if all(np.isfinite(raw[row]).all() and np.ptp(raw[row]) < FOREHEAD_NOISY_UV*8 for row in (1, 2)):
            left, right = (peak_to_peak(raw[row], 256) for row in (1, 2))
            if math.isfinite(left) and math.isfinite(right):
                blink = (left, right)
        return Sample(level, channels, blocked, blink)


class DemoSource:
    """Explicit hardware-free waveform demo; never loads a real person's profile."""
    profile = dict(emg_rest=5., emg_threshold=20., emg_peak=60.,
                   blink_rest=10., blink_threshold=60., blink_enabled=True)

    def open(self):
        self.started = time.monotonic()

    def close(self):
        pass

    def read(self):
        t = time.monotonic()-self.started
        # Three brief clenches, then a long clench, then a double blink, quiet between each.
        phase = t % 30
        active = any(start <= phase < end for start, end in ((5,5.5),(10,10.5),(15,15.5),(22,25.5)))
        fs = 256
        sample_time = np.arange(fs)/fs
        raw = 8*np.sin(2*np.pi*35*sample_time)
        if active:
            raw += 75*np.sin(2*np.pi*75*sample_time)
        # Two swoops 400 ms apart pair into a DOUBLE_BLINK; both channels together, as a real blink is.
        blinking = any(start <= phase < start+.25 for start in (27., 27.4))
        blink = (120., 120.) if blinking else (4., 4.)
        return Sample(envelope(raw, fs), [float(np.std(raw)), 5., 5., float(np.std(raw))], None, blink)
