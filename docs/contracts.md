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

| Message | Sender | Receiver | Meaning |
|---|---|---|---|
| CLENCH | Sensor Service, web dev panel | Core | Pick the highlighted tile |
| DOUBLE_BLINK | Sensor Service, web dev panel | Core | Go back / No / Cancel |
| LONG_CLENCH | Sensor Service, web dev panel | Core | Start the help alert countdown |
| STATE | Sensor Service | Core | Body state (only reorders options) |
| SIGNAL | Sensor Service | Core, relayed to Console | Thinned signal for the live chart |
| POINT | Board (Webcam), Sensor Service (Head tilt) | Core | Person is facing a tile |
| FACE_OK | Board | Core | Webcam can or cannot see a face |
| SETTINGS | Console | Core | Pointing mode and scan speed |
| SCREEN | Core | Board, Console | What to draw and which tile is highlighted |
| CONFIRM | Core | Board | "Send this?" screen before anything is spoken or sent |
| PLAY_AUDIO | Core | Board | Play an audio file |

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

## Console -> Core

### SETTINGS

Caregiver changes pointing mode or scan speed. Applies at once, no restart (PRD P1).

| Field | Type | Notes |
|---|---|---|
| `pointing_mode` | `"auto"` \| `"scan"` \| `"webcam"` \| `"headtilt"` | Auto is the default |
| `scan_ms` | int | ms per tile in Scan mode, > 0, default 1000 |

```json
{"type": "SETTINGS", "pointing_mode": "auto", "scan_ms": 1000}
```

## Core -> Board

### SCREEN

What the board should draw. The board is "dumb": the Core owns the highlight position.

| Field | Type | Notes |
|---|---|---|
| `screen` | `"menu"` \| `"suggestions"` \| `"help_countdown"` \| `"paused"` \| `"calibrating"` | |
| `tiles` | `{"id": string, "label": string}[]` | at most 6 (PRD D8) |
| `highlight` | int \| null | 0-based index into `tiles`, null = nothing highlighted |
| `lang` | `"en"` \| `"es"` | |

```json
{"type": "SCREEN", "screen": "menu", "tiles": [{"id": "suggested", "label": "Tengo hambre"}, {"id": "need", "label": "Necesito"}, {"id": "people", "label": "Personas"}], "highlight": 2, "lang": "es"}
```

### CONFIRM

The "Send this?" screen. Nothing is spoken or sent until the person clenches here; a
DOUBLE_BLINK cancels (PRD D5).

| Field | Type | Notes |
|---|---|---|
| `text` | string | the exact sentence that will be spoken or sent |
| `action` | `"speak"` \| `"send_text"` \| `"place_call"` \| `"room_control"` \| `"help_alert"` | from the action registry |

```json
{"type": "CONFIRM", "text": "Mija, estoy bien, llámame a las seis.", "action": "send_text"}
```

### PLAY_AUDIO

Play an audio file served by the Core (cached TTS).

| Field | Type | Notes |
|---|---|---|
| `url` | string | path on the Core server |

```json
{"type": "PLAY_AUDIO", "url": "/audio/abc123.mp3"}
```

## Not yet defined

The PRD (A3.3) says the board also sends "ready" and audio status to the Core. Those messages
will be added here, with their code, when the board is built.
