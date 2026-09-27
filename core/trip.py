"""The trip screen: the six car controls a rider has during a (Waymo-style) ride.

Trip mode is switched on from the dev panel (SETTINGS `trip`); until then the board is the
communication menu as before. The trip screen always shows exactly these six tiles, in this order,
with no "Other..." and no ranking, so a rider's eyes learn where each one is.

Routine controls (windows, heat, music) act as soon as they are picked: the Core sends CAR_ACTION and
locks input while the board and the tablet play the short confirm animation. Nothing is spoken or sent
for them (PRD D5 is about speaking and sending). Pull over is a safety action: it opens a confirm
screen first, and only a confirm (clench or tap) asks the car to stop. The controls are mocks for now.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.contracts import CarActionName, Lang

# How long a routine control locks input (the board's fade / scale / particle sequence), and how long
# the pull-over sequence runs after its confirm. Kept under a second: a rider may repeat heat or music
# several times a trip.
ROUTINE_S = 0.9
PULL_OVER_S = 0.6


@dataclass(frozen=True)
class TripControl:
    action: CarActionName
    label_en: str
    label_es: str

    @property
    def id(self) -> str:
        return f"trip.{self.action}"

    def label(self, lang: Lang) -> str:
        return self.label_en if lang == "en" else self.label_es


CONTROLS: tuple[TripControl, ...] = (
    TripControl("window_up", "Window up", "Subir ventana"),
    TripControl("window_down", "Window down", "Bajar ventana"),
    TripControl("warmer", "Warmer", "Más calor"),
    TripControl("cooler", "Cooler", "Más fresco"),
    TripControl("music", "Music", "Música"),
    TripControl("pull_over", "Pull over", "Orillarse"),
)

# What is said once the rider confirms Pull over (the confirm screen asks "Pull over here?").
PULL_OVER_PHRASE: dict[Lang, str] = {
    "en": "Please pull over here.",
    "es": "Por favor, oríllate aquí.",
}
TRIP_CRUMB: dict[Lang, str] = {"en": "Trip", "es": "Viaje"}
