# AGENTS.md: Clench

Instructions for every coding agent working in this repo (Claude Code, Codex, Cursor, Muse Code, and
others). Read this whole file before you change anything.

## What Clench is

Clench lets a person who cannot move or speak (for example, someone with late-stage ALS) talk,
message their family, and control their room with jaw clenches and blinks, read by a Muse 2 EEG
headband. A board shows up to six big tiles; a highlight moves across them (Scan, Webcam head
turns, Gaze from an eye tracker, or Head tilt). A clench picks, a double blink goes back, a long clench calls for help. After
two or three picks, an AI (Gemini first) writes the full sentence in English or Spanish; the person
confirms with one more clench, and the laptop speaks it, texts it (Telegram), or places a call
(Twilio). It is a communication prototype for ShellHacks 2026, not a medical device.

## Sources of truth, in order

1. `docs/Clench_PRD.pdf`: the PRD. If it is unclear, pick what fits it best and log the decision.
2. `core/contracts.py`, `web/src/contracts.ts`, `docs/contracts.md`: event formats.
3. `docs/decisions.md`: every change from the PRD. Read it before assuming the PRD still applies.
4. This file.

Human setup and service details also live in `docs/RUNBOOK.md`.

## Repo map

| Path | What lives there |
| --- | --- |
| `sensor/` | Python Sensor Service. BrainFlow (synthetic, replay, Muse 2) to clean events: CLENCH, DOUBLE_BLINK, LONG_CLENCH, STATE, SIGNAL. Sends events, never raw data. |
| `core/` | Python FastAPI Core Server. All decisions live here: session state machine (`session.py`, owns the highlight, SCREEN `seq`, clench look-back, and help countdown), menus (`menu.py`), pointers (`pointer/`), actions (`actions/`), AI (`suggest/`), ranking (`rank/`), metrics (`metrics.py`), voice (`voice.py`), SQLite (`db.py`), config (`config.py`), broadcast (`hub.py`), injectable timers (`clock.py`). |
| `web/` | React + TypeScript + Vite + Tailwind. Patient Board (`src/board/`), Caregiver Console (`/console`), webcam face tracking (`src/facetrack/`), keyboard stand-in (`src/dev/DevPanel.tsx`), socket hook (`src/lib/useSocket.ts`). |
| `data/` | `menu.yaml`, `contacts.yaml`, `profile.yaml`, `seed_demo_week.json`, `recordings/` (CSVs git-ignored), `clench.db` and `audio_cache/` (git-ignored). |
| `docs/` | The PRD and project docs: `contracts.md` (event formats), `decisions.md` (running log of every change from the PRD), `RUNBOOK.md` (human setup). |
| `scripts/` | One-off tools. Every script only prints what it would do unless you pass `--send`. |
| `tests/` | pytest tests for `core/` and `sensor/`. |

Components talk over WebSocket with small JSON events: `/ws/board`, `/ws/console`, `/ws/input`.
Only the AI, voice, and texting services reach the internet. The keyboard stand-in sends the same
events as the headband, so the Core cannot tell them apart.

**Changes from the PRD** that shape the code most (full log in `docs/decisions.md`): the keyboard
stand-in is a web dev panel (`web/src/dev/`), not a Python `KeyboardSource`; there is no SMS —
messages go through a Telegram bot and calls through Twilio Programmable Voice (the action is
`send_message`).

### Key files

- `core/main.py`: FastAPI app, WebSocket routes, `GET` / `PUT /api/head-range`
- `core/session.py`: state machine; owns the highlight, SCREEN `seq`, clench look-back, help countdown
- `core/computer/`: managed Chromium, page bridge and overlay, band/target scanning, navigation policy
- `core/pointer/`: Pointer interface; `scan.py`, `webcam.py` follows POINT, `gaze.py` follows gaze
  POINTs, `auto.py` gaze or head while the person is seen and scan after 3 s without, `headtilt.py`
  scans until the sensor chunk
