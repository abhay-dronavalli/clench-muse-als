"""Pointer interface (PRD A3.3a): one slot in the Core that decides which tile is highlighted.

Each pointing mode (Scan, Webcam, Head tilt, Auto) is a Pointer. The session only talks to this
interface, so adding a mode never touches the session code. Picking is always a clench, whatever
the mode (PRD D2); a Pointer only moves the highlight. The session's selection code is the same for
every mode: it asks the Pointer where the highlight is.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from core.contracts import ActivePointer, Point, Settings

OnHighlight = Callable[[int], None]
OnSource = Callable[[], None]


class Pointer(ABC):
    # Where the highlight comes from right now (SCREEN `pointer`). Auto changes it as it switches.
    source: ActivePointer = "scan"

    def __init__(self, on_highlight: OnHighlight, on_source: OnSource | None = None) -> None:
        # Called whenever the pointer moves the highlight by itself (timer tick, head turn).
        self._on_highlight = on_highlight
        # Called when `source` changes (Auto falling back to scan or returning to webcam).
        self._on_source = on_source or (lambda: None)
        self._highlight = 0
        self._count = 0

    @property
    def highlight(self) -> int:
        """Index of the highlighted tile in the current level."""
        return self._highlight

    @abstractmethod
    def start(self) -> None:
        """Begin moving the highlight (e.g. start the scan timer, or follow POINT messages)."""

    @abstractmethod
    def stop(self) -> None:
        """Freeze the highlight (confirm screen, speaking, loading, pause)."""

    def close(self) -> None:
        """This pointer is being replaced (pointing mode changed) or the session ends."""
        self.stop()

    def on_tiles_changed(self, count: int) -> None:
        """A new level is shown with `count` tiles. The highlight goes back to the first tile (PRD D7)."""
        self._count = count
        self._highlight = 0

    def place(self, count: int, highlight: int) -> None:
        """Take over a screen mid-way (live mode switch): `count` tiles, `highlight` where it was.
        Nothing is announced; the session redraws."""
        self._count = count
        self._highlight = min(max(highlight, 0), max(count - 1, 0))

    def apply_settings(self, settings: Settings) -> None:
        """Settings changed live. Default: nothing to do."""

    def on_point(self, msg: Point) -> None:
        """A POINT for the current screen arrived while scanning (webcam / head tilt). Default: ignored."""

    def on_face(self, ok: bool) -> None:
        """FACE_OK from the board: the webcam can (or cannot) see a face. Default: ignored."""
