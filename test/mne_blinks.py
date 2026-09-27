"""The bench's MNE blink detector now lives in sensor/detect/mne_blinks.py, shared with the Sensor
Service, so the app and the bench detect blinks the same way. Re-exported here for the bench."""
from sensor.detect.mne_blinks import (  # noqa: F401
    GUARD_SECONDS, SCAN_SECONDS, WINDOW_SECONDS, BlinkGroups, MNEBlinkWorker, RollingMNEBlinks)
