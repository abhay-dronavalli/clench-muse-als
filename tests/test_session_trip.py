"""Trip mode (core/trip.py): the trip menu tree (Trip / Comfort / Trip changes), the mock car's
telemetry, the input lock while a LOW control animates, and the confirm screens of the HIGH-safety
requests (Pull over, Contact Support). The car answers through the car link (core/car/mock.py)."""

import pytest

from core.car.mock import PULL_OVER_S as CAR_PULL_OVER_S
from core.contracts import (
    ActionResult,
    CarAction,
    CarResult,
    CarState,
    Clench,
    Confirm,
    DoubleBlink,
    FaceOk,
    LongClench,
    Point,
    Screen,
    Settings,
    Tap,
)
from core.db import Db
from core.session import CLENCH_DEBOUNCE_S, HELP_COUNTDOWN_S, RIDE_MINUTE_S, Session, SessionState
from core.trip import PULL_OVER_S, ROUTINE_S, Car
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

TOP = ["Trip", "Comfort", "Trip changes"]
COMFORT = ["Cooler", "Warmer", "Music off", "Volume down", "Windows", "Back"]
CHANGES = ["Pull over", "Slow down", "Contact Support", "Back"]


def trip(on: bool = True, lang: str | None = None) -> Settings:
    return Settings(pointing_mode="auto", scan_ms=int(SCAN_S * 1000), trip=on, lang=lang)


def settle(sched) -> None:
    sched.advance(CLENCH_DEBOUNCE_S + 0.05)


def tap_label(s: Session, sched, sent, label: str) -> None:
    """Tap the tile with this label on the current screen (after the debounce)."""
    settle(sched)
    screen = last_screen(sent)
    s.handle(Tap(tile=[t.label for t in screen.tiles].index(label), seq=screen.seq, t=0.0))


def go(s: Session, sched, sent, *path: str) -> None:
    """Tap down a path of labels."""
    for label in path:
        tap_label(s, sched, sent, label)


def car_results(sent) -> list[CarResult]:
    return [m for m in sent if isinstance(m, CarResult)]


def labels(sent) -> list[str]:
    return [t.label for t in last_screen(sent).tiles]


def car_actions(sent) -> list[CarAction]:
    return [m for m in sent if isinstance(m, CarAction)]


def car_state(sent) -> CarState:
    return next(m for m in reversed(sent) if isinstance(m, CarState))


@pytest.fixture
def in_trip(session, sent):
    session.handle(trip())
    return session


def test_trip_mode_opens_the_top_of_the_trip_menu_with_a_fresh_ride(in_trip, sent):
    screen = last_screen(sent)
    assert screen.screen == "trip"
    assert labels(sent) == TOP
    assert [t.id for t in screen.tiles][:2] == ["trip.ride", "trip.comfort"]
    assert {t.kind for t in screen.tiles} == {"car"}
    assert screen.highlight == 0 and screen.path == []
    assert in_trip.settings().trip is True
    assert car_state(sent) == Car().message()


def test_windows_up_or_down_then_which_window(in_trip, sched, sent):
    go(in_trip, sched, sent, "Comfort", "Windows")
    assert labels(sent) == ["Up", "Down", "Back"]
    assert last_screen(sent).path == ["Comfort", "Windows"]
    tap_label(in_trip, sched, sent, "Down")
    assert labels(sent) == ["Front left", "Front right", "Rear left", "Rear right", "All windows", "Back"]
    assert last_screen(sent).path == ["Comfort", "Windows", "Down"]
    tap_label(in_trip, sched, sent, "Front left")
    assert car_actions(sent) == [CarAction(action="window_down", window="front_left", ms=round(ROUTINE_S * 1000))]
    assert car_state(sent).windows.front_left == 25
    assert car_state(sent).windows.front_right == 0


def test_a_routine_control_locks_input_then_stays_on_its_level(in_trip, sched, sent):
    go(in_trip, sched, sent, "Comfort", "Warmer")
    assert in_trip.state is SessionState.ACTING
    assert car_state(sent).cabin_temp_f == 73
    assert spoken(sent) == []  # nothing said or sent for a routine control
    assert not [m for m in sent if isinstance(m, (Confirm, ActionResult))]

    n = len(sent)
    settle(sched)
    in_trip.handle(Clench(t=0.0, strength=1.0))
    in_trip.handle(Tap(tile=1, seq=last_screen(sent).seq, t=0.0))
    in_trip.handle(Point(source="webcam", tile=1, seq=last_screen(sent).seq, t=0.0))
    assert len(sent) == n  # locked: nothing happened

    sched.advance(ROUTINE_S)
    assert in_trip.state is SessionState.SCANNING
    assert labels(sent) == COMFORT  # same level: warmer again is one pick away
    tap_label(in_trip, sched, sent, "Warmer")
    assert car_state(sent).cabin_temp_f == 74


