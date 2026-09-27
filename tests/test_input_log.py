"""INPUT_EVENT (the board's "/" input log) and the headband-loss grace period."""

import time

import pytest
from fastapi.testclient import TestClient

from core.clock import AsyncioScheduler
from core.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.db", scheduler=AsyncioScheduler(),
                               scan_ms=600_000, lang="en")) as client:
        yield client


def drain(ws, kind, limit=40):
    """The next message of type `kind`, skipping whatever else is queued."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"no {kind} in the next {limit} messages")


def signal(**over):
    return {"type": "SIGNAL", "t": time.time(), "ch": [], "connected": True,
            "profile": "taher", "emg": 12.0, "threshold": 35.0, "blocked": None, **over}


def enable(console):
    settings = drain(console, "SETTINGS")
    console.send_json({**settings, "muse_enabled": True})


def test_a_refused_clench_is_reported_with_its_reason(client):
    """Muse paused and a detector that never fired look identical without this."""
    with client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/sensor") as sensor:
        drain(console, "SETTINGS")
        sensor.send_json(signal())
        sensor.send_json({"type": "CLENCH", "t": time.time(), "strength": 0.62})
        event = drain(console, "INPUT_EVENT")
        assert event["kind"] == "CLENCH" and event["source"] == "muse"
        assert event["accepted"] is False
        assert event["reason"] == "Muse input is paused"
        assert event["strength"] == pytest.approx(0.62)


def test_an_accepted_clench_is_reported(client):
    with client.websocket_connect("/ws/board") as board, \
         client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/sensor") as sensor:
        drain(board, "SETTINGS")
        board.send_json({"type": "READY"})
        enable(console)
        sensor.send_json(signal())
        sensor.send_json({"type": "CLENCH", "t": time.time(), "strength": 0.8})
        event = drain(console, "INPUT_EVENT")
        assert event["accepted"] is True and event["reason"] is None


def test_a_blocked_signal_reports_the_sensor_s_own_reason(client):
    with client.websocket_connect("/ws/board") as board, \
         client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/sensor") as sensor:
        drain(board, "SETTINGS")
        board.send_json({"type": "READY"})
        enable(console)
        sensor.send_json(signal(blocked="Head moving - hold still to clench"))
        sensor.send_json({"type": "DOUBLE_BLINK", "t": time.time()})
        event = drain(console, "INPUT_EVENT")
        assert event["kind"] == "DOUBLE_BLINK"
        assert event["accepted"] is False
        assert event["reason"] == "Head moving - hold still to clench"


def test_a_stale_gesture_is_refused(client):
    with client.websocket_connect("/ws/board") as board, \
         client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/sensor") as sensor:
        drain(board, "SETTINGS")
        board.send_json({"type": "READY"})
        enable(console)
        sensor.send_json(signal())
        sensor.send_json({"type": "CLENCH", "t": time.time() - 30, "strength": 0.5})
        event = drain(console, "INPUT_EVENT")
        assert event["accepted"] is False and "older than a second" in event["reason"]


def test_a_dev_panel_gesture_is_reported_as_dev(client):
    with client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/input") as inp:
        drain(console, "SETTINGS")
        inp.send_json({"type": "CLENCH", "t": time.time(), "strength": 0.4})
        event = drain(console, "INPUT_EVENT")
        assert event["source"] == "dev" and event["accepted"] is True


def test_input_events_reach_consoles_and_the_dev_panel_but_not_the_board():
    """The board's own log opens a console socket; the patient screen has no use for these.

    Checked at the hub, not over a socket: proving a message did NOT arrive over a TestClient
    socket means a blocking read that never returns.
    """
    from core.contracts import InputEvent
    from core.hub import Client, Hub

    hub = Hub()
    clients = {role: Client(None, role) for role in ("board", "console", "input", "sensor")}
    for c in clients.values():
        hub.add(c)
    hub.broadcast(InputEvent(t=time.time(), kind="CLENCH", source="dev", accepted=True))
    assert clients["console"].queue.qsize() == 1
    assert clients["input"].queue.qsize() == 1
    assert clients["board"].queue.qsize() == 0
    assert clients["sensor"].queue.qsize() == 0


# --- the headband-loss grace period -------------------------------------------


def test_a_brief_headband_dropout_does_not_pause_muse(client):
    """A Bluetooth reconnect used to flap the switch between Paused and Ready every few seconds."""
    with client.websocket_connect("/ws/board") as board, \
         client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/sensor") as sensor:
        drain(board, "SETTINGS")
        board.send_json({"type": "READY"})
        enable(console)
        session = client.app.state.session
        sensor.send_json(signal())
        assert session.muse_enabled is True
        for _ in range(3):  # three reconnect attempts, well inside the grace period
            sensor.send_json(signal(connected=False, blocked="Connecting to Muse"))
            drain(console, "SIGNAL")
        assert session.muse_enabled is True


def test_a_lasting_headband_loss_pauses_muse(client, monkeypatch):
    monkeypatch.setattr("core.main.MUSE_LOSS_GRACE_S", 0.0)
    with client.websocket_connect("/ws/board") as board, \
         client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/sensor") as sensor:
        drain(board, "SETTINGS")
        board.send_json({"type": "READY"})
        enable(console)
        session = client.app.state.session
        sensor.send_json(signal())
        assert session.muse_enabled is True
        sensor.send_json(signal(connected=False, blocked="Headband disconnected"))
        drain(console, "SIGNAL")
        sensor.send_json(signal(connected=False, blocked="Headband disconnected"))
        drain(console, "SIGNAL")
        assert session.muse_enabled is False


def test_a_command_while_the_headband_is_gone_is_refused(client):
    """The gate, not the pause switch, is what keeps a dropout from acting on stale gestures."""
    with client.websocket_connect("/ws/board") as board, \
         client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/sensor") as sensor:
        drain(board, "SETTINGS")
        board.send_json({"type": "READY"})
        enable(console)
        sensor.send_json(signal(connected=False, blocked=None))
        sensor.send_json({"type": "CLENCH", "t": time.time(), "strength": 0.5})
        event = drain(console, "INPUT_EVENT")
        assert event["accepted"] is False
        assert event["reason"] == "the headband is not connected"