- `core/menu.py`: loads `data/menu.yaml` + `data/contacts.yaml`
- `core/profile.py`: `data/profile.yaml`
- `core/actions/`: speak, send_message via Telegram, place_call via Twilio Voice, room_control mock
- `core/suggest/`: `provider.py` LLMProvider protocol and validated models, `prompts.py`, `gemini.py`,
  `fake.py` (offline), `stubs.py` (claude/openai placeholders), `service.py` Suggester with 4 s
  timeout, 10 min cache, fallback, and the one-request-per-level `level_bundle`
- `core/rank/`: `score.py` (PRD section 9 score and stability rule), `history.py`, `ranker.py`,
  `jev.py` (optional TypeSafe Jev prior)
- `core/metrics.py`: clenches and scan steps per message, Day 1 cost
- `core/voice.py`: ElevenLabs TTS, disk audio cache, circuit breaker, prewarm, browser-speech fallback
- `core/db.py`: SQLite events, phrases, audio_cache, calibrated head range on the profile
- `web/src/board/`: patient board; audio player and browser speech in `speech.ts` with the in-order
  sound queue in `queue.ts`; the "Other..." click; toasts; help countdown; suggestion / "Other..." /
  loading tiles in `views.tsx`
- `web/src/facetrack/`: webcam and gaze pointing — `tracker.ts` (camera + MediaPipe Face
  Landmarker), `pose.ts`, `tiles.ts` (sticky tile choice), `calibrate.ts` + `CalibrationOverlay.tsx`,
  `source.ts` (pluggable point sources: head, gaze), `gaze.ts` (the gaze slot an eye tracker feeds,
  see `docs/eye-tracking.md`), `usePointing.ts` (POINT / FACE_OK), `indicators.tsx`; `*.test.ts` vitest
- `web/scripts/mediapipe-assets.mjs`: puts the MediaPipe wasm and model in `web/public/mediapipe/`
- `web/src/dev/DevPanel.tsx`: keyboard stand-in; settings the Core reports; pointing mode; camera
  preview in `CameraPreview.tsx`; Day 1 toggle; METRICS and SHORTCUT_DEBUG lines; Reset to Home
- `scripts/seed_demo.py` + `data/seed_demo_week.json`: the simulated demo week

## Commands (PowerShell, from the repo root)

```powershell
# One-time setup
uv sync                                   # creates .venv with Python deps (incl. dev: pytest)
uv run playwright install chromium        # first-time computer-mode browser download
Copy-Item .env.example .env               # then fill in keys; .env is git-ignored (runs fine left empty)
npm --prefix web ci                       # also copies the MediaPipe wasm and downloads the face model (~4 MB)
                                          # (npm 10.9's `npm --prefix web install` fails with ENOENT; ci or cd web works)

# Tests (must pass before every commit; web checks only if you touched web/)
uv run pytest                             # Python tests
uv run python -c "import core.contracts"
npm --prefix web test                     # vitest: head pose, sticky tiles, calibration
npm --prefix web run build                # type-check + production build
npm --prefix web run lint

# YOUR testing only — never 8000 / 5173 (see "Ports" below)
uv run uvicorn core.main:app --port 8001
$env:CORE_URL = 'http://127.0.0.1:8001'; npm --prefix web run dev -- --port 5174   # proxy to 8001, not 8000
```

Without uv: `python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install fastapi "uvicorn[standard]" "pydantic>=2" pyyaml python-dotenv pytest httpx`, then `python -m pytest`.

The demo machine runs the core on http://127.0.0.1:8000 (`/health`) and the web app on
http://localhost:5173 (board) and http://localhost:5173/console. `/ws/*`, `/api/*`, and `/audio/*`
are proxied to the core. Do not start, stop, or restart those processes. Sensor service is not built
yet (planned: `uv run python -m sensor.main`).

The core loads `data/menu.yaml`, `data/contacts.yaml`, and `data/profile.yaml` at startup and refuses
to start on a bad file. A menu level holds at most 5 items; the core adds "Other..." as the sixth
tile on every level. `--reload` only watches `.py` files; restart the core after editing the YAML.
Every pick, confirmed send, cancelled confirm, and help alert is written to `data/clench.db` (delete
the file to start fresh). The board starts in the profile's language (Spanish for Luis).

Without internet at `npm ci` time, run `npm --prefix web run assets` later; until then the board
says face tracking could not start and Auto scans.

## Hard rules

