"""What a gesture does on the desktop (docs/desktop-control.md, "Input model"). Pure: time and gaze
come in as arguments, effects go out as values, and the running agent carries them out.

  pointing  the highlight follows the gaze: a sure element (clench clicks it), or a spot (clench
            zooms). The "Clench" tab on the right edge opens the palette.
  zoom      a 3x picture of the area; the highlight follows the gaze inside it; a clench clicks the
            matching real point (snapped to an element when sure). A double blink closes it.
  palette   the agent's own big tiles: right click, double click, scroll, drag, type, Clench board,
            calibrate, pause, close. The nearest tile is highlighted, as on the board. A double blink
            closes it. Right click, double click, scroll and drag arm the next clench.
  scroll    a control at the spot the clench picked: looking into its Up or Down zone scrolls the
            window under it, faster past the zone. A clench or a double blink ends it.
  drag      the button is held from the spot the clench picked; the pointer follows the eyes; a clench
            drops there, a double blink cancels (Esc, then release where it started).
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
SCROLL_EVERY_S = 0.15  # one wheel notch this often while the eyes are in a scroll zone (twice past it)
WHEEL_NOTCH = 120  # Windows' WHEEL_DELTA: one notch, usually three lines
DRAG_MOVE_PX = 3  # move the held pointer only when the gaze moved at least this much

Mode = Literal["pointing", "zoom", "palette", "back", "calibrating", "scroll", "drag"]
Armed = Literal["left", "right", "double", "scroll", "drag"]


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


@dataclass(frozen=True)
class Scroll:
    x: float
    y: float
    delta: int  # + up, - down, in wheel units (WHEEL_NOTCH = one notch)


@dataclass(frozen=True)
class Press:
    """Put the pointer at (x, y) and hold the left button down (a drag starts)."""

    x: float
    y: float


@dataclass(frozen=True)
class MoveTo:
    x: float
    y: float


@dataclass(frozen=True)
class Release:
    """Put the pointer at (x, y) and let the left button go."""

    x: float
    y: float


Effect = Union[Click, Keys, ZoomShot, SetTarget, FocusBoard, Calibrate, CancelCalibration, Compose, Scroll, Press,
               MoveTo, Release]


@dataclass(frozen=True)
class ScrollControl:
    """Where the scroll zones are: `up` above the anchor, `down` below, a still band between."""

    anchor: tuple[float, float]
    up: Rect
    down: Rect

    def direction(self, x: float, y: float) -> tuple[int, bool]:
        """(+1 up / -1 down / 0, fast): fast when the eyes are past the zone's outer edge."""
        up, down = self.up, self.down
        if up.left <= x < up.right and y < up.bottom:
            return 1, y < up.top
        if down.left <= x < down.right and y >= down.top:
            return -1, y >= down.bottom
        return 0, False


def scroll_control(x: float, y: float, screen: Rect, px_per_mm: float) -> ScrollControl:
    """Zones 40 x 28 mm, 10 mm apart around the anchor, moved inward to fit the screen."""
    w, h, gap = 40 * px_per_mm, 28 * px_per_mm, 5 * px_per_mm
    cx = min(max(x, screen.left + w / 2), screen.right - w / 2)
    cy = min(max(y, screen.top + h + gap), screen.bottom - h - gap)
    return ScrollControl((x, y), Rect(cx - w / 2, cy - gap - h, cx + w / 2, cy - gap),
                         Rect(cx - w / 2, cy + gap, cx + w / 2, cy + gap + h))

