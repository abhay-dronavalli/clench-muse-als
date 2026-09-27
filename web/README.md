# web/

Patient board and caregiver console (React + TypeScript + Vite + Tailwind). See the root `CLAUDE.md` for how to run.

- `src/contracts.ts` event formats shared with `core/contracts.py` (keep in sync, see `docs/contracts.md`).
- `src/board/` patient screen: big tiles, highlight, confirm screen.
- `src/facetrack/` MediaPipe Face Landmarker in the browser, head angle to a point on the screen to a tile
  (Webcam and Auto pointing modes), head-range calibration; `*.test.ts` are vitest unit tests (`npm test`).
- `scripts/mediapipe-assets.mjs` copies the MediaPipe wasm and downloads the face model into
  `public/mediapipe/` (git-ignored) after `npm install` and before `dev` / `build`, so nothing loads from a CDN.
- `src/console/` caregiver screen and live signal chart.
- `src/dev/` keyboard stand-in dev panel: sends CLENCH / DOUBLE_BLINK / LONG_CLENCH over the WebSocket so the app runs with no headband.
