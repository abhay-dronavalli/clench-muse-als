"""Desktop control, Core side (docs/desktop-control.md, decisions.md 20): SETTINGS `input_target`,
DESKTOP_INPUT to the agent, and the help alert that must not change in desktop mode."""

import time

import pytest
from fastapi.testclient import TestClient

from core.clock import AsyncioScheduler, ManualScheduler
from core.contracts import (ActionResult, BackPrompt, Clench, Confirm, DesktopInput, DoubleBlink, LongClench, Screen,
                            Settings, Speak)
from core.main import create_app
from core.menu import load_menu
from core.profile import load_profile
from core.session import CLENCH_DEBOUNCE_S, HELP_COUNTDOWN_S, Session, SessionState
from tests.test_session import pick, run_now

SCAN_S = 1.0


@pytest.fixture(scope="module")
def menu():
    return load_menu()


@pytest.fixture(scope="module")
def profile(menu):
    return load_profile(menu.contacts)


@pytest.fixture
def sched():
    return ManualScheduler(start=100.0)


@pytest.fixture
def sent():
    return []


@pytest.fixture
def session(menu, profile, sched, sent):
    s = Session(menu, sent.append, sched, profile=profile, spawn=run_now, lang="en", scan_ms=int(SCAN_S * 1000))
    s.start()
    return s


def target(session, value):
    session.handle(Settings(**dict(session.settings().model_dump(), input_target=value)))


def desktop_inputs(sent):
    return [(m.kind, m.t) for m in sent if isinstance(m, DesktopInput)]


def screens(sent):
    return [m for m in sent if isinstance(m, Screen)]


def test_the_board_is_the_target_until_told_otherwise(session, sent):
    assert session.input_target == "board"
    assert session.settings().input_target == "board"
    session.handle(Clench(t=1.0, strength=1.0))
    assert desktop_inputs(sent) == []


def test_desktop_target_goes_home_and_stops_scanning(session, sched, sent):
    pick(session, sched, sent, "need")
    assert screens(sent)[-1].path == ["I need"]
    target(session, "desktop")
    assert [m for m in sent if isinstance(m, Settings)][-1].input_target == "desktop"
    home = screens(sent)[-1]
    assert home.path == [] and home.highlight == 0
    before = len(screens(sent))
    sched.advance(5 * SCAN_S)
    assert session.highlight == 0
    assert len(screens(sent)) == before  # the highlight never moved on its own


def test_clench_and_double_blink_go_to_the_agent_and_do_nothing_on_the_board(session, sched, sent):
    target(session, "desktop")
    before = len(screens(sent))
    session.handle(Clench(t=123.4, strength=0.9))
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)
    session.handle(DoubleBlink(t=124.0))
    session.handle(Clench(t=124.1, strength=0.9))  # no debounce here: the agent owns its own timing
    assert desktop_inputs(sent) == [("CLENCH", 123.4), ("DOUBLE_BLINK", 124.0), ("CLENCH", 124.1)]
    assert session.state is SessionState.SCANNING
    assert len(screens(sent)) == before  # nothing picked
    assert not any(isinstance(m, (BackPrompt, Speak, Confirm)) for m in sent)


def test_help_still_works_in_desktop_mode_and_a_double_blink_cancels_it(session, sched, sent):
    target(session, "desktop")
    session.handle(LongClench(t=1.0, duration=2.6))
    assert session.state is SessionState.HELP_COUNTDOWN
    assert screens(sent)[-1].screen == "help_countdown"
    session.handle(Clench(t=1.5, strength=1.0))  # a clench during help does nothing, anywhere
    session.handle(DoubleBlink(t=2.0))
    assert desktop_inputs(sent) == []  # neither gesture reached the agent
    assert session.state is SessionState.SCANNING
    assert screens(sent)[-1].screen == "menu"  # the agent's overlay closes on this
    sched.advance(5 * SCAN_S)
    assert session.highlight == 0  # cancelling help did not restart the board's scanning
    session.handle(Clench(t=9.0, strength=1.0))
    assert desktop_inputs(sent) == [("CLENCH", 9.0)]


def test_help_fires_in_desktop_mode(session, sched, sent):
    target(session, "desktop")
    session.handle(LongClench(t=1.0, duration=2.6))
    sched.advance(HELP_COUNTDOWN_S + 0.1)
    assert {r.action for r in sent if isinstance(r, ActionResult)} == {"place_call", "send_message"}
    assert session.state is SessionState.SCANNING
    assert session.input_target == "desktop"
    sched.advance(5 * SCAN_S)
    assert session.highlight == 0  # still not scanning


def test_switching_on_the_confirm_screen_sends_nothing(session, sched, sent):
    for tile in ("need", "water"):
        pick(session, sched, sent, tile)
    assert session.state is SessionState.CONFIRMING
    target(session, "desktop")
    assert session.state is SessionState.SCANNING and screens(sent)[-1].path == []
    target(session, "board")
    session.handle(Clench(t=1.0, strength=1.0))  # would have confirmed; now it picks on Home
    assert not any(isinstance(m, Speak) and m.kind == "phrase" for m in sent)
    assert not any(isinstance(m, ActionResult) for m in sent)


