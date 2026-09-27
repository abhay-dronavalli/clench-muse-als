"""Replay the rolling MNE + calibrated jaw path, without future recording samples."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import clench_detect as cd
from compare_detectors import assess, load_manifest, measured_levels
from mne_blinks import BlinkGroups, RollingMNEBlinks, SCAN_SECONDS

CAPABILITIES = {'CLENCH', 'LONG_CLENCH', 'BLINK', 'DOUBLE_BLINK'}


def replay(raw, fs, profile):
    args = SimpleNamespace(long_ms=1500, double_ms=700, long_blink_ms=400,
                           blink_detector='mne')
    recognizer = cd.recognizer_from_calibration(profile, args)
    scanner = RollingMNEBlinks(fs, since=0)
    groups = BlinkGroups(args.double_ms)
    events, next_scan = [], 0
    for t, levels in measured_levels(raw, fs):
        events.extend(dict(t=t, event=name, available_at=t)
                      for name, _ in recognizer.update(levels, t))
        if t + 1e-9 >= next_scan:
            end = round(t*fs)
            peaks, through = scanner.scan(raw[1:3, max(0, end-scanner.samples):end], (end-1)/fs)
            events.extend(dict(t=peak, event=name, available_at=t)
                          for name, _, peak in groups.advance(peaks, through))
            next_scan = t+SCAN_SECONDS
    return sorted(events, key=lambda e: e['t'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--profile', type=Path, help='Explicit profile override; original manifest is preserved')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reports = []
    for case in load_manifest(args.manifest):
        profile = json.loads(args.profile.read_text()) if args.profile else case['profile']
        if profile is None or profile.get('fs') != case['fs']:
            raise ValueError('Need a matching clench profile; supply --profile')
        events = replay(case['raw'], case['fs'], profile)
        report = dict(recording=case['name'], profile=profile,
                      **assess(events, case['actions'], CAPABILITIES, case['score_start']))
        reports.append(report)
        print(case['name'], report['per_gesture'])
    args.output.write_text(json.dumps(dict(
        note='Rolling windows use only received samples. available_at excludes worker/computation delay. '
             'Prompt-window matches are not verified human accuracy; unmatched blinks may be natural.',
        cases=reports), indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
