# Clench

Clench is a headband system (Muse 2) that lets a person who cannot move or speak, for example
someone with late-stage ALS, talk, message their family and control their room using only jaw
clenches and blinks. A screen shows up to six big tiles in a menu that opens into smaller menus. A
highlight moves across the tiles, either by itself (Scan), by following small head turns seen by the
laptop webcam (Webcam), by an eye tracker plugged into the board (Gaze), or by the headband's
motion sensor (Head tilt). A clench picks, a double
blink goes back, and a long clench calls for help. After two or three picks an AI (Gemini first)
writes the full sentence in English or Spanish, the person confirms with one more clench, and the
laptop speaks it, texts it or places a call (Twilio). Over time it learns the person: their signals,
what they say most and when, and their body state, so common messages move to the front. It is a
communication prototype for ShellHacks 2026, not a medical device, and it does not read minds.

The PRD, `docs/Clench_PRD.pdf`, is the source of truth.

## Architecture (PRD Part 2)

Separate programs on one laptop, talking over WebSocket with small JSON events. Only the AI, voice
and texting services reach the internet.

- `sensor/` (Python): Sensor Service. Turns headband signals (BrainFlow: synthetic, replay, Muse 2)
  into clean events: CLENCH, DOUBLE_BLINK, LONG_CLENCH, STATE, SIGNAL. Sends events, not raw data.
- `core/` (Python, FastAPI): Core Server. Session state machine, menu engine, pointing modes, AI
  suggestions, personalization ranking, action registry (speak, text, call, room, help), voice, SQLite.
  All decisions live here, including where the highlight is.
- `web/` (React + TypeScript + Vite + Tailwind): Patient Board (big tiles, highlight, webcam face
  tracking) and Caregiver Console (live signals, settings, calibration, history) as two pages of one
  app, plus the keyboard stand-in dev panel.
- `data/`: menu tree (`menu.yaml`), contacts (`contacts.yaml`), patient profile (`profile.yaml`),
  simulated demo week (`seed_demo_week.json`), Muse recordings (`recordings/`, CSVs git-ignored) and
  the local SQLite database `clench.db` (git-ignored, created on first run).
- `docs/`: the PRD and project docs: `contracts.md` (event formats) and `decisions.md` (running log
  of every change from the PRD).
- `scripts/`: one-off tools, e.g. real-send tests for Telegram and Twilio.
- `tests/`: pytest tests for `core/` and `sensor/`.

Key files so far: `core/main.py` (FastAPI app, WebSocket routes), `core/session.py` (state machine,
owns the highlight, SCREEN `seq`, clench look-back, help countdown), `core/pointer/` (Pointer interface;
`scan.py`, `webcam.py` follows POINT, `gaze.py` follows gaze POINTs, `auto.py` gaze or head while
the person is seen, scan after 3 s without, `headtilt.py` scans until the sensor chunk), `core/menu.py`
(loads `data/menu.yaml` + `data/contacts.yaml`), `core/profile.py` (`data/profile.yaml`),
`core/actions/` (action registry: speak, send_message via Telegram, place_call via Twilio Voice,
room_control mock), `core/suggest/` (AI layer: `provider.py` LLMProvider protocol and validated
models, `prompts.py` every prompt, `gemini.py`, `fake.py` offline provider, `stubs.py` claude/openai
placeholders, `service.py` Suggester with 4 s timeout, 10 min cache, fallback and the one-request-per-level
`level_bundle`), `core/rank/` (learning: `score.py` the PRD section 9 score and the stability rule,
`history.py` evidence from the events table, `ranker.py` Ranker and the Jev state summary, `jev.py`
optional TypeSafe Jev prior), `core/metrics.py` (clenches and scan steps per message, Day 1 cost),
`core/voice.py` (everything the board says: ElevenLabs TTS, disk audio cache,
circuit breaker, prewarm, browser-speech fallback), `core/db.py` (SQLite events, phrases, audio_cache), `core/config.py` (.env loading),
`core/hub.py` (broadcast to boards/consoles), `core/clock.py` (injectable timers for tests),
`web/src/board/` (patient board, audio player and browser speech in `speech.ts` with the in-order
sound queue in `queue.ts`, the "Other..." click, toasts, help countdown,
suggestion / "Other..." / loading tiles in `views.tsx`), `web/src/facetrack/` (webcam and gaze pointing:
`tracker.ts` camera + MediaPipe Face Landmarker, `pose.ts` head angles to a screen point, `tiles.ts`
sticky tile choice, `calibrate.ts` + `CalibrationOverlay.tsx` head range, `source.ts` pluggable point
sources (head, gaze), `gaze.ts` the gaze slot an eye tracker feeds (`docs/eye-tracking.md`),
`usePointing.ts` sends POINT / FACE_OK, `indicators.tsx` camera light, cursor dot, Scanning badge; `*.test.ts` vitest),
`web/scripts/mediapipe-assets.mjs` (puts the MediaPipe wasm and model in `web/public/mediapipe/`),
`web/src/dev/DevPanel.tsx` (keyboard stand-in, shows the settings the Core reports, pointing mode,
camera preview in `CameraPreview.tsx`, Day 1 toggle, METRICS and SHORTCUT_DEBUG lines, Reset to Home),
`web/src/lib/useSocket.ts` (auto-reconnect), `scripts/seed_demo.py` + `data/seed_demo_week.json`
(the simulated demo week).