def test_back_to_the_board_scans_again_from_the_first_tile(session, sched, sent):
    target(session, "desktop")
    target(session, "board")
    assert screens(sent)[-1].highlight == 0
    sched.advance(SCAN_S + 0.01)
    assert session.highlight == 1
    session.handle(Clench(t=1.0, strength=1.0))
    assert desktop_inputs(sent) == []


def test_a_help_countdown_survives_a_target_switch(session, sched, sent):
    session.handle(LongClench(t=1.0, duration=2.6))
    target(session, "desktop")
    assert session.state is SessionState.HELP_COUNTDOWN
    session.handle(DoubleBlink(t=2.0))  # still cancels help, not a desktop gesture
    assert session.state is SessionState.SCANNING and desktop_inputs(sent) == []
    sched.advance(5 * SCAN_S)
    assert session.highlight == 0  # the cancel applied the desktop target: no scanning


# --- the app: /ws/desktop, the Muse gate, disconnects ---------------------------------------------


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.db", scheduler=AsyncioScheduler(),
                               scan_ms=600_000, lang="en")) as client:
        yield client


def drain(ws, kind, limit=40):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"no {kind} in the next {limit} messages")


def signal():
    return {"type": "SIGNAL", "t": time.time(), "ch": [], "connected": True,
            "profile": "taher", "emg": 12.0, "threshold": 35.0, "blocked": None}


def set_settings(ws, **change):
    settings = drain(ws, "SETTINGS")
    ws.send_json({**settings, **change})
    return drain(ws, "SETTINGS")


def test_a_muse_clench_reaches_the_agent_with_no_board_open(client):
    with client.websocket_connect("/ws/desktop") as desktop, \
         client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/sensor") as sensor:
        assert set_settings(desktop, input_target="desktop", muse_enabled=True)["input_target"] == "desktop"
        sensor.send_json(signal())
        t = time.time()
        sensor.send_json({"type": "CLENCH", "t": t, "strength": 0.8})
        assert drain(desktop, "DESKTOP_INPUT") == {"type": "DESKTOP_INPUT", "kind": "CLENCH", "t": t}
        event = drain(console, "INPUT_EVENT")
        assert event["accepted"] is True and event["source"] == "muse"


def test_without_an_agent_desktop_gestures_are_refused(client):
    with client.websocket_connect("/ws/board") as board, \
         client.websocket_connect("/ws/console") as console, \
         client.websocket_connect("/ws/sensor") as sensor:
        board.send_json({"type": "READY"})
        set_settings(console, input_target="desktop", muse_enabled=True)
        sensor.send_json(signal())
        sensor.send_json({"type": "DOUBLE_BLINK", "t": time.time()})
        event = drain(console, "INPUT_EVENT")
        assert event["accepted"] is False and event["reason"] == "no desktop agent is connected"


def test_the_agent_s_stand_in_keys_are_logged_as_dev_input(client):
    with client.websocket_connect("/ws/desktop") as desktop, \
         client.websocket_connect("/ws/console") as console:
        set_settings(desktop, input_target="desktop")
        desktop.send_json({"type": "DOUBLE_BLINK", "t": 5.0})
        assert drain(desktop, "DESKTOP_INPUT") == {"type": "DESKTOP_INPUT", "kind": "DOUBLE_BLINK", "t": 5.0}
        event = drain(console, "INPUT_EVENT")
        assert event["source"] == "dev" and event["kind"] == "DOUBLE_BLINK"


def test_the_agent_gets_the_help_countdown_and_the_board_never_gets_desktop_input(client):
    with client.websocket_connect("/ws/board") as board, \
         client.websocket_connect("/ws/desktop") as desktop:
        board.send_json({"type": "READY"})
        set_settings(desktop, input_target="desktop")
        desktop.send_json({"type": "CLENCH", "t": 1.0, "strength": 1.0})
        drain(desktop, "DESKTOP_INPUT")
        desktop.send_json({"type": "LONG_CLENCH", "t": 2.0, "duration": 2.6})
        screen = drain(desktop, "SCREEN")
        assert screen["screen"] == "help_countdown" and screen["countdown"] == HELP_COUNTDOWN_S
        desktop.send_json({"type": "DOUBLE_BLINK", "t": 3.0})
        assert drain(desktop, "SCREEN")["screen"] == "menu"
        seen = [board.receive_json()["type"] for _ in range(6)]
        assert "DESKTOP_INPUT" not in seen


def test_the_last_agent_leaving_puts_the_board_back_in_charge(client):
    with client.websocket_connect("/ws/board") as board:
        assert drain(board, "SETTINGS")["input_target"] == "board"  # on connect
        board.send_json({"type": "READY"})
        with client.websocket_connect("/ws/desktop") as desktop:
            set_settings(desktop, input_target="desktop")
        assert client.get("/health").json()["input_target"] == "board"
        assert drain(board, "SETTINGS")["input_target"] == "desktop"
        assert drain(board, "SETTINGS")["input_target"] == "board"


def test_closing_the_board_keeps_muse_on_while_the_agent_is_in_charge(client):
    with client.websocket_connect("/ws/desktop") as desktop:
        with client.websocket_connect("/ws/board") as board:
            board.send_json({"type": "READY"})
            set_settings(desktop, input_target="desktop", muse_enabled=True)
        health = client.get("/health").json()
        assert health["muse_enabled"] is True and health["desktop_agents"] == 1
    assert client.get("/health").json()["muse_enabled"] is False  # nobody left to act on a gesture
