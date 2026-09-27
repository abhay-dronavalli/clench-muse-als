"""The car link (core/car): the mock car's answers, and the session's safety rules around it
(HIGH-safety requests only after the confirm screen, Support questions with help still working)."""

from core.car.link import ActionRequest
from core.car.mock import PULL_OVER_DELAYED_S, MockCar
from core.contracts import CarLog, CarResult, Confirm, DoubleBlink, LongClench, Tap
from core.session import HELP_COUNTDOWN_S, SessionState
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
