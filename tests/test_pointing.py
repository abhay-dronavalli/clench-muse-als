"""Pointing modes (PRD D2, A3.3a): Webcam follows POINT, Auto falls back to Scan without a face and
comes back, Head tilt scans until it is built, the mode switches live, stale POINTs are ignored and
a clench while following the head picks the tile highlighted 250 ms before it."""

import logging

import pytest
from fastapi.testclient import TestClient

from core.clock import AsyncioScheduler, ManualScheduler
from core.contracts import Clench, DoubleBlink, FaceOk, Point, Screen, Settings
from core.main import create_app
from core.menu import load_menu
from core.pointer import (
    FACE_LOST_S,
    AutoPointer,
    GazePointer,
    HeadTiltPointer,
    ScanPointer,
    WebcamPointer,
    make_pointer,
)
from core.profile import load_profile
from core.session import CLENCH_DEBOUNCE_S, Session, SessionState
from tests.gestures import go_back

SCAN_S = 1.0
HOME = ["suggested", "need", "people", "feel", "room", "other"]


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


def make_session(menu, profile, sched, sent, mode="auto", **kw) -> Session:
    s = Session(
        menu, sent.append, sched, profile=profile, lang="en", scan_ms=int(SCAN_S * 1000), pointing_mode=mode,
        learning=False, **kw,
    )
    s.start()
    return s


def screens(sent) -> list[Screen]:
    return [m for m in sent if isinstance(m, Screen)]


def last_screen(sent) -> Screen:
    return screens(sent)[-1]


def point(session: Session, tile: int, seq: int | None = None, source: str = "webcam") -> None:
    session.handle(Point(source=source, tile=tile, seq=session.seq if seq is None else seq, t=0.0))


def clench(session: Session, sched: ManualScheduler) -> None:
    sched.advance(CLENCH_DEBOUNCE_S + 0.01)
    session.handle(Clench(t=0.0, strength=1.0))


# --- make_pointer ------------------------------------------------------------------------


def test_each_mode_gets_its_pointer(sched):
    modes = ("scan", "webcam", "gaze", "auto", "headtilt")
    kinds = {mode: type(make_pointer(mode, sched, lambda i: None)) for mode in modes}
    assert kinds == {
        "scan": ScanPointer, "webcam": WebcamPointer, "gaze": GazePointer, "auto": AutoPointer, "headtilt": HeadTiltPointer,
    }


# --- SCREEN seq and stale POINTs ----------------------------------------------------------


def test_seq_goes_up_when_the_tiles_change_not_when_the_highlight_moves(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="scan")
    home = last_screen(sent)
    sched.advance(SCAN_S * 2)
    assert [s.highlight for s in screens(sent)] == [0, 1, 2]
    assert {s.seq for s in screens(sent)} == {home.seq}
    clench(session, sched)  # "people" (the scan was on tile 2)
    assert last_screen(sent).path == ["People"] and last_screen(sent).seq == home.seq + 1


def test_a_stale_point_is_ignored(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="webcam")
    home_seq = session.seq
    point(session, 1)  # need
    clench(session, sched)
    assert last_screen(sent).path == ["I need"]
    new_seq = session.seq
    assert new_seq > home_seq
    before = len(sent)
    point(session, 4, seq=home_seq)  # measured on the home tiles, arrived late
    assert len(sent) == before and session.highlight == 1
    point(session, 4)  # for the tiles on screen now
    assert last_screen(sent).highlight == 4 and last_screen(sent).seq == new_seq


# --- Webcam ----------------------------------------------------------------------------


def test_webcam_follows_point_and_has_no_timer(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="webcam")
    assert last_screen(sent).pointer == "webcam"
    sched.advance(SCAN_S * 5)
    assert len(screens(sent)) == 1  # nothing moves by itself
    point(session, 2)
    point(session, 2)  # same tile again: nothing new
    point(session, 9)  # off the screen: ignored
    assert [s.highlight for s in screens(sent)] == [0, 2]
    clench(session, sched)
    assert last_screen(sent).path == ["People"]
    assert last_screen(sent).highlight == 2  # the head has not moved: same position on the new tiles


def test_webcam_moves_are_not_scan_steps(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="webcam")
    point(session, 1)
    point(session, 3)
    point(session, 1)
    assert session._effort.effort.scan_steps == 0


