"""The car link (core/car): the mock car's answers, and the session's safety rules around it
(HIGH-safety requests only after the confirm screen, Support questions with help still working)."""

import pytest

from core.car.link import ActionRequest
from core.car.mock import PULL_OVER_DELAYED_S, MockCar
from core.contracts import CarLog, CarResult, CarState, Clench, Confirm, DoubleBlink, LongClench, Tap
from core.geo.trip import load_trip
from core.session import HELP_COUNTDOWN_S, RIDE_MINUTE_S, Session, SessionState
from tests.test_session import SCAN_S, done, last_screen, menu, profile, run_now, sched, sent, session, said  # noqa: F401
from tests.test_session_trip import go, in_trip, settle, tap_label, trip  # noqa: F401


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


def test_drop_off_is_sent_only_after_the_confirm(in_trip, sched, sent):
    go(in_trip, sched, sent, "Trip")
    screen = last_screen(sent)
    assert screen.prompt and "Drop-off:" in screen.prompt  # the reason, from the open-data result
    drop = next(t.label for t in screen.tiles if t.label.startswith("Drop off at"))
    tap_label(in_trip, sched, sent, drop)
    assert isinstance(sent[-1], Confirm) and sent[-1].action == "dropoff"
    assert results(sent) == []  # nothing sent yet
    settle(sched)
    in_trip.handle(Tap(tile=None, seq=None, t=0.0))
    assert [(r.action_id, r.status) for r in results(sent)] == [("dropoff", "ACCEPTED")]
    assert said(sent, "system")[-1][0] == results(sent)[-1].message  # a confirmed request's answer is said


def test_a_route_tile_confirms_then_asks_the_car(in_trip, sched, sent):
    go(in_trip, sched, sent, "Trip")
    route = last_screen(sent).tiles[0].label
    tap_label(in_trip, sched, sent, route)
    assert isinstance(sent[-1], Confirm) and sent[-1].action == "route"
    settle(sched)
    in_trip.handle(Tap(tile=None, seq=None, t=0.0))
    assert results(sent)[-1].status == "ACCEPTED" and results(sent)[-1].action_id.startswith("route:")


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


# --- boarding: the ride starts parked on planning the trip ---------------------------------------


@pytest.fixture
def boarding(menu, profile, sched, sent):
    """A session whose rides start parked (BOARDING), as the app runs (core/main.py)."""
    trip_data = load_trip()
    routes = {t.route_id: t.label for t in trip_data.ride.tiles} if trip_data and trip_data.ride else {}
    s = Session(menu, sent.append, sched, profile=profile, spawn=run_now, lang="en", scan_ms=int(SCAN_S * 1000),
                car_link=MockCar(sched, routes=routes, boarding=True), geo_trip=trip_data)
    s.start()
    s.handle(trip())
    return s


def states(sent) -> list:
    return [m for m in sent if isinstance(m, CarState)]


def test_a_boarding_ride_opens_parked_on_planning_the_trip(boarding, sched, sent):
    screen = last_screen(sent)
    assert screen.screen == "trip" and screen.path == ["Trip"]
    assert any(t.label.startswith("Drop off at") for t in screen.tiles) and screen.tiles[-1].label == "Back"
    assert states(sent)[-1].phase == "BOARDING" and states(sent)[-1].speed_mph == 0
    eta = states(sent)[-1].eta_min
    sched.advance(RIDE_MINUTE_S)
    assert states(sent)[-1].eta_min == eta  # parked: arrival does not count down


def test_a_confirmed_route_makes_the_parked_car_leave(boarding, sched, sent):
    route = last_screen(sent).tiles[0].label
    tap_label(boarding, sched, sent, route)
    assert states(sent)[-1].phase == "BOARDING"  # nothing before the confirm
    settle(sched)
    boarding.handle(Tap(tile=None, seq=None, t=0.0))
    assert results(sent)[-1].status == "ACCEPTED"
    assert states(sent)[-1].phase == "EN_ROUTE" and states(sent)[-1].speed_mph > 0
    boarding.handle(done(sent))
    assert last_screen(sent).path == []  # under way, home is the top of the trip menu


def test_a_cancelled_route_or_a_drop_off_leaves_the_car_parked(boarding, sched, sent):
    route = last_screen(sent).tiles[0].label
    tap_label(boarding, sched, sent, route)
    settle(sched)
    boarding.handle(DoubleBlink(t=0.0))
    settle(sched)
    boarding.handle(Clench(t=0.0, strength=1.0))  # confirms the go-back prompt: cancel
    assert last_screen(sent).path == ["Trip"]
    assert results(sent) == [] and states(sent)[-1].phase == "BOARDING"
    drop = next(t.label for t in last_screen(sent).tiles if t.label.startswith("Drop off at"))
    tap_label(boarding, sched, sent, drop)
    settle(sched)
    boarding.handle(Tap(tile=None, seq=None, t=0.0))
    assert results(sent)[-1].action_id == "dropoff" and states(sent)[-1].phase == "BOARDING"


def test_pull_over_before_leaving_is_refused_and_help_still_works(boarding, sched, sent):
    boarding.car_link.request(ActionRequest(request_id="r1", action_id="pull_over", confirmed_at=1.0))
    assert results(sent)[-1].status == "REJECTED" and "left" in results(sent)[-1].message
    boarding.handle(LongClench(t=0.0, duration=2.5))
    assert boarding.state is SessionState.HELP_COUNTDOWN


def test_the_car_sim_can_board_and_leave(sched):
    car, _, _ = mock(sched, boarding=True)
    car.start_ride()
    assert car.state().phase == "BOARDING" and car.state().speed_mph == 0
    car.set_situation(phase="EN_ROUTE")
    assert car.state().speed_mph > 0
    car.set_situation(phase="BOARDING")
    assert car.state().speed_mph == 0
