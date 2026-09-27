"""What a gesture does on the desktop (docs/desktop-control.md, "Input model"). Pure: time and gaze
come in as arguments, effects go out as values, and the running agent carries them out.

  pointing  the highlight follows the gaze: a sure element (clench clicks it), or a spot (clench
            zooms). The "Clench" tab on the right edge opens the palette.
  zoom      a 3x picture of the area; the highlight follows the gaze inside it; a clench clicks the
            matching real point (snapped to an element when sure). A double blink closes it.
  palette   the agent's own big tiles: right click, double click, Clench board, calibrate, pause,
            close. The nearest tile is highlighted, as on the board. A double blink closes it.
  back      "Go back?" for BACK_CONFIRM_S: a clench sends Alt+Left, doing nothing does nothing and
            then clenches are ignored for LATE_CLENCH_S (the Core's rule, decisions.md 17).
  calibrating  gestures do nothing but a double blink, which stops the calibration.

A clench uses the highlight from `lookback_s` before it (clenching moves the eyes). No click happens
while the eyes are not detected, while paused, during the help countdown, or while the board (not
the desktop) is the input target.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

from desktop.agent.gaze import Trail
from desktop.agent.snap import Candidate, Rect, Target, Zoom, choose, make_zoom, nearest, usable

BACK_CONFIRM_S = 3.0
LATE_CLENCH_S = 1.0
DEBOUNCE_S = 0.3
TOAST_S = 2.5

Mode = Literal["pointing", "zoom", "palette", "back", "calibrating"]
Armed = Literal["left", "right", "double"]


@dataclass(frozen=True)
class Click:
    x: float
    y: float
    button: Literal["left", "right"] = "left"
    double: bool = False


@dataclass(frozen=True)
class Keys:
    combo: str  # "alt+left"


@dataclass(frozen=True)
class ZoomShot:
    """Grab the screen for this zoom and look up the elements inside its source."""

    zoom: Zoom


@dataclass(frozen=True)
class SetTarget:
    target: Literal["board", "desktop"]


@dataclass(frozen=True)
class FocusBoard:
    pass


@dataclass(frozen=True)
class Calibrate:
    pass


@dataclass(frozen=True)
class Compose:
    """Write something where the desktop's focus is: the board composes it, the agent types it."""


@dataclass(frozen=True)
class CancelCalibration:
    pass


Effect = Union[Click, Keys, ZoomShot, SetTarget, FocusBoard, Calibrate, CancelCalibration, Compose]

TEXT = {
    "en": {
        "right": "Right click", "double": "Double click", "type": "Type", "board": "Clench board",
        "calibrate": "Calibrate eyes",
        "pause": "Pause clicks", "resume": "Resume clicks", "close": "Close", "menu": "Clench",
        "no_eyes": "Eyes not detected", "look_inside": "Look inside the zoom", "paused": "Clicks are paused",
        "armed_right": "Next clench right-clicks", "armed_double": "Next clench double-clicks",
        "disarmed": "Back to left click", "now_paused": "Clicks paused", "now_resumed": "Clicks back on",
    },
    "es": {
        "right": "Clic derecho", "double": "Doble clic", "type": "Escribir", "board": "Tablero Clench",
        "calibrate": "Calibrar ojos",
        "pause": "Pausar clics", "resume": "Reanudar clics", "close": "Cerrar", "menu": "Clench",
        "no_eyes": "No se detectan los ojos", "look_inside": "Mira dentro del zoom", "paused": "Clics en pausa",
        "armed_right": "El próximo apretón hace clic derecho", "armed_double": "El próximo apretón hace doble clic",
        "disarmed": "Vuelve el clic normal", "now_paused": "Clics en pausa", "now_resumed": "Clics activos",
    },
}


