"""Head tilt mode (PRD D2): the headband's motion sensor moves the highlight, no camera.

Not built yet: it needs the headband's accelerometer data from the Sensor Service (a later chunk),
which will send POINT with source "headtilt". Until then this mode scans, says so in the log once,
and the board shows the "Scanning" badge (SCREEN `pointer` = "scan"), so choosing it never leaves
the person stuck.
"""

from __future__ import annotations

import logging

from core.clock import Scheduler
from core.pointer.base import OnHighlight, OnSource
from core.pointer.scan import DEFAULT_SCAN_MS, ScanPointer

log = logging.getLogger("clench.pointer")


class HeadTiltPointer(ScanPointer):
    def __init__(
        self,
        scheduler: Scheduler,
        on_highlight: OnHighlight,
        scan_ms: int = DEFAULT_SCAN_MS,
        on_source: OnSource | None = None,
    ) -> None:
        super().__init__(scheduler, on_highlight, scan_ms, on_source)
        log.warning(
            "pointing mode headtilt is not built yet (it needs headband motion data from the Sensor "
            "Service): scanning instead"
        )