def test_music_off_and_volume_down(in_trip, sched, sent):
    go(in_trip, sched, sent, "Comfort", "Volume down")
    assert car_state(sent).volume == Car().volume - 1
    sched.advance(ROUTINE_S)
    tap_label(in_trip, sched, sent, "Music off")
    assert car_state(sent).music_playing is False
    sched.advance(ROUTINE_S)
    assert "Music on" in labels(sent)  # the tile now turns it back on
    assert [a.action for a in car_actions(sent)] == ["softer", "softer"]


def test_slow_down_acts_at_once(in_trip, sched, sent):
    go(in_trip, sched, sent, "Trip changes", "Slow down")
    assert car_actions(sent)[-1].action == "slow_down"
    assert car_state(sent).speed_mph == Car().speed_mph - 5


def test_back_tile_and_double_blink_go_up_a_level(in_trip, sched, sent):
    go(in_trip, sched, sent, "Comfort", "Windows", "Up", "Back")
    assert labels(sent) == ["Up", "Down", "Back"]
    settle(sched)
    in_trip.handle(DoubleBlink(t=0.0))  # the go-back prompt, then a clench confirms it
    settle(sched)
    in_trip.handle(Clench(t=0.0, strength=1.0))
    assert labels(sent) == COMFORT


def test_double_blink_at_the_top_of_the_trip_menu_does_nothing(in_trip, sent):
    n = len(sent)
    in_trip.handle(DoubleBlink(t=0.0))
    assert len(sent) == n


def test_help_still_works_during_the_lock(in_trip, sched, sent):
    go(in_trip, sched, sent, "Trip changes", "Slow down")
    assert in_trip.state is SessionState.ACTING
    in_trip.handle(LongClench(t=0.0, duration=2.5))
    assert in_trip.state is SessionState.HELP_COUNTDOWN
    sched.advance(ROUTINE_S)  # the lock ending must not pull the person out of the countdown
    assert in_trip.state is SessionState.HELP_COUNTDOWN
    in_trip.handle(DoubleBlink(t=0.0))
    assert in_trip.state is SessionState.SCANNING
    assert last_screen(sent).screen == "trip"


def test_pull_over_confirms_first_and_tap_cancel_cancels(in_trip, sched, sent):
    go(in_trip, sched, sent, "Trip changes", "Pull over")
    assert in_trip.state is SessionState.CONFIRMING
    assert sent[-1] == Confirm(text="Please pull over here.", action="pull_over")
    assert car_actions(sent) == []
    settle(sched)
    in_trip.handle(Tap(tile=None, seq=None, cancel=True, t=0.0))
    assert in_trip.state is SessionState.SCANNING
    assert labels(sent) == CHANGES
    assert spoken(sent) == [] and car_actions(sent) == [] and car_results(sent) == []
    assert car_state(sent).speed_mph > 0


def test_pull_over_confirmed_is_sent_to_the_car_which_stops(in_trip, sched, sent):
    go(in_trip, sched, sent, "Trip changes", "Pull over")
    settle(sched)
    in_trip.handle(Tap(tile=None, seq=None, t=0.0))  # Confirm
    assert spoken(sent) == [("Please pull over here.", "en")]
    assert [(r.action_id, r.status) for r in car_results(sent)] == [("pull_over", "ACCEPTED")]
    in_trip.handle(done(sent))
    assert last_screen(sent).screen == "trip" and labels(sent) == TOP
    sched.advance(CAR_PULL_OVER_S)
    assert [(r.action_id, r.status) for r in car_results(sent)][-1] == ("pull_over", "COMPLETED")
    assert car_actions(sent) == [CarAction(action="pull_over", ms=round(PULL_OVER_S * 1000))]
    assert car_state(sent).speed_mph == 0 and car_state(sent).phase == "PULLED_OVER"
    go(in_trip, sched, sent, "Trip changes", "Slow down")
    assert car_state(sent).speed_mph == 0  # a car that has pulled over stays stopped
    assert car_results(sent)[-1].status == "REJECTED"