class Controller:
    def __init__(self, screen: Rect, px_per_mm: float, *, lookback_s: float = 0.25, radius_mm: float = 15.0,
                 slack_mm: float = 8.0, lang: str = "en") -> None:
        self.screen = screen
        self.px_per_mm = px_per_mm
        self.radius = radius_mm * px_per_mm
        self.slack = slack_mm * px_per_mm
        self.lookback_s = lookback_s
        self.lang = lang if lang in TEXT else "en"
        self.mode: Mode = "pointing"
        self.input_target = "board"  # the Core's SETTINGS says; nothing happens until it is "desktop"
        self.help = False
        self.armed: Armed = "left"
        self.paused = False
        self.eyes = False
        self.target: Target | None = None
        self.zoom: Zoom | None = None
        self.candidates: list[Candidate] = []
        self.zoom_candidates: list[Candidate] = []
        self.back_until: float | None = None
        self.toast: tuple[str, float] | None = None
        self._late_until = float("-inf")
        self._last_clench = float("-inf")
        self._trail: Trail[Target | None] = Trail(2.0)
        w, h = 9 * px_per_mm, 34 * px_per_mm
        cy = (screen.top + screen.bottom) / 2
        self.menu_tab = Candidate(Rect(screen.right - w, cy - h / 2, screen.right, cy + h / 2), "Clench", "agent:menu")

    # --- what the overlay reads ---------------------------------------------------------------

    def t(self, key: str) -> str:
        return TEXT[self.lang][key]

    @property
    def active(self) -> bool:
        return self.input_target == "desktop"

    def palette_tiles(self) -> list[Candidate]:
        """Big tiles, 4 x 2, in the middle of the screen."""
        keys = ["right", "double", "type", "board", "calibrate", "resume" if self.paused else "pause", "close"]
        s = self.screen
        gap = 3 * self.px_per_mm
        cols = 4
        tw, th = s.width * 0.2, s.height * 0.26
        left0 = s.left + (s.width - cols * tw - (cols - 1) * gap) / 2
        top0 = s.top + (s.height - 2 * th - gap) / 2
        tiles = []
        for i, key in enumerate(keys):
            col, row = i % cols, i // cols
            left, top = left0 + col * (tw + gap), top0 + row * (th + gap)
            tiles.append(Candidate(Rect(left, top, left + tw, top + th), self.t(key), f"agent:{key}"))
        return tiles

    # --- inputs ---------------------------------------------------------------------------------

    def set_lang(self, lang: str) -> None:
        self.lang = lang if lang in TEXT else "en"

    def set_candidates(self, candidates: list[Candidate]) -> None:
        self.candidates = usable(candidates, self.screen)

    def set_zoom_candidates(self, candidates: list[Candidate]) -> None:
        if self.zoom is not None:
            self.zoom_candidates = [c for c in usable(candidates, self.screen) if c.rect.intersects(self.zoom.source)]

    def set_input_target(self, target: str) -> None:
        self.input_target = target
        if not self.active:
            self._to("pointing")
            self.armed = "left"

    def set_help(self, on: bool) -> None:
        """The Core's help countdown is on screen: everything of ours closes, gestures stay with it."""
        self.help = on
        if on and self.mode != "calibrating":
            self._to("pointing")

    def start_calibration(self) -> list[Effect]:
        """F7 or the palette: nothing else happens until it ends (a double blink stops it)."""
        if self.mode == "calibrating":
            return []
        self._to("calibrating")
        return [Calibrate()]

    def calibration_ended(self) -> None:
        if self.mode == "calibrating":
            self._to("pointing")

    def tick(self, now: float, point: tuple[float, float] | None, fresh: bool) -> None:
        """Called every frame: timers, and the highlight for the current gaze."""
        if self.back_until is not None and now >= self.back_until:
            self.back_until = None
            self._late_until = now + LATE_CLENCH_S  # doing nothing was the answer
            self._to("pointing")
        if self.toast and now >= self.toast[1]:
            self.toast = None
        self.eyes = fresh and point is not None
        target: Target | None = None
        if self.eyes and point is not None and self.active and not self.help:
            if self.mode in ("pointing", "back"):
                target = choose([self.menu_tab, *self.candidates], *point, self.radius, self.slack)
            elif self.mode == "zoom" and self.zoom is not None:
                real = self.zoom.to_real(*point)
                if real is not None:
                    f = self.zoom.factor
                    target = choose(self.zoom_candidates, *real, self.radius / f, self.slack / f)
            elif self.mode == "palette":
                target = nearest(self.palette_tiles(), *point)
        elif self.mode == "palette":
            target = self.target  # the palette keeps its tile while the eyes blink or wander
        self.target = target
        self._trail.add(now, target)

    def on_gesture(self, kind: str, t: float, now: float) -> list[Effect]:
        if self.mode == "calibrating":
            if kind == "DOUBLE_BLINK":
                self._to("pointing")
                return [CancelCalibration()]
            return []
        if not self.active or self.help:
            return []
        if kind == "CLENCH":
            return self._clench(t, now)
        if kind == "DOUBLE_BLINK":
            return self._double_blink(now)
        return []

    # --- gestures -------------------------------------------------------------------------------

    def _clench(self, t: float, now: float) -> list[Effect]:
        if now - self._last_clench < DEBOUNCE_S:
            return []
        self._last_clench = now
        if self.mode == "back":
            self._to("pointing")
            return [Keys("alt+left")]
        if now < self._late_until:
            return []
        target = self._trail.at(t - self.lookback_s)
        if target is None:
            target = self.target
        if self.mode == "palette":
            return self._palette(target)
        if target is None:
            self._say("look_inside" if self.mode == "zoom" and self.eyes else "no_eyes", now)
            return []
        if target.candidate is not None and target.candidate.agent_action == "menu":
            self._to("palette")
            return []
        if self.mode == "zoom":
            self._to("pointing")
            return [self._click(*target.click_point)]
        if self.paused:
            self._say("paused", now)
            return []
        if target.sure:
            return [self._click(*target.click_point)]
        zoom = make_zoom(*target.point, self.screen)
        self._to("zoom")
        self.zoom = zoom
        self.zoom_candidates = [c for c in self.candidates if c.rect.intersects(zoom.source)]
        return [ZoomShot(zoom)]

    def _double_blink(self, now: float) -> list[Effect]:
        if self.mode == "back":
            return []  # already asking
        if self.mode in ("zoom", "palette"):
            self._to("pointing")  # our own screens: straight back, nothing is lost
            return []
        if self.armed != "left":
            self.armed = "left"
            self._say("disarmed", now)
            return []
        self._to("back")
        self.back_until = now + BACK_CONFIRM_S
        return []

    def _palette(self, target: Target | None) -> list[Effect]:
        action = target.candidate.agent_action if target and target.candidate else None
        if action is None:
            return []
        self._to("pointing")
        now_toast = self._last_clench
        if action in ("right", "double"):
            self.armed = action  # type: ignore[assignment]
            self._say(f"armed_{action}", now_toast)
        elif action == "board":
            return [SetTarget("board"), FocusBoard()]
        elif action == "type":
            return [Compose()]
        elif action == "calibrate":
            return self.start_calibration()
        elif action in ("pause", "resume"):
            self.paused = action == "pause"
            self._say("now_paused" if self.paused else "now_resumed", now_toast)
        return []

    def _click(self, x: float, y: float) -> Click:
        armed, self.armed = self.armed, "left"
        return Click(x, y, "right" if armed == "right" else "left", armed == "double")

    def _to(self, mode: Mode) -> None:
        if mode != "back":
            self.back_until = None
        if mode != "zoom":
            self.zoom = None
            self.zoom_candidates = []
        if mode != self.mode:
            self._trail = Trail(2.0)  # never look back into another screen
        self.mode = mode

    def _say(self, key: str, now: float) -> None:
        self.toast = (self.t(key), now + TOAST_S)