TEXT = {
    "en": {
        "right": "Right click", "double": "Double click", "scroll": "Scroll", "drag": "Drag", "type": "Type",
        "board": "Clench board", "calibrate": "Calibrate eyes",
        "armed_scroll": "Clench where you want to scroll", "armed_drag": "Clench what you want to drag",
        "scrolling": "Look up or down to scroll. Clench or double blink to stop.",
        "dragging": "Look where it goes, then clench to drop. Double blink cancels.",
        "pause": "Pause clicks", "resume": "Resume clicks", "close": "Close", "menu": "Clench",
        "no_eyes": "Eyes not detected", "look_inside": "Look inside the zoom", "paused": "Clicks are paused",
        "armed_right": "Next clench right-clicks", "armed_double": "Next clench double-clicks",
        "disarmed": "Back to left click", "up": "Up", "down": "Down", "now_paused": "Clicks paused", "now_resumed": "Clicks back on",
    },
    "es": {
        "right": "Clic derecho", "double": "Doble clic", "scroll": "Desplazar", "drag": "Arrastrar",
        "type": "Escribir", "board": "Tablero Clench", "calibrate": "Calibrar ojos",
        "armed_scroll": "Aprieta donde quieras desplazar", "armed_drag": "Aprieta lo que quieras arrastrar",
        "scrolling": "Mira arriba o abajo para desplazar. Aprieta o parpadea dos veces para parar.",
        "dragging": "Mira adónde va y aprieta para soltar. Parpadea dos veces para cancelar.",
        "pause": "Pausar clics", "resume": "Reanudar clics", "close": "Cerrar", "menu": "Clench",
        "no_eyes": "No se detectan los ojos", "look_inside": "Mira dentro del zoom", "paused": "Clics en pausa",
        "armed_right": "El próximo apretón hace clic derecho", "armed_double": "El próximo apretón hace doble clic",
        "disarmed": "Vuelve el clic normal", "now_paused": "Clics en pausa", "now_resumed": "Clics activos",
        "up": "Arriba", "down": "Abajo",
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
        self.scroll: ScrollControl | None = None
        self._scroll_at = float("-inf")
        self.drag_from: tuple[float, float] | None = None
        self._drag_at: tuple[float, float] | None = None  # where the held pointer was last put
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
        """Nine big tiles, 3 x 3, in the middle of the screen."""
        keys = ["right", "double", "scroll", "drag", "type", "board", "calibrate",
                "resume" if self.paused else "pause", "close"]
        s = self.screen
        gap = 3 * self.px_per_mm
        cols, rows = 3, 3
        tw, th = s.width * 0.25, s.height * 0.25
        left0 = s.left + (s.width - cols * tw - (cols - 1) * gap) / 2
        top0 = s.top + (s.height - rows * th - (rows - 1) * gap) / 2
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

    def set_input_target(self, target: str) -> list[Effect]:
        """Returns what must happen at once (a held drag is let go)."""
        self.input_target = target
        if self.active:
            return []
        self.armed = "left"
        return self._close()

    def set_help(self, on: bool) -> list[Effect]:
        """The Core's help countdown is on screen: everything of ours closes (a held drag is let go),
        gestures stay with it."""
        self.help = on
        if on and self.mode != "calibrating":
            return self._close()
        return []

    def start_calibration(self) -> list[Effect]:
        """F7 or the palette: nothing else happens until it ends (a double blink stops it)."""
        if self.mode == "calibrating":
            return []
        self._to("calibrating")
        return [Calibrate()]

    def calibration_ended(self) -> None:
        if self.mode == "calibrating":
            self._to("pointing")

    def tick(self, now: float, point: tuple[float, float] | None, fresh: bool) -> list[Effect]:
        """Called every frame: timers, the highlight for the current gaze, and what scrolling and
        dragging do with it."""
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
            elif self.mode == "drag":
                target = Target(point)  # drop exactly where the eyes are: no snapping
        elif self.mode == "palette":
            target = self.target  # the palette keeps its tile while the eyes blink or wander
        self.target = target
        self._trail.add(now, target)
        if not (self.eyes and point is not None and self.active and not self.help):
            return []  # eyes lost: scrolling stops, a drag holds still
        if self.mode == "scroll" and self.scroll is not None:
            direction, fast = self.scroll.direction(*point)
            every = SCROLL_EVERY_S / 2 if fast else SCROLL_EVERY_S
            if direction and now - self._scroll_at >= every:
                self._scroll_at = now
                return [Scroll(*self.scroll.anchor, direction * WHEEL_NOTCH)]
        elif self.mode == "drag" and self._drag_at is not None:
            if abs(point[0] - self._drag_at[0]) + abs(point[1] - self._drag_at[1]) >= DRAG_MOVE_PX:
                self._drag_at = point
                return [MoveTo(*point)]
        return []

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
        if self.mode == "scroll":
            self._to("pointing")  # done scrolling
            return []
        if self.mode == "drag":
            point = target.point if target is not None else self._drag_at
            self._to("pointing")
            return [Release(*point)] if point is not None else []
        if target is None:
            self._say("look_inside" if self.mode == "zoom" and self.eyes else "no_eyes", now)
            return []
        if target.candidate is not None and target.candidate.agent_action == "menu":
            self._to("palette")
            return []
        if self.mode == "zoom":
            self._to("pointing")
            return self._act(*target.click_point, now)
        if self.paused:
            self._say("paused", now)
            return []
        if target.sure:
            return self._act(*target.click_point, now)
        zoom = make_zoom(*target.point, self.screen)
        self._to("zoom")
        self.zoom = zoom
        self.zoom_candidates = [c for c in self.candidates if c.rect.intersects(zoom.source)]
        return [ZoomShot(zoom)]

    def _double_blink(self, now: float) -> list[Effect]:
        if self.mode == "back":
            return []  # already asking
        if self.mode in ("zoom", "palette", "scroll"):
            self._to("pointing")  # our own screens: straight back, nothing is lost
            return []
        if self.mode == "drag":
            return self._close()  # cancel: Esc, then let go where it started
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
        if action in ("right", "double", "scroll", "drag"):
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

    def _act(self, x: float, y: float, now: float) -> list[Effect]:
        """What a clench on (x, y) does: the armed action once, then left click again."""
        armed, self.armed = self.armed, "left"
        if armed == "scroll":
            self._to("scroll")
            self.scroll = scroll_control(x, y, self.screen, self.px_per_mm)
            self._scroll_at = now  # the first notch after a moment, not at once
            self._say("scrolling", now)
            return []
        if armed == "drag":
            self._to("drag")
            self.drag_from = self._drag_at = (x, y)
            self._say("dragging", now)
            return [Press(x, y)]
        return [Click(x, y, "right" if armed == "right" else "left", armed == "double")]

    def _close(self) -> list[Effect]:
        """Back to pointing from anywhere, letting go of a held drag (cancelled where it started)."""
        effects: list[Effect] = []
        if self.mode == "drag" and self.drag_from is not None:
            effects = [Keys("escape"), Release(*self.drag_from)]
        self._to("pointing")
        return effects

    def _to(self, mode: Mode) -> None:
        if mode != "back":
            self.back_until = None
        if mode != "zoom":
            self.zoom = None
            self.zoom_candidates = []
        if mode != "scroll":
            self.scroll = None
        if mode != "drag":
            self.drag_from = self._drag_at = None
        if mode != self.mode:
            self._trail = Trail(2.0)  # never look back into another screen
        self.mode = mode

    def _say(self, key: str, now: float) -> None:
        self.toast = (self.t(key), now + TOAST_S)
