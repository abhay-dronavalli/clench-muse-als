# Desktop control: eyes and jaw for all of Windows

Status: plan (branch `desktop-control`). Decision log: `docs/decisions.md` section 20.

Today the Eyedid gaze and the Muse gestures only work inside the board tab. The goal is for the
same two inputs to drive all of Windows. The eyes point, a clench clicks, and a double blink then a
clench goes back. The board stays as the place to talk, text, call and ask for help. The person
switches between the board and the rest of Windows with the same gestures.

## What stays the same

- The Core owns every decision about gestures and safety. The help alert (LONG_CLENCH, the 5 s
  countdown, DOUBLE_BLINK cancels) works in desktop mode exactly as on the board, never depends on
  the AI, and is never passed on to the desktop.
- The app runs with no headband: the desktop agent has its own keyboard stand-in (global hotkeys)
  that sends the same gestures.
- Video never leaves the laptop, and gaze never goes to the Core. The Core only sees gestures.

## Architecture

```
Muse ─► sensor.main ─► Core /ws/sensor ─┐
                                        ├─► Session (help, back prompt, input_target)
agent hotkeys (F8 / F9) ─► Core /ws/desktop
                                        │  input_target = "desktop"
                                        ▼
                     /ws/desktop: DESKTOP_INPUT (CLENCH, DOUBLE_BLINK), SCREEN, SETTINGS
                                        │
                        desktop/agent (Python, one process)
   gaze source ─► One Euro filter ─► 2 s trail (clench look-back)
   targeting: UI Automation snap ─► zoom when unsure
   effectors: SendInput click / right / double / Alt+Left
   overlay: PySide6, topmost, click-through, never takes focus
   gaze bridge ws://127.0.0.1:8766 ─► board (one camera owner)
                                        ▲ JSON lines (stdout / stdin)
                     desktop/eyedid: clench-gaze.exe (C++)
   OpenCV camera ─► Eyedid addFrame ─► CoordConverterV2 ─► screen pixels
```

Why Python plus a small C++ program (asked 2026-09-27: "whatever's most reliable and easiest"):

- Eyedid for Windows has only a C++ API. The C++ program does only what needs C++: open the
  camera, run Eyedid, and run the calibration callbacks. It writes JSON lines to stdout and reads
  commands on stdin. It is small, easy to replace, and easy to fake in tests.
- Everything else is Python, like the rest of the repo, and tested with pytest. Windows calls go
  through `ctypes` (SendInput, DPI awareness, hotkeys). UI Automation uses `comtypes`. The overlay
  uses PySide6, which supports transparent, click-through, topmost windows. The supervisor copies
  `core/sensor_service.py`.

## Input model (asked 2026-09-27)

| Gesture | Desktop mode |
|---|---|
| Look | The overlay draws a soft ring at the filtered gaze. The nearest clickable element under it (UI Automation) gets a highlight box, like a board tile. |
| CLENCH | **Snap, then zoom.** If one element is clearly the nearest (at least 1.5x closer than the next, the same rule as ranking stability), click its center. Otherwise open a 3x magnified view of the area; look at the target in it, then clench again to click the matching real point. The click uses the gaze from 250 ms before the clench (`clench_lookback_ms`). |
| DOUBLE_BLINK | Inside the agent's own zoom or dock mode: step back at once (harmless). Anywhere else: open a "Go back?" prompt for 3 s; a CLENCH within it sends **Alt+Left**; doing nothing does nothing. The rule and timing are the same as the board's (decisions 17). |
| LONG_CLENCH | Help countdown in the Core, drawn full-screen by the overlay and spoken by the board tab. |

The dock is a slim column of large targets on the right edge: Right click, Double click, Scroll,
Drag, Keyboard, Board. Look at a target and clench to arm it. The next clench then does that
action instead of a left click, and the dock goes back to left click. Scroll stays armed until you
double blink: looking at the upper or lower third of the window under the gaze scrolls it. Board
switches the input target back to the board and brings the board window to the front.

No clicks without eyes: a CLENCH when the gaze is lost, stale, or calibrating shows "Eyes not
detected" and does nothing.

## Safety decisions to confirm

