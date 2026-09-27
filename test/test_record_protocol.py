import json
from types import SimpleNamespace

import numpy as np
import pytest
from brainflow.board_shim import BoardShim, BrainFlowPresets

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
