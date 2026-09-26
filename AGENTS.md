# AGENTS.md: Clench

Instructions for every coding agent working in this repo (Claude Code, Codex, Cursor, Muse Code, and
others). Read this whole file before you change anything.

## What Clench is

Clench lets a person who cannot move or speak (for example, someone with late-stage ALS) talk,
message their family, and control their room with jaw clenches and blinks, read by a Muse 2 EEG
headband. A board shows up to six big tiles; a highlight moves across them (Scan, Webcam head
turns, or Head tilt). A clench picks, a double blink goes back, a long clench calls for help. After
two or three picks, an AI (Gemini first) writes the full sentence in English or Spanish; the person
confirms with one more clench, and the laptop speaks it, texts it (Telegram), or places a call
(Twilio). It is a communication prototype for ShellHacks 2026, not a medical device.

## Sources of truth, in order

1. `docs/Clench_PRD.pdf`: the PRD. If it is unclear, pick what fits it best and log the decision.
2. `core/contracts.py`, `web/src/contracts.ts`, `docs/contracts.md`: event formats.
3. `docs/decisions.md`: every change from the PRD. Read it before assuming the PRD still applies.
4. This file.

## Repo map

| Path | What lives there |
| --- | --- |
| `sensor/` | Python Sensor Service. BrainFlow (synthetic, replay, Muse 2) to clean events: CLENCH, DOUBLE_BLINK, LONG_CLENCH, STATE, SIGNAL. Sends events, never raw data. |
| `core/` | Python FastAPI Core Server. All decisions live here: session state machine (`session.py`, owns the highlight and help countdown), menus (`menu.py`), pointers (`pointer/`), actions (`actions/`), AI (`suggest/`), voice (`voice.py`), SQLite (`db.py`), config (`config.py`), broadcast (`hub.py`), injectable timers (`clock.py`). |
| `web/` | React + TypeScript + Vite + Tailwind. Patient Board (`src/board/`), Caregiver Console (`/console`), keyboard stand-in (`src/dev/DevPanel.tsx`), socket hook (`src/lib/useSocket.ts`). |
| `data/` | `menu.yaml`, `contacts.yaml`, `profile.yaml`, `seed_demo_week.json`, `recordings/` (CSVs git-ignored), `clench.db` and `audio_cache/` (git-ignored). |
| `scripts/` | One-off tools. Every script only prints what it would do unless you pass `--send`. |
| `tests/` | pytest tests for `core/` and `sensor/`. |

Components talk over WebSocket with small JSON events: `/ws/board`, `/ws/console`, `/ws/input`.
The keyboard stand-in sends the same events as the headband, so the Core cannot tell them apart.

## Commands (PowerShell, from the repo root)

```powershell
uv sync                                   # Python deps
npm --prefix web install                  # web deps

uv run pytest                             # Python tests (must pass before every commit)
npm --prefix web run build                # type-check + build (must pass if you touched web/)
npm --prefix web run lint                 # lint (must pass if you touched web/)

uv run uvicorn core.main:app --port 8001  # core for YOUR testing (see "Ports" below)
npm --prefix web run dev -- --port 5174   # web for YOUR testing
```

## Hard rules

1. **Nothing is spoken or sent without a confirm step** (PRD D5). The help countdown is the confirm
   for help.
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

`ACTIONS_DRY_RUN=true` is the default. The AI never picks the action or the contact; those come
from the menu path. Only the data listed in PRD D14 goes to the AI.

## Keyboard stand-in (for testing the board)

| Key | Event |
| --- | --- |
| Space (tap) | CLENCH: pick / confirm |
| Space (hold 1.5 s) | LONG_CLENCH: start the 5 s help countdown |
| B | DOUBLE_BLINK: back / cancel confirm / cancel help |
| `` ` `` | Show or hide the dev panel |

The board needs a "Click to start" click before input counts.

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