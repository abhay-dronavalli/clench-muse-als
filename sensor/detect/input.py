"""Map the shared calibrated detectors to application commands: jaw picks, two blinks go back."""
import math

from sensor.detect.clench import DOUBLE_BLINK_MS, BlinkDetector, EdgeDetector


class ClenchInput:
    def __init__(self, profile, long_ms=2500, blink=None):
        self.profile = profile
        self.long_ms = long_ms
        # None = follow the profile's own eye calibration. The bench writes blink_enabled=False when
        # the calibration could not separate a blink from rest, and a guessed blink threshold fires
        # on every forehead twitch, so a missing or failed calibration means no blinks at all.
        self.blink_wanted = profile.get('blink_enabled', True) if blink is None else blink
        self.gate = None  # (enabled, clear) last tick; any change re-arms the detector
        self.reset()

    @property
    def blink_enabled(self):
        return bool(self.blink_wanted) and self.blink_threshold is not None

    @property
    def blink_threshold(self):
        threshold = self.profile.get('blink_threshold')
        rest = self.profile.get('blink_rest')
        if threshold is None or rest is None:
            return None
        if not (math.isfinite(threshold) and math.isfinite(rest) and 0 <= rest < threshold):
            return None
        return threshold

    def reset(self):
        self.edge = EdgeDetector(self.profile['emg_threshold'], 80, 250, self.profile['emg_rest'])
        self.armed = False
        self.quiet_since = None
        self.long_fired = False
        threshold = self.blink_threshold
        self.blinks = (BlinkDetector(threshold, baseline=self.profile['blink_rest'])
                       if self.blink_enabled and threshold is not None else None)
        self.pending_blink = None

    def update(self, level, now, *, enabled, blocked=None, blink=None):
        # Detection keeps running while paused or blocked, and the Core refuses (and logs) what it
        # sends then: otherwise the input log stays empty and "the detector never fired" looks the
        # same as "the switch was off". Any change of the pause or block state resets and re-arms,
        # so a clench held across Enable, or a crossing during head motion, can never fire.
        gate = (bool(enabled), not blocked)
        if gate != self.gate:
            self.gate = gate
            self.reset()
        if not math.isfinite(level):
            self.reset()
            return []
        # Enabling or reconnecting mid-clench cannot select/confirm. Require a
        # released jaw for 600 ms before accepting a fresh gesture.
        if not self.armed:
            if level < self.edge.release:
                if self.quiet_since is None:
                    self.quiet_since = now
                self.armed = now-self.quiet_since >= .6
            else:
                self.quiet_since = None
            return []
        commands = self._jaw(level, now)
        # A clench can pull the brow, so a jaw command wins the tick: never send both.
        return commands or self._eyes(blink, now)

    def _jaw(self, level, now):
        edge = self.edge.update(level, now)
        if edge and edge[0] == 'rise':
            self.long_fired = False
        if edge and edge[0] == 'fall' and not self.long_fired:
            span = max(self.profile.get('emg_peak') or self.edge.threshold, self.edge.threshold)-self.profile['emg_rest']
            strength = min(1., max(0., (edge[2]-self.profile['emg_rest'])/span))
            return [dict(type='CLENCH', t=now, strength=strength)]
        if self.edge.active and not self.long_fired and self.edge.held_ms(now) >= self.long_ms:
            self.long_fired = True
            return [dict(type='LONG_CLENCH', t=now, duration=self.edge.held_ms(now)/1000)]
        return []

    def _eyes(self, blink, now):
        """Two blinks inside DOUBLE_BLINK_MS mean "go back". A single blink sends nothing."""
        if self.blinks is None:
            return []
        if blink is None:  # forehead contact too poor to read; the jaw is unaffected
            self.blinks.reset()
            self.pending_blink = None
            return []
        left, right = blink
        if not (math.isfinite(left) and math.isfinite(right)):
            return []
        if self.blinks.update(left, right, now) is None:
            if self.pending_blink is not None and (now-self.pending_blink)*1000 > DOUBLE_BLINK_MS:
                self.pending_blink = None  # the second blink never came
            return []
        if self.pending_blink is not None and (now-self.pending_blink)*1000 <= DOUBLE_BLINK_MS:
            self.pending_blink = None
            return [dict(type='DOUBLE_BLINK', t=now)]
        self.pending_blink = now
        return []
