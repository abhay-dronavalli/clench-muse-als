# Plugging in an eye tracker (the gaze slot)

The board can be pointed at with the eyes. An eye tracker running in the board page feeds a gaze
point into one small interface; the board does the rest exactly as it does for head pointing: the
tile under the point (with sticky edges), POINT to the Core, FACE_OK, the cursor dot. The Core never
sees the video or the gaze, only which tile is looked at (PRD section 11).

Code: `web/src/facetrack/gaze.ts` (the slot), `web/src/facetrack/source.ts` (head and gaze as point
sources), `web/src/facetrack/usePointing.ts` (turns the active source into POINT / FACE_OK).

## The interface

```ts
import { gaze } from '../facetrack/gaze'

// Every frame of your tracker (15 to 60 times a second):
gaze.feed({
  x: 0.42,          // 0..1 across the board window, 0 = left edge   (clamped)
  y: 0.61,          // 0..1 down the board window, 0 = top edge      (clamped)
  found: true,      // the eyes are seen this frame
  confidence: 0.8,  // 0..1, how sure the tracker is about this point
})

// When your tracker stops (camera off, page leaving): gaze is unavailable at once.
gaze.clear()
```

A tracker loaded as a separate script can use the same object as `window.clenchGaze` (also handy from
the browser console: `window.clenchGaze.feed({ x: 0.2, y: 0.3, found: true, confidence: 0.9 })`).

Rules:

- `x`, `y` are fractions of the browser window (`window.innerWidth` / `innerHeight`), not pixels and
  not the camera image. Map your calibrated screen point with `x = px / innerWidth`,
  `y = py / innerHeight`. The tracker owns its calibration (the 9-point regression in
  `kushagra/`); the board's head calibration does not apply to gaze.
- The board smooths gaze itself: a One Euro filter, then the sticky tile edges (`tile_switch_margin`,
  5% by default, dev panel slider), then a 300 ms hold (a new tile must stay the candidate that long
  before the highlight moves). All three are in `tilePointer.ts` and tunable on `/gaze-test` (see
  "Gaze test" below). A tracker may smooth too; measure the combination there.
- A sample counts as seen when `found` is true and `confidence >= 0.5` (`GAZE_MIN_CONFIDENCE`).
  Gaze is "available" while such a sample is at most 500 ms old (`GAZE_STALE_MS`). Keep feeding
  (with `found: false` when the eyes are lost) so the board knows the tracker is alive.
- One tracker per page. If it needs the webcam, note that the board's head tracker also opens it in
  Auto and Webcam mode; browsers share one camera between `getUserMedia` calls in the same page, so
  both work. Reusing the board's MediaPipe Face Landmarker (`tracker.ts`) instead of a second one is
  a possible later step (its landmarks include the irises, 468 to 477).

## What the board does with it

| Pointing mode | Highlight follows | POINT `source` |
|---|---|---|
| Gaze | follows gaze only; scans before detection and after 3 s without eyes, returning to gaze on FACE_OK plus a fresh POINT | `"gaze"` |
| Auto | the gaze while it is available, else the head, else the Core scans (after 3 s without either) | `"gaze"` or `"webcam"` |
| Webcam | the head only (gaze ignored) | `"webcam"` |
| Scan, Head tilt | nothing from the board | none |

- POINT goes out when the looked-at tile changes, once for every new screen, and when the source
  switches between gaze and head. FACE_OK goes out when "seen" changes and holds for 300 ms.
- A clench picks the tile that was highlighted 250 ms before it (`clench_lookback_ms`), as for the
  head: eyes move when the jaw clenches too.
- The dev panel (backtick) has a Gaze mode button and an "Eye tracker: connected / not connected"
  line; the cursor dot shows where the gaze lands.

## Trying it without a tracker

1. Start the core and the web app, open the board, click "Click to start", press backtick, click Gaze.
2. In the browser console:

   ```js
   const r = document.querySelector('[data-tile-index="4"]').getBoundingClientRect()
   const p = { x: (r.left + r.width / 2) / innerWidth, y: (r.top + r.height / 2) / innerHeight }
   const id = setInterval(() => window.clenchGaze.feed({ ...p, found: true, confidence: 0.9 }), 40)
   ```

   Tile 4 (Room) is highlighted; press Space to pick it. `clearInterval(id)` stops the feed: after
   about half a second the board says no eye tracker is connected.

## Gaze pipeline, dwell select, "eyes not detected"

`web/src/facetrack/tilePointer.ts` is the one path from points to a highlighted tile, used by the
board (`usePointing.ts`) and by `/gaze-test`, so the test measures what the board does:

