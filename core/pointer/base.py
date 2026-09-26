"""Pointer interface (PRD A3.3a): one slot in the Core that decides which tile is highlighted.

Each pointing mode (Scan, Webcam, Head tilt, Auto) is a Pointer. The session only talks to this
interface, so adding a mode never touches the session code. Picking is always a clench, whatever
the mode (PRD D2); a Pointer only moves the highlight.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from core.contracts import Point, Settings

OnHighlight = Callable[[int], None]


class Pointer(ABC):
    def __init__(self, on_highlight: OnHighlight) -> None:
        # Called whenever the pointer moves the highlight by itself (timer tick, head turn).
        self._on_highlight = on_highlight
        self._highlight = 0
        self._count = 0

    @property
    def highlight(self) -> int:
        """Index of the highlighted tile in the current level."""
        return self._highlight

    @abstractmethod
    def start(self) -> None:
        """Begin moving the highlight (e.g. start the scan timer)."""

    @abstractmethod
    def stop(self) -> None:
        """Freeze the highlight (confirm screen, speaking, pause)."""

    def on_tiles_changed(self, count: int) -> None:
        """A new level is shown with `count` tiles. The highlight goes back to the first tile (PRD D7)."""
        self._count = count
        self._highlight = 0

    def apply_settings(self, settings: Settings) -> None:
        """Settings changed live. Default: nothing to do."""

    def on_point(self, msg: Point) -> None:
        """A POINT message arrived (webcam / head tilt). Default: ignored."""