**Changes from the PRD** are logged in `docs/decisions.md`. The two that shape the code most: the
keyboard stand-in is a web dev panel (`web/src/dev/`), not a Python `KeyboardSource`, and it sends
the same events so the Core cannot tell them apart; and there is no SMS: messages go through a
Telegram bot and calls through Twilio Programmable Voice (the action is `send_message`).

## Rules

a) The PRD is the source of truth. If the PRD is unclear, pick what fits it best and report the decision.

b) Git: at the end of every chunk that works on its own, commit using Conventional Commits (feat, fix,
   chore, docs, refactor, test) with a scope (core, web, sensor, data, docs). Prefer several small
   focused commits over one big one. Commit automatically. Never push, never force, never rewrite
   history, never delete branches. No AI attribution in commits: no Co-Authored-By trailers, no
   'Generated with' lines. (`.claude/settings.json` turns off Claude Code's automatic attribution.)

c) Event formats in `core/contracts.py` and `web/src/contracts.ts` are the single source of truth. Any
   change must update both files and `docs/contracts.md` together.

d) Nothing is spoken or sent without a confirm step (PRD D5).

e) The app must always run with no headband (keyboard stand-in).

f) No secrets in git. Use `.env`; keep `.env.example` updated.

g) Only modify files inside this repo.

h) Before committing, run the tests and make sure they pass.

i) Every chunk ends with the CHUNK REPORT format below.

## How to run

