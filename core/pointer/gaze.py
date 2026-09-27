"""Gaze follows only eye POINTs and scans before detection or after three seconds without eyes."""

from __future__ import annotations

from core.clock import Scheduler
from core.pointer.auto import AutoPointer
from core.pointer.base import OnHighlight, OnSource
from core.pointer.scan import DEFAULT_SCAN_MS


class GazePointer(AutoPointer):
    """Gaze-only pointing; scan before detection and after a three-second loss."""

    def __init__(self, scheduler: Scheduler, on_highlight: OnHighlight,
                 scan_ms: int = DEFAULT_SCAN_MS, on_source: OnSource | None = None) -> None:
        super().__init__(scheduler, on_highlight, scan_ms, on_source, accepts=("gaze",))
