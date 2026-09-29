"""The car link (core/car): the mock car's answers, and the session's safety rules around it
(HIGH-safety requests only after the confirm screen, Support questions with help still working)."""

from core.car.link import ActionRequest
from core.car.mock import PULL_OVER_DELAYED_S, MockCar
from core.contracts import CarLog, CarResult, Confirm, DoubleBlink, LongClench, Tap
from core.session import HELP_COUNTDOWN_S, SessionState
from tests.test_session import SCAN_S, done, last_screen, menu, profile, run_now, sched, sent, session, said  # noqa: F401
from tests.test_session_trip import board_and_go, go, in_trip, settle, tap_label, trip  # noqa: F401


class Listener:
    def __init__(self):
        self.results: list[CarResult] = []
        self.states = []
        self.questions = []

    def on_car_result(self, r):
        self.results.append(r)

    def on_car_state(self, s):
        self.states.append(s)

    def on_support_question(self, q):
        self.questions.append(q)


def mock(sched, **kw) -> tuple[MockCar, Listener, list[CarLog]]:
    car = MockCar(sched, **kw)
    lst = Listener()
    logs: list[CarLog] = []
    car.subscribe(lst)
    car.on_log(logs.append)
    return car, lst, logs


def req(action_id: str, confirmed: bool = True) -> ActionRequest:
    return ActionRequest(request_id=f"r-{action_id}", action_id=action_id, confirmed_at=1.0 if confirmed else None)


# --- the mock car -------------------------------------------------------------------------------


def test_pull_over_on_the_highway_is_delayed_then_completed(sched):
    car, lst, _ = mock(sched)
    car.set_situation(on_highway=True)
    car.request(req("pull_over"))
    assert [(r.status, r.expected_in_seconds > 0) for r in lst.results] == [("DELAYED", True)]
    assert car.state().speed_mph > 0
    sched.advance(PULL_OVER_DELAYED_S)
    assert lst.results[-1].status == "COMPLETED"
    assert car.state().speed_mph == 0 and car.state().phase == "PULLED_OVER"


def test_window_down_at_highway_speed_is_rejected_with_a_reason(sched):
    car, lst, _ = mock(sched)
    car.set_situation(on_highway=True)
    car.request(req("window_down:front_left", confirmed=False))
    assert lst.results[-1].status == "REJECTED" and "highway" in lst.results[-1].message
    assert car.state().windows.front_left == 0


def test_the_car_refuses_an_unconfirmed_high_safety_request_and_unknown_actions(sched):
    car, lst, logs = mock(sched)
    car.request(req("pull_over", confirmed=False))
    car.request(req("self_destruct"))
    assert [r.status for r in lst.results] == ["REJECTED", "REJECTED"]
    assert car.state().speed_mph > 0
    assert [e.kind for e in logs if e.direction == "to_car"] == ["ActionRequest", "ActionRequest"]


# --- the session ----------------------------------------------------------------------------------


def results(sent) -> list[CarResult]:
    return [m for m in sent if isinstance(m, CarResult)]


def test_a_rejected_comfort_control_says_why(in_trip, sched, sent):
    in_trip.car_link.set_situation(on_highway=True)
    go(in_trip, sched, sent, "Comfort", "Windows", "Down", "Rear left")
    assert results(sent)[-1].status == "REJECTED"
    assert said(sent, "system")[-1][0] == results(sent)[-1].message


def test_car_mode_plans_first_and_the_car_moves_only_after_the_route_confirm(session, sched, sent):
    session.handle(trip())
    screen = last_screen(sent)
    assert screen.prompt == "Plan a trip"
    assert [t.label for t in screen.tiles] == ["Home (demo)", "Miami Cancer Institute", "Pharmacy", "MDC Kendall"]
    assert session.car.phase == "BOARDING" and session.car.speed_mph == 0  # the rider got in: parked
    tap_label(session, sched, sent, "MDC Kendall")
    routes = last_screen(sent)
    assert routes.prompt.startswith("Drop-off: Main entrance, Jack Kassewitz Building\n")  # no OSM codes
    tap_label(session, sched, sent, routes.tiles[0].label)
    assert isinstance(sent[-1], Confirm) and sent[-1].action == "route"
    assert "Go to MDC Kendall?" in sent[-1].text and "without traffic" in sent[-1].text
    assert results(sent) == [] and session.car.speed_mph == 0  # nothing sent, still parked
    settle(sched)
    session.handle(Tap(tile=None, seq=None, t=0.0))
    assert {(r.action_id.split(":")[0], r.status) for r in results(sent)} == {("dropoff", "ACCEPTED"), ("route", "ACCEPTED")}
    assert session.car.phase == "EN_ROUTE" and session.car.speed_mph > 0
    session.handle(done(sent))
    assert [t.label for t in last_screen(sent).tiles] == ["Comfort", "Trip changes"]
    go(session, sched, sent, "Trip changes", "Change trip")
    assert last_screen(sent).prompt == "Plan a trip"


def ask(s, text="Are you hurt?", timeout=30):
    return s.car_link.ask(text, ["Yes", "No", "Not sure"], timeout, True)


