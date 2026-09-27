"""Gaze mode: the highlight follows an eye tracker's POINT messages (source "gaze"). No timer.

An eye tracker plugs into the board's pointing code (web/src/facetrack/gaze.ts, see
docs/eye-tracking.md) and feeds it a gaze point on the screen; the board turns it into POINT
exactly as it does for the head. Same rules as Webcam mode: no fallback to scanning (Auto does
that), and a clench picks the tile highlighted `clench_lookback_ms` before it.
"""

from __future__ import annotations

from core.pointer.webcam import WebcamPointer


class GazePointer(WebcamPointer):
    source = "gaze"