def test_points_are_ignored_off_the_scanning_screen(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="webcam")
    point(session, 4)  # room
    clench(session, sched)
    point(session, 1)  # tv
    clench(session, sched)
    point(session, 0)  # on
    clench(session, sched)
    assert session.state is SessionState.CONFIRMING
    before = len(sent)
    point(session, 1)
    assert len(sent) == before


# --- Auto ------------------------------------------------------------------------------


def test_auto_scans_until_a_face_and_a_point_arrive(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent)
    assert last_screen(sent).pointer == "scan"
    point(session, 3)  # no face reported yet: still scanning
    assert session.pointer.source == "scan"
    sched.advance(SCAN_S)
    assert last_screen(sent).highlight == 1
    session.handle(FaceOk(ok=True))
    assert session.pointer.source == "scan"  # a face alone does not switch
    point(session, 3)
    assert session.pointer.source == "webcam"
    assert last_screen(sent).pointer == "webcam" and last_screen(sent).highlight == 3
    n = len(screens(sent))
    sched.advance(SCAN_S * 5)
    assert len(screens(sent)) == n  # the scan timer is off


def test_auto_falls_back_after_3_s_without_a_face_and_comes_back(menu, profile, sched, sent, caplog):
    session = make_session(menu, profile, sched, sent)
    session.handle(FaceOk(ok=True))
    point(session, 4)
    assert session.pointer.source == "webcam"
    session.handle(FaceOk(ok=False))
    sched.advance(FACE_LOST_S - 0.1)
    assert session.pointer.source == "webcam" and session.highlight == 4  # frozen, not yet scanning
    with caplog.at_level(logging.INFO, logger="clench.pointer"):
        sched.advance(0.2)
    assert session.pointer.source == "scan"
    assert "no face for 3 s" in caplog.text
    fallback = last_screen(sent)
    assert fallback.pointer == "scan" and fallback.highlight == 4  # scanning on from where it was
    sched.advance(SCAN_S)
    assert last_screen(sent).highlight == 5
    # The face comes back: webcam again at the next POINT.
    session.handle(FaceOk(ok=True))
    assert session.pointer.source == "scan"
    point(session, 1)
    assert session.pointer.source == "webcam" and last_screen(sent).highlight == 1


def test_auto_face_back_within_3_s_cancels_the_fallback(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent)
    session.handle(FaceOk(ok=True))
    point(session, 2)
    session.handle(FaceOk(ok=False))
    sched.advance(2.0)
    session.handle(FaceOk(ok=True))
    sched.advance(FACE_LOST_S * 2)
    assert session.pointer.source == "webcam"


def test_auto_falls_back_while_confirming_and_scans_when_back(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent)
    session.handle(FaceOk(ok=True))
    point(session, 4)  # room
    clench(session, sched)
    point(session, 1)  # tv
    clench(session, sched)
    point(session, 0)  # on
    clench(session, sched)
    assert session.state is SessionState.CONFIRMING
    session.handle(FaceOk(ok=False))
    sched.advance(FACE_LOST_S + 1)
    assert session.pointer.source == "scan" and session.state is SessionState.CONFIRMING
    go_back(session)  # back to the TV level: scanning, badge on
    assert last_screen(sent).pointer == "scan"
    h = last_screen(sent).highlight
    sched.advance(SCAN_S)
    assert last_screen(sent).highlight == (h + 1) % len(last_screen(sent).tiles)


# --- Head tilt -------------------------------------------------------------------------


def test_headtilt_falls_back_to_scan(menu, profile, sched, sent, caplog):
    with caplog.at_level(logging.WARNING, logger="clench.pointer"):
        session = make_session(menu, profile, sched, sent, mode="headtilt")
    assert "headtilt is not built yet" in caplog.text
    assert last_screen(sent).pointer == "scan"
    sched.advance(SCAN_S)
    assert last_screen(sent).highlight == 1  # it scans
    session.handle(Point(source="headtilt", tile=4, seq=session.seq, t=0.0))
    assert session.highlight == 1  # head tilt POINTs do nothing yet


# --- live mode switch --------------------------------------------------------------------


def settings(mode: str) -> Settings:
    return Settings(pointing_mode=mode, scan_ms=int(SCAN_S * 1000))


