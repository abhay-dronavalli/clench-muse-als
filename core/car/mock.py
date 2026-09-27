"""MockCar: an in-process car that follows rider.proto semantics, for the demo and /car-sim.

Every request gets an ActionResult: ACCEPTED (will happen), COMPLETED (has happened), DELAYED (once
safe) or REJECTED (will not), each with a short sentence the board says. The car stays in charge:

  - comfort controls (LOW): done at once, unless unsafe (a window down at highway speed) or at a limit
  - pull_over (HIGH): on the highway DELAYED to the next safe spot, else ACCEPTED; then COMPLETED
    (the car stops); REJECTED if already stopped
  - contact_support (HIGH): ACCEPTED
  - DropoffRequest (HIGH): ACCEPTED with the point's description
  - route:<id> (HIGH): ACCEPTED if the route is one the car offered, else REJECTED
  - anything not in the catalog: REJECTED

`latency_s` simulates the link's round trip (0 = answer inline, for tests). Telemetry is the trip
screen's Car (core/trip.py), so the 3D scene and CAR_STATE work as before.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from core.car.link import (
    ActionRequest,
    AnswerOption,
    CarActionSpec,
    CarListener,
    CarRequest,
    LogFn,
    SupportAnswer,
    SupportQuestion,
)
from core.clock import Scheduler
from core.contracts import CarLog, CarResult, CarState, CarStatus, Lang
from core.geo.model import DropoffRequest, RideProfile
from core.trip import TEMP_RANGE, VOLUME_RANGE, WINDOW_STEP, Car

log = logging.getLogger("clench.car")

HIGHWAY_MPH = 45  # windows stay up at or above this speed
HIGHWAY_SPEED = 62  # the mock's speed on the highway
CITY_SPEED = 32
PULL_OVER_S = 6.0  # demo time from ACCEPTED to stopped (a real car: about 20-30 s)
PULL_OVER_DELAYED_S = 12.0  # demo time from DELAYED (on the highway) to stopped
WINDOWS = ("front_left", "front_right", "rear_left", "rear_right")

Say = tuple[str, str]  # (en, es)


@dataclass(frozen=True)
class Step:
    after_s: float
    status: CarStatus
    say: Say
    expected_in_seconds: int = 0
    effect: Callable[[], None] | None = None


def _window_specs() -> list[CarActionSpec]:
    names = {"front_left": ("front left", "delantera izquierda"), "front_right": ("front right", "delantera derecha"),
             "rear_left": ("rear left", "trasera izquierda"), "rear_right": ("rear right", "trasera derecha"),
             "all": ("all windows", "todas las ventanas")}
    out = []
    for w, (en, es) in names.items():
        out.append(CarActionSpec(f"window_down:{w}", f"Window down, {en}", f"Bajar ventana, {es}", "LOW", "window_down"))
        out.append(CarActionSpec(f"window_up:{w}", f"Window up, {en}", f"Subir ventana, {es}", "LOW", "window_up"))
    return out


BASE_CATALOG: list[CarActionSpec] = [
    CarActionSpec("cooler", "Cooler", "Más fresco", "LOW", "cooler"),
    CarActionSpec("warmer", "Warmer", "Más calor", "LOW", "warmer"),
    CarActionSpec("music_off", "Music off", "Apagar música", "LOW", "softer"),
    CarActionSpec("music_on", "Music on", "Poner música", "LOW", "louder"),
    CarActionSpec("volume_down", "Volume down", "Bajar volumen", "LOW", "softer"),
    CarActionSpec("volume_up", "Volume up", "Subir volumen", "LOW", "louder"),
    CarActionSpec("slow_down", "Slow down", "Más despacio", "LOW", "slow_down"),
    *_window_specs(),
    CarActionSpec("pull_over", "Pull over", "Orillarse", "HIGH"),
    CarActionSpec("contact_support", "Contact Support", "Llamar a soporte", "HIGH"),
]


class MockCar:
    def __init__(self, scheduler: Scheduler, *, latency_s: float = 0.0, routes: dict[str, str] | None = None,
                 clock: Callable[[], float] = time.time, boarding: bool = False) -> None:
        self._scheduler = scheduler
        # boarding: a ride starts parked (BOARDING) and leaves once a confirmed route is accepted. Off, a
        # ride starts already under way (the tests of the controls during a ride).
        self.boarding = boarding
        self.latency_s = latency_s
        self._clock = clock
        self._listeners: list[CarListener] = []
        self._log_fns: list[LogFn] = []
        self.car = Car()
        self.lang: Lang = "en"
        self.routes = dict(routes or {})  # route id -> spoken name ("the route via SW 107th Ave")
        self._sent: dict[str, float] = {}
        self._questions = itertools.count(1)
        self.open_questions: dict[str, SupportQuestion] = {}

    # --- CarLink --------------------------------------------------------------

    def subscribe(self, listener: CarListener) -> None:
        self._listeners.append(listener)

    def on_log(self, fn: LogFn) -> None:
        self._log_fns.append(fn)

    def catalog(self) -> list[CarActionSpec]:
        routes = [CarActionSpec(f"route:{rid}", name, name, "HIGH") for rid, name in self.routes.items()]
        return BASE_CATALOG + routes

    def spec(self, action_id: str) -> CarActionSpec | None:
        return next((a for a in self.catalog() if a.id == action_id), None)

    def state(self) -> CarState:
        return self.car.message()

    def set_language(self, lang: Lang) -> None:
        self.lang = lang

    def start_ride(self) -> None:
        self.car = Car()
        if self.boarding:
            self.car.speed_mph, self.car.phase = 0, "BOARDING"
        self.open_questions.clear()
        self._state_changed()

    def tick(self) -> None:
        self.car.tick()
        self._state_changed()

    def request(self, req: CarRequest) -> None:
        now = self._clock()
        if isinstance(req, RideProfile):
            self._log("to_car", "RideProfile", f"{req.route_preference}, wheelchair={req.uses_wheelchair}", None)
            return  # a preference, not a request: the car honors it as it can (no ActionResult)
        rid = req.request_id
        self._sent[rid] = now
        if isinstance(req, DropoffRequest):
            self._log("to_car", "DropoffRequest", f"{req.description} ({req.point.latitude:.5f}, {req.point.longitude:.5f})", rid)
            steps = [Step(0, "ACCEPTED", (f"Okay. I'll drop you off at {req.description}.",
                                           f"De acuerdo. Te dejo en {req.description}."))]
            self._answer(rid, "dropoff", steps)
            return
        how = "confirmed" if req.confirmed_at is not None else "routine"
        self._log("to_car", "ActionRequest", f"{req.action_id} ({how}, {req.input_method.lower()})", rid)
        self._answer(rid, req.action_id, self._decide(req))

    def answer(self, answer: SupportAnswer) -> None:
        q = self.open_questions.pop(answer.question_id, None)
        if answer.option_id is not None:
            label = next((o.label for o in q.options if o.id == answer.option_id), answer.option_id) if q else answer.option_id
            text = f"{label!r} to {q.text!r}" if q else label
        else:
            text = f"no response (input connected: {answer.no_response_input_connected})"
        self._log("to_car", "SupportAnswer", text, answer.question_id)

    # --- the car's side (/car-sim) ----------------------------------------------

    def ask(self, text: str, options: list[str], timeout_s: int, urgent: bool) -> SupportQuestion:
        qid = f"q{next(self._questions)}"
        opts = tuple(AnswerOption(f"{qid}.{_slug(o)}", o) for o in (options or ["Yes", "No", "Not sure"]))
        q = SupportQuestion(qid, text, opts, timeout_s, urgent)
        self.open_questions[qid] = q
        self._sent[qid] = self._clock()
        self._log("to_rider", "SupportQuestion", f"{text} [{', '.join(o.label for o in opts)}]{' (urgent)' if urgent else ''}", qid)
        self._later(0, lambda: [lst.on_support_question(q) for lst in self._listeners])
        return q

    def set_situation(self, *, on_highway: bool | None = None, phase: str | None = None) -> None:
        c = self.car
        if phase is not None:
            c.phase = phase
            if phase in ("BOARDING", "PULLED_OVER", "ARRIVED"):
                c.speed_mph, c.on_highway = 0, False
                if phase == "ARRIVED":
                    c.eta_min = 0
            elif c.speed_mph == 0:
                c.speed_mph = CITY_SPEED
        if on_highway is not None and c.phase == "EN_ROUTE":
            c.on_highway = on_highway
            c.speed_mph = HIGHWAY_SPEED if on_highway else CITY_SPEED
        self._log("to_rider", "RideState", f"phase {c.phase}, {c.speed_mph} mph{', highway' if c.on_highway else ''}", None)
        self._state_changed()

    # --- decisions --------------------------------------------------------------

    def _decide(self, req: ActionRequest) -> list[Step]:
        c = self.car
        a = req.action_id
        spec = self.spec(a)
        if spec is None:
            return [Step(0, "REJECTED", ("That isn't available right now.", "Eso no está disponible ahora."))]
        if spec.safety == "HIGH" and req.confirmed_at is None:
            # The Core must never send these without the confirm screen (proto: Clench only sends a
            # request after the rider confirmed it). The car refuses rather than trusting it.
            return [Step(0, "REJECTED", ("I need you to confirm that first.", "Primero necesito que lo confirmes."))]
        stopped = c.speed_mph == 0
        if a == "cooler":
            if c.cabin_temp_f <= TEMP_RANGE[0]:
                return [Step(0, "REJECTED", (f"It's already as cool as it goes, {c.cabin_temp_f} degrees.", f"Ya está lo más fresco posible, {c.cabin_temp_f} grados."))]
            return [Step(0, "COMPLETED", (f"Cooler: {c.cabin_temp_f - 1} degrees.", f"Más fresco: {c.cabin_temp_f - 1} grados."), effect=lambda: c.apply("cooler"))]
        if a == "warmer":
            if c.cabin_temp_f >= TEMP_RANGE[1]:
                return [Step(0, "REJECTED", (f"It's already as warm as it goes, {c.cabin_temp_f} degrees.", f"Ya está lo más cálido posible, {c.cabin_temp_f} grados."))]
            return [Step(0, "COMPLETED", (f"Warmer: {c.cabin_temp_f + 1} degrees.", f"Más calor: {c.cabin_temp_f + 1} grados."), effect=lambda: c.apply("warmer"))]
        if a in ("music_off", "music_on"):
            on = a == "music_on"
            return [Step(0, "COMPLETED", (("Music on." if on else "Music off."), ("Música encendida." if on else "Música apagada.")),
                         effect=lambda: setattr(c, "music_playing", on))]
        if a in ("volume_down", "volume_up"):
            down = a == "volume_down"
            if (down and c.volume <= VOLUME_RANGE[0]) or (not down and c.volume >= VOLUME_RANGE[1]):
                return [Step(0, "REJECTED", ("The volume is already at its limit.", "El volumen ya está al límite."))]
            return [Step(0, "COMPLETED", ("Volume down." if down else "Volume up.", "Volumen más bajo." if down else "Volumen más alto."),
                         effect=lambda: c.apply("softer" if down else "louder"))]
        if a.startswith("window_"):
            verb, _, which = a.partition(":")
            if verb == "window_down" and c.speed_mph >= HIGHWAY_MPH:
                return [Step(0, "REJECTED", ("Windows stay up at highway speed. I'll open it when we slow down.",
                                             "Las ventanas no se abren a velocidad de autopista. La abro cuando bajemos la velocidad."))]
            names = list(WINDOWS) if which == "all" else [which]
            if all(c.windows[n] == (100 if verb == "window_down" else 0) for n in names):
                return [Step(0, "REJECTED", (("It's already all the way down." if verb == "window_down" else "It's already closed."),
                                             ("Ya está abierta del todo." if verb == "window_down" else "Ya está cerrada.")))]
            return [Step(0, "COMPLETED", (("Window down." if verb == "window_down" else "Window up."), ("Ventana abajo." if verb == "window_down" else "Ventana arriba.")),
                         effect=lambda: c.apply(verb, which))]  # type: ignore[arg-type]
        if a == "slow_down":
            if stopped:
                return [Step(0, "REJECTED", ("We're stopped.", "Estamos detenidos."))]
            return [Step(0, "COMPLETED", ("Slowing down.", "Bajando la velocidad."), effect=lambda: c.apply("slow_down"))]
        if a == "pull_over":
            if c.phase == "BOARDING":
                return [Step(0, "REJECTED", ("We haven't left yet.", "Todavía no hemos salido."))]
            if stopped:
                return [Step(0, "REJECTED", ("We're already stopped.", "Ya estamos detenidos."))]
            stop = lambda: c.apply("pull_over")  # noqa: E731
            if c.on_highway:
                wait = round(PULL_OVER_DELAYED_S)
                return [
                    Step(0, "DELAYED", (f"We're on the highway. I'll pull over at the next safe spot, in about {wait} seconds.",
                                        f"Estamos en la autopista. Me orillo en el próximo lugar seguro, en unos {wait} segundos."), expected_in_seconds=wait),
                    Step(PULL_OVER_DELAYED_S, "COMPLETED", ("We've pulled over.", "Ya nos orillamos."), effect=stop),
                ]
            return [
                Step(0, "ACCEPTED", (f"Pulling over in about {round(PULL_OVER_S)} seconds.", f"Me orillo en unos {round(PULL_OVER_S)} segundos."), expected_in_seconds=round(PULL_OVER_S)),
                Step(PULL_OVER_S, "COMPLETED", ("We've pulled over.", "Ya nos orillamos."), effect=stop),
            ]
        if a == "contact_support":
            return [Step(0, "ACCEPTED", ("Connecting you to Support. Someone will talk to you shortly.",
                                         "Te conecto con soporte. Alguien te hablará en un momento."))]
        if a.startswith("route:"):
            name = self.routes[a.partition(":")[2]]
            if c.phase == "BOARDING":
                return [Step(0, "ACCEPTED", (f"Okay, taking {name}. Here we go.", f"De acuerdo, voy por {name}. Vamos."),
                             effect=self._depart)]
            return [Step(0, "ACCEPTED", (f"Okay, taking {name}.", f"De acuerdo, voy por {name}."))]
        return [Step(0, "REJECTED", ("That isn't available right now.", "Eso no está disponible ahora."))]

    def _depart(self) -> None:
        """Leave the pick-up: en route at city speed."""
        self.car.phase, self.car.speed_mph = "EN_ROUTE", CITY_SPEED

    # --- plumbing -----------------------------------------------------------------

    def _answer(self, rid: str, action_id: str, steps: list[Step]) -> None:
        for step in steps:
            self._later(step.after_s, lambda step=step: self._deliver(rid, action_id, step))

    def _deliver(self, rid: str, action_id: str, step: Step) -> None:
        if step.effect is not None:
            step.effect()
        sent = self._sent.get(rid)
        rtt = round((self._clock() - sent) * 1000) if sent is not None else None
        text = step.say[0] if self.lang == "en" else step.say[1]
        result = CarResult(request_id=rid, action_id=action_id, status=step.status, message=text,
                           expected_in_seconds=step.expected_in_seconds, rtt_ms=rtt)
        self._log("to_rider", "ActionResult", f"{action_id}: {step.status}, {text!r}", rid, rtt)
        if step.effect is not None:
            self._state_changed()
        for lst in self._listeners:
            lst.on_car_result(result)

    def _later(self, after_s: float, fn: Callable[[], object]) -> None:
        delay = self.latency_s + after_s
        if delay <= 0:
            fn()
        else:
            self._scheduler.call_later(delay, fn)

    def _state_changed(self) -> None:
        state = self.car.message()
        for lst in self._listeners:
            lst.on_car_state(state)

    def _log(self, direction: str, kind: str, summary: str, request_id: str | None, rtt_ms: int | None = None) -> None:
        entry = CarLog(t=self._clock(), direction=direction, kind=kind, summary=summary, request_id=request_id, rtt_ms=rtt_ms)  # type: ignore[arg-type]
        log.info("car %s %s: %s", direction, kind, summary)
        for fn in self._log_fns:
            fn(entry)


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_") or "option"