All commands are PowerShell, from the repo root. Python uses [uv](https://docs.astral.sh/uv/).

```powershell
# One-time setup
uv sync                              # creates .venv with Python deps (incl. dev: pytest)
Copy-Item .env.example .env          # then fill in keys; .env is git-ignored (runs fine left empty)
npm --prefix web ci                  # also copies the MediaPipe wasm and downloads the face model (~4 MB)
                                     # (npm 10.9's `npm --prefix web install` fails with ENOENT; ci or cd web works)

# Tests and checks
uv run pytest                        # Python tests
uv run python -c "import core.contracts"
npm --prefix web test                # vitest: the web's pure functions (head pose, sticky tiles, calibration)
npm --prefix web run build           # type-check + production build of the web app
npm --prefix web run lint

# Terminal 1: core server on http://127.0.0.1:8000 (health check: http://127.0.0.1:8000/health)
uv run uvicorn core.main:app --reload --port 8000

# Terminal 2: web app, board at http://localhost:5173/ and console at http://localhost:5173/console
# (/ws/*, /api/* and /audio/* are proxied to the core on 127.0.0.1:8000)
npm --prefix web run dev

# A second copy side by side (e.g. for checks while the first one runs): core on 8100, web on 5273
uv run uvicorn core.main:app --port 8100
$env:CORE_URL = 'http://127.0.0.1:8100'; npm --prefix web run dev -- --port 5273

# Sensor service: not built yet (planned: uv run python -m sensor.main)
```

Without uv: `python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install fastapi "uvicorn[standard]" "pydantic>=2" pyyaml python-dotenv pytest httpx`, then `python -m pytest`.

The core loads `data/menu.yaml`, `data/contacts.yaml` and `data/profile.yaml` at startup and refuses
to start on a bad file. A menu level holds at most 5 items; the core adds "Other..." as the sixth tile
on every level. `--reload` only watches `.py` files; restart the core after editing the YAML.
Every pick, confirmed send, cancelled confirm and help alert is written to `data/clench.db` (delete
the file to start fresh). The board starts in the profile's language (Spanish for Luis).

### Voice (ElevenLabs, optional)

With no ElevenLabs keys everything is said with the browser's voice and the app works the same.
For the natural voice:

1. In `.env` set `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` (`ELEVENLABS_MODEL` defaults to
   `eleven_flash_v2_5`). Test the key on its own (always calls ElevenLabs, 62 characters):

   ```powershell
   uv run python scripts/test_voice.py --send   # prints OK (es) / OK (en) with the saved file paths
   ```

   Without `--send` it only prints what it would do (true for every script in `scripts/`).

2. Restart the core. It logs `voice: ElevenLabs (voice ..., model ...)` (or `voice: browser speech
   (ELEVENLABS_API_KEY ... missing)`), and `/health` shows `voice`, `voice_paused` and
   `voice_chars_sent`.
3. Prewarm: at startup the core makes audio for every menu label and leaf phrase in both languages,
   plus the help lines, in the background (about 4,000 characters the first time; the free tier has
   10,000 a month). It logs `voice prewarm: N new, M already cached, ...; X characters sent`. Anything
   already cached is skipped, so later starts send nothing. `ELEVENLABS_PREWARM=false` turns it off.

Audio is cached in `data/audio_cache/<sha256>.mp3` (git-ignored; delete the folder's mp3 files to
start fresh) and served at `/audio/...`, which the web dev server proxies. A cached line plays at once
and never touches the network. An uncached picked word is said by the browser voice at once (in its
place among the other picked words) and made in the background for next time. An uncached sentence or
system line waits at most 4 s for ElevenLabs; if it is slower or fails, the browser voice says it
right away and a slow request is still cached for next time. After a bad key (401), no credit or quota (402), or 3
errors in a row, the core logs one warning and uses the browser voice for 5 minutes.

Speak picks: each picked tile's label is said as it is picked (70% volume); "Other..." plays a
short soft click instead of a word. On by default (`speak_picks` in `data/profile.yaml`), switchable
live from the dev panel. Picked words play in order, one after another, never cutting each other
off; a word whose audio has not started within 300 ms is said by the browser voice in its place.
The confirmed sentence is still only said after the confirm clench, and waits for the queued words;
a help line clears the queue and plays at once.

### Real messages and calls

By default `ACTIONS_DRY_RUN=true`: a confirmed message or call is only logged in the core terminal
(`DRY RUN send_message to maria: ...`) and the board shows a gray "(modo demo)" toast. To really send:

1. Fill in `.env` (see `.env.example`): `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID_MARIA` for
   messages; `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` and
   `CONTACT_MARIA_PHONE` (E.164, `+13055550123`) for calls. Each contact in `data/contacts.yaml`
   names its own `phone_env` / `telegram_chat_env` variables.
2. Test each service on its own. With `--send` these send for real, whatever `ACTIONS_DRY_RUN`
   says; without it they only print what they would send:

   ```powershell
   uv run python scripts/test_telegram.py --send   # one Telegram message to Maria
   uv run python scripts/test_call.py --send       # one Twilio call to Maria (add "carlos", "en", ...)
   ```

   Each prints `OK: ...` or `FAILED: <exact error>` (e.g. `Telegram error (HTTP 400): Bad Request:
   chat not found` = Maria has not pressed Start in the bot's chat; `Twilio error 21219` = trial
   account calling an unverified number).
3. Set `ACTIONS_DRY_RUN=false` in `.env` and restart the core. The core logs
   `ACTIONS_DRY_RUN is off` at startup, and `/health` shows `"dry_run": false`.

The same action to the same contact with the same text is sent at most once per 30 s.

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
- Home "Suggested" shows the AI's sentences for right now, the patient's most used sentences and its
  fixed phrases, best first (see "Learning" below).
- The AI never picks the action or the contact; those come from the menu path.
- Results are fetched in the background as soon as a level opens, in ONE request for the whole level
  (every leaf's sentences, the "Other..." options and, at home, the Suggested sentences; none when it
  is all cached), so they are usually ready. If not, the board shows "Finding options..." (scan
  paused) for at most 4 s, then uses the fixed phrase.

Setup:

1. In `.env` set `GEMINI_API_KEY` (`LLM_PROVIDER=gemini` is the default; `GEMINI_MODEL` defaults to
   `gemini-3.8-flash`). `LLM_PROVIDER=fake` runs the whole AI flow offline with canned sentences.
2. Check the key (4 small requests, prints each result and its time):

   ```powershell
   uv run python scripts/test_gemini.py --send
   ```

3. Restart the core. It logs `AI: gemini (gemini-3.8-flash)`; `/health` shows `ai` and `ai_calls`.
   Timeouts and errors are logged (`AI compose for ... timed out ...`); a bad key or a rate limit
   pauses the AI for 5 min / 1 min (fixed phrases meanwhile).

Only the current path, language, hour, patient name, the leaf's fixed phrase, the 20 most used
phrases, the last 5 confirmed sentences and the contacts' first names are sent (PRD D14). In Day 1
mode the phrases and sentences are left out.

### Learning (ranking, Day 1 mode, demo week, Jev)

Every screen is ordered by the PRD section 9 score from the history in `data/clench.db` (confirmed
sends and cancelled confirms of the last 30 days): recent use (half-life 3 days), time of day
(+/- 1 hour), body state (later), the Jev prior (optional) and recent cancels. Weights and the
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
  shows the top phrase, the history share, Jev's pick and "shortcut: yes/no (reason)". B there opens the full Suggested list instead of going
  home. The confirm clench is still required.
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
running core uses the new history from its next screen (no restart). Every simulated use is written in
both Spanish and English (`--lang es` or `--lang en` for one), so either board shows the learned
sentences; the ranking scales each score part across the candidates, so the order is the same.
Demo: toggle Day 1 mode on, text María (People › María › Mensaje › fixed phrase › confirm: 5
clenches with a Gemini key), toggle it off, then Sugerencias › confirm: 2 clenches.

Jev (optional AI prior, TypeSafe): with no key the ranking uses history and time only. In `.env` set
`TYPESAFE_API_KEY` (TypeSafe API), or `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` (Cloudflare
Workers AI, model `typesafe/jev`); the first one set is used. Check it:

```powershell
uv run python scripts/test_jev.py --send   # prints the probabilities, confidence and latency
```

Restart the core: it logs `ranking: learning on, Jev via TypeSafe API` and `/health` shows `learning`
and `jev`. Jev never blocks the scan: its answer (1.5 s timeout, 10 min cache) re-ranks the screen
quietly only if the person has not moved yet; after a bad key or 3 failures it is off for 5 minutes.

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
`data/profile.yaml`, 0 turns it off). `/health` shows `pointing_mode`, `pointer` and `face_ok`.