1. **Nothing is spoken or sent without a confirm step** (PRD D5). The help countdown is the confirm
   for help. Speak-picks (echoing each picked tile's label at 70% volume) is allowed; the confirmed
   sentence is still only said after the confirm clench.
2. **The emergency path must never depend on the AI.** LONG_CLENCH and the help countdown must work
   with Gemini missing, slow, or failing.
3. **The app must always run with no headband.** The keyboard stand-in is the fallback for the demo.
4. **Contracts change together.** Any change to an event format updates `core/contracts.py`,
   `web/src/contracts.ts`, and `docs/contracts.md` in the same commit, and you call it out in your
   CHUNK REPORT so other agents can rebase.
5. **No secrets in git.** Use `.env`; keep `.env.example` updated when you add a variable.
6. **Only modify files inside this repo.**
7. **Tests pass before every commit.** Never delete, skip, or weaken a test to make it pass. If a test
   is wrong, fix it and explain why in the report.
8. **Log decisions.** Anything the PRD does not specify goes in `docs/decisions.md`.

## Working alongside other agents

Several agents work on this repo at the same time. To avoid collisions:

- **One task, one branch, one worktree.** Never work directly on `main`. A human merges.
- **Stay in your module.** Only touch the module your task names (`sensor/`, `core/`, `web/`,
  `data/`, `docs/`). If you must touch another module, keep the change minimal and flag it.
- **Ports.** The demo machine runs the core on 8000 and the web app on 5173. Never start, stop, or
  restart processes on those ports. Test on 8001 and 5174.
- **Never pass `--send` to a script** unless your task explicitly says so. `--send` messages real
  people, places real calls, and spends real credits.
- **Keep `ELEVENLABS_PREWARM=false`** in any core you start. Prewarm spends about 4,000 of a
  10,000-character monthly quota.
- **Do not touch** `.env`, `data/clench.db`, `data/audio_cache/`, or `data/recordings/*.csv`.
  Read recordings; never overwrite them.
- **Small, frequent commits** so merges stay easy.

## Sensor work (you cannot wear the headband)

- Develop against BrainFlow's synthetic board and against recorded sessions in `data/recordings/`
  replayed through BrainFlow's playback board. A human records new sessions when needed.
- Muse 2 EEG channels: TP9, AF7, AF8, TP10 at 256 Hz. Clench shows as high-frequency muscle
  energy, strongest on TP9 and TP10. Blinks show as large slow spikes on AF7 and AF8.
- Set every detection threshold from a per-person calibration. No thresholds tuned to one head.
- Suppress commands while the gyroscope shows significant head movement.
- Measure detectors on labeled recordings: report hit rate and false triggers per minute, not
  impressions.
- The sensor must survive a Bluetooth disconnect: reconnect automatically and send a STATE or
  SIGNAL event so the Core and console know contact was lost.

## External services

Every service is optional. The app must behave correctly with none of them configured.

| Service | `.env` variables | Check script | Behavior without it |
| --- | --- | --- | --- |
| Gemini | `GEMINI_API_KEY`, `LLM_PROVIDER` (`gemini` or `fake`), `GEMINI_MODEL` | `scripts/test_gemini.py` | Fixed phrases from `menu.yaml`. Use `LLM_PROVIDER=fake` to exercise the AI flow offline. |
| ElevenLabs | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL`, `ELEVENLABS_PREWARM` | `scripts/test_voice.py` | Browser speech. |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID_<NAME>` | `scripts/test_telegram.py` | Dry run: logged, gray "(demo mode)" toast. |
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `CONTACT_<NAME>_PHONE` | `scripts/test_call.py` | Dry run. |
| Jev (ranking prior) | `TYPESAFE_API_KEY` (+ optional `TYPESAFE_MODEL`), or `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | `scripts/test_jev.py` | Ranking by history and time of day only. First one set is used. |

`ACTIONS_DRY_RUN=true` is the default. The AI never picks the action or the contact; those come
from the menu path. Only the data listed in PRD D14 goes to the AI.

### Voice (ElevenLabs, optional)

With no ElevenLabs keys everything is said with the browser's voice and the app works the same.

1. In `.env` set `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` (`ELEVENLABS_MODEL` defaults to
   `eleven_flash_v2_5`). Check with `uv run python scripts/test_voice.py` (add `--send` only if the
   task says so; that always calls ElevenLabs, 62 characters).
2. Restart the core. It logs `voice: ElevenLabs (voice ..., model ...)` (or `voice: browser speech
   (ELEVENLABS_API_KEY ... missing)`), and `/health` shows `voice`, `voice_paused`, and
   `voice_chars_sent`.
3. Prewarm: at startup the core makes audio for every menu label and leaf phrase in both languages,
   plus the help lines, in the background (about 4,000 characters the first time; the free tier has
   10,000 a month). Anything already cached is skipped. **Keep `ELEVENLABS_PREWARM=false` in any
   core you start.**

Audio is cached in `data/audio_cache/<sha256>.mp3` (git-ignored) and served at `/audio/...`, which
the web dev server proxies. A cached line plays at once. An uncached picked word is said by the
browser voice at once (in its place among the other picked words) and made in the background for
next time. An uncached sentence or system line waits at most 4 s for ElevenLabs; if it is slower or
fails, the browser voice says it right away and a slow request is still cached for next time. After a bad key (401), no credit or
quota (402), or 3 errors in a row, the core logs one warning and uses the browser voice for 5
minutes.

Speak picks: each picked tile's label is said as it is picked (70% volume); "Other..." plays a
short soft click instead of a word. On by default (`speak_picks` in `data/profile.yaml`), switchable
live from the dev panel. Picked words play in order, one after another, never cutting each other
off; a word whose audio has not started within 300 ms is said by the browser voice in its place.
The confirmed sentence is still only said after the confirm clench, and waits for the queued words;
a help line clears the queue and plays at once.

### Real messages and calls

By default `ACTIONS_DRY_RUN=true`: a confirmed message or call is only logged
(`DRY RUN send_message to maria: ...`) and the board shows a gray "(modo demo)" toast. The same
action to the same contact with the same text is sent at most once per 30 s.

Each contact in `data/contacts.yaml` names its own `phone_env` / `telegram_chat_env` variables
(`TELEGRAM_CHAT_ID_MARIA`, `CONTACT_MARIA_PHONE` in E.164). Check scripts without `--send` only
print what they would send. Typical failures: Telegram HTTP 400 "chat not found" (Maria has not
pressed Start in the bot's chat); Twilio error 21219 (trial account calling an unverified number).
With `ACTIONS_DRY_RUN=false`, the core logs `ACTIONS_DRY_RUN is off` and `/health` shows
`"dry_run": false`.

### AI suggestions (Gemini, optional)

With no key the app works the same with the fixed phrases from `data/menu.yaml` (the core logs
`AI: off (GEMINI_API_KEY missing): fixed phrases only`). With AI:

- Picking a leaf opens a suggestions screen: up to 3 sentences in the patient's words, then the
  leaf's fixed phrase, then "Other...". A clench on a sentence opens the confirm screen with exactly
  that sentence. Without AI (or if it is slow or fails) the leaf goes straight to the confirm screen.
- "Other..." ("Otro...") on every level brings up to 5 new options for the same path (without AI:
  the level's `more` list from `menu.yaml`, if any). Each pick shows the next page; after 3 pages,
  or when there is nothing new (or no AI), it loops back to the level's own options. A double blink
  on an "Other..." page goes up one menu level (the pages count as the level they came from).
- Home "Suggested" shows the AI's sentences for right now, the patient's most used sentences, and
  its fixed phrases, best first (see Learning below).
- The AI never picks the action or the contact; those come from the menu path.
- Results are fetched in the background as soon as a level opens, in ONE request for the whole level
  (every leaf's sentences, the "Other..." options, and at home the Suggested sentences; none when it
  is all cached). If not ready, the board shows "Finding options..." (scan paused) for at most 4 s,
  then uses the fixed phrase.

`LLM_PROVIDER=gemini` is the default; `GEMINI_MODEL` defaults to `gemini-3.8-flash`.
`LLM_PROVIDER=fake` runs the whole AI flow offline with canned sentences. `/health` shows `ai` and
`ai_calls`. A bad key or rate limit pauses the AI for 5 min / 1 min (fixed phrases meanwhile).

Only the current path, language, hour, patient name, the leaf's fixed phrase, the 20 most used
phrases, the last 5 confirmed sentences, and the contacts' first names are sent (PRD D14). In Day 1
mode the phrases and sentences are left out.

### Learning (ranking, Day 1 mode, demo week, Jev)

Every screen is ordered by the PRD section 9 score from the history in `data/clench.db` (confirmed
sends and cancelled confirms of the last 30 days): recent use (half-life 3 days), time of day
(+/- 1 hour), body state (later), the Jev prior (optional), and recent cancels. Weights and the
stability margin are in `data/profile.yaml` (`ranking`).

- Menu levels keep the `menu.yaml` order unless an item's score is clearly higher (1.5x) than the
  one above it, so tiles do not jump around. Suggested is always first on Home, "Other..." always
  last. The suggestions screen and the Suggested list are fully reordered.
- Suggested also offers the sentences the patient confirms most, each with the action and contact of
  the menu leaf it was said under (María's text is still a Telegram message to María).
- One-clench shortcut: when the top Suggested phrase is a confident guess (history share of this
  hour >= 0.6 with at least 3 recent uses, or >= 0.4 when Jev picks the same phrase with confidence
  >= 0.45; Jev never blocks what the history alone allows), picking Suggested goes straight to the
  confirm screen with it. The dev panel's Shortcut line (SHORTCUT_DEBUG, after every Home render)
  shows the top phrase, the history share, Jev's pick and "shortcut: yes/no (reason)". B there opens
  the full Suggested list instead of going home. The confirm clench is still required.
- Day 1 mode (`learning: false` in SETTINGS; default `learning` in `data/profile.yaml`, dev panel
  toggle): `menu.yaml` order, the fixed Suggested list, no shortcut, no Jev, no history for the AI.
  Switching it while scanning goes back to Home.
- After every confirm the Core sends METRICS to consoles and the dev panel: "Took 2 clenches, 0 s
  waiting (Day 1: 5 clenches, 6 s)". The collapsed Dev pill shows "2 vs 5 clenches".

Demo week (the "5 -> 2" moment; say plainly that the week is simulated):

```powershell
uv run python scripts/seed_demo.py --reset             # Day 1: deletes all events and phrases (asks; --yes skips)
uv run python scripts/seed_demo.py --load              # 7 days of habits ending now, the María text tied to this hour
uv run python scripts/seed_demo.py --reset --load --yes --focus-hour 15   # for a demo at 15:xx
```

`--load` prints what it wrote and, per language, the top 3 Suggested phrases at the focus hour. The
running core uses the new history from its next screen (no restart). Every simulated use is written
in both Spanish and English (`--lang es` or `--lang en` for one), so either board shows the learned
sentences; the ranking scales each score part across the candidates, so the order is the same.
**Do not run `--reset` against a shared `data/clench.db` unless the task says so.**

Jev never blocks the scan: its answer (1.5 s timeout, 10 min cache) re-ranks the screen quietly only
if the person has not moved yet; after a bad key or 3 failures it is off for 5 minutes. `/health`
shows `learning` and `jev`.

### Webcam pointing (Auto, Webcam, Gaze, Head tilt)

Pointing mode (PRD D2) is a setting, switchable live from the dev panel (Auto / Scan / Webcam / Gaze /
Head tilt) or SETTINGS: the clench always picks; only where the highlight comes from changes.

- Scan: the highlight moves by itself (scan speed). No camera.
- Webcam: turn the head slightly toward a tile; the highlight follows. No timer.
- Gaze: an eye tracker plugged into the board's gaze slot moves the highlight (`docs/eye-tracking.md`).
  Without one the board says "No eye tracker connected" and the highlight stays put.
- Auto (the default): scans at first, switches to the gaze (when an eye tracker sees the eyes) or
  else the head once the person is seen and points at a tile, and back to scanning after 3 s without
  a face. While Auto is scanning the board
  shows a blue "Scanning" / "Escaneando" badge (top right).
- Head tilt: not built yet (it needs the headband's motion data from the sensor chunk). It scans, the
  core logs `pointing mode headtilt is not built yet ...` and the badge says so.

How it works: the board runs MediaPipe Face Landmarker in the browser (GPU, CPU if the GPU fails,
about 25 frames a second), smooths the head's yaw and pitch, maps them to a point on the screen with
the calibrated head range, and highlights the tile under that point (or the nearest one). A new tile
is taken only once the point is 5% inside it (`tile_switch_margin` in `data/profile.yaml`, 0 to
0.2, and a dev panel slider that changes it live), so borders do not flicker. The board sends POINT (tile
and the SCREEN `seq` it belongs to) only when the tile changes, and FACE_OK when the face is seen or
lost for 300 ms; the core ignores a POINT for an older screen. Clenching can nudge the head, so in
webcam mode a clench picks the tile that was highlighted 250 ms before it (`clench_lookback_ms` in
`data/profile.yaml`, 0 turns it off). `/health` shows `pointing_mode`, `pointer`, and `face_ok`.

Privacy (PRD section 11): video never leaves the browser and is never saved; only POINT and FACE_OK
are sent. The camera is on only in Auto or Webcam mode, and a red "Camera on" / "Cámara encendida"
light shows on the board whenever it is. Switching to Scan or Head tilt turns it off at once.

Offline: the model (`face_landmarker.task`, 3.8 MB) and the wasm are served from
`web/public/mediapipe/` (git-ignored), never a CDN.

Head-range calibration (once per person and seat; before it the board uses defaults for a laptop
camera above the screen): open the dev panel, click "Calibrate head range" (needs Auto or Webcam
with the camera on). A big dot appears at the center, then the left, right, top, and bottom edges,
1.5 s each. The board saves the range to the core (`PUT /api/head-range`, the database profile's
`head_range_json`), so it survives reloads and restarts. If a step did not see the face or the
person barely turned, nothing is saved. Esc cancels.

Camera troubleshooting (Windows):

- "Camera blocked": allow the camera in the address-bar icon (or lock icon > Site settings), then
  reload. If still blocked: Windows Settings > Privacy & security > Camera — turn on Camera access,
  Let apps access your camera, and Let desktop apps access your camera.
- "The camera is in use by another app": Windows gives the camera to one app at a time. Close Teams,
  Zoom, OBS, the Camera app, and other browser tabs using it, then click "retry" in the dev panel.
- "No camera found": plug in a webcam, check the laptop's camera privacy switch or F-key, and look
  under Device Manager > Cameras.
- Highlight moves the wrong way or will not reach the edges: calibrate. The dev panel's yaw and
  pitch numbers show whether the camera sees the head move.
- Use localhost (not the laptop's IP): browsers only allow the camera on localhost or https.

## Keyboard stand-in (for testing the board)

### Computer mode (part 1)

Home has **Computer / Computadora** in place of Room. Pick it to open the core's maximized
Chromium window. The board stays connected, displays Computer mode and plays the usual echo/help
audio. Four horizontal bands group visible page controls; empty bands are omitted. Browser menu
is always the last choice. Clench a band, then clench a target. Eight choices fit on a target page
(seven targets plus More when needed). Double blink returns to bands. Browser menu contains
Scroll down/up, history Back, Home and Exit. Text fields currently show "Search options coming
next" and Cancel. Hold is always help, never Back.

Space, hold Space and B work in both the board dev panel and the foreground managed browser.
The browser keyboard listener runs in a Chromium isolated world so website scripts cannot forge
help events. No Muse, AI key or cloud voice is needed. Start/unlock the board's audio first.

First-time setup: `uv sync`, then `uv run playwright install chromium`.
The managed profile is `data/browser-profile/` (git-ignored; contains login cookies). Use one
core/browser owner for this profile at a time. For a one-time caregiver Spotify login, first Exit
computer mode, then run `uv run playwright open --browser chromium --user-data-dir data/browser-profile https://open.spotify.com`.
Log in manually in that setup window and close it before entering Computer again. The setup
window allows the login provider's redirects; active patient mode only allows the domains in
`data/computer.yaml` (accounts.spotify.com is deliberately not allowed). Never commit or share the
profile. Spotify playback can depend on Chromium's media/DRM support; YouTube is the first demo.

For a separate test core, use an in-memory database and no service keys:

```powershell
$env:ELEVENLABS_PREWARM = 'false'
uv run python -c "import uvicorn; from core.main import create_app; uvicorn.run(create_app(env={'ELEVENLABS_PREWARM':'false','COMPUTER_START_URL':'http://127.0.0.1:8001/computer/start'}), host='127.0.0.1', port=8001)"
# A second terminal:
$env:CORE_URL = 'http://127.0.0.1:8001'
npm --prefix web run dev -- --port 5174
```

`COMPUTER_START_URL` defaults to `http://127.0.0.1:8000/computer/start`; match it to the core's
port. The allowlist permits exactly that local route plus HTTPS youtube.com / google.com and their
subdomains, and exactly open.spotify.com. External navigations, popups and downloads are blocked;
ordinary media/CDN resources can load. Blocked action substrings live in `data/computer.yaml` and
are checked at discovery and again just before a real click. Room-control action code remains
available for custom menus but is unused by the default board.

`uv run pytest` runs a headless local browser test on **8001**, with temporary profiles. Stop your
own 8001 test core before running it; never stop the 8000 demo. Real network tests are skipped by
default; opt in with `$env:CLENCH_NETWORK_TESTS='1'; uv run pytest tests/test_computer_browser.py`.
If the browser download is missing or Chromium cannot launch, computer mode logs the failure,
closes its resources and returns Home. Help is independent of browser work and still runs during
startup, page timeouts and browser closure.

| Key | Event |
| --- | --- |
| Space (tap) | CLENCH: pick the highlighted tile / confirm |
| Space (hold 2.5 s) | LONG_CLENCH: start the 5 s help countdown (`long_clench_ms` in `data/profile.yaml`) |
| B | DOUBLE_BLINK: go up one menu level / cancel the confirm screen / cancel the help countdown |
| `` ` `` (backtick) | expand / collapse the dev panel (a small "Dev" pill bottom-left by default) |

The board needs a "Click to start" click before input counts.

The expanded panel also has buttons for the same events, "Reset to Home" (RESET: Home, first tile),
the pointing mode selector (Auto / Scan / Webcam / Gaze / Head tilt) with the camera preview, the eye
tracker status, "Calibrate head range", the Tile switch margin slider and the Cursor dot toggle (a
subtle dot where the head points, remembered in this browser), a scan speed slider, an EN/ES toggle,
a Speak picks on/off toggle, a Day 1 mode on/off toggle (all showing the values the Core reports in
SETTINGS), a line showing where the last thing said came from ("ElevenLabs (cached)", "ElevenLabs",
or "Browser"), the last METRICS ("Took 2 clenches, 0 s waiting (Day 1: 5 clenches, 6 s)") and the
Shortcut line (SHORTCUT_DEBUG).

## Milestone manual test (press Space, pick, confirm, hear it)

Use 8001 / 5174 for your own run. On the demo machine (8000 / 5173) only observe; do not restart
those processes.

1. Start from Day 1 (`uv run python scripts/seed_demo.py --reset`) so the menu is in `menu.yaml`
   order. Start the core and the web app. Open the board in Chrome or Edge.
2. Click "Click to start". The status dot (top right) and the "Dev" pill dot (bottom left) turn
   green, and the home board shows six Spanish tiles (Sugerencias, Necesito, Personas, Cómo me
   siento, Cuarto and the dashed "Otro...") with the highlight on Sugerencias (the click sends RESET),
   then moving about once a second. The
   browser asks for the camera (Auto mode): for steps 3 to 10 press backtick and click Scan, so the
   highlight keeps scanning whoever sits in front of the laptop.
3. Press backtick and click EN/ES in the dev panel: the tiles switch to English. Press backtick again.
4. When "I need" is highlighted press Space, then do the same for Pain, Back and A lot. Each tile's
   label is said softly as you pick it. The breadcrumb reads Home › I need › Pain › Back. With no
   Gemini key the "Say this?" screen then shows "My back hurts a lot. Can you help me turn over?".
   With a key a suggestions screen comes first: up to 3 sentences, then that fixed phrase, then
   "Other..."; pick one to get the "Say this?" screen. Nothing has been spoken yet.
5. Press B: you are back where you picked it, still silent. Pick it again and press Space on the
   confirm screen: the laptop speaks the sentence, then the board returns to Home.
6. Pick People › Maria › Text (with a key, then pick the fixed phrase, the tile just before
   "Other...") and confirm with Space: the laptop says "Honey, I'm okay, call me at six." and a gray
   "(demo mode) would message Maria" toast shows for 4 s (green "Message sent to Maria" with real
   sends on). The core terminal logs `DRY RUN send_message to maria: ...`.
7. Hold Space for 2.5 s (a shorter hold is a normal clench): a red full-screen countdown 5, 4, 3... and the laptop says "Calling for
   help. Double blink to cancel." Press B: back where you were. Hold Space again and let it reach 0:
   the board returns to Home at once, the laptop says "Calling Maria", and toasts show the call and
   the message ("Luis needs help now").
8. Press backtick: the Voice line shows "Browser" with no ElevenLabs keys, "ElevenLabs" /
   "ElevenLabs (cached)" with them. Click Speak picks to Off and pick a tile: nothing is said.
9. On Home pick "Other...": a soft click (no word) and new options (without a key: Yes, No, Good
   morning, Wait a moment). Pick "Other..." again: without a key it loops back to Home's own tiles;
   with a key up to 3 pages, then back. Pick I need › Other... › Other... and press B: Home.
10. Learning: run `uv run python scripts/seed_demo.py --load` (the core keeps running). Open the
    dev panel and click Day 1 mode to On: the board goes Home in `menu.yaml` order. Text María as
    in step 6: the Last message line reads "Took 5 clenches, 6 s waiting" with a Gemini key (4
    clenches, 3 s without). Click Day 1 mode to Off and pick Suggested (Sugerencias): the confirm
    screen shows "Mija, estoy bien, llámame a las seis." at once; confirm: "Took 2 clenches, 0 s
    waiting (Day 1: 5 clenches, 6 s)". Pick Suggested again and press B on the confirm screen: the
    full Suggested list opens, the María text first.
11. Webcam: in the dev panel click Auto and allow the camera. The red "Cámara encendida" light
    shows, the preview shows your face with yaw and pitch, and the blue "Escaneando" badge shows
    until the camera sees you; then the badge goes away and the highlight follows your head. Click
    "Calibrate head range" and follow the dot (center, left, right, top, bottom); the panel then
    says "calibrated". Turn toward a tile and press Space: that tile is picked. Cover the camera
    for 3 s: the badge comes back and the highlight scans; uncover it and turn your head: it
    follows again. Click Head tilt: scanning with "Inclinar la cabeza aún no está listo" and the
    camera light off. Click Scan: no camera, no badge.

## Definition of done

A chunk is done when all of these are true:

- The behavior works with no headband and no API keys.
- New behavior has tests, including the failure case (timeout, missing key, disconnect).
- `uv run pytest` passes; `npm --prefix web run build` and `lint` pass if you touched `web/`.
- Contracts are in sync if you touched an event.
- Decisions are logged in `docs/decisions.md`.
- Work is committed on your branch.
- You wrote the CHUNK REPORT.

## Review

Before a human merges your branch, a different model reviews the diff. If you receive review
findings, treat each one as a hypothesis: verify it, fix what you confirm, and explain what you
rejected and why. Confirmed findings on safety paths (confirm step, help, emergency) block the merge.

## Commits

- Conventional Commits with a scope: `feat(core): ...`, `fix(web): ...`, `test(sensor): ...`.
  Types: feat, fix, chore, docs, refactor, test. Scopes: core, web, sensor, data, docs.
- Commit automatically at the end of every chunk that works on its own. Prefer several small commits.
- Never push, never force, never rewrite history, never delete branches.
- No AI attribution: no Co-Authored-By trailers, no "Generated with" lines.

## CHUNK REPORT format

End every chunk with:

```
CHUNK REPORT
- Chunk: <number and name>
- Branch: <branch name>
- Built: <bullet list>
- How to run / test: <PowerShell commands>
- Test results: <pass/fail counts>
- Commits: <hash + message for each>
- Contracts changed: <none, or what changed>
- Decisions I made: <anything the PRD did not specify>
- Problems or warnings: <anything broken, skipped, or risky>
- Suggested next step: <one line>
```
