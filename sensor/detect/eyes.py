"""DOUBLE_BLINK from MNE, run the way the Tkinter bench runs it (test/clench_detect.py detect_loop).

MNE's `find_eog_events` looks at the last 20 s of AF7/AF8 in a background thread (the scan must never
block clench polling), commits blink peaks half a second behind real time, and `BlinkGroups` pairs
two peaks within 700 ms into a DOUBLE_BLINK. A single blink sends nothing. MNE needs no per-person
eye threshold, so the profile's saved blink numbers are not used, exactly as in the bench.

MNE is slow to import and optional: if it is missing or crashes, blinks stop and clenches carry on.
"""
import math

from sensor.detect.mne_blinks import SCAN_SECONDS, WINDOW_SECONDS, BlinkGroups, MNEBlinkWorker

DOUBLE_BLINK_MS = 700
STALLED_S = 5.0  # no finished scan for this long = blinks are not being read


class MNEEyes:
    def __init__(self, fs, since, double_ms=DOUBLE_BLINK_MS, worker_factory=MNEBlinkWorker):
        self.fs = fs
        self.since = since
        self.worker = worker_factory(fs, since)
        self.groups = BlinkGroups(double_ms)
        self.next_scan = since
        self.through = since
        self.ready = False
        self.failed = None

    @property
    def window_samples(self):
        return round(WINDOW_SECONDS*self.fs)

    def due(self, now):
        return self.failed is None and now >= self.next_scan

    def submit(self, eyes, end_time, now):
        """Hand the worker the latest 2 x N AF7/AF8 window ending at `end_time` (epoch seconds)."""
        self.next_scan = now+SCAN_SECONDS
        if eyes is not None and math.isfinite(end_time):
            self.worker.submit(eyes, end_time)

    def poll(self, now):
        """Collect finished scans. Returns (commands, log lines)."""
        commands, notes = [], []
        for peaks, through, error in self.worker.drain():
            if error:
                self.failed = error
                self.groups.pending = None
                notes.append(f'MNE blinks stopped: {error}. Clenches still work.')
                continue
            self.through = through
            for name, _detail, _peak in self.groups.advance(peaks, through):
                if name == 'DOUBLE_BLINK':
                    # Stamped when sent, not at the peak: MNE commits a peak at least half a second
                    # late, and the Core refuses any gesture more than a second old.
                    commands.append(dict(type='DOUBLE_BLINK', t=now))
        # 5 s, not 2: MNE's first real scan pays its lazy imports and can take a couple of seconds,
        # which flipped the log to "paused" and back once at every connect.
        ready = self.failed is None and self.through > self.since and now-self.through < STALLED_S
        if ready and not self.ready:
            notes.append('MNE blinks ready: blink twice for DOUBLE_BLINK (back).')
        elif self.ready and not ready and self.failed is None:
            notes.append('MNE blinks paused: waiting for continuous AF7/AF8 data.')
        self.ready = ready
        return commands, notes

    def close(self):
        self.worker.close()
