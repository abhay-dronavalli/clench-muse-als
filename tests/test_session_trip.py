"""Trip mode (core/trip.py): the six car controls, the input lock while a control animates, and Pull
over's confirm screen."""

import pytest

from core.clock import ManualScheduler
from core.contracts import (
    ActionResult,
    CarAction,
    Clench,
    Confirm,
    DoubleBlink,
    LongClench,
    Point,
    Screen,
    Settings,
    Tap,
)
from core.db import Db
from core.session import CLENCH_DEBOUNCE_S, HELP_COUNTDOWN_S, Session, SessionState
from core.trip import PULL_OVER_S, ROUTINE_S
from tests.test_session import (  # noqa: F401 (fixtures)
    SCAN_S,
    done,
    last_screen,
    menu,
    profile,
    run_now,
    sched,
    sent,
    session,
    spoken,
)

CONTROLS = ["trip.window_up", "trip.window_down", "trip.warmer", "trip.cooler", "trip.music", "trip.pull_over"]


def trip(on: bool = True, lang: str | None = None) -> Settings:
    return Settings(pointing_mode="auto", scan_ms=int(SCAN_S * 1000), trip=on, lang=lang)


def tap(sent, index: int) -> Tap:
    return Tap(tile=index, seq=last_screen(sent).seq, t=0.0)


def settle(sched) -> None:
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)


def car_actions(sent) -> list[CarAction]:
    return [m for m in sent if isinstance(m, CarAction)]


@pytest.fixture
def in_trip(session, sent):
    session.handle(trip())
    return session


def test_trip_mode_shows_exactly_the_six_controls(in_trip, sent):
    screen = last_screen(sent)
    assert screen.screen == "trip"
    assert [t.id for t in screen.tiles] == CONTROLS
    assert [t.label for t in screen.tiles] == ["Window up", "Window down", "Warmer", "Cooler", "Music", "Pull over"]
    assert {t.kind for t in screen.tiles} == {"car"}
    assert screen.highlight == 0
    assert screen.path == []
    assert in_trip.settings().trip is True


def test_the_scan_covers_the_six_controls_and_wraps(in_trip, sched, sent):
    sched.advance(5 * SCAN_S)
    assert last_screen(sent).highlight == 5
    sched.advance(SCAN_S)
    assert last_screen(sent).highlight == 0


def test_a_routine_control_acts_at_once_and_locks_input(in_trip, sched, sent):
    settle(sched)
    in_trip.handle(tap(sent, 2))  # Warmer
    assert car_actions(sent) == [CarAction(action="warmer", ms=round(ROUTINE_S * 1000))]
    assert in_trip.state is SessionState.ACTING
    assert spoken(sent) == []  # nothing said or sent for a routine control
    assert not [m for m in sent if isinstance(m, (Confirm, ActionResult))]

    # Locked: a clench, a tap and pointing do nothing, and the highlight stays put.
    n = len(sent)
    settle(sched)
    in_trip.handle(Clench(t=0.0, strength=1.0))
    in_trip.handle(tap(sent, 4))
    in_trip.handle(Point(source="webcam", tile=4, seq=last_screen(sent).seq, t=0.0))
    assert len(car_actions(sent)) == 1
    assert len(sent) == n

    sched.advance(ROUTINE_S)
    assert in_trip.state is SessionState.SCANNING
    assert last_screen(sent).screen == "trip"
    settle(sched)
    in_trip.handle(tap(sent, 3))  # the lock is over: Cooler works
    assert car_actions(sent)[-1].action == "cooler"


def test_a_clench_picks_the_highlighted_control(in_trip, sched, sent):
    sched.advance(CLENCH_DEBOUNCE_S + 0.05 + 4 * SCAN_S)
    assert in_trip.highlight == 4
    in_trip.handle(Clench(t=0.0, strength=1.0))
    assert car_actions(sent)[-1].action == "music"


