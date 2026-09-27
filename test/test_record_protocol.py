import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
from brainflow.board_shim import BoardShim, BrainFlowPresets, BrainFlowInputParams
from brainflow.data_filter import DataFilter

import record_protocol as r


def test_focused_cues_keep_gentle_and_firm_labels_and_dropped_reps(monkeypatch):
    now = [100.]
    monkeypatch.setattr(r.time, "time", lambda: now[0])
    def countdown(seconds, label, session, bar=False):
        now[0] += seconds
        return ["x"] if label == "HOLD" and not any(not a["happened"] for a in session.actions) else []
    monkeypatch.setattr(r, "countdown", countdown)
    session = r.Session()
    r.run_protocol(session, False, 10, r.BLINK_PROTOCOL)
    assert len(session.actions) == 18
    assert len(session.phases) == 6
    assert session.actions[0]["happened"] is False
    assert sum(not a["happened"] for a in session.actions) == 1
    assert {a["phase"] for a in session.actions if a["event"] == "BLINK"} == {"gentle blinks", "firm blinks"}
    assert now[0]-100 == pytest.approx(r.total_seconds(10, r.BLINK_PROTOCOL))
    assert session.baseline[0] < session.baseline[1]


def test_recordings_keep_their_own_calibration_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "HERE", tmp_path)
    monkeypatch.setattr(r, "RECORDINGS", tmp_path)
    monkeypatch.setattr(r, "available_presets", lambda _: [BrainFlowPresets.DEFAULT_PRESET])
    monkeypatch.setattr(r, "board_label", lambda _: "SYNTHETIC_BOARD")
    board_id = -1
    data = np.zeros((BoardShim.get_num_rows(board_id), 500))
    data[BoardShim.get_timestamp_channel(board_id)] = 100 + np.arange(500)/250
    board = SimpleNamespace(board_id=board_id, get_board_data=lambda **_: data.copy())
    session = r.Session()
    session.baseline = (100.1, 100.5)
    session.action(r.BLINK_PROTOCOL[1], 1, 101, .25)
    source = tmp_path / "calibration.person.synthetic.json"
    source.write_text('{"emg_threshold": 5}')
    r.write_outputs(board, session, "first", "_person", "person", "person", 1, False)
    source.write_text('{"emg_threshold": 20}')
    r.write_outputs(board, session, "second", "_person", "person", "person", 1, False)
    first = json.loads((tmp_path / "first_person_manifest.json").read_text())[0]
    second = json.loads((tmp_path / "second_person_manifest.json").read_text())[0]
    assert first["profile"] != second["profile"]
    assert json.loads((tmp_path / first["profile"]).read_text())["emg_threshold"] == 5
    assert first["actions"][0]["phase"] == "gentle blinks"


def test_progress_is_saved_before_run_finishes(tmp_path, monkeypatch):
    progress = tmp_path / "progress.json"
    session = r.Session(progress)
    monkeypatch.setattr(r.time, "time", lambda: 123.)
    session.action(r.BLINK_PROTOCOL[1], 1, 122, .25)
    assert json.loads(progress.read_text())["actions"][0]["happened"] is True
    session.actions[-1]["happened"] = False
    session.checkpoint()
    session.mark("band slip")
    saved = json.loads(progress.read_text())
    assert saved["actions"][0]["happened"] is False
    assert saved["markers"][0]["t_unix"] == 123.
    assert saved["timebase"] == "unix_seconds"


def test_calibration_frozen_at_start_not_replaced_at_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "HERE", tmp_path)
    monkeypatch.setattr(r, "RECORDINGS", tmp_path)
    board = SimpleNamespace(board_id=-1)
    source = tmp_path / "calibration.person.synthetic.json"
    source.write_text('{"emg_threshold": 5}')
    path = r.snapshot_calibration(board, "run", "", "person")
    source.write_text('{"emg_threshold": 99}')
    r.snapshot_calibration(board, "run", "", "person")
    assert json.loads(path.read_text())["emg_threshold"] == 5


def test_native_backup_grows_during_acquisition_and_matches_raw_data(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "RECORDINGS", tmp_path)
    BoardShim.disable_board_logger()
    board = BoardShim(-1, BrainFlowInputParams())
    board.prepare_session()
    try:
        paths = r.start_waveform_backups(board, "live", "")
        default = next(path for path in paths if path.name.endswith("_default.csv"))
        board.start_stream()
        deadline = time.monotonic() + 3
        while default.stat().st_size == 0 and time.monotonic() < deadline:
            time.sleep(.05)
        first_size = default.stat().st_size
        assert first_size > 0, "No raw data reached disk during acquisition"
        time.sleep(.35)
        assert default.stat().st_size > first_size
        board.stop_stream()
        raw = board.get_board_data()
    finally:
        board.release_session()
    saved = DataFilter.read_file(str(default))
    assert saved.shape == raw.shape
    np.testing.assert_allclose(saved, raw, atol=1e-5, rtol=0)
    # A retry with an identical filename must preserve the previous waveforms.
    with pytest.raises(FileExistsError, match="overwrite"):
        r.start_waveform_backups(board, "live", "")
