"""Reference values for the tablet's Kotlin clench filter (com.clench.eyetrack.muse.EmgFilter).

The tablet has no BrainFlow, so EmgFilter re-creates sensor/detect/clench.py's envelope() with
fixed second-order sections. This script checks those sections against BrainFlow, prints them for
EmgFilter.kt, and writes app/src/test/resources/muse/emg_golden.json: windows fed through the real
sensor.detect.clench (BrainFlow) that EmgFilterTest must reproduce.

Run from the repository root:
    $env:PYTHONPATH = '.'; uv run --extra sensor python kushagra/tablet/tools/muse_golden.py
"""
import json
from pathlib import Path

import numpy as np
from scipy import signal as ss

from sensor.detect.clench import EMG_BAND, _filtered, envelope

FS = 256
OUT = Path(__file__).resolve().parent.parent / 'app/src/test/resources/muse/emg_golden.json'

# BrainFlow's FIFTY_AND_SIXTY: a zero-phase 48-52 Hz band-stop, then a ONE-pass 58-62 Hz band-stop.
# Its zero-phase pass runs the reversed signal through the same filter WITHOUT resetting its state.
NOTCH50 = ss.butter(4, [48, 52], 'bandstop', fs=FS, output='sos')
NOTCH60 = ss.butter(4, [58, 62], 'bandstop', fs=FS, output='sos')
BAND = ss.butter(4, list(EMG_BAND), 'bandpass', fs=FS, output='sos')


def zero_phase(sos, x):
    y, zi = ss.sosfilt(sos, x, zi=np.zeros((sos.shape[0], 2)))
    return ss.sosfilt(sos, y[::-1], zi=zi)[0][::-1]


def filtered(x):
    return zero_phase(BAND, ss.sosfilt(NOTCH60, zero_phase(NOTCH50, x)))


def windows():
    rng = np.random.default_rng(7)
    t = np.arange(FS)/FS
    out = []
    for k in range(24):
        rest = rng.normal(0, rng.uniform(1, 8), FS)
        hum = rng.uniform(0, 40)*np.sin(2*np.pi*(50 if k % 2 else 60)*t+rng.uniform(0, 6))
        offset = rng.uniform(-900, 900)
        x = rest+hum+offset
        if k % 3 == 0:  # a clench burst over the latest part of the window
            start = rng.integers(0, FS-40)
            x[start:] += rng.uniform(20, 120)*np.sin(2*np.pi*rng.uniform(40, 100)*t[start:])*rng.uniform(.5, 1.5, FS-start)
        if k % 5 == 0:  # a blink-shaped slow bump
            x += 150*np.exp(-((t-rng.uniform(.2, .8))/.07)**2)
        out.append(x)
    # DemoSource's rest and clench windows (sensor/sources/muse.py)
    out.append(8*np.sin(2*np.pi*35*t))
    out.append(8*np.sin(2*np.pi*35*t)+75*np.sin(2*np.pi*75*t))
    return out


def main():
    worst = 0.
    cases = []
    for x in windows():
        reference = _filtered(x, FS, EMG_BAND, notch=True)
        worst = max(worst, float(np.max(np.abs(filtered(x)-reference))))
        cases.append(dict(x=[float(v) for v in x], envelope=envelope(x, FS), tail=[float(v) for v in reference[-8:]]))
    print(f'max difference from BrainFlow over {len(cases)} windows: {worst:.2e} uV')
    assert worst < 1e-6
    for name, sos in (('NOTCH50', NOTCH50), ('NOTCH60', NOTCH60), ('BAND', BAND)):
        print(f'private val {name} = arrayOf(')
        for b0, b1, b2, _a0, a1, a2 in sos:
            print(f'    doubleArrayOf({float(b0)!r}, {float(b1)!r}, {float(b2)!r}, {float(a1)!r}, {float(a2)!r}),')
        print(')')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dict(fs=FS, cases=cases)))
    print(f'wrote {OUT}')


if __name__ == '__main__':
    main()
