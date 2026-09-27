"""Snap, then zoom (docs/desktop-control.md): which element a clench would click. Pure.

Webcam gaze is off by roughly 1 to 2 cm, more than the gap between two toolbar buttons. So the
highlight only snaps to an element when it is clearly the nearest one once that error is allowed
for: with d1 <= d2 the distances from the gaze to the two nearest elements and `slack` the expected
error, the nearest is sure when d2 - d1 >= slack and d2 >= RATIO * d1. RATIO is the ranking's
stability rule (1.5x). Otherwise a clench opens the zoom, where the same error covers a third of the
distance on the real screen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RATIO = 1.5
BIG_SHARE = 0.25  # an element bigger than this share of the screen is a pane, not a target


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        return max(self.width, 0) * max(self.height, 0)

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.right) / 2, (self.top + self.bottom) / 2

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def distance(self, x: float, y: float) -> float:
        """0 inside, else the distance to the nearest edge."""
        dx = max(self.left - x, 0, x - self.right)
        dy = max(self.top - y, 0, y - self.bottom)
        return math.hypot(dx, dy)

    def encloses(self, other: "Rect") -> bool:
        return (self.left <= other.left and self.top <= other.top and self.right >= other.right
                and self.bottom >= other.bottom and self.area > other.area)

    def intersects(self, other: "Rect") -> bool:
        return self.left < other.right and other.left < self.right and self.top < other.bottom and other.top < self.bottom


@dataclass(frozen=True)
class Candidate:
    rect: Rect
    name: str = ""
    kind: str = ""  # UI Automation control type ("Button", "Hyperlink", ...) or "agent:<action>"

    @property
    def agent_action(self) -> str | None:
        return self.kind[len("agent:"):] if self.kind.startswith("agent:") else None


@dataclass(frozen=True)
class Target:
    """What the highlight shows: a sure element (a clench clicks its center), or a spot where a clench
    opens the zoom (`candidate` then names the nearest element, if any, for the overlay only)."""

    point: tuple[float, float]
    candidate: Candidate | None = None
    sure: bool = False

    @property
    def click_point(self) -> tuple[float, float]:
        return self.candidate.rect.center if self.sure and self.candidate else self.point


def usable(candidates: list[Candidate], screen: Rect) -> list[Candidate]:
    """Drop panes and anything off the screen; keep the innermost of nested elements (a list item,
    not the list holding it)."""
    small = [c for c in candidates
             if c.rect.area > 0 and c.rect.area <= BIG_SHARE * screen.area and c.rect.intersects(screen)]
    return [c for c in small if not any(c.rect.encloses(o.rect) for o in small if o is not c)]


def choose(candidates: list[Candidate], x: float, y: float, radius: float, slack: float) -> Target:
    """The highlight for gaze at (x, y). `candidates` should already be `usable`."""
    for c in candidates:  # the agent's own controls are big and win outright
        if c.agent_action and c.rect.contains(x, y):
            return Target((x, y), c, True)
    near = sorted(((c.rect.distance(x, y), c) for c in candidates if not c.agent_action),
                  key=lambda dc: dc[0])
    near = [(d, c) for d, c in near if d <= radius]
    if not near:
        return Target((x, y))
    d1, best = near[0]
    if len(near) == 1:
        return Target((x, y), best, True)
    d2 = near[1][0]
    return Target((x, y), best, d2 - d1 >= slack and d2 >= RATIO * d1)


def nearest(candidates: list[Candidate], x: float, y: float) -> Target:
    """For the agent's own tiles (the palette): always the nearest one, like the board."""
    best = min(candidates, key=lambda c: c.rect.distance(x, y))
    return Target((x, y), best, True)


@dataclass(frozen=True)
class Zoom:
    """`source` (a part of the real screen) drawn magnified in `panel`."""

    source: Rect
    panel: Rect

    @property
    def factor(self) -> float:
        return self.panel.width / self.source.width

    def to_real(self, x: float, y: float) -> tuple[float, float] | None:
        if not self.panel.contains(x, y):
            return None
        return (self.source.left + (x - self.panel.left) / self.factor,
                self.source.top + (y - self.panel.top) / self.factor)

    def to_panel(self, x: float, y: float) -> tuple[float, float]:
        return (self.panel.left + (x - self.source.left) * self.factor,
                self.panel.top + (y - self.source.top) * self.factor)

    def rect_to_panel(self, r: Rect) -> Rect:
        (l, t), (rr, b) = self.to_panel(r.left, r.top), self.to_panel(r.right, r.bottom)
        return Rect(l, t, rr, b)


def make_zoom(x: float, y: float, screen: Rect, factor: float = 3.0, panel_share: float = 0.7) -> Zoom:
    """A zoom around (x, y): the panel fills `panel_share` of the screen, centered; the source is the
    panel's size divided by `factor`, centered on the gaze and kept on the screen."""
    pw, ph = screen.width * panel_share, screen.height * panel_share
    panel = Rect(screen.left + (screen.width - pw) / 2, screen.top + (screen.height - ph) / 2,
                 screen.left + (screen.width + pw) / 2, screen.top + (screen.height + ph) / 2)
    sw, sh = pw / factor, ph / factor
    left = min(max(x - sw / 2, screen.left), screen.right - sw)
    top = min(max(y - sh / 2, screen.top), screen.bottom - sh)
    return Zoom(Rect(left, top, left + sw, top + sh), panel)
