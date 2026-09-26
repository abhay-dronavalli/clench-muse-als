# Event contracts

Every message that crosses a process boundary in Clench (PRD A4). The code versions are
`core/contracts.py` (Pydantic) and `web/src/contracts.ts` (TypeScript). **Change all three files
together**; `tests/` checks that they list the same messages and that every example below parses.

General rules:

- Every message is one small JSON object sent over the WebSocket, with a `type` field naming it.
- `t` is a timestamp in float seconds since the Unix epoch.
- `tile` and `highlight` are 0-based indexes into the `tiles` of the SCREEN the board is showing.
- Unknown fields are rejected. To add a field, change the contract, don't just send it.
- The keyboard stand-in (dev panel in `web/src/dev/`) sends CLENCH, DOUBLE_BLINK and LONG_CLENCH
  exactly as the Sensor Service would. The Core cannot tell them apart (PRD D15).
- Invalid messages are logged and ignored by the Core; the connection stays open.

## WebSocket endpoints (Core, `ws://127.0.0.1:8000`)

The web dev server proxies `/ws/*` to the Core, so the browser connects to `ws://localhost:5173/ws/...`.

| Endpoint | Who connects | Accepted messages | Receives |
|---|---|---|---|
| `/ws/board` | Patient board | READY, AUDIO_DONE, POINT, FACE_OK | SCREEN, CONFIRM, SPEAK, PLAY_AUDIO, ACTION_RESULT |
| `/ws/console` | Caregiver console | SETTINGS | the same Core -> Board messages (mirror) |
| `/ws/input` | Sensor Service, web dev panel | CLENCH, DOUBLE_BLINK, LONG_CLENCH, STATE, SIGNAL, POINT, SETTINGS (dev panel) | nothing |

| Message | Sender | Receiver | Meaning |
|---|---|---|---|
| CLENCH | Sensor Service, web dev panel | Core | Pick the highlighted tile |
| DOUBLE_BLINK | Sensor Service, web dev panel | Core | Go back / No / Cancel |
| LONG_CLENCH | Sensor Service, web dev panel | Core | Start the help alert countdown (5 s, DOUBLE_BLINK cancels) |
| STATE | Sensor Service | Core | Body state (only reorders options) |
| SIGNAL | Sensor Service | Core, relayed to Console | Thinned signal for the live chart |
| POINT | Board (Webcam), Sensor Service (Head tilt) | Core | Person is facing a tile |
| FACE_OK | Board | Core | Webcam can or cannot see a face |
| READY | Board | Core | Board connected; Core replies with the current view |
| AUDIO_DONE | Board | Core | Speech or audio finished (or failed) |
| SETTINGS | Console, web dev panel | Core | Pointing mode, scan speed, language |
| SCREEN | Core | Board, Console | What to draw and which tile is highlighted |
| CONFIRM | Core | Board, Console | "Send this?" screen before anything is spoken or sent |
| SPEAK | Core | Board, Console | Speak a confirmed sentence with browser speech |
| PLAY_AUDIO | Core | Board, Console | Play an audio file (later, cloud voices) |
| ACTION_RESULT | Core | Board, Console | A confirmed message, call or room action succeeded or failed |

## Sensor Service -> Core

### CLENCH

Short jaw clench: pick the highlighted tile. Also sent by the web dev panel (Space).

| Field | Type | Notes |
|---|---|---|
| `t` | float | seconds |
| `strength` | float | 0 to 1, relative to the calibrated threshold range |

```json
{"type": "CLENCH", "t": 1727300000.12, "strength": 0.83}
```

### DOUBLE_BLINK

Two blinks within about 700 ms: go back one step, answer No, or cancel (help countdown,
confirm screen). Single blinks are never sent (PRD D4).

| Field | Type | Notes |
|---|---|---|
| `t` | float | seconds |

```json
{"type": "DOUBLE_BLINK", "t": 1727300003.4}
```

### LONG_CLENCH