def test_help_still_works_during_the_lock(in_trip, sched, sent):
    settle(sched)
    in_trip.handle(tap(sent, 0))
    assert in_trip.state is SessionState.ACTING
    in_trip.handle(LongClench(t=0.0, duration=2.5))
    assert in_trip.state is SessionState.HELP_COUNTDOWN
    sched.advance(ROUTINE_S)  # the lock ending must not pull the person out of the countdown
    assert in_trip.state is SessionState.HELP_COUNTDOWN
    in_trip.handle(DoubleBlink(t=0.0))
    assert in_trip.state is SessionState.SCANNING
    assert last_screen(sent).screen == "trip"


def test_pull_over_confirms_first_and_tap_cancel_cancels(in_trip, sched, sent):
    settle(sched)
    in_trip.handle(tap(sent, 5))
    assert in_trip.state is SessionState.CONFIRMING
    assert sent[-1] == Confirm(text="Please pull over here.", action="pull_over")
    assert car_actions(sent) == []
    settle(sched)
    in_trip.handle(Tap(tile=None, seq=None, cancel=True, t=0.0))
    assert in_trip.state is SessionState.SCANNING
    assert last_screen(sent).screen == "trip"
    assert spoken(sent) == [] and car_actions(sent) == []


def test_pull_over_confirmed_animates_speaks_and_runs(in_trip, sched, sent):
    settle(sched)
    in_trip.handle(tap(sent, 5))
    settle(sched)
    in_trip.handle(Tap(tile=None, seq=None, t=0.0))  # the Confirm button
    assert car_actions(sent) == [CarAction(action="pull_over", ms=round(PULL_OVER_S * 1000))]
    assert spoken(sent) == [("Please pull over here.", "en")]
    assert [(r.action, r.ok) for r in sent if isinstance(r, ActionResult)] == [("pull_over", True)]
    in_trip.handle(done(sent))
    assert in_trip.state is SessionState.SCANNING
    assert last_screen(sent).screen == "trip"  # home in trip mode is the trip screen


def test_double_blink_on_the_trip_screen_does_nothing(in_trip, sched, sent):
    n = len(sent)
    in_trip.handle(DoubleBlink(t=0.0))
    assert len(sent) == n
    assert in_trip.state is SessionState.SCANNING


def test_ending_the_trip_goes_back_to_the_menus(in_trip, sent):
    in_trip.handle(trip(False))
    screen = last_screen(sent)
    assert screen.screen == "menu"
    assert screen.tiles[0].id == "suggested"
    assert in_trip.settings().trip is False


def test_labels_follow_the_language(in_trip, sent):
    in_trip.handle(trip(True, lang="es"))
    assert [t.label for t in last_screen(sent).tiles] == [
        "Subir ventana", "Bajar ventana", "Más calor", "Más fresco", "Música", "Orillarse"
    ]


def test_trip_events_never_reach_the_sentence_history(menu, profile, sched, sent):
    db = Db(":memory:")
    s = Session(menu, sent.append, sched, profile=profile, spawn=run_now, lang="en", scan_ms=1000, db=db)
    s.start()
    s.handle(trip())
    settle(sched)
    s.handle(tap(sent, 2))
    sched.advance(ROUTINE_S)
    settle(sched)
    s.handle(tap(sent, 5))
    settle(sched)
    s.handle(Tap(tile=None, seq=None, t=0.0))
    assert db.recent_messages("en") == []


def test_trip_screen_is_what_a_reconnecting_board_sees(in_trip, sched, sent):
    settle(sched)
    in_trip.handle(tap(sent, 1))
    view = in_trip.current_view()
    assert isinstance(view, Screen) and view.screen == "trip"  # mid-animation too


def test_help_countdown_cancel_back_to_trip_during_scan(in_trip, sched, sent):
    in_trip.handle(LongClench(t=0.0, duration=2.5))
    sched.advance(HELP_COUNTDOWN_S - 1)
    in_trip.handle(DoubleBlink(t=0.0))
    assert last_screen(sent).screen == "trip"