def test_switching_modes_live_mid_screen(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="scan")
    sched.advance(SCAN_S * 2)
    assert session.highlight == 2
    seq = session.seq

    session.handle(settings("webcam"))
    assert session.pointing_mode == "webcam" and isinstance(session.pointer, WebcamPointer)
    assert session.settings().pointing_mode == "webcam"
    now = last_screen(sent)
    assert now.pointer == "webcam" and now.highlight == 2 and now.seq == seq  # same tiles, same place
    n = len(screens(sent))
    sched.advance(SCAN_S * 3)
    assert len(screens(sent)) == n  # the old scan timer is gone
    point(session, 5)
    assert session.highlight == 5

    session.handle(settings("scan"))
    assert last_screen(sent).pointer == "scan" and session.highlight == 5
    sched.advance(SCAN_S)
    assert session.highlight == 6  # scanning on from tile 5: the Car mode corner
    sched.advance(SCAN_S)
    assert session.highlight == 0  # then wrapping

    session.handle(settings("auto"))
    session.handle(FaceOk(ok=True))
    point(session, 3)
    assert session.pointer.source == "webcam"
    session.handle(settings("headtilt"))
    assert last_screen(sent).pointer == "scan" and session.highlight == 3
    sched.advance(SCAN_S)
    assert session.highlight == 4


def test_switch_to_auto_keeps_the_known_face(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="webcam")
    session.handle(FaceOk(ok=True))
    session.handle(settings("auto"))
    point(session, 2)
    assert session.pointer.source == "webcam"


def test_switching_modes_while_confirming_waits_for_the_next_screen(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="webcam")
    point(session, 4)
    clench(session, sched)
    point(session, 1)
    clench(session, sched)
    clench(session, sched)  # the head still points at tile 1 on the TV level
    assert session.state is SessionState.CONFIRMING
    session.handle(settings("scan"))
    n = len(screens(sent))
    sched.advance(SCAN_S * 3)
    assert len(screens(sent)) == n  # not scanning while confirming
    assert session.state is SessionState.CONFIRMING


# --- clench look-back ---------------------------------------------------------------------


def test_clench_look_back_picks_the_earlier_tile(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="webcam")
    point(session, 2)  # people, held for a while
    sched.advance(1.0)
    point(session, 3)  # the clench nods the head onto "how I feel"...
    sched.advance(0.1)
    session.handle(Clench(t=0.0, strength=1.0))  # ...100 ms before the clench arrives
    assert last_screen(sent).path == ["People"]


def test_clench_look_back_uses_a_steady_tile(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="webcam")
    point(session, 2)
    sched.advance(0.1)
    point(session, 3)
    sched.advance(0.5)  # "how I feel" held for 500 ms: that is the choice
    session.handle(Clench(t=0.0, strength=1.0))
    assert last_screen(sent).path == ["How I feel"]


def test_clench_look_back_stays_on_the_current_screen(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="webcam")
    point(session, 3)
    sched.advance(1.0)
    session.handle(Settings(pointing_mode="webcam", scan_ms=1000, lang="es"))  # new labels: new tiles
    seq = session.seq
    assert last_screen(sent).seq == seq and last_screen(sent).highlight == 3
    point(session, 1)
    sched.advance(0.15)  # the new screen is only 150 ms old
    session.handle(Clench(t=0.0, strength=1.0))
    # The tile this screen started with, never an entry from before the screen changed.
    assert last_screen(sent).path == ["Cómo me siento"]


def test_scan_mode_picks_the_tile_under_the_highlight(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="scan")
    sched.advance(SCAN_S * 2 + 0.05)  # on "people" for only 50 ms
    session.handle(Clench(t=0.0, strength=1.0))
    assert last_screen(sent).path == ["People"]


def test_look_back_can_be_turned_off(menu, profile, sched, sent):
    session = make_session(menu, profile.model_copy(update={"clench_lookback_ms": 0}), sched, sent, mode="webcam")
    point(session, 2)
    sched.advance(1.0)
    point(session, 3)
    sched.advance(0.1)
    session.handle(Clench(t=0.0, strength=1.0))
    assert last_screen(sent).path == ["How I feel"]


# --- Core app: head range REST and the board going away ------------------------------------


HEAD_RANGE = {"center_yaw": 0.5, "center_pitch": -2.0, "left_yaw": -18.0, "right_yaw": 17.0, "up_pitch": 9.0, "down_pitch": -12.0}


