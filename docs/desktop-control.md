# Desktop control: eyes and jaw for all of Windows

Status: chunks 1 to 4 built (branch `desktop-control`), chunks 5 and 6 to do. Decision log:
`docs/decisions.md` section 20.

The Eyedid gaze and the Muse gestures used to work only inside the board tab. Now they also drive
all of Windows: the eyes point, a clench clicks, and a double blink then a clench goes back. The
board stays the place to talk, text, call and ask for help. The person switches between the board
and the rest of Windows with the same gestures.

## Run it

```powershell
uv sync --extra desktop --extra sensor        # once: PySide6, opencv-python, comtypes, websockets
uv run uvicorn core.main:app --port 8000      # the Core (the demo machine already runs it)
uv run --extra desktop python -m desktop.agent --person taher                  # Eyedid gaze
uv run --extra desktop python -m desktop.agent --gaze mouse                    # the mouse stands in for the eyes
uv run --extra desktop python -m desktop.agent --url ws://127.0.0.1:8001/ws/desktop   # a test Core
```

On start the agent asks the Core to send gestures to the desktop (`input_target: desktop`). The
first run with Eyedid has no calibration: press **F7** (or open the Clench tab > Calibrate eyes) and
follow the five dots. The calibration is saved per person and per screen in
`data/desktop/eyedid.<person>.json` and loaded at the next start. Recalibrate after moving the laptop
or the chair. Close the agent with Ctrl+C in its terminal.

| Key (global, swallowed) | Does |
|---|---|
| F8 tap / hold 2.5 s | CLENCH / LONG_CLENCH (help), sent to the Core like the headband's |
| F9 | DOUBLE_BLINK |
| F7 | calibrate the eyes |
| F10 | pause / resume clicks |
| Ctrl+F8 | switch the input target: desktop <-> Clench board (brings the board window forward) |

Smoothing: Savitzky-Golay (a quadratic fit over the last 500 ms, read 80 ms back), then One Euro.
Steadier: `--sg-window-ms 600 --sg-lag-ms 100`. Faster: `--sg-window-ms 300 --sg-lag-ms 40`. Off:
`--sg-window-ms 0`.

The headband works the same once Muse input is enabled (Muse panel on the board or console): in
desktop mode its gestures need the agent connected, not a board.

## What stays the same

- The Core owns every decision about gestures and safety. The help alert (LONG_CLENCH, the 5 s
  countdown, DOUBLE_BLINK cancels) works in desktop mode exactly as on the board, never depends on
  the AI, and is never passed on to the desktop. The overlay draws the countdown full-screen.
- The app runs with no headband: the agent's F8 / F9 keys send the same gestures.
- Video never leaves the Eyedid worker process, and gaze never goes to the Core. The Core only sees
  gestures.

## Architecture

```
Muse ─► sensor.main ─► Core /ws/sensor ─┐
                                        ├─► Session (help, input_target)
agent keys (F8 / F9) ─► Core /ws/desktop
                                        │  input_target = "desktop"
                                        ▼
                     /ws/desktop: DESKTOP_INPUT (CLENCH, DOUBLE_BLINK), SCREEN, SETTINGS
                                        │
                        desktop/agent (Python, one process, Qt thread at 60 fps)
   gaze ─► Savitzky-Golay (500 ms, order 2) ─► One Euro ─► highlight trail (clench look-back 250 ms)
   UI Automation (own thread): clickable elements near the gaze ─► snap, or zoom when unsure
   SendInput: left / right / double click, Alt+Left
   overlay: PySide6, full screen, topmost, click-through, never focused
                                        ▲ JSON lines on stdout / commands on stdin
                     desktop/eyedid/worker.py (its own process)
   OpenCV camera 640x480 ─► eyedid_core.dll (C API via ctypes) ─► mm ─► screen pixels
```

Why no C++ after all: the SDK's `eyedid_core.dll` exports a plain C API (`c_api.h`). The worker calls
it with `ctypes`, so there is nothing to compile and no OpenCV C++ build. It still runs as its own
process, so an SDK crash never takes the agent down, and it speaks the same JSON-lines protocol the
plan described (see the docstring of `desktop/eyedid/worker.py`). The SDK's own C++ wrapper converts
the camera millimetres to screen pixels with a 2x2 transform; `desktop/eyedid/convert.py` does the
same, tested against its formula.

## Input model (asked 2026-09-27)

| Gesture | Desktop mode |
|---|---|
| Look | A small dot at the filtered gaze. The nearest clickable element (UI Automation) gets a yellow box when it is clearly the one; otherwise a dashed blue circle with a magnifier says "a clench zooms here". |
| CLENCH | **Snap, then zoom.** A yellow box: click its center. A blue circle: open a 3x magnified picture of the area; look at the target in it (snapped when sure, else a crosshair on the exact point) and clench again to click there. A double blink closes the zoom. The click uses the highlight from 250 ms before the clench. |
| DOUBLE_BLINK | In the zoom or the palette, or with a right / double click armed: straight back (harmless). Otherwise "Go back?" for 3 s: a CLENCH sends **Alt+Left**; doing nothing does nothing, and clenches are then ignored for 1 s (decisions 17). |
| LONG_CLENCH | Help countdown in the Core, drawn full-screen by the overlay and spoken by the board tab. |

