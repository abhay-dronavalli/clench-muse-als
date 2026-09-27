"""Windows calls the agent needs (ctypes, no extra packages): DPI awareness, the primary display,
clicks and keys through SendInput, window styles for the overlay, and bringing the board forward.

Coordinates are physical pixels: the agent makes itself per-monitor DPI aware before anything else,
so Eyedid's pixels, UI Automation's rectangles and SetCursorPos all agree.

Limits (docs/desktop-control.md, "Safety decisions"): SendInput cannot reach windows running as
administrator or the UAC secure desktop.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from desktop.eyedid.convert import Display

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
VK_MENU, VK_LEFT = 0x12, 0x25
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]


def set_dpi_aware() -> None:
    """Per-monitor v2, before Qt starts (Qt then only notes it is already set)."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)


def primary_display() -> Display:
    """The primary screen: pixels, and millimetres from the monitor's EDID (what GetDeviceCaps reports
    for a DPI-aware process). The laptop's own panel and camera are what Eyedid is set up for."""
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    hdc = user32.GetDC(0)
    try:
        wmm, hmm = gdi32.GetDeviceCaps(hdc, 4), gdi32.GetDeviceCaps(hdc, 6)  # HORZSIZE, VERTSIZE
    finally:
        user32.ReleaseDC(0, hdc)
    if wmm <= 0 or hmm <= 0:  # no EDID: assume a 15.6" 16:9 laptop panel
        wmm, hmm = 344, 193
    return Display(w, h, float(wmm), float(hmm))


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        raise OSError(ctypes.get_last_error(), "SendInput was blocked (an administrator window?)")


def _mouse(flags: int) -> INPUT:
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))


def _key(vk: int, up: bool = False, extended: bool = False) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, 0, flags, 0, 0))


def click(x: float, y: float, button: str = "left", double: bool = False) -> None:
    user32.SetCursorPos(round(x), round(y))
    down, up = ((MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if button == "right"
                else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
    _send(_mouse(down), _mouse(up))
    if double:
        time.sleep(0.05)
        _send(_mouse(down), _mouse(up))


def alt_left() -> None:
    """Back in browsers, File Explorer and Settings (Taher's choice, decisions.md 20)."""
    _send(_key(VK_MENU), _key(VK_LEFT, extended=True), _key(VK_LEFT, up=True, extended=True), _key(VK_MENU, up=True))


def cursor() -> tuple[int, int]:
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


# --- windows --------------------------------------------------------------------------------------

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]


def make_overlay(hwnd: int) -> None:
    """Click-through, never focused, not in the taskbar or Alt+Tab, above other windows."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    keep_on_top(hwnd)


def keep_on_top(hwnd: int) -> None:
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def window_titles() -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def visit(hwnd: int, _l: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(EnumWindowsProc(visit), 0)
    return found


def board_window() -> int | None:
    """The browser window showing the board: its tab title is "Clench" (web/index.html), so the
    window reads "Clench - Google Chrome" / "Clench - Microsoft Edge" / "Clench"."""
    for hwnd, title in window_titles():
        if title == "Clench" or title.startswith("Clench - ") or title.startswith("Clench — "):
            return hwnd
    return None


def focus(hwnd: int) -> bool:
    """Bring a window to the front. Windows only lets the foreground app do that, so tap Alt first
    (the usual way for an assistive tool that is not focused itself)."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _send(_key(VK_MENU), _key(VK_MENU, up=True))
    return bool(user32.SetForegroundWindow(hwnd))
