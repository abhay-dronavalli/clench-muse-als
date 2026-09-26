"""Pointing modes: scan, webcam, headtilt, auto (PRD A3.3a)."""

from core.clock import Scheduler
from core.contracts import PointingMode
from core.pointer.base import OnHighlight, Pointer
from core.pointer.scan import DEFAULT_SCAN_MS, ScanPointer

__all__ = ["DEFAULT_SCAN_MS", "Pointer", "ScanPointer", "make_pointer"]


def make_pointer(
    mode: PointingMode, scheduler: Scheduler, on_highlight: OnHighlight, scan_ms: int = DEFAULT_SCAN_MS
) -> Pointer:
    """Build the Pointer for a pointing mode.

    TODO(chunk: webcam): WebcamPointer for "webcam", HeadTiltPointer for "headtilt", and an
    AutoPointer that uses webcam while FACE_OK and falls back to scan after ~3 s. Until then every
    mode scans, which is the PRD's safe fallback.
    """
    return ScanPointer(scheduler, on_highlight, scan_ms)
