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
| Gaze | the gaze only. No fresh gaze = not seen (FACE_OK false), the highlight stays put and the board says "No eye tracker connected" | `"gaze"` |
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
  `gazeActive()`, `calibrate(person)`, `person()`, `gazeFilter()`, `setGazeFilter(on)`.
- Shell to page: `window.clenchGaze.feed({ x, y, found, confidence, state })` about 30 times a second,
  with `x`, `y` already fractions of the WebView (the shell subtracts the WebView's position on the
  screen and divides by its size). `state` is Eyedid's tracking state (`SUCCESS`, `GAZE_MISSING`,
  `FACE_MISSING`), shown on `/gaze-test`. And `window.clenchNativeEvent({ type: 'blink' | 'tracker' |
  'calibration', ... })`. Blinks are only counted on `/gaze-test`; nothing is picked with them.
- One camera owner: while `ClenchNative.gazeActive()` is true the page never opens the camera. Auto
  and Webcam then follow the gaze (there is no head); Auto still scans after 3 s of lost eyes.

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
- Settings: One Euro on/off and its parameters, the Eyedid SDK filter (tablet only), the hold, the
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
