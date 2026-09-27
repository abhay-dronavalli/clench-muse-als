"""Map the calibrated jaw detector to CLENCH / LONG_CLENCH. Blinks come from MNE (eyes.py)."""
import math

from sensor.detect.clench import EdgeDetector


class ClenchInput:
    def __init__(self, profile, long_ms=2500):
        self.profile = profile
        self.long_ms = long_ms
        self.gate = None  # (enabled, clear) last tick; any change re-arms the detector
        self.reset()

    def reset(self):
        self.edge = EdgeDetector(self.profile['emg_threshold'], 80, 250, self.profile['emg_rest'])
        self.armed = False
        self.quiet_since = None
        self.long_fired = False

    def update(self, level, now, *, enabled, blocked=None):
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
