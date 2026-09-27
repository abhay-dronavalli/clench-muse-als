"""Headless BrainFlow acquisition. No Tkinter, MNE, or UI imports."""
import time
from dataclasses import dataclass

import numpy as np
from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams, BrainFlowPresets

from sensor.detect.clench import envelope


@dataclass
class Sample:
    level: float
    channels: list[float]
    blocked: str | None = None


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
        return Sample(level, channels, blocked)

    def eyes(self, samples):
        """The last `samples` of AF7/AF8 (2 x N raw uV) and the last sample's epoch time, for MNE.

        None while the buffer is still filling or when the window has a timestamp gap (the bench
        skips those windows too: MNE would read the jump as a blink).
        """
        data = self.board.get_current_board_data(samples)
        if data.shape[1] < samples:
            return None
        stamps = data[BoardShim.get_timestamp_channel(self.board.board_id)]
        if not np.isfinite(stamps).all():
            return None
        if np.max(np.abs(stamps-stamps[-1]-(np.arange(samples)-samples+1)/256)) > .1:
            return None
        rows = BoardShim.get_eeg_channels(self.board.board_id)[1:3]
        return data[rows], float(stamps[-1])


class DemoSource:
    """Explicit hardware-free waveform demo; never loads a real person's profile."""
    profile = dict(emg_rest=5., emg_threshold=20., emg_peak=60.)
    # Seconds into each 30 s cycle. Natural single blinks every ~4 s, as a real wearer blinks, then two
    # blinks 400 ms apart. MNE thresholds each 20 s window against itself, so a window with no real
    # blink in it turns noise into "blinks"; a wearer (and this demo) never produces one.
    BLINKS = (1.5, 5.5, 9.5, 13.5, 17.5, 21.5, 27., 27.4)

    def open(self):
        self.started = time.time()

    def close(self):
        pass

    def read(self):
        t = time.time()-self.started
        # Three brief clenches, then a long clench, then a double blink (see eyes), quiet between.
        phase = t % 30
        active = any(start <= phase < end for start, end in ((5,5.5),(10,10.5),(15,15.5),(22,25.5)))
        fs = 256
        sample_time = np.arange(fs)/fs
        raw = 8*np.sin(2*np.pi*35*sample_time)
        if active:
            raw += 75*np.sin(2*np.pi*75*sample_time)
        return Sample(envelope(raw, fs), [float(np.std(raw)), 5., 5., float(np.std(raw))])

    def eyes(self, samples, fs=256):
        """A continuous synthetic AF7/AF8 recording ending now: low forehead noise, plus a blink-shaped
        bump on both channels at each BLINKS time, so the real MNE path finds the double blink."""
        end = time.time()
        stamps = end-(np.arange(samples)[::-1])/fs
        t = stamps-self.started
        noise = 3*np.sin(2*np.pi*13.1*t)+2*np.sin(2*np.pi*21.7*t+1)+2*np.sin(2*np.pi*.9*t)
        bumps = np.zeros_like(t)
        cycle_start = np.floor(t/30)*30
        for at in self.BLINKS:
            bumps += 150*np.exp(-((t-cycle_start-at)/.07)**2)
        return np.vstack([noise+bumps, noise*.8+bumps]), float(end)
