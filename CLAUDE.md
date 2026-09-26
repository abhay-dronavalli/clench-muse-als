# Clench

Clench is a headband system (Muse 2) that lets a person who cannot move or speak, for example
someone with late-stage ALS, talk, message their family and control their room using only jaw
clenches and blinks. A screen shows up to six big tiles in a menu that opens into smaller menus. A
highlight moves across the tiles, either by itself (Scan), by following small head turns seen by the
laptop webcam (Webcam), or by the headband's motion sensor (Head tilt). A clench picks, a double
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
owns the highlight, help countdown), `core/pointer/` (Pointer interface, ScanPointer), `core/menu.py`
(loads `data/menu.yaml` + `data/contacts.yaml`), `core/profile.py` (`data/profile.yaml`),
`core/actions/` (action registry: speak, send_message via Telegram, place_call via Twilio Voice,
room_control mock), `core/db.py` (SQLite events and phrases), `core/config.py` (.env loading),
`core/hub.py` (broadcast to boards/consoles), `core/clock.py` (injectable timers for tests),
`web/src/board/` (patient board, browser speech, toasts, help countdown), `web/src/dev/DevPanel.tsx`
(keyboard stand-in), `web/src/lib/useSocket.ts` (auto-reconnect).

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
npm --prefix web install

# Tests and checks
uv run pytest                        # Python tests
uv run python -c "import core.contracts"
npm --prefix web run build           # type-check + production build of the web app
npm --prefix web run lint

# Terminal 1: core server on http://127.0.0.1:8000 (health check: http://127.0.0.1:8000/health)
uv run uvicorn core.main:app --reload --port 8000

# Terminal 2: web app, board at http://localhost:5173/ and console at http://localhost:5173/console
# (/ws/* is proxied to the core on 127.0.0.1:8000)
npm --prefix web run dev

# Sensor service: not built yet (planned: uv run python -m sensor.main)
```

Without uv: `python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install fastapi "uvicorn[standard]" "pydantic>=2" pyyaml python-dotenv pytest httpx`, then `python -m pytest`.

The core loads `data/menu.yaml`, `data/contacts.yaml` and `data/profile.yaml` at startup and refuses
to start on a bad file. `--reload` only watches `.py` files; restart the core after editing the YAML.
Every pick, confirmed send, cancelled confirm and help alert is written to `data/clench.db` (delete
the file to start fresh). The board starts in the profile's language (Spanish for Luis).

### Real messages and calls

By default `ACTIONS_DRY_RUN=true`: a confirmed message or call is only logged in the core terminal
(`DRY RUN send_message to maria: ...`) and the board shows a gray "(modo demo)" toast. To really send:

1. Fill in `.env` (see `.env.example`): `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID_MARIA` for
   messages; `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` and
   `CONTACT_MARIA_PHONE` (E.164, `+13055550123`) for calls. Each contact in `data/contacts.yaml`
   names its own `phone_env` / `telegram_chat_env` variables.
2. Test each service on its own. These always send for real, whatever `ACTIONS_DRY_RUN` says:

   ```powershell
   uv run python scripts/test_telegram.py          # one Telegram message to Maria
   uv run python scripts/test_call.py              # one Twilio call to Maria (add "carlos", "en", ...)
   ```

   Each prints `OK: ...` or `FAILED: <exact error>` (e.g. `Telegram error (HTTP 400): Bad Request:
   chat not found` = Maria has not pressed Start in the bot's chat; `Twilio error 21219` = trial
   account calling an unverified number).
3. Set `ACTIONS_DRY_RUN=false` in `.env` and restart the core. The core logs
   `ACTIONS_DRY_RUN is off` at startup, and `/health` shows `"dry_run": false`.

The same action to the same contact with the same text is sent at most once per 30 s.

### Keyboard stand-in (dev panel on the board page)

| Key | Event |
|---|---|
| Space (tap) | CLENCH: pick the highlighted tile / confirm |
| Space (hold 1.5 s) | LONG_CLENCH: start the 5 s help countdown |
| B | DOUBLE_BLINK: go back one level / cancel the confirm screen / cancel the help countdown |
| `` ` `` (backtick) | expand / collapse the dev panel (a small "Dev" pill bottom-left by default) |

The expanded panel also has buttons for the same events, a scan speed slider and an EN/ES toggle.

### Milestone manual test (press Space, pick, confirm, hear it)

1. Start the core and the web app (two terminals, commands above). Open http://localhost:5173/ in Chrome or Edge.
2. Click "Click to start". The status dot (top right) and the "Dev" pill dot (bottom left) turn
   green, and the home board shows six Spanish tiles with the highlight moving about once a second.
3. Press backtick and click EN/ES in the dev panel: the tiles switch to English. Press backtick again.
4. When "I need" is highlighted press Space, then do the same for Pain, Back and A lot. The breadcrumb
   reads Home › I need › Pain › Back, then the "Say this?" screen shows "My back hurts a lot. Can you
   help me turn over?". Nothing has been spoken yet.
5. Press B: you are back on the Back level, still silent. Pick A lot again and press Space on the
   confirm screen: the laptop speaks the sentence, then the board returns to Home.
6. Pick People › Maria › Text and confirm with Space: the laptop says "Honey, I'm okay, call me at
   six." and a gray "(demo mode) would message Maria" toast shows for 4 s (green "Message sent to
   Maria" with real sends on). The core terminal logs `DRY RUN send_message to maria: ...`.
7. Hold Space for 1.5 s: a red full-screen countdown 5, 4, 3... Press B: back where you were. Hold
   Space again and let it reach 0: the laptop says "Calling Maria", toasts show the call and the
   message ("Luis needs help now"), then the board returns to Home.

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
