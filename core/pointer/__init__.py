"""Pointing modes: scan, webcam, gaze, headtilt, auto (PRD A3.3a)."""

from core.clock import Scheduler
from core.contracts import PointingMode
from core.pointer.auto import FACE_LOST_S, AutoPointer
from core.pointer.base import OnHighlight, OnSource, Pointer
from core.pointer.gaze import GazePointer
from core.pointer.headtilt import HeadTiltPointer
from core.pointer.scan import DEFAULT_SCAN_MS, ScanPointer
from core.pointer.webcam import WebcamPointer

__all__ = [
    "DEFAULT_SCAN_MS",
    "FACE_LOST_S",
    "AutoPointer",
    "GazePointer",
    "HeadTiltPointer",
    "Pointer",
    "ScanPointer",
    "WebcamPointer",
    "make_pointer",
]


def make_pointer(
    mode: PointingMode,
    scheduler: Scheduler,
    on_highlight: OnHighlight,
    scan_ms: int = DEFAULT_SCAN_MS,
    on_source: OnSource | None = None,
) -> Pointer:
    """Build the Pointer for a pointing mode. Head tilt scans until the sensor chunk builds it."""
    match mode:
        case "webcam":
            return WebcamPointer(on_highlight, on_source)
        case "gaze":
            return GazePointer(on_highlight, on_source)
        case "auto":
            return AutoPointer(scheduler, on_highlight, scan_ms, on_source)
        case "headtilt":
            return HeadTiltPointer(scheduler, on_highlight, scan_ms, on_source)
        case _:
            return ScanPointer(scheduler, on_highlight, scan_ms, on_source)
