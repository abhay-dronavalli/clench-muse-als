# web/

Patient board and caregiver console (React + TypeScript + Vite + Tailwind). See the root `CLAUDE.md` for how to run.

- `src/contracts.ts` event formats shared with `core/contracts.py` (keep in sync, see `docs/contracts.md`).
- `src/board/` patient screen: big tiles, highlight, confirm screen.
- `src/facetrack/` MediaPipe Face Landmarker, head angle to tile (Webcam pointing mode).
- `src/console/` caregiver screen and live signal chart.
- `src/dev/` keyboard stand-in dev panel: sends CLENCH / DOUBLE_BLINK / LONG_CLENCH over the WebSocket so the app runs with no headband.