def test_support_confirms_then_calls(in_trip, sched, sent):
    go(in_trip, sched, sent, "Trip changes", "Contact Support")
    assert sent[-1] == Confirm(text="Please connect me to rider support.", action="support")
    settle(sched)
    in_trip.handle(Clench(t=0.0, strength=1.0))
    assert spoken(sent) == [("Please connect me to rider support.", "en")]
    assert [(r.action_id, r.status) for r in car_results(sent)] == [("contact_support", "ACCEPTED")]
    assert not [m for m in sent if isinstance(m, ActionResult)]  # the car answers, not a local mock


def test_cancelling_a_confirm_returns_to_the_same_level(in_trip, sched, sent):
    go(in_trip, sched, sent, "Trip changes", "Contact Support")
    settle(sched)
    in_trip.handle(DoubleBlink(t=0.0))
    settle(sched)
    in_trip.handle(Clench(t=0.0, strength=1.0))  # confirms the go-back prompt: cancel
    assert labels(sent) == CHANGES
    assert car_results(sent) == []
    assert spoken(sent) == []


def test_ending_the_trip_goes_back_to_the_menus(in_trip, sched, sent):
    tap_label(in_trip, sched, sent, "Comfort")
    in_trip.handle(trip(False))
    screen = last_screen(sent)
    assert screen.screen == "menu" and screen.tiles[0].id == "suggested"
    in_trip.handle(trip(True))
    assert labels(sent) == TOP  # a new trip starts at the top


def test_labels_follow_the_language(in_trip, sent):
    in_trip.handle(trip(True, lang="es"))
    assert labels(sent) == ["Viaje", "Comodidad", "Cambios de viaje"]


def test_the_ride_moves_on_each_minute(in_trip, sched, sent):
    start = car_state(sent).eta_min
    sched.advance(RIDE_MINUTE_S)
    assert car_state(sent).eta_min == start - 1


def test_trip_events_never_reach_the_sentence_history(menu, profile, sched, sent):
    db = Db(":memory:")
    s = Session(menu, sent.append, sched, profile=profile, spawn=run_now, lang="en", scan_ms=1000, db=db)
    s.start()
    s.handle(trip())
    go(s, sched, sent, "Trip changes", "Slow down")
    sched.advance(ROUTINE_S)
    tap_label(s, sched, sent, "Pull over")
    settle(sched)
    s.handle(Tap(tile=None, seq=None, t=0.0))
    assert db.recent_messages("en") == []


def test_trip_screen_is_what_a_reconnecting_board_sees(in_trip, sched, sent):
    go(in_trip, sched, sent, "Trip changes", "Slow down")
    view = in_trip.current_view()
    assert isinstance(view, Screen) and view.screen == "trip"  # mid-animation too


def test_help_countdown_cancel_back_to_trip(in_trip, sched, sent):
    in_trip.handle(LongClench(t=0.0, duration=2.5))
    sched.advance(HELP_COUNTDOWN_S - 1)
    in_trip.handle(DoubleBlink(t=0.0))
    assert last_screen(sent).screen == "trip"


def test_a_scan_covers_every_tile_of_a_level_back_included(in_trip, sched, sent):
    tap_label(in_trip, sched, sent, "Trip changes")
    sched.advance(3 * SCAN_S)
    assert last_screen(sent).highlight == 3  # Back
    sched.advance(SCAN_S)
    assert last_screen(sent).highlight == 0


def test_the_split_layout_keeps_only_the_three_most_important_controls(in_trip, sched, sent):
    in_trip.handle(FaceOk(ok=True))
    in_trip.handle(Point(source="gaze", tile=0, seq=in_trip.seq, t=0.0))
    in_trip.handle(Settings(pointing_mode="auto", scan_ms=int(SCAN_S * 1000), trip_layout="split"))
    assert labels(sent) == TOP  # the three top levels are all important now
    assert in_trip.settings().trip_layout == "split"
    tap_label(in_trip, sched, sent, "Comfort")  # the levels below are whole
    assert labels(sent) == COMFORT
    tap_label(in_trip, sched, sent, "Back")
    assert labels(sent) == TOP
    sched.advance(3 * SCAN_S)
    assert last_screen(sent).highlight == 0  # tracked gaze does not cycle the highlight
    in_trip.handle(Settings(pointing_mode="auto", scan_ms=int(SCAN_S * 1000), trip_layout="car"))
    assert labels(sent) == TOP