The calibration dots sit at the centre and 14 mm in from each corner (the SDK puts them on the
corners of the area it is given, so the agent insets that area).

The **Clench tab** sits on the right edge. Looking at it and clenching opens the **palette**: six
big tiles in the middle of the screen (Right click, Double click, Clench board, Calibrate eyes,
Pause / Resume clicks, Close), the nearest tile highlighted as on the board. Right click and Double
click arm the next click only. Clench board switches the input target and brings the board
window forward. A palette in the middle of the screen replaced the planned dock column: a column on
the edge would cover scrollbars of maximized windows, and big centered tiles are easier to hit.

Snap rule: with d1 <= d2 the distances from the gaze to the two nearest elements, the nearest is
sure when d2 - d1 >= 8 mm and d2 >= 1.5 x d1 (webcam gaze is off by 1 to 2 cm, more than the gap
between toolbar buttons, so dense toolbars zoom and isolated buttons click at once). Elements come
from UI Automation: the finder asks what is under 17 points around the gaze (ElementFromPoint) and
climbs to the nearest button, link, list item, menu item, tab, checkbox, edit box and similar
(about 70 to 100 ms per look-up). Anything UI Automation cannot see is still clickable through the
zoom.

No clicks without eyes: a CLENCH while the gaze is lost or stale says "Eyes not detected" and does
nothing. No clicks while paused, while calibrating, during the help countdown, or while the board
is the input target.

## Safety decisions

1. **Clicks have no confirm step.** Hard rule 1 ("nothing is spoken or sent without a confirm
   step") covers what Clench itself says and sends. A desktop click is direct manipulation, like a
   mouse click, and asking to confirm every click would make the computer unusable. The zoom is the
   extra step whenever the target is not certain. Text that Clench composes and types into another
   app ("Type this", chunk 5) will still go through the board's confirm screen. Taher went ahead
   with the plan as written (2026-09-27).
2. **Pause:** F10, or the palette's Pause clicks. The palette still opens while paused, to resume.
3. **Windows limits:** SendInput cannot reach elevated windows (Task Manager, installers run as
   admin) or the UAC secure desktop. The overlay cannot be drawn above the Start menu without
   `uiAccess` (a signed exe in Program Files). Both are documented as limits, not worked around.

## Chunks

1. **Core routing** (done). SETTINGS `input_target`, `/ws/desktop`, DESKTOP_INPUT; help unchanged.
2. **Agent** (done). Core link with reconnect, mouse-as-gaze, One Euro filter, look-back trail,
   overlay, SendInput, F7 to F10 keys, pause.
3. **Snap and zoom** (done). UI Automation finder thread, the pure snap chooser, the zoom picture
   (Qt screen grab with the overlay hidden for 90 ms), the palette.
4. **Eyedid** (done). The ctypes worker, supervised with restarts (it gives up after three failed
   starts, e.g. a refused key); calibration with five dots drawn by the overlay, saved per person
   and screen.
5. **Board integration** (to do). One camera owner: while the agent runs, the board takes gaze from
   the agent instead of starting Eyedid web (the rule `ClenchNative` follows on the tablet). A "Use
   the computer" item in Room and a dev panel toggle; a board banner while desktop mode is on. New
   action `type_text`: compose with the AI, confirm, and the agent types it into the window that
   had focus. A calibration check dot, as on the board.
6. **Scroll, drag, keyboard, packaging** (to do). Scroll and drag from the palette. A large-key
   gaze keyboard with word prediction. A `scripts/start_desktop.ps1` launcher, and later PyInstaller.

## The Eyedid SDK

The SDK is licensed and not in git. Unzip `Eyedid.Windows_1_0_0_beta_3.zip` (from
https://manage.eyedid.ai) into `desktop/eyedid/third_party/` so that
`desktop/eyedid/third_party/eyedid/bin/eyedid/eyedid_core.dll` exists. The Windows / C++ license key
goes in `.env` as `EYEDID_DESKTOP_KEY` (agents never edit `.env`). The key is checked online at every
start; the SDK also prints its own log, which the worker sends to its stderr (the agent keeps the
last lines).

Screen size: the worker is told the primary screen's pixels and millimetres (from the monitor's
EDID, 1920 x 1080 and 344 x 193 mm on the demo laptop) and assumes the camera is centered above it.
The camera opens with DirectShow first (about 1 s here; Media Foundation took 12 s).

## Tests

`tests/test_desktop_routing.py` (Core side), `tests/test_desktop_agent.py` (coordinates, filter,
look-back, snap, zoom, the whole gesture state machine), `tests/test_desktop_calibration.py`
(calibration flow and storage, the F8 / F9 stand-in). They need no Qt, no SDK and no camera.
