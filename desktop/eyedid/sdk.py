"""ctypes binding to eyedid_core.dll (the SDK's C API, `include/eyedid/framework/c_api.h`).

Only what the worker needs. Structs mirror `c_def.h` for Windows (enums are 32-bit ints). The C++
signature of EyedidTrackerStartCalibration takes its two enums by reference, which on x64 is a
pointer, so they are passed with ctypes.byref. Callbacks run on the SDK's threads.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import POINTER, c_char_p, c_float, c_int, c_int32, c_int64, c_uint32, c_uint64, c_void_p
from pathlib import Path

SDK_DIR = Path(__file__).resolve().parent / "third_party" / "eyedid"

TRACKING_STATES = {0: "SUCCESS", 1: "FACE_MISSING", 2: "GAZE_NOT_FOUND"}
MOVEMENT_STATES = {0: "fixation", 2: "saccade", 3: "unknown"}


class TrackerOptions(ctypes.Structure):
    _fields_ = [
        ("use_blink", c_int),
        ("use_user_status", c_int),
        ("use_gaze_filter", c_int),
        ("stream_mode", c_int),
        ("camera_fov", c_float),
        ("max_concurrency", c_int),
    ]


class GazeData(ctypes.Structure):
    _fields_ = [
        ("x", c_float),
        ("y", c_float),
        ("fixation_x", c_float),
        ("fixation_y", c_float),
        ("tracking_state", c_int),
        ("movement_state", c_int),
    ]


class FaceData(ctypes.Structure):
    _fields_ = [(name, c_float) for name in (
        "score", "left", "top", "right", "bottom", "yaw", "pitch", "roll", "center_x", "center_y", "center_z")]


class BlinkData(ctypes.Structure):
    _fields_ = [
        ("is_blink", c_int),
        ("is_blink_left", c_int),
        ("is_blink_right", c_int),
        ("left_openness", c_float),
        ("right_openness", c_float),
    ]


class UserStatusData(ctypes.Structure):
    _fields_ = [("is_drowsy", c_int), ("drowsiness_intensity", c_float), ("attention_score", c_float)]


class EyedidData(ctypes.Structure):
    _fields_ = [("gaze", GazeData), ("face", FaceData), ("blink", BlinkData), ("user_status", UserStatusData)]


OnMetrics = ctypes.CFUNCTYPE(None, c_void_p, c_uint64, POINTER(EyedidData))
OnDrop = ctypes.CFUNCTYPE(None, c_void_p, c_uint64)
OnCalibrationNextPoint = ctypes.CFUNCTYPE(None, c_void_p, c_float, c_float)
OnCalibrationProgress = ctypes.CFUNCTYPE(None, c_void_p, c_float)
OnCalibrationData = ctypes.CFUNCTYPE(None, c_void_p, POINTER(c_float), c_uint32)  # finish and cancel


def load(sdk_dir: Path = SDK_DIR) -> ctypes.CDLL:
    """Load eyedid_core.dll and declare the functions the worker uses. Raises FileNotFoundError with
    what to do when the SDK is not unzipped."""
    core = sdk_dir / "bin" / "eyedid" / "eyedid_core.dll"
    if not core.is_file():
        raise FileNotFoundError(f"{core} is missing: unzip the Eyedid Windows SDK (docs/desktop-control.md)")
    os.add_dll_directory(str(sdk_dir / "bin" / "third-parties"))  # opencv_world410, libcurl, tbb12
    os.add_dll_directory(str(core.parent))
    dll = ctypes.CDLL(str(core))
    sig = {
        "EyedidVersionString": (c_char_p, []),
        "EyedidTrackerCreate": (c_void_p, [c_char_p, c_uint32]),
        "EyedidTrackerDelete": (None, [c_void_p]),
        "EyedidTrackerGetAuthorizationResult": (c_int, [c_void_p]),
        "EyedidTrackerInit": (None, [c_void_p, POINTER(TrackerOptions)]),
        "EyedidTrackerDeInit": (None, [c_void_p]),
        "EyedidTrackerInitialized": (c_int, [c_void_p]),
        "EyedidTrackerSetFPS": (None, [c_void_p, c_int32]),
        "EyedidTrackerSetFaceDistance": (None, [c_void_p, c_int32]),
        "EyedidTrackerSetTargetBoundRegion": (None, [c_void_p, c_float, c_float, c_float, c_float]),
        "EyedidTrackerAddFrame": (c_int, [c_void_p, c_int64, c_void_p, c_int32, c_int32]),
        "EyedidTrackerStartCalibration": (
            None, [c_void_p, POINTER(c_int), POINTER(c_int), c_float, c_float, c_float, c_float, c_int]),
        "EyedidTrackerStartCollectSamples": (None, [c_void_p]),
        "EyedidTrackerStopCalibration": (None, [c_void_p]),
        "EyedidTrackerSetCalibrationData": (None, [c_void_p, POINTER(c_float), c_uint32]),
        "EyedidTrackerSetMetricsCallback": (None, [c_void_p, OnMetrics, OnDrop]),
        "EyedidTrackerSetCalibrationCallback": (
            None, [c_void_p, OnCalibrationNextPoint, OnCalibrationProgress, OnCalibrationData, OnCalibrationData]),
        "EyedidTrackerSetCallbackUserData": (None, [c_void_p, c_void_p]),
        "EyedidTrackerRemoveCallbackInterface": (None, [c_void_p]),
    }
    for name, (restype, argtypes) in sig.items():
        fn = getattr(dll, name)
        fn.restype = restype
        fn.argtypes = argtypes
    return dll
