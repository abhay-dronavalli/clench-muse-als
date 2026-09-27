"""Auto mode (the default, PRD D2): the board's pointing (gaze or head) while it can see the person,
scan when it cannot.

  - Starts scanning, so the highlight moves at once even before the camera is up.
  - Switches to following the board when it has reported a face (FACE_OK true) and a POINT arrives.
    The board sends gaze POINTs while an eye tracker is plugged in and sees the eyes, head (webcam)
    POINTs otherwise; `source` says which one the highlight follows right now.
  - Falls back to scanning when the face has been lost for 3 s (FACE_OK false), from the tile that
    was highlighted. A face that comes back within 3 s cancels the fallback.

The board shows a small "Scanning" badge while Auto is scanning (SCREEN `pointer` = "scan").
"""

from __future__ import annotations

import logging

from core.clock import Scheduler, TimerHandle
from core.contracts import ActivePointer, Point, Settings
from core.pointer.base import OnHighlight, OnSource, Pointer
from core.pointer.scan import DEFAULT_SCAN_MS, ScanPointer
from core.pointer.webcam import WebcamPointer

log = logging.getLogger("clench.pointer")

FACE_LOST_S = 3.0  # PRD D2: drop to Scan when the face has been lost for about 3 seconds


class AutoPointer(Pointer):
    def __init__(
        self,
        scheduler: Scheduler,
        on_highlight: OnHighlight,
        scan_ms: int = DEFAULT_SCAN_MS,
        on_source: OnSource | None = None,
        face_lost_s: float = FACE_LOST_S,
    ) -> None:
        super().__init__(on_highlight, on_source)
        self._scheduler = scheduler
        self.face_lost_s = face_lost_s
        self._scan = ScanPointer(scheduler, on_highlight, scan_ms)
        self._webcam = WebcamPointer(on_highlight, accepts=("webcam", "gaze"))
        self._active: Pointer = self._scan
        self.source = "scan"
        self._face = False
        self._running = False
        self._lost_timer: TimerHandle | None = None

    @property
    def highlight(self) -> int:
        return self._active.highlight

    @property
    def face(self) -> bool:
        return self._face

    def start(self) -> None:
        self._running = True
        self._active.start()

    def stop(self) -> None:
        self._running = False
        self._active.stop()

    def close(self) -> None:
        self.stop()
        self._cancel_lost()

    def on_tiles_changed(self, count: int) -> None:
        self._count = count
        self._active.on_tiles_changed(count)

    def place(self, count: int, highlight: int) -> None:
        self._count = count
        self._active.place(count, highlight)

    def apply_settings(self, settings: Settings) -> None:
        self._scan.apply_settings(settings)

    def on_face(self, ok: bool) -> None:
        self._face = ok
        if ok:
            self._cancel_lost()  # back in time: no fallback; a POINT brings webcam back if scanning
        elif self._active is self._webcam and self._lost_timer is None:
            self._lost_timer = self._scheduler.call_later(self.face_lost_s, self._fall_back)

    def on_point(self, msg: Point) -> None:
        if msg.source not in self._webcam.accepts:
            return
        if self._active is self._webcam:
            if msg.source != self.source:  # the board went from the head to the eyes, or back
                self.source = msg.source
                log.info("auto pointing: %s now", msg.source)
                self._on_source()
            self._webcam.on_point(msg)
        elif self._face and 0 <= msg.tile < self._count:
            self._switch(self._webcam, msg.tile, "face seen and a POINT arrived", source=msg.source)

    def _fall_back(self) -> None:
        self._lost_timer = None
        if self._active is self._webcam and not self._face:
            self._switch(self._scan, self._webcam.highlight, f"no face for {self.face_lost_s:.0f} s")

    def _switch(self, to: Pointer, highlight: int, reason: str, source: ActivePointer | None = None) -> None:
        self._active.stop()
        to.place(self._count, highlight)
        self._active = to
        self.source = source or to.source
        if self._running:
            to.start()
        log.info("auto pointing: %s now (%s)", to.source, reason)
        self._on_source()

    def _cancel_lost(self) -> None:
        if self._lost_timer is not None:
            self._lost_timer.cancel()
            self._lost_timer = None