1. One Euro filter (`oneEuro.ts`; on by default for gaze, min cutoff 1 Hz, beta 10 in window units).
   It starts over after a 250 ms gap.
2. `chooseTile` with the sticky margin.
3. Hold: the highlight moves only after the new tile has been the candidate for 300 ms. A glance
   away and back does not move it.
4. Dwell select (off by default; dev panel "Dwell select (gaze)" or `/gaze-test` settings): the same
   highlighted tile for 1.5 s picks it. The board sends CLENCH on `/ws/input`, as the headband does,
   and shows a ring filling over the tile. Dwell only runs on a menu screen that is not loading, so it
   never confirms a message and never touches the help countdown: those still need a clench. It
   fires once, then waits for the highlight to move or a new screen.

The head keeps its old path (smoothed in `tracker.ts`, no hold). Gaze settings are kept in the browser
(`localStorage` `clench.gazeTuning`), not in the Core.

While the gaze drives the highlight and a live tracker reports `found: false`, the board shows a small
"Eyes not detected" / "No se detectan los ojos" badge. A tracker that stops feeding gets the "No eye
tracker connected" notice instead.

## Native shell (Android tablet, `kushagra/tablet`)

The tablet app wraps the board in a WebView and runs the Eyedid SDK, which owns the front camera.

- Page to shell: `window.ClenchNative` (an Android `JavascriptInterface`, `web/src/facetrack/native.ts`):
  `gazeActive()`, `setPointingMode(mode)`, `calibrate(person)`, `person()`, `gazeFilter()`,
  `setGazeFilter(on)`. Also, not about gaze: `speak(id, text, lang, volume)` / `stopSpeaking()`
  (Android's voice for the board's browser speech, answered by a `speech` event), and
  `museAvailable()`, `museStatus()`, `museConnect()`, `museDisconnect()` (the tablet's own Muse
  sensor behind the Muse panel's Connect headband, in the `/api/sensor` status shape).
- The tracker runs only while the page asks for it: the board calls `setPointingMode` with the Core's
  pointing mode whenever SETTINGS arrive (so never before "Click to start"), and `/gaze-test` calls
  it with `gaze` for the gaze source and `off` otherwise. Auto, Webcam and Gaze start the tracker;
  Scan, Head tilt and `off` stop it and give the camera back. Every page load resets it to off. In a
  camera mode the call claims the camera before it returns (`gazeActive()` is true at once), so the
  render that follows never opens the camera for the head.
- The board's red "Camera on" light also shows while the shell's tracker has the camera.
- Shell to page: `window.clenchGaze.feed({ x, y, found, confidence, state })` about 30 times a second,
  with `x`, `y` already fractions of the WebView (the shell subtracts the WebView's position on the
  screen and divides by its size). `state` is Eyedid's tracking state (`SUCCESS`, `GAZE_MISSING`,
  `FACE_MISSING`), shown on `/gaze-test`. And `window.clenchNativeEvent({ type: 'blink' | 'tracker' |
  'calibration', ... })`. Onboarding pairs bilateral blinks (100-750 ms apart) to choose its own
  highlighted option; they never become a Core pick or confirm. Elsewhere blinks are still only
  counted on `/gaze-test`.
- One camera owner: while `ClenchNative.gazeActive()` is true the page never opens the camera. Auto
  and Webcam then follow the gaze (there is no head); Auto still scans after 3 s of lost eyes. The
  shell also denies any camera request from the page while its tracker runs or starts. If the tracker
  cannot start (no key, no network, an auth error), `gazeActive()` turns false, a `tracker` event with
  state `error` tells the page, and the page may use the camera for head pointing.
- Calibration is native: five points, saved per person on the tablet, reloaded at start and checked
  with one target (a miss offers to recalibrate). While calibrating or checking, the shell feeds
  `found: false` with state `CALIBRATING`, so the board holds still. During onboarding only, live
  gaze still reaches the setup options; board pointing and dwell are paused and the Core's
  `onboarding` input guard is active.

### Timed onboarding bridge

- `gazeReady()` reports SDK readiness to calibrate; `gazeActive()` also includes initialization.
- `carPreview(on)` shows a parked car at the centre, with fixed camera height and radius. Gaze x
  controls horizontal orbit speed (centre stops, edges up to 30 degrees/s). No gaze y, vehicle
  pitch, or roll is applied; missing/stale gaze eases the orbit to a stop.
- `cancelCalibration()` stops collection and removes targets/failure prompts when a step is skipped,
  times out, or closes. Older shells without this method do not auto-start timed calibration.
