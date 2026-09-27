"""Exercise real MNE windows, event timing, worker failures and saved jaw settings."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import clench_detect as cd
import compare_detectors as c
from evaluate_hybrid import replay
from mne_blinks import BlinkGroups, MNEBlinkWorker, RollingMNEBlinks


@pytest.mark.parametrize('scale,noise,weak_eye', [(1, 2, 1), (.2, 2, 1), (1, 8, 1), (1, 2, .1)])
def test_real_rolling_mne_and_calibrated_jaw(scale, noise, weak_eye):
    case = c.synthetic_case('rolling', scale=scale, noise=noise, weak_eye=weak_eye)
    # Rest history precedes every prompted blink; don't score warm-up as misses.
    pad = np.random.default_rng(54).normal(0, noise*scale, (4, 10*256))
    case['raw'] = np.concatenate([pad, case['raw']], axis=1)
    case['actions'] = [dict(a, start=a['start']+10, end=a['end']+10) for a in case['actions']]
    events = replay(case['raw'], 256, case['profile'])
    score = c.assess(events, case['actions'], {'BLINK', 'CLENCH'}, 10)
    # Check recovery of prompted blinks. MNE can also label noise as peaks;
    # this test does not certify specificity on noisy signals.
    assert score['per_gesture']['BLINK']['hits'] == 4
    for action in case['actions']:
        if action['event'] == 'BLINK' and noise == 2:
            assert sum(e['event'] == 'BLINK' and action['start'] <= e['t'] <= action['end']
                       for e in events) == 1
    assert score['per_gesture']['CLENCH']['hits'] == 3
    original = c.current_events(case['raw'], 256, case['baseline'], case['profile'])
    assert [(e['t'],e['event']) for e in events if 'CLENCH' in e['event']] == [
        (e['t'],e['event']) for e in original if 'CLENCH' in e['event']]
    assert all(e['available_at'] >= e['t']+.5 for e in events if 'BLINK' in e['event'])


def test_groups_wait_for_processed_time_and_deduplicate():
    groups = BlinkGroups(700)
    assert groups.advance([10], 10.1) == []
    assert groups.advance([], 10.5) == []
    events = groups.advance([10, 10.6], 10.65)
    assert [(name, t) for name, _, t in events] == [('DOUBLE_BLINK', 10.6)]
    events = groups.advance([12, 14], 15)
    assert [(name, t) for name, _, t in events] == [('BLINK', 12), ('BLINK', 14)]
    assert groups.advance([14], 16) == []


def test_warmup_history_and_duplicate_window_do_not_emit():
    scanner = RollingMNEBlinks(256, since=0)
    t = np.arange(scanner.samples)/256
    eyes = np.tile(-100*np.exp(-.5*((t-5)/.045)**2), (2,1))
    peaks, through = scanner.scan(eyes, t[-1])
    assert peaks == []
    assert scanner.scan(eyes, t[-1]) == ([], through)
    with pytest.raises(ValueError, match='finite'):
        scanner.scan(eyes*np.nan, t[-1]+1)


def test_worker_error_is_reported_and_closes():
    def broken(*_):
        raise ImportError('MNE unavailable')
    worker = MNEBlinkWorker(256, 0, scanner_factory=broken)
    assert worker.stopped.wait(2)
    assert 'MNE unavailable' in worker.drain()[0][2]
    worker.close()
    assert not worker.thread.is_alive()


def test_worker_keeps_latest_window_without_blocking_jaw_thread():
    entered, release = threading.Event(), threading.Event()
    seen = []
    class Scanner:
        def __init__(self, *_): pass
        def scan(self, eyes, end):
            seen.append(end)
            entered.set()
            assert release.wait(2)
            return [], end
    worker = MNEBlinkWorker(256, 0, scanner_factory=Scanner)
    try:
        worker.submit(np.zeros((2,10)), 1)
        assert entered.wait(2)
        worker.submit(np.zeros((2,10)), 2)
        worker.submit(np.zeros((2,10)), 3)
        assert worker.input.qsize() == 1
        assert worker.input.queue[0][1] == 3
    finally:
        release.set()
        worker.close()


def test_mne_does_not_use_saved_eye_thresholds_or_disable_clench():
    profile = dict(emg_rest=6.37, emg_threshold=8.81, blink_threshold=999,
                   blink_enabled=False, hold_threshold=118, hold_rest=56)
    args = SimpleNamespace(long_ms=1500, double_ms=700, blink_detector='mne')
    rec = cd.recognizer_from_calibration(profile, args)
    assert rec.clench.threshold == 8.81
    assert rec.clench.release == pytest.approx(6.37+(8.81-6.37)*.6)
    assert not rec.blink_enabled
    assert rec.long_blink is None
    assert profile['hold_threshold'] == 118
    assert rec.update(cd.Levels(15, 2000, 2000, 500, 500), 1) == []
    assert rec.update(cd.Levels(15, 2000, 2000, 500, 500), 2.6)[0][0] == 'LONG_CLENCH'


def test_drill_scores_delayed_mne_blink_by_peak_time():
    from station_activities import DrillState, BLINK
    clock = SimpleNamespace(now=100.)
    drill = DrillState(inputs=(BLINK,), clock=lambda: clock.now)
    clock.now = 102
    drill.update_frame(0)
    clock.now = 107
    drill.update_frame(0)
    drill.on_gesture('BLINK', 'MNE', occurred_at=105.8)
    assert drill.rounds[0].outcome == 'hit'
    assert drill.PROMPT[BLINK] == 'BLINK ONCE'


def test_mne_calibration_collects_only_rest_and_jaw(monkeypatch, tmp_path):
    from test_detection import calibration_run, samples
    result, path, _ = calibration_run(monkeypatch, tmp_path,
        [samples(5, duration=10)] + [samples(30)]*3, blink_detector='mne')
    assert result['emg_threshold'] == pytest.approx(12.5)
    assert result['emg_trials'] == [30]*3
    assert result['hold_threshold'] is None
    assert result['blink_trials'] == []
    assert path.exists()


def test_live_loop_mne_failure_keeps_clenches_and_closes_worker(monkeypatch):
    import mne_blinks
    from test_detection import QuietUI, args
    clock = SimpleNamespace(now=100.)
    monkeypatch.setattr(cd.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(cd.time, 'sleep', lambda seconds: setattr(clock, 'now', clock.now+seconds))
    monkeypatch.setattr(cd.time, 'time', lambda: 1000.)
    ui = QuietUI()
    ticks = iter([5]*10+[30]*6+[5]*15)
    def read(*_):
        v = next(ticks, None)
        if v is None:
            ui.done = True
            return None
        return cd.Levels(v, 10, 10)
    monkeypatch.setattr(cd, 'read_levels', read)
    class BrokenWorker:
        def __init__(self, *_):
            self.stopped = threading.Event()
            self.stopped.set()
            self.reported = False
            self.closed = False
        def drain(self):
            if self.reported: return []
            self.reported = True
            return [([], 100., 'simulated missing MNE')]
        def close(self): self.closed = True
    worker = BrokenWorker()
    monkeypatch.setattr(mne_blinks, 'MNEBlinkWorker', lambda *_: worker)
    from brainflow.board_shim import BoardIds
    cd.detect_loop(SimpleNamespace(board_id=BoardIds.MUSE_2_BOARD), {}, 256, 256,
                   dict(emg_rest=5, emg_threshold=10, blink_threshold=40),
                   args(blink_detector='mne'), ui)
    assert ui.events == ['CLENCH']
    assert any('MNE blinks stopped' in message for message in ui.logs)
    assert worker.closed


def test_keyboard_flappy_needs_neither_mne_nor_headband(monkeypatch):
    import builtins
    import sys
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import clench_flappy as game
    real_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        assert name != 'mne', 'Keyboard path must not import MNE'
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    monkeypatch.setattr(sys, 'argv', ['clench_flappy.py', '--keyboard'])
    monkeypatch.setattr(game, 'get_board', lambda *_: pytest.fail('Keyboard opened headband'))
    monkeypatch.setattr(game, 'run', lambda *_: 0)
    try:
        assert game.main() == 0
    finally:
        plt.close('all')
