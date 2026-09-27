"""CarLink: how the Core talks to the car (proto/clench/rider/v1/rider.proto, RiderLink.Session).

Rider -> car: ActionRequest (a control from the car's catalog), DropoffRequest, RideProfile,
SupportAnswer. Car -> rider: ActionResult for every request (ACCEPTED / COMPLETED / DELAYED /
REJECTED with a short sentence), RideState (telemetry), SupportQuestion.

The car stays in charge: everything the Core sends is a request. Today the only implementation is
the in-process MockCar (core/car/mock.py). A gRPC server can sit in front of the same interface later:
it would translate CarMessage / RiderMessage to these calls and callbacks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from core.contracts import CarActionName, CarLog, CarResult, CarState, Lang
from core.geo.model import DropoffRequest, RideProfile

Safety = Literal["LOW", "HIGH"]  # proto Safety: LOW = comfort, HIGH = changes the trip or involves people
InputMethod = Literal["CLENCH", "EYEBROW", "GAZE", "CAREGIVER"]


@dataclass(frozen=True)
class CarActionSpec:
    """One entry of the car's ActionCatalog (proto Action)."""

    id: str  # "cooler", "window_down:front_left", "pull_over", "route:route-1", ...
    label_en: str
    label_es: str
    safety: Safety
    anim: CarActionName | None = None  # the board / tablet animation for a LOW control

    def label(self, lang: Lang) -> str:
        return self.label_en if lang == "en" else self.label_es


@dataclass(frozen=True)
class ActionRequest:
    """proto ActionRequest. HIGH-safety requests only after the rider confirmed on the confirm screen."""

    request_id: str
    action_id: str
    confirmed_at: float | None  # when the confirm screen was confirmed; None for a LOW control
    input_method: InputMethod = "CLENCH"


@dataclass(frozen=True)
class AnswerOption:
    id: str
    label: str


@dataclass(frozen=True)
class SupportQuestion:
    """proto SupportQuestion."""

    question_id: str
    text: str
    options: tuple[AnswerOption, ...]
    timeout_seconds: int
    urgent: bool = False


@dataclass(frozen=True)
class SupportAnswer:
    """proto SupportAnswer: an option, or no_response (with whether the rider's input was connected)."""

    question_id: str
    answered_at: float
    option_id: str | None = None
    no_response_input_connected: bool | None = None  # set only for no_response


CarRequest = ActionRequest | DropoffRequest | RideProfile


class CarListener(Protocol):
    """What the car link calls back. Every method may be called from the Core's event loop only."""

    def on_car_state(self, state: CarState) -> None: ...

    def on_car_result(self, result: CarResult) -> None: ...

    def on_support_question(self, question: SupportQuestion) -> None: ...


class CarLink(Protocol):
    def subscribe(self, listener: CarListener) -> None: ...

    def on_log(self, fn: "LogFn") -> None: ...

    def catalog(self) -> list[CarActionSpec]: ...

    def state(self) -> CarState: ...

    def start_ride(self) -> None: ...

    def tick(self) -> None: ...

    def request(self, req: CarRequest) -> None: ...

    def answer(self, answer: SupportAnswer) -> None: ...

    def set_language(self, lang: Lang) -> None: ...


class LogFn(Protocol):
    def __call__(self, entry: CarLog) -> None: ...