- Native `calibration_progress` events carry `progress` in 0..1 for the setup progress bar. Only
  `calibration` / `finished` means calibration succeeded. During setup the native targets are
  transparent over the car, with the page's instruction and countdown panels still visible.
- Build, install and run: `kushagra/tablet/README.md`, "Board shell with Eyedid gaze".

## Eyedid web (laptop)

On a laptop the board runs VisualCamp's browser eye tracker (npm `seeso`, Eyedid's web SDK from
before the rename) on the webcam: `web/src/facetrack/eyedidWeb.ts`. It feeds the gaze slot like any
tracker, so everything above (One Euro filter, sticky edges, the 300 ms hold, dwell, `/gaze-test`)
applies unchanged.

- It runs in **Auto** and **Gaze** mode only, after "Click to start", when `VITE_EYEDID_WEB_KEY` is
  set in the repo's `.env` (a browser key from https://console.seeso.io, with this origin allowed),
  and never inside the tablet shell (which has its own Eyedid). Webcam mode stays head pointing.
- One camera owner, as in the tablet shell: while it starts or runs it owns the webcam and the head
  tracker stays off (`cameraOwner.ts`). Auto follows the gaze, and after 3 s of lost eyes scans. If
  it cannot start (no network, a refused key, no camera) it gives the camera back and Auto points
  with the head. The dev panel shows `Eyedid web: on / starting / off / error (why)`.
- Network: at start the SDK checks the key at console.seeso.io and downloads its engine (about
  14 MB, then cached by the browser) from cdn.seeso.io. Gaze itself is computed in the browser; no
  video or gaze leaves the laptop.
- The engine is multithreaded WebAssembly, which browsers only allow on a cross-origin isolated
  page, so the web dev server sends `Cross-Origin-Opener-Policy: same-origin` and
  `Cross-Origin-Embedder-Policy: require-corp` (`web/vite.config.ts`). Everything the board loads is
  same-origin and the CDN allows cross-origin loads, so nothing else changes.
- Calibration, the same flow as the tablet shell (`BoardActivity.kt`): when the tracker comes on
  with nothing saved, the board asks "The eye tracker is not calibrated yet: Calibrate now / Later".
  Uncalibrated, the SDK's guess of the screen (a 15.5" screen, camera centered on top, 50 cm away)
  does not reach the whole board. With a calibration saved it is loaded and checked with one dot on a
  random tile (`gazeCheck.ts`, the tablet's `validationPasses`: after 0.8 s, 1.5 s of samples, the
  median must land on that tile); a miss asks "Recalibrate / Try the check again / Keep it". The board
  holds still during both. Five dots, each sampled after a 1 s settle, as on the tablet. Also from the
  dev panel (backtick) > "Calibrate eyes". Saved in this browser (`localStorage`
  `clench.eyedidWeb.calibration`). Recalibrate after moving the laptop or the chair. Full screen (F11)
  keeps the page where the SDK expects it.
- `/gaze-test` runs it too when its source is "Gaze slot".

## Gaze test (`/gaze-test`)

How we judge a tracker. Open `http://localhost:5173/gaze-test` (5174 for your own dev server; the
tablet shell can load it too). It needs no Core.

- A 2x3 grid laid out like the board (3 across in landscape, 2 across in portrait), the raw gaze
  (small gray dot), the filtered point (blue dot) and the highlighted tile (yellow ring).
- Type the person's name, pick the source (Gaze slot, Head, or Mouse to check the page), then
  "Start 10-target test". Each prompt is a blue dashed tile with "Look here"; it is a hit when the
  highlight lands on it within 2 s. Esc stops.
- Results: hit rate, average time to highlight (prompt to highlight, including the 300 ms hold),
  wrong highlights on the way, blinks. Kept per person in this browser, with JSON and CSV export.
  A person passes with a run at 90% or better; the goal is three of the four of us, with the tablet
  mounted at a fixed distance.
- Targets are picked when each prompt starts, never the tile highlighted at that moment (after a
  miss the highlight can rest anywhere) nor the previous target, so no hit is free.
- Settings (locked during a run, so a run has one set of settings): One Euro on/off and its parameters, the Eyedid SDK filter (tablet only), the hold, the
  sticky margin (this page only), and the dwell ring. The board uses the same gaze settings.

Without a tracker, a simulated eye in the browser console checks the page:

```js
setInterval(() => {
  const t = document.querySelector('[data-test-tile].outline-dashed')
  if (!t) return
  const r = t.getBoundingClientRect()
  window.clenchGaze.feed({ x: (r.left + r.width / 2) / innerWidth, y: (r.top + r.height / 2) / innerHeight, found: true, confidence: 1 })
}, 33)
```