Clench held about 1.5 s: start the 5 second help alert countdown, which a DOUBLE_BLINK cancels.

| Field | Type | Notes |
|---|---|---|
| `t` | float | seconds |
| `duration` | float | seconds held, > 0 |

```json
{"type": "LONG_CLENCH", "t": 1727300009.9, "duration": 1.6}
```

### STATE

Body state. It only changes the order of options, never takes action (PRD D10).

| Field | Type | Notes |
|---|---|---|
| `t` | float | seconds |
| `level` | `"calm"` \| `"normal"` \| `"elevated"` | changes only after holding for several seconds |
| `hr` | float \| null | heart rate in bpm, null when there is no reading |
| `motion` | float | restlessness, >= 0, 0 = still |
| `eyes_closed` | bool | drives the rest pause (PRD D13) |

```json
{"type": "STATE", "t": 1727300010.0, "level": "elevated", "hr": 94.0, "motion": 0.7, "eyes_closed": false}
```

### SIGNAL

Thinned copy of the signal, for the caregiver chart only. Never used for decisions.

| Field | Type | Notes |
|---|---|---|
| `t` | float | seconds |
| `ch` | float[] | one value per channel |

```json
{"type": "SIGNAL", "t": 1727300010.05, "ch": [12.5, -3.1, 40.2, 8.8]}
```

## Board / Sensor Service -> Core (pointing)

### POINT

The person is facing a tile. Sent only when the tile changes. The Core still decides what is
highlighted (PRD A3.3).

| Field | Type | Notes |
|---|---|---|
| `source` | `"webcam"` \| `"headtilt"` | webcam comes from the board, headtilt from the Sensor Service |
| `tile` | int | 0-based, >= 0 |
| `t` | float | seconds |

```json
{"type": "POINT", "source": "webcam", "tile": 3, "t": 1727300011.2}
```

### FACE_OK

Webcam face tracking status, sent by the board when it changes. In Auto mode the Core falls back
to Scan when the face has been lost for about 3 s, and switches back when it returns.

| Field | Type | Notes |
|---|---|---|
| `ok` | bool | false = face lost |

```json
{"type": "FACE_OK", "ok": false}
```

## Board -> Core

### READY

Sent by the board right after it connects (after the "Click to start" overlay has unlocked speech).
The Core replies to that board only with the current view: SCREEN while scanning, CONFIRM while
confirming, nothing while speaking (the next SCREEN follows when speech ends).

No fields besides `type`.

```json
{"type": "READY"}
```

### AUDIO_DONE

Sent by the board when a SPEAK (or PLAY_AUDIO) finishes or fails. The Core returns to home. If no
AUDIO_DONE arrives within 10 s the Core returns to home anyway. With several boards open, the first
AUDIO_DONE wins and later ones are ignored.

No fields besides `type`.

```json
{"type": "AUDIO_DONE"}
```

## Console -> Core

### SETTINGS

Caregiver changes pointing mode, scan speed or language. Applies at once, no restart (PRD P1). The
web dev panel also sends it (scan speed slider, EN/ES toggle) on `/ws/input`.

| Field | Type | Notes |
|---|---|---|
| `pointing_mode` | `"auto"` \| `"scan"` \| `"webcam"` \| `"headtilt"` | Auto is the default |
| `scan_ms` | int | ms per tile in Scan mode, > 0, default 1000 |
| `lang` | `"en"` \| `"es"` (optional) | omit to keep the current language; default `"en"` |

```json
{"type": "SETTINGS", "pointing_mode": "auto", "scan_ms": 1000, "lang": "es"}
```

## Core -> Board

### SCREEN

What the board should draw. The board is "dumb": the Core owns the highlight position. Sent after
every change while scanning (level change, highlight move, language change), and once a second
during the help countdown.

