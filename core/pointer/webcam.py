"""Webcam mode: the highlight follows the board's POINT messages (PRD D2, A3.3a). No timer.

The board turns the head pose (MediaPipe face tracking in the browser) into a point on the screen
and sends POINT only when the tile under it changes (and once per new SCREEN). The Core still owns
the highlight: this pointer just moves it to the tile the board reports. Gaze mode (gaze.py) is the
same pointer for an eye tracker's POINTs.
"""

from __future__ import annotations

import logging

from collections.abc import Collection

from core.contracts import Point, PointSource
from core.pointer.base import OnHighlight, OnSource, Pointer

log = logging.getLogger("clench.pointer")


class WebcamPointer(Pointer):
    source = "webcam"

    def __init__(
        self,
        on_highlight: OnHighlight,
        on_source: OnSource | None = None,
        accepts: Collection[PointSource] | None = None,
    ) -> None:
        super().__init__(on_highlight, on_source)
        self._running = False
        # The POINT sources this pointer follows: its own by default (Auto follows webcam and gaze).
        self.accepts: frozenset[str] = frozenset(accepts or (self.source,))

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def on_tiles_changed(self, count: int) -> None:
        # The head has not moved: keep the same position until the board's POINT for the new
        # screen arrives (the 3x2 grid puts the same index in the same place).
        self.place(count, self._highlight)

    def on_point(self, msg: Point) -> None:
        if msg.source not in self.accepts or not self._running:
            return
        self.follow(msg.tile)

    def follow(self, tile: int) -> None:
        """Move the highlight to `tile` (announced only when it changes)."""
        if not 0 <= tile < self._count:
            log.warning("POINT tile %d outside %d tiles, ignored", tile, self._count)
            return
        if tile == self._highlight:
            return
        self._highlight = tile
        try:
            self._on_highlight(tile)
        except Exception:
            log.exception("highlight callback failed")
