# Clench

**Talk with whatever movement you have left.**

Clench lets people who can't speak or use their hands communicate and control the world around them with one reliable movement. A Muse 2 headband senses jaw clenches (even attempts too weak to see), eye tracking on a tablet highlights choices, and AI turns two or three selections into a full sentence. Nothing is ever said, sent, or done without a final confirming clench.

Built at ShellHacks 2026.

## What it does

- **Look, clench, speak.** Gaze highlights one of six large tiles, a clench selects it, Gemini writes full sentences in English or Spanish, and one more clench speaks the sentence aloud, messages family, or places a call.
- **Help from anywhere.** A long clench starts a help countdown on any screen, with no AI involved.
- **Learns the person.** Tiles are ranked by what the person says and when, so a common message drops from 5 clenches on Day 1 to 2 after a week.
- **Car mode.** A rider plans a trip, picks the smoothest route and the most accessible drop-off (computed from open map data), and controls the cabin (temperature, music, windows, pull over, contact Support) with gaze and clenches. The car always decides and answers every request.
- **Caregiver insights.** Tiger Data tracks clench strength, refused gestures, and speed of communication over time, and flags a weakening signal before communication breaks down.

## Demo setup

| Device | Role |
|---|---|
| Laptop 1 | Clench core, web app, headband connection; mirrors the tablet screen |
| Android tablet | The rider's screen: the board with Eyedid eye tracking and the 3D car |
| Laptop 2 | The car: `/car-sim` shows the car's state, plays its music, and sends Support questions |

## Quick start (Windows, PowerShell)

Requirements: Python with [uv](https://docs.astral.sh/uv/), Node.js, and (for the tablet) the Android SDK.

```powershell
# 1. Install
uv sync --extra sensor
npm --prefix web ci

# 2. Configure: copy .env.example to .env and fill in the keys you have.
#    With no keys, Clench still runs in dry-run mode with fixed phrases.

# 3. Core (window 1)
uv run uvicorn core.main:app --port 8000

# 4. Web app (window 2); --host lets laptop 2 reach /car-sim
npm --prefix web run dev -- --host
```

Open:

| Page | URL |
|---|---|
| Patient board | http://localhost:5173 |
| Caregiver console | http://localhost:5173/console |
| Car simulator (laptop 2) | http://<laptop 1 IP>:5173/car-sim |
| Trip planner | http://localhost:5173/trip |
| Gaze accuracy test | http://localhost:5173/gaze-test |

No headband? Open the dev panel with the backtick key: Space = clench, hold Space = long clench.

### Tablet

```powershell
cd kushagra\tablet
# local.properties needs EYEDID_LICENSE_KEY=... and sdk.dir=...
.\gradlew.bat installDebug
adb reverse tcp:5173 tcp:5173
```

Then open **Clench Board** on the tablet. The 3D car model lives at `kushagra/tablet/app/src/main/assets/jaguar_i-pace.glb`.

### Caregiver insights (Tiger Data)

```powershell
# .env needs TIGER_DATABASE_URL=...
uv run --no-project --with-requirements insights/requirements.txt python -m insights.db apply
uv run --no-project --with-requirements insights/requirements.txt python -m insights.seed
uv run --no-project --with-requirements insights/requirements.txt python -m insights.logger
uv run --no-project --with-requirements insights/requirements.txt python -m insights.app
```

Open http://127.0.0.1:8300. The four-week history is simulated and labeled as such; today's points are live.

## Repository layout

| Folder | What's in it |
|---|---|
| `sensor/` | Muse 2 sensor service (BrainFlow): calibrated jaw clenches, blink detection |
| `core/` | FastAPI core: state machine, menus, confirmation, help, learning, actions, car link, trip planning (`core/geo/`) |
| `web/` | React + TypeScript board, caregiver console, `/car-sim`, `/trip`, `/gaze-test` |
| `kushagra/tablet/` | Android (Kotlin) shell: the board in a WebView, Eyedid eye tracking, the 3D car |
| `insights/` | Tiger Data logger, schema, simulated history, and caregiver trends page |
| `proto/` | `RiderLink` gRPC / protobuf contract between Clench and a vehicle |
| `data/` | Menus, profile, contacts, geo config, precomputed trips |
| `docs/` | Contracts, decisions log, eye tracking notes |
| `test/` | Standalone headband test bench |

## Built with

Python, FastAPI, WebSockets, SQLite, React, TypeScript, Vite, Tailwind, Kotlin, Android, Muse 2, BrainFlow, MNE-Python, Eyedid SDK, MediaPipe, Gemini, ElevenLabs, TypeSafe Jev, Telegram, Twilio, protobuf, OpenStreetMap, USGS elevation data, OSRM, Leaflet, Tiger Data (TimescaleDB).

## Safety and privacy

- Nothing is spoken, sent, or done without a final confirming clench. Low-risk comfort controls in the car act at once; anything that changes the trip or contacts a person always confirms.
- Help never depends on AI or the network.
- The AI sees only menu words, the hour, first names, and recent phrases. The insights database stores counts and timings, never what the person said.
- Eye and face tracking run on the device, and a light shows whenever the camera is on.
- Trip scoring uses only open data; Google content is shown live only, never stored.

## Team

Nikhil Sangamkar, Kushagra Katiyar, Taher Akolawala, Abhay Dronavalli
