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
MOUSEEVENTF_MOVE, MOUSEEVENTF_WHEEL = 0x0001, 0x0800
VK_ESCAPE, VK_BACK = 0x1B, 0x08
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
VK_RETURN = 0x0D
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


def _mouse(flags: int, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0))


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


def scroll(x: float, y: float, delta: int) -> None:
    """Turn the wheel over (x, y): Windows scrolls the window under the pointer. + = up."""
    user32.SetCursorPos(round(x), round(y))
    _send(_mouse(MOUSEEVENTF_WHEEL, delta))


def press(x: float, y: float) -> None:
    """Hold the left button down at (x, y): a drag starts."""
    user32.SetCursorPos(round(x), round(y))
    _send(_mouse(MOUSEEVENTF_LEFTDOWN))


def move_to(x: float, y: float) -> None:
    """Move the pointer (a held drag follows). The zero move makes apps see a real mouse move."""
    user32.SetCursorPos(round(x), round(y))
    _send(_mouse(MOUSEEVENTF_MOVE))


def release(x: float, y: float) -> None:
    user32.SetCursorPos(round(x), round(y))
    _send(_mouse(MOUSEEVENTF_LEFTUP))


def escape() -> None:
    _send(_key(VK_ESCAPE), _key(VK_ESCAPE, up=True))


def tap(combo: str) -> None:
    """One named key: "escape", "backspace", "enter", or "alt+left"."""
    if combo == "alt+left":
        alt_left()
        return
    vk = {"escape": VK_ESCAPE, "backspace": VK_BACK, "enter": VK_RETURN}[combo]
    _send(_key(vk), _key(vk, up=True))


def alt_left() -> None:
    """Back in browsers, File Explorer and Settings (Taher's choice, decisions.md 20)."""
    _send(_key(VK_MENU), _key(VK_LEFT, extended=True), _key(VK_LEFT, up=True, extended=True), _key(VK_MENU, up=True))


def text_keys(text: str) -> list[tuple[int, int, int]]:
    """`text` as (virtual key, UTF-16 unit, flags) key events: each character as a Unicode key press
    and release (any language, accents included; one outside the BMP is two units), Enter for a
    newline."""
    keys: list[tuple[int, int, int]] = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            keys += [(VK_RETURN, 0, 0), (VK_RETURN, 0, KEYEVENTF_KEYUP)]
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i:i + 2], "little")
            keys += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    return keys


def type_text(text: str, gap_s: float = 0.005) -> None:
    """Type `text` into the focused window, one character per SendInput call, `gap_s` apart. Sent as
    one batch, Qt put an emoji (two UTF-16 units) before the text in front of it (checked
    2026-09-27); one character at a time arrives in order everywhere tried."""
    keys = text_keys(text)
    i = 0
    while i < len(keys):
        # one character: a newline or a BMP character is 2 events, one outside the BMP is 4
        n = 4 if keys[i][0] == 0 and 0xD800 <= keys[i][1] <= 0xDBFF else 2
        _send(*(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, flags, 0, 0)) for vk, scan, flags in keys[i:i + n]))
        i += n
        if gap_s:
            time.sleep(gap_s)


def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


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