def test_head_range_is_saved_in_the_profile(tmp_path):
    db = tmp_path / "clench.db"
    with TestClient(create_app(db_path=db, scan_ms=600_000, lang="en")) as client:
        assert client.get("/api/head-range").json() is None
        assert client.put("/api/head-range", json=HEAD_RANGE).json() == HEAD_RANGE
        bad = {**HEAD_RANGE, "left_yaw": 5.0}  # both sides right of the center
        assert client.put("/api/head-range", json=bad).status_code == 422
        assert client.get("/api/head-range").json() == HEAD_RANGE
    with TestClient(create_app(db_path=db, scan_ms=600_000, lang="en")) as client:  # survives a restart
        assert client.get("/api/head-range").json() == HEAD_RANGE


def test_last_board_leaving_counts_as_face_lost():
    with TestClient(create_app(scheduler=AsyncioScheduler(), scan_ms=600_000, lang="en")) as client:
        with client.websocket_connect("/ws/board") as board:
            board.receive_json()  # SETTINGS
            board.send_json({"type": "FACE_OK", "ok": True})
            board.send_json({"type": "READY"})
            board.receive_json()  # SCREEN (handled after FACE_OK)
            assert client.get("/health").json()["face_ok"] is True
        health = client.get("/health").json()
        assert health["face_ok"] is False
        assert health["pointing_mode"] == "auto" and health["pointer"] == "scan"


# --- Gaze -------------------------------------------------------------------------------


def test_gaze_follows_gaze_points_only(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="gaze")
    assert last_screen(sent).pointer == "scan"
    session.handle(FaceOk(ok=True))
    point(session, 3, source="gaze")
    assert last_screen(sent).highlight == 3
    point(session, 1, source="webcam")  # the head does not drive Gaze mode
    assert session.highlight == 3
    n = len(screens(sent))
    sched.advance(SCAN_S * 5)
    assert len(screens(sent)) == n  # no scan timer


def test_gaze_clench_looks_back_like_webcam(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="gaze")
    session.handle(FaceOk(ok=True))
    point(session, 2, source="gaze")
    sched.advance(1.0)
    point(session, 3, source="gaze")  # the eyes moved as the jaw clenched
    session.handle(Clench(t=0.0, strength=1.0))
    assert last_screen(sent).path == ["People"]  # tile 2, highlighted 250 ms before


def test_auto_follows_gaze_and_switches_between_gaze_and_head(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent)
    session.handle(FaceOk(ok=True))
    point(session, 3, source="gaze")
    assert session.pointer.source == "gaze" and last_screen(sent).pointer == "gaze"
    assert last_screen(sent).highlight == 3
    point(session, 4, source="webcam")  # the eye tracker lost the eyes: the head takes over
    assert session.pointer.source == "webcam"
    assert last_screen(sent).pointer == "webcam" and last_screen(sent).highlight == 4
    point(session, 2, source="gaze")
    assert last_screen(sent).pointer == "gaze" and last_screen(sent).highlight == 2
    session.handle(FaceOk(ok=False))
    sched.advance(FACE_LOST_S + 0.1)
    assert session.pointer.source == "scan"  # neither: scanning


def test_gaze_mode_from_settings(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="scan")
    session.handle(Settings(pointing_mode="gaze", scan_ms=1000))
    assert isinstance(session.pointer, GazePointer) and session.settings().pointing_mode == "gaze"
    assert last_screen(sent).pointer == "scan"  # eyes have not been detected yet


def test_gaze_scans_after_loss_and_recovers_without_accepting_head_points(menu, profile, sched, sent):
    session = make_session(menu, profile, sched, sent, mode="gaze")
    sched.advance(SCAN_S)
    assert session.highlight == 1
    session.handle(FaceOk(ok=True))
    point(session, 3, source="gaze")
    session.handle(FaceOk(ok=False))
    sched.advance(FACE_LOST_S - 0.1)
    assert session.pointer.source == "gaze"
    sched.advance(0.2)
    assert session.pointer.source == "scan"
    sched.advance(SCAN_S)
    assert session.highlight == 4
    session.handle(FaceOk(ok=True))
    point(session, 0, source="webcam")
    assert session.pointer.source == "scan"
    point(session, 2, source="gaze")
    assert session.pointer.source == "gaze" and session.highlight == 2