def test_pull_over_from_the_split_layout_still_confirms(in_trip, sched, sent):
    in_trip.handle(FaceOk(ok=True))
    in_trip.handle(Point(source="gaze", tile=0, seq=in_trip.seq, t=0.0))
    in_trip.handle(Settings(pointing_mode="auto", scan_ms=int(SCAN_S * 1000), trip_layout="split"))
    go(in_trip, sched, sent, "Trip changes", "Pull over")
    assert sent[-1] == Confirm(text="Please pull over here.", action="pull_over")


def test_tracking_loss_expands_split_preserves_control_and_restores_preference(in_trip, sched, sent):
    in_trip.handle(Settings(pointing_mode="auto", scan_ms=1000, trip_layout="split"))
    assert labels(sent) == TOP  # no gaze at startup: all six controls are available
    in_trip.handle(FaceOk(ok=True))
    in_trip.handle(Point(source="gaze", tile=0, seq=in_trip.seq, t=0.0))
    assert labels(sent) == ["Windows", "Pull over", "Support"]
    in_trip.handle(Point(source="gaze", tile=1, seq=in_trip.seq, t=0.0))
    old_seq = in_trip.seq
    in_trip.handle(FaceOk(ok=False))
    sched.advance(2.9)
    assert len(labels(sent)) == 3  # a blink never expands the layout
    sched.advance(0.2)
    assert labels(sent) == TOP
    assert last_screen(sent).tiles[in_trip.highlight].label == "Pull over"
    assert in_trip.settings().trip_layout == "split"
    in_trip.handle(Tap(tile=1, seq=old_seq, t=0.0))
    assert in_trip.state is SessionState.SCANNING  # stale Split tile cannot pick Temperature
    sched.advance(6 * SCAN_S)
    assert in_trip.highlight == 3  # wraps across all six controls
    in_trip.handle(FaceOk(ok=True))
    in_trip.handle(Point(source="gaze", tile=3, seq=in_trip.seq, t=0.0))
    assert labels(sent) == ["Windows", "Pull over", "Support"]
    assert in_trip.highlight == 1


def test_tracking_recovery_keeps_the_current_submenu(in_trip, sched, sent):
    in_trip.handle(Settings(pointing_mode="auto", scan_ms=1000, trip_layout="split"))
    tap_label(in_trip, sched, sent, "Temperature")
    before = labels(sent)
    in_trip.handle(FaceOk(ok=True))
    in_trip.handle(Point(source="gaze", tile=0, seq=in_trip.seq, t=0.0))
    assert labels(sent) == before == ["Warmer", "Cooler", "Back"]
    assert last_screen(sent).path == ["Temperature"]


def test_setup_input_never_operates_hidden_tiles_but_help_still_works(in_trip, sched, sent):
    in_trip.handle(Settings(pointing_mode="auto", scan_ms=1000, onboarding=True))
    before = labels(sent)
    in_trip.handle(Clench(t=0, strength=1))
    in_trip.handle(Tap(tile=0, seq=in_trip.seq, t=0))
    in_trip.handle(DoubleBlink(t=0))
    assert labels(sent) == before
    assert in_trip.state is SessionState.SCANNING
    assert not car_actions(sent)
    in_trip.handle(LongClench(t=0, duration=2.5))
    assert in_trip.state is SessionState.HELP_COUNTDOWN
    in_trip.handle(DoubleBlink(t=0))
    assert in_trip.state is SessionState.SCANNING
    in_trip.handle(Settings(pointing_mode="auto", scan_ms=1000, onboarding=False))
    tap_label(in_trip, sched, sent, "Windows")
    assert labels(sent) == ["Up", "Down", "Back"]


def test_setup_timeout_cannot_confirm_an_existing_outward_action(in_trip, sched, sent):
    tap_label(in_trip, sched, sent, "Support")
    in_trip.handle(Settings(pointing_mode="auto", scan_ms=1000, onboarding=True))
    in_trip.handle(Clench(t=0, strength=1))
    in_trip.handle(Tap(tile=None, seq=None, t=0))
    sched.advance(120)
    assert in_trip.state is SessionState.CONFIRMING
    assert not any(isinstance(m, ActionResult) for m in sent)
    in_trip.handle(Settings(pointing_mode="auto", scan_ms=1000, onboarding=False))
    assert in_trip.state is SessionState.CONFIRMING