Privacy (PRD section 11): video never leaves the browser and is never saved; only POINT and FACE_OK
are sent. The camera is on only in Auto or Webcam mode, and a red "Camera on" / "Cámara encendida"
light shows on the board whenever it is. Switching to Scan or Head tilt turns it off at once.

Offline: the model (`face_landmarker.task`, 3.8 MB) and the wasm are served from `web/public/mediapipe/`
(git-ignored), never a CDN. `npm --prefix web ci` puts them there, and `npm run dev` / `build`
check again (instant when present). Without internet at install time, run
`npm --prefix web run assets` later; until then the board says face tracking could not start and
Auto scans.

Head-range calibration (do it once per person and seat; before it the board uses defaults for a
laptop camera above the screen): open the dev panel, click "Calibrate head range" (needs Auto or
Webcam with the camera on). A big dot appears at the center, then the left, right, top and bottom
edges, 1.5 s each: the person turns the head comfortably toward it. The board saves the range to the
core (`PUT /api/head-range`, the database profile's `head_range_json`), so it survives reloads and
restarts; the dev panel then says "calibrated". If a step did not see the face or the person barely
turned, the overlay says so and nothing is saved. Esc cancels.

Dev panel extras: the pointing mode selector, a small mirrored camera preview with the live yaw and
pitch (and "face" / "no face", GPU or CPU), "Calibrate head range", and a Cursor dot toggle (a
subtle dot where the head points, remembered in this browser).

Camera troubleshooting (Windows):

- "Camera blocked": Chrome / Edge asked and it was refused. Click the camera icon at the right of
  the address bar (or the lock icon > Site settings) and Allow, then reload. If it is still blocked,
  Windows Settings > Privacy & security > Camera: turn on "Camera access", "Let apps access your
  camera" and "Let desktop apps access your camera".
- "The camera is in use by another app": Windows gives the camera to one app at a time. Close Teams,
  Zoom, OBS or the Camera app (and other browser tabs using it), then click "retry" in the dev panel.
- "No camera found": plug in a webcam, check the laptop's camera privacy switch or F-key, and look
  for it under Device Manager > Cameras.
- The highlight moves the wrong way or will not reach the edges: calibrate. The dev panel's yaw
  and pitch numbers show whether the camera sees the head move.
- Use http://localhost:5173 (not the laptop's IP address): browsers only allow the camera on
  localhost or https.

### Keyboard stand-in (dev panel on the board page)

| Key | Event |
|---|---|
| Space (tap) | CLENCH: pick the highlighted tile / confirm |
| Space (hold 2.5 s) | LONG_CLENCH: start the 5 s help countdown (`long_clench_ms` in `data/profile.yaml`) |
| B | DOUBLE_BLINK: go up one menu level / cancel the confirm screen / cancel the help countdown |
| `` ` `` (backtick) | expand / collapse the dev panel (a small "Dev" pill bottom-left by default) |

The expanded panel also has buttons for the same events, "Reset to Home" (RESET: Home, first tile),
the pointing mode selector (Auto / Scan / Webcam / Gaze / Head tilt) with the camera preview, the eye
tracker status, "Calibrate head range", the Tile switch margin slider and the Cursor dot toggle (see
"Webcam pointing"), a scan speed slider, an EN/ES toggle, a Speak picks on/off toggle, a Day 1 mode
on/off toggle (all showing the values the Core reports in SETTINGS), a line showing where the last
thing said came from ("ElevenLabs (cached)", "ElevenLabs" or "Browser") and the last METRICS ("Took 2
clenches, 0 s waiting (Day 1: 5 clenches, 6 s)") and the Shortcut line (SHORTCUT_DEBUG).

### Milestone manual test (press Space, pick, confirm, hear it)

1. Start from Day 1 (`uv run python scripts/seed_demo.py --reset`) so the menu is in `menu.yaml`
   order. Start the core and the web app (two terminals, commands above). Open http://localhost:5173/ in Chrome or Edge.
2. Click "Click to start". The status dot (top right) and the "Dev" pill dot (bottom left) turn
   green, and the home board shows six Spanish tiles (Sugerencias, Necesito, Personas, Cómo me
   siento, Cuarto and the dashed "Otro...") with the highlight on Sugerencias (the click sends RESET),
   then moving about once a second. The
   browser asks for the camera (Auto mode): for steps 3 to 10 press backtick and click Scan, so the
   highlight keeps scanning whoever sits in front of the laptop.
3. Press backtick and click EN/ES in the dev panel: the tiles switch to English. Press backtick again.
4. When "I need" is highlighted press Space, then do the same for Pain, Back and A lot. Each tile's
   label is said softly as you pick it ("I need", "Pain", ...). The breadcrumb reads Home › I need ›
   Pain › Back. With no Gemini key the "Say this?" screen then shows "My back hurts a lot. Can you
   help me turn over?". With a key a suggestions screen comes first: up to 3 sentences, then that
   fixed phrase, then "Other..."; pick one to get the "Say this?" screen. Nothing has been spoken yet.
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
10. Learning: run `uv run python scripts/seed_demo.py --load` (the core keeps running). Open the dev
    panel and click Day 1 mode to On: the board goes Home in `menu.yaml` order. Text María as in
    step 6: the Last message line reads "Took 5 clenches, 6 s waiting" with a Gemini key (4
    clenches, 3 s without). Click Day 1 mode to Off and pick Suggested (Sugerencias): the confirm
    screen shows "Mija, estoy bien, llámame a las seis." at once; confirm: "Took 2 clenches, 0 s
    waiting (Day 1: 5 clenches, 6 s)". Pick Suggested again and press B on the confirm screen: the
    full Suggested list opens, the María text first.
11. Webcam: in the dev panel click Auto and allow the camera. The red "Cámara encendida" light shows,
    the preview shows your face with yaw and pitch, and the blue "Escaneando" badge shows until the
    camera sees you; then the badge goes away and the highlight follows your head. Click "Calibrate
    head range" and follow the dot (center, left, right, top, bottom); the panel then says
    "calibrated". Turn toward a tile and press Space: that tile is picked. Cover the camera for 3 s:
    the badge comes back and the highlight scans; uncover it and turn your head: it follows again.
    Click Head tilt: scanning with "Inclinar la cabeza aún no está listo" and the camera light off.
    Click Scan: no camera, no badge.

## CHUNK REPORT format

Every chunk ends with this report:

```
CHUNK REPORT
- Chunk: <number and name>
- Built: <bullet list>
- How to run / test: <PowerShell commands>
- Test results: <pass/fail counts>
- Commits: <hash + message for each>
- Decisions I made: <anything the PRD did not specify>
- Problems or warnings: <anything broken, skipped, or risky>
- Suggested next step: <one line>
```
