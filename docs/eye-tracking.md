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
- Smooth in the tracker (the prototype's Savitzky-Golay filter is fine); the board adds no smoothing
  to gaze, only the sticky tile edges (`tile_switch_margin`, 5% by default, dev panel slider).
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
