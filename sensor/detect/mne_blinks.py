"""MNE blink peaks on overlapping windows of samples already received (shared by the Sensor
Service and the Tkinter bench in test/clench_detect.py).

MNE's offline filter needs a trailing guard. Only peaks older than that guard
are committed; overlapping windows never intentionally replay an old peak.
This is a delayed online adapter, not MNE's whole-recording accuracy benchmark.
"""
import queue
import threading

import numpy as np

# MNE's default 10 s FIR is rounded to 4096 samples at 256 Hz (16 s).
# Keep more history than the filter; shorter windows produce distortion warnings.
WINDOW_SECONDS = 20.0
GUARD_SECONDS = .5
SCAN_SECONDS = .25


class RollingMNEBlinks:
    def __init__(self, fs, since):
        import mne
        self.mne = mne
        self.fs = fs
        self.samples = round(WINDOW_SECONDS * fs)
        self.through = since
        self.last_peak = -float("inf")
        self.primed = False
        self.info = mne.create_info(["AF7", "AF8"], fs, "eeg")

    def scan(self, eyes, end_time):
        """Return (peak times, committed-through time), all on caller's clock.

        eyes is 2 x N raw microvolts. end_time is the last sample's timestamp.
        Call only with samples already received; future samples are never used.
        """
        eyes = np.asarray(eyes, dtype=float)
        if eyes.ndim != 2 or eyes.shape[0] != 2 or not np.isfinite(eyes).all():
            raise ValueError("MNE needs finite AF7/AF8 samples")
        if not np.isfinite(end_time):
            raise ValueError("MNE needs a finite sample timestamp")
        if eyes.shape[1] < self.samples or end_time-GUARD_SECONDS <= self.through:
            return [], self.through
        eyes = eyes[:, -self.samples:]
        start = end_time-(eyes.shape[1]-1)/self.fs
        if not self.primed:
            # Never emit the warm-up history when Listen or Drill starts.
            self.through = max(self.through, end_time-GUARD_SECONDS)
            self.primed = True
            return [], self.through
        raw = self.mne.io.RawArray(eyes*1e-6, self.info, verbose=False)
        events = self.mne.preprocessing.find_eog_events(
            raw, ch_name=["AF7", "AF8"], verbose=False)
        through = end_time-GUARD_SECONDS
        peaks = []
        for sample in events[:, 0]:
            peak = start+float(sample)/self.fs
            # The 150 ms duplicate guard handles small peak shifts as rolling
            # filtering changes. Faster events are not distinct deliberate blinks.
            if self.through < peak <= through and peak-self.last_peak >= .15:
                peaks.append(peak)
                self.last_peak = peak
        self.through = through
        return peaks, through


class BlinkGroups:
    """Group peak timestamps, waiting for the scanner's completed-time watermark."""
    def __init__(self, double_ms=700):
        self.gap = double_ms / 1000
        self.pending = None
        self.last_peak = -float('inf')

    def advance(self, peaks, through):
        events = []
        for peak in peaks:
            if peak <= self.last_peak:
                continue
            self.last_peak = peak
            if self.pending is not None:
                if peak-self.pending <= self.gap:
                    events.append(('DOUBLE_BLINK', 'MNE: two blink peaks', peak))
                    self.pending = None
                    continue
                events.append(('BLINK', 'MNE: blink peak', self.pending))
            self.pending = peak
        if self.pending is not None and through-self.pending > self.gap:
            events.append(('BLINK', 'MNE: blink peak', self.pending))
            self.pending = None
        return events


class MNEBlinkWorker:
    """A bounded latest-window queue: MNE work cannot block clench polling."""
    def __init__(self, fs, since, scanner_factory=RollingMNEBlinks):
        self.input = queue.Queue(maxsize=1)
        self.output = queue.Queue()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run,
            args=(scanner_factory, fs, since), daemon=True, name="mne-blinks")
        self.thread.start()

    def submit(self, eyes, end_time):
        if self.stopped.is_set():
            return
        item = (np.array(eyes, dtype=float, copy=True), end_time)
        try:
            self.input.put_nowait(item)
        except queue.Full:
            try:
                self.input.get_nowait()
            except queue.Empty:
                pass
            self.input.put_nowait(item)

    def _run(self, factory, fs, since):
        try:
            scanner = factory(fs, since)
            while not self.stopped.is_set():
                try:
                    eyes, end_time = self.input.get(timeout=.1)
                except queue.Empty:
                    continue
                peaks, through = scanner.scan(eyes, end_time)
                self.output.put((peaks, through, None))
        except Exception as exc:
            self.output.put(([], since, f"{type(exc).__name__}: {exc}"))
            self.stopped.set()

    def drain(self):
        batches = []
        while True:
            try:
                batches.append(self.output.get_nowait())
            except queue.Empty:
                return batches

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=1)
