"""The trip screen: the car controls a rider has during a (Waymo-style) ride, and the mock car's state.

Trip mode is switched on from the dev panel (SETTINGS `trip`); until then the board is the
communication menu as before. The trip menu is a small fixed tree (never ranked, never "Other...",
so a rider's eyes learn where each control is):

    Trip          -> the route tiles and "Drop off at ...?" (data/geo/demo_trip.json)   HIGH: confirm
    Comfort       -> Cooler, Warmer, Music off / on, Volume down, Windows > Up / Down > which   LOW
    Trip changes  -> Pull over (HIGH: confirm), Slow down (LOW), Contact Support (HIGH: confirm)

Every level below the top one ends with a Back tile. LOW-safety comfort controls (proto Safety) are
sent to the car at once (decisions #21): the Core sends CAR_ACTION and locks input while the board and
the tablet play the short animation, and stays on the same level so a control can be repeated. The car
may still refuse (a window at highway speed); its reason is said. HIGH-safety requests always open the
confirm screen first. The car answers every request through the car link (core/car, decisions #26).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.contracts import CarActionName, CarState, Lang, WindowName, WindowsOpen

# How long a routine control locks input (the board's fade / scale / particle sequence), and how long
# the pull-over sequence runs after its confirm. Kept under a second: a rider may repeat heat or music
# several times a trip.
ROUTINE_S = 0.9
PULL_OVER_S = 0.6

WINDOW_STEP = 25  # % a window moves per Up / Down
TEMP_RANGE = (60, 85)  # °F
VOLUME_RANGE = (0, 10)
SLOW_STEP = 5  # mph per Slow down
MIN_SPEED = 10


@dataclass(frozen=True)
class TripNode:
    """One tile of the trip menu: a level (children, or `dynamic` ones built from data) or a control.

    `car_id` is the control's id in the car's catalog (core/car). `confirm` = a HIGH-safety request
    (proto Safety): a confirm screen before it is sent. The others are LOW-safety comfort controls,
    sent at once (decisions #21). `action` is the board / tablet animation of a LOW control."""

    key: str  # id part: "comfort", "cooler", "front_left", ...
    label_en: str
    label_es: str
    children: tuple["TripNode", ...] = ()
    action: CarActionName | None = None
    window: WindowName | None = None
    confirm: bool = False
    car_id: str | None = None
    dynamic: str | None = None  # "ride": the route and drop-off tiles from data/geo/demo_trip.json

    def label(self, lang: Lang) -> str:
        return self.label_en if lang == "en" else self.label_es

    @property
    def is_level(self) -> bool:
        return bool(self.children) or self.dynamic is not None


def _windows(action: CarActionName) -> tuple[TripNode, ...]:
    return tuple(
        TripNode(key, en, es, action=action, window=key, car_id=f"{action}:{key}")  # type: ignore[arg-type]
        for key, en, es in (
            ("front_left", "Front left", "Delantera izquierda"),
            ("front_right", "Front right", "Delantera derecha"),
            ("rear_left", "Rear left", "Trasera izquierda"),
            ("rear_right", "Rear right", "Trasera derecha"),
            ("all", "All windows", "Todas"),
        )
    )


# Music shows "Music off" while music plays and "Music on" once it is off (core/session.py).
MUSIC_ON = ("music", "Music on", "Poner música", "louder", "music_on")

ROOT = TripNode(
    "trip", "Trip", "Viaje",
    children=(
        TripNode("ride", "Trip", "Viaje", dynamic="ride"),
        TripNode("comfort", "Comfort", "Comodidad", children=(
            TripNode("cooler", "Cooler", "Más fresco", action="cooler", car_id="cooler"),
            TripNode("warmer", "Warmer", "Más calor", action="warmer", car_id="warmer"),
            TripNode("music", "Music off", "Apagar música", action="softer", car_id="music_off"),
            TripNode("volume_down", "Volume down", "Bajar volumen", action="softer", car_id="volume_down"),
            TripNode("windows", "Windows", "Ventanas", children=(
                TripNode("up", "Up", "Subir", children=_windows("window_up")),
                TripNode("down", "Down", "Bajar", children=_windows("window_down")),
            )),
        )),
        TripNode("changes", "Trip changes", "Cambios de viaje", children=(
            TripNode("pull_over", "Pull over", "Orillarse", action="pull_over", car_id="pull_over", confirm=True),
            TripNode("slow_down", "Slow down", "Más despacio", action="slow_down", car_id="slow_down"),
            TripNode("support", "Contact Support", "Llamar a soporte", action="support", car_id="contact_support", confirm=True),
        )),
    ),
)
BACK_LABEL: dict[Lang, str] = {"en": "Back", "es": "Atrás"}
# In the split layout (the route map beside the car) the top level shows only these, the most
# important controls, so the tiles stay big in half the screen. (All three top levels today.)
SPLIT_TOP = ("ride", "comfort", "changes")

# What is said once the rider confirms (the confirm screen asks "Pull over here?" / "Call support?").
CONFIRM_PHRASE: dict[str, dict[Lang, str]] = {
    "pull_over": {"en": "Please pull over here.", "es": "Por favor, oríllate aquí."},
    "contact_support": {"en": "Please connect me to rider support.", "es": "Por favor, comunícame con soporte."},
}


@dataclass
class Car:
    """The mock car the trip controls act on. Starts like a ride just under way."""

    speed_mph: int = 32
    eta_min: int = 14
    battery_pct: int = 78
    cabin_temp_f: int = 72
    windows: dict[str, int] = field(
        default_factory=lambda: {"front_left": 0, "front_right": 0, "rear_left": 0, "rear_right": 0}
    )  # % open, 0 = fully up
    volume: int = 4
    phase: str = "EN_ROUTE"  # EN_ROUTE / PULLED_OVER / ARRIVED (core/car)
    music_playing: bool = True
    on_highway: bool = False

    def apply(self, action: CarActionName, window: WindowName | None = None) -> None:
        """Change the state the way `action` would (clamped to sensible ranges)."""
        if action in ("window_up", "window_down"):
            step = WINDOW_STEP if action == "window_down" else -WINDOW_STEP
            names = list(self.windows) if window in (None, "all") else [window]
            for name in names:
                self.windows[name] = max(0, min(100, self.windows[name] + step))
        elif action == "warmer":
            self.cabin_temp_f = min(TEMP_RANGE[1], self.cabin_temp_f + 1)
        elif action == "cooler":
            self.cabin_temp_f = max(TEMP_RANGE[0], self.cabin_temp_f - 1)
        elif action == "louder":
            self.volume = min(VOLUME_RANGE[1], self.volume + 1)
        elif action == "softer":
            self.volume = max(VOLUME_RANGE[0], self.volume - 1)
        elif action == "slow_down":
            if self.speed_mph > 0:  # a car that has pulled over stays stopped
                self.speed_mph = max(MIN_SPEED, self.speed_mph - SLOW_STEP)
        elif action == "pull_over":
            self.speed_mph = 0
            self.phase = "PULLED_OVER"
            self.on_highway = False

    def tick(self) -> None:
        """A minute of the ride: closer to arriving, a little battery used."""
        if self.speed_mph > 0:
            self.eta_min = max(1, self.eta_min - 1)
            self.battery_pct = max(1, self.battery_pct - 1) if self.eta_min % 3 == 0 else self.battery_pct

    def message(self) -> CarState:
        return CarState(
            speed_mph=self.speed_mph,
            eta_min=self.eta_min,
            battery_pct=self.battery_pct,
            cabin_temp_f=self.cabin_temp_f,
            windows=WindowsOpen(**self.windows),
            volume=self.volume,
            phase=self.phase,  # type: ignore[arg-type]
            music_playing=self.music_playing,
            on_highway=self.on_highway,
        )

