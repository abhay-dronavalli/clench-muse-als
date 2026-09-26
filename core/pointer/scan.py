"""Scan mode: the highlight moves by itself, one tile every scan_ms, wrapping around (PRD D2)."""

from __future__ import annotations

import logging

from core.clock import Scheduler, TimerHandle
from core.contracts import Settings
from core.pointer.base import OnHighlight, Pointer

log = logging.getLogger("clench.pointer")

DEFAULT_SCAN_MS = 1000


class ScanPointer(Pointer):
    def __init__(self, scheduler: Scheduler, on_highlight: OnHighlight, scan_ms: int = DEFAULT_SCAN_MS) -> None:
        super().__init__(on_highlight)
        self._scheduler = scheduler
        self.scan_ms = scan_ms
        self._timer: TimerHandle | None = None

    @property
    def running(self) -> bool:
        return self._timer is not None

    def start(self) -> None:
        self._cancel()
        self._schedule()

    def stop(self) -> None:
        self._cancel()

    def on_tiles_changed(self, count: int) -> None:
        super().on_tiles_changed(count)
        if self.running:
            self.start()  # the first tile gets a full scan step

    def apply_settings(self, settings: Settings) -> None:
        if settings.scan_ms != self.scan_ms:
            self.scan_ms = settings.scan_ms
            if self.running:
                self.start()

    def _schedule(self) -> None:
        self._timer = self._scheduler.call_later(self.scan_ms / 1000, self._tick)

    def _cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _tick(self) -> None:
        self._schedule()  # reschedule first so an error below never stops the scan
        if self._count <= 1:
            return
        self._highlight = (self._highlight + 1) % self._count
        try:
            self._on_highlight(self._highlight)
        except Exception:
            log.exception("highlight callback failed")
