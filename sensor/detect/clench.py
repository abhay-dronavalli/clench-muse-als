"""Shared jaw filter and edge detector used by the service and debug bench."""
import numpy as np
from brainflow.data_filter import DataFilter, FilterTypes, NoiseTypes

EMG_BAND = (20.0, 110.0)
ENVELOPE_SECONDS = .20
EDGE_SECONDS = .02
RELEASE_FRACTION = .6

def _filtered(window, fs, band, notch):
    """Copy one channel, clean it, and band-limit it.

    Order matters. The mains hum sits at 50/60 Hz, which is INSIDE the 20-110 Hz
    EMG band, so it has to come out BEFORE the bandpass -- notching afterwards
    leaves the hum sitting in the number we measure. A venue has far more
    electrical hum than a bedroom, so this is not a theoretical concern.
    """
    # BrainFlow filters in place. ascontiguousarray can alias a float64 row,
    # causing the hold feature to read the already blink-filtered samples.
    signal = np.array(window, dtype=np.float64, order="C", copy=True)
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



class EdgeDetector:
    """Threshold crossing with hysteresis, a minimum duration and a refractory gap.

    Hysteresis (rise at threshold, fall at 60% of it) stops a signal hovering right
    at the line from machine-gunning events. The refractory gap stops the tail of
    one clench from being read as the start of the next.
    """

    def __init__(self, threshold, min_ms, refractory_ms, baseline=0.0):
        self.threshold = threshold
        self.release = baseline + (threshold - baseline) * RELEASE_FRACTION
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




# --- blinks: the second input, measured differently from the jaw ---------------
# A blink is one quick swoop on the forehead channels, not a sustained buzz, so RMS is
# the wrong tool and peak-to-peak is right (see the bench's notes in test/clench_detect.py).
BLINK_BAND = (1.0, 10.0)
BLINK_P2P_SECONDS = .30       # a blink swoop fits comfortably inside 300 ms
BLINK_COINCIDENCE_MS = 60     # both forehead channels must spike this close together
BLINK_REFRACTORY_MS = 200     # minimum gap between two counted blinks
DOUBLE_BLINK_MS = 700         # two blinks inside this gap mean "go back"


def peak_to_peak(window, fs, band=BLINK_BAND):
    """How big the biggest swing was in the recent window, in uV."""
    segment = _tail(_filtered(window, fs, band, notch=False), fs, BLINK_P2P_SECONDS)
    if segment.size == 0:
        return 0.0
    return float(np.max(segment) - np.min(segment))


class BlinkDetector:
    """One blink = both forehead channels spiking together.

    Requiring coincidence is what separates a blink from a single-channel artifact: a
    loose electrode or a chewing burst moves one side, a blink moves both. Ticks run at
    20 Hz, so the 60 ms window means "same tick, or one tick apart".
    """

    def __init__(self, threshold, coincidence_ms=BLINK_COINCIDENCE_MS,
                 refractory_ms=BLINK_REFRACTORY_MS, baseline=0.):
        self.left = EdgeDetector(threshold, 0, refractory_ms, baseline)
        self.right = EdgeDetector(threshold, 0, refractory_ms, baseline)
        self.coincidence_ms = coincidence_ms
        self.refractory_ms = refractory_ms
        self.rose_left = None
        self.rose_right = None
        self.last_blink_at = -1e9

    def reset(self):
        self.rose_left = self.rose_right = None

    def update(self, left, right, now):
        """Feed one tick. Returns the two channels' gap in ms when a blink fired."""
        edge_left = self.left.update(left, now)
        edge_right = self.right.update(right, now)
        if edge_left and edge_left[0] == 'rise':
            self.rose_left = now
        if edge_right and edge_right[0] == 'rise':
            self.rose_right = now
        # Forget a rise once it is too old to pair with: a channel stuck above threshold
        # must not sit there waiting to pair with an unrelated spike minutes later.
        window = self.coincidence_ms/1000.
        if self.rose_left is not None and now-self.rose_left > window:
            self.rose_left = None
        if self.rose_right is not None and now-self.rose_right > window:
            self.rose_right = None
        if self.rose_left is not None and self.rose_right is not None:
            gap_ms = abs(self.rose_left-self.rose_right)*1000
            if (now-self.last_blink_at)*1000 > self.refractory_ms:
                self.last_blink_at = now
                self.rose_left = self.rose_right = None
                return gap_ms
        return None