def test_a_support_answer_needs_the_confirm_and_then_reaches_the_car(in_trip, sched, sent):
    logs: list[CarLog] = []
    in_trip.car_link.on_log(logs.append)
    ask(in_trip)
    screen = last_screen(sent)
    assert screen.screen == "support_question" and [t.label for t in screen.tiles] == ["Yes", "No", "Not sure"]
    assert screen.prompt == "Support asks: Are you hurt?"
    tap_label(in_trip, sched, sent, "No")
    assert isinstance(sent[-1], Confirm) and sent[-1].action == "support_answer"
    assert not [e for e in logs if e.kind == "SupportAnswer"]  # not before the confirm
    settle(sched)
    in_trip.handle(Tap(tile=None, seq=None, t=0.0))
    answers = [e for e in logs if e.kind == "SupportAnswer"]
    assert len(answers) == 1 and "'No'" in answers[0].summary
    in_trip.handle(done(sent))
    assert last_screen(sent).screen == "trip"


def test_an_unanswered_question_times_out_as_no_response(in_trip, sched, sent):
    logs: list[CarLog] = []
    in_trip.car_link.on_log(logs.append)
    in_trip.input_connected = lambda: False
    ask(in_trip, timeout=10)
    sched.advance(10)
    answers = [e for e in logs if e.kind == "SupportAnswer"]
    assert len(answers) == 1 and "no response (input connected: False)" in answers[0].summary
    assert last_screen(sent).screen == "trip"


def test_help_works_on_a_support_question_and_cancelling_returns_to_it(in_trip, sched, sent):
    ask(in_trip)
    in_trip.handle(LongClench(t=0.0, duration=2.5))
    assert in_trip.state is SessionState.HELP_COUNTDOWN
    sched.advance(HELP_COUNTDOWN_S - 1)
    in_trip.handle(DoubleBlink(t=0.0))
    assert in_trip.state is SessionState.SCANNING
    assert last_screen(sent).screen == "support_question"


# --- Car mode entry and exit (the corner button) --------------------------------------------------


def test_car_mode_is_entered_only_after_its_confirm(session, sched, sent):
    session.start()
    screen = last_screen(sent)
    assert screen.corner is not None and screen.corner.label == "Car mode"
    settle(sched)
    session.handle(Tap(tile=len(screen.tiles), seq=screen.seq, t=0.0))
    assert isinstance(sent[-1], Confirm) and sent[-1].action == "car_mode"
    assert session.trip is False and session.ride_active is False
    settle(sched)
    session.handle(Tap(tile=None, seq=None, t=0.0))
    assert session.trip is True and session.ride_active is True
    assert last_screen(sent).screen == "trip" and last_screen(sent).corner.label == "Home"
    assert last_screen(sent).prompt == "Plan a trip"


def test_leaving_car_mode_keeps_the_ride_and_the_corner_brings_it_back(in_trip, sched, sent):
    go(in_trip, sched, sent, "Comfort", "Cooler")
    sched.advance(1.0)
    temp = in_trip.car.cabin_temp_f
    screen = last_screen(sent)
    settle(sched)
    in_trip.handle(Tap(tile=len(screen.tiles), seq=screen.seq, t=0.0))  # the Home corner: no confirm
    assert in_trip.trip is False and in_trip.ride_active is True
    home = last_screen(sent)
    assert home.screen == "menu" and home.corner.label == "Car mode"
    settle(sched)
    in_trip.handle(Tap(tile=len(home.tiles), seq=home.seq, t=0.0))
    settle(sched)
    in_trip.handle(Tap(tile=None, seq=None, t=0.0))  # confirm "Start Car mode?"
    assert in_trip.trip is True
    assert in_trip.car.cabin_temp_f == temp  # the same ride, not a new one


def test_scan_reaches_the_corner_and_help_works_in_car_mode(in_trip, sched, sent):
    screen = last_screen(sent)
    sched.advance(len(screen.tiles) * SCAN_S)
    assert last_screen(sent).highlight == len(screen.tiles)  # the corner, after the grid
    in_trip.handle(LongClench(t=0.0, duration=2.5))
    assert in_trip.state is SessionState.HELP_COUNTDOWN


# --- touch fallbacks for blinks (the tablet has no DOUBLE_BLINK yet) ---------------------------------


def test_the_help_countdown_is_cancelled_only_by_the_explicit_cancel_touch(in_trip, sched, sent):
    in_trip.handle(LongClench(t=0.0, duration=2.5))
    assert in_trip.state is SessionState.HELP_COUNTDOWN
    screen = in_trip.current_view()
    in_trip.handle(Tap(tile=0, seq=getattr(screen, "seq", 0) or 0, t=0.0))  # a stray tile touch: ignored
    assert in_trip.state is SessionState.HELP_COUNTDOWN
    in_trip.handle(Tap(tile=None, seq=None, cancel=True, t=0.0))  # the large Cancel button
    assert in_trip.state is SessionState.SCANNING


def test_the_go_back_prompt_is_dismissed_by_the_stay_touch(in_trip, sched, sent):
    go(in_trip, sched, sent, "Comfort")
    settle(sched)
    in_trip.handle(DoubleBlink(t=0.0))  # the go-back prompt opens
    assert in_trip._back is not None
    in_trip.handle(Tap(tile=None, seq=None, cancel=True, t=0.0))  # Stay here
    assert in_trip._back is None
    assert [t.label for t in last_screen(sent).tiles][0] == "Cooler"  # still on Comfort
