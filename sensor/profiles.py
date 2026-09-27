"""Read the same per-person jaw calibration files as the Muse debug bench."""
import json
import math
import re
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent.parent / 'test'


def load_profile(name, directory=PROFILE_DIR):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', name):
        raise ValueError('Profile must contain only letters, digits, hyphens or underscores')
    profile = json.loads((directory / f'calibration.{name}.json').read_text())
    rest, threshold = profile['emg_rest'], profile['emg_threshold']
    if not (math.isfinite(rest) and math.isfinite(threshold) and 0 <= rest < threshold):
        raise ValueError('Clench profile needs a finite threshold above its resting baseline')
    if profile.get('board') != 'MUSE_2_BOARD' or profile.get('fs') != 256:
        raise ValueError('Choose a Muse 2 profile at 256 Hz, not a synthetic profile')
    return profile