1. **Clicks have no confirm step.** Hard rule 1 ("nothing is spoken or sent without a confirm
   step") covers what Clench itself says and sends. A desktop click is direct manipulation, like a
   mouse click, and asking to confirm every click would make the computer unusable. The zoom is the
   extra step whenever the target is not certain. Text that Clench composes and types into another
   app ("Type this", chunk 5) still goes through the board's confirm screen.
2. **Pause key:** F10 (and a dock target) pauses the agent at once. No clicks until it resumes.
3. **Windows limits:** SendInput cannot reach elevated windows (Task Manager, installers run as
   admin) or the UAC secure desktop. The overlay cannot be drawn above the Start menu without
   `uiAccess` (a signed exe in Program Files). Both are documented as limits, not worked around.

## Chunks

Each chunk ends with tests, a decisions entry, a commit, and a CHUNK REPORT.

1. **Core routing** (`core/`, contracts). SETTINGS gets `input_target: "board" | "desktop"`. A new
   `/ws/desktop` route accepts SETTINGS and the stand-in gestures, and receives SETTINGS, SCREEN,
   ACTION_RESULT and the new DESKTOP_INPUT. In desktop mode the session goes home and stops
   scanning. CLENCH and DOUBLE_BLINK go to the agent. LONG_CLENCH still starts help, and during
   help DOUBLE_BLINK still cancels it. Muse gestures need a consumer for the current target: the
   board, or the agent in desktop mode. If the agent disconnects in desktop mode, the target goes
   back to the board. Tests cover every case, including help in desktop mode.
2. **Agent skeleton, mouse as gaze** (`desktop/agent/`). Core client with reconnect. Gaze sources:
   mouse (for development) and replay. One Euro filter and a look-back trail. Overlay with the gaze
   ring, the help countdown and the back prompt. Left click and Alt+Left via SendInput. Hotkeys:
   F8 tap = CLENCH, F8 hold `long_clench_ms` = LONG_CLENCH, F9 = DOUBLE_BLINK, F10 = pause. By the
   end, the full loop works with no SDK and no headband.
3. **Snap and zoom.** A UI Automation worker thread finds clickable elements near the gaze, with a
   per-window cache. The snap chooser is a pure function tested on rectangles. The zoom view uses
   an `mss` capture. The dock gets Right click, Double click and Board.
4. **Eyedid sidecar** (`desktop/eyedid/`, C++). CMake project against the SDK in the git-ignored
   `desktop/eyedid/third_party/`. It speaks JSON lines: `gaze`, `state`, `calib_point`,
   `calib_progress`, `calib_done` out; `start`, `stop`, `calibrate`, `calib_next`,
   `set_calibration` in. Calibration uses 5 points drawn by the overlay, one check dot, and saves
   per person in the git-ignored `data/desktop/`. A fake sidecar (`desktop/fake_gaze.py`) speaks
   the same protocol for tests.
5. **Board integration.** One camera owner: while the agent runs, the board takes gaze from the
   agent's bridge instead of starting Eyedid web (the same rule as `ClenchNative` on the tablet).
   A "Use the computer" item in Room and a dev panel toggle. A board banner shows while desktop
   mode is on. New action `type_text`: compose with the AI, confirm, and the agent types the text
   into the window that had focus.
6. **Scroll, drag, keyboard, packaging.** Scroll and drag from the dock. A large-key gaze keyboard
   with word prediction. An AppBar so maximized windows leave room for the dock. A
   `scripts/start_desktop.ps1` launcher, and later PyInstaller.

## What the human does (Eyedid SDK)

The C++ build tools are already on this laptop: VS 2022 Build Tools with MSVC 14.44, Windows SDK
10.0.26100, and CMake 4.0.

1. Sign in at https://manage.eyedid.ai with the account that has the dev license key. Download
   the **Windows C++ SDK** (latest, 1.0.0-beta or newer).
2. Unzip it so the SDK's own `CMakeLists.txt` is at `desktop/eyedid/third_party/eyedid/CMakeLists.txt`
   (git-ignored).
3. Check in the console that the key is valid for the Windows / C++ SDK. Web keys are tied to an
   origin and may not work here. Then add it to `.env` yourself:
   `EYEDID_DESKTOP_KEY=...`. Agents never edit `.env`.

Chunks 1 to 3 do not need the SDK: they run with mouse-as-gaze.