| Field | Type | Notes |
|---|---|---|
| `screen` | `"menu"` \| `"suggestions"` \| `"help_countdown"` \| `"paused"` \| `"calibrating"` | |
| `tiles` | `{"id": string, "label": string}[]` | at most 6 (PRD D8); `id` is the dotted menu path, e.g. `need.pain.back` |
| `highlight` | int \| null | 0-based index into `tiles`, null = nothing highlighted |
| `lang` | `"en"` \| `"es"` | |
| `path` | string[] | breadcrumb labels (current language) from home down to this level; `[]` at home |
| `countdown` | int \| null | optional, >= 0. Seconds left before the help alert fires; only set when `screen` is `"help_countdown"`, null (or absent) otherwise |

```json
{"type": "SCREEN", "screen": "menu", "tiles": [{"id": "need.pain.back.a_little", "label": "Un poco"}, {"id": "need.pain.back.a_lot", "label": "Mucho"}], "highlight": 1, "lang": "es", "path": ["Necesito", "Dolor", "Espalda"], "countdown": null}
```

**Help countdown** (PRD D3, section 5 step 8). A LONG_CLENCH while scanning or on the confirm
screen starts a 5 second countdown. The Core sends this SCREEN with `countdown` 5, 4, 3, 2, 1, one
per second, with no tiles. A DOUBLE_BLINK cancels it and the board goes back to where it was. At
0 the Core calls and messages the profile's help contact (the countdown is the confirmation, so no
CONFIRM screen), sends SPEAK "Calling Maria" / "Llamando a María", and returns home after
AUDIO_DONE. Each of the call and the message then reports an ACTION_RESULT.

```json
{"type": "SCREEN", "screen": "help_countdown", "tiles": [], "highlight": null, "lang": "es", "path": [], "countdown": 5}
```

### CONFIRM

The "Send this?" screen. Nothing is spoken or sent until the person clenches here; a
DOUBLE_BLINK cancels (PRD D5).

| Field | Type | Notes |
|---|---|---|
| `text` | string | the exact sentence that will be spoken or sent |
| `action` | `"speak"` \| `"send_message"` \| `"place_call"` \| `"room_control"` \| `"help_alert"` | from the action registry |

```json
{"type": "CONFIRM", "text": "Mija, estoy bien, llámame a las seis.", "action": "send_message"}
```

### SPEAK

Speak a sentence with the browser's speech synthesis (`en-US` / `es-US` voice when installed).
Only ever sent after a confirming CLENCH on a CONFIRM screen (PRD D5). The board replies with
AUDIO_DONE.

| Field | Type | Notes |
|---|---|---|
| `text` | string | the confirmed sentence |
| `lang` | `"en"` \| `"es"` | |

```json
{"type": "SPEAK", "text": "My back hurts a lot. Can you help me turn over?", "lang": "en"}
```

### PLAY_AUDIO

Play an audio file served by the Core (cached cloud TTS, later chunk). The board replies with
AUDIO_DONE.

| Field | Type | Notes |
|---|---|---|
| `url` | string | path on the Core server |

```json
{"type": "PLAY_AUDIO", "url": "/audio/abc123.mp3"}
```

### ACTION_RESULT

Sent when a confirmed action that leaves the laptop finishes: `send_message` (Telegram),
`place_call` (Twilio voice) or `room_control`. Speaking aloud has no ACTION_RESULT; the board
already knows from AUDIO_DONE. The help alert sends one for its call and one for its message. The
board shows it as a toast for 4 s.

| Field | Type | Notes |
|---|---|---|
| `action` | same values as CONFIRM `action` | the action that ran |
| `ok` | bool | true = sent (or, in dry run, would have been sent) |
| `detail` | string | `"dry run"` when `ACTIONS_DRY_RUN` is on; `"duplicate suppressed"` when the same action, contact and text ran in the last 30 s; otherwise the service's result or error, e.g. `"Twilio error 21219 (HTTP 400): ..."` |
| `contact` | string \| null | contact's display name in the current language, null when there is none (room control) |

```json
{"type": "ACTION_RESULT", "action": "send_message", "ok": true, "detail": "sent", "contact": "María"}
```
