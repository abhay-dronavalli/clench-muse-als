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
| `/ws/board` | Patient board | READY, AUDIO_DONE, POINT, FACE_OK | SETTINGS, SCREEN, CONFIRM, SPEAK, PLAY_AUDIO, ACTION_RESULT |
| `/ws/console` | Caregiver console | SETTINGS | SETTINGS, METRICS and the same Core -> Board messages (mirror) |
| `/ws/input` | Sensor Service, web dev panel | CLENCH, DOUBLE_BLINK, LONG_CLENCH, STATE, SIGNAL, POINT, SETTINGS (dev panel) | SETTINGS, METRICS |

Every client gets the current SETTINGS the moment it connects, and again after every change.

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
| AUDIO_DONE | Board | Core | A phrase or system line finished (or failed, or was interrupted) |
| SETTINGS | Console, web dev panel / Core | Core / every client | Pointing mode, scan speed, language, speak picks, learning; the Core announces the current values |
| SCREEN | Core | Board, Console | What to draw and which tile is highlighted |
| CONFIRM | Core | Board, Console | "Send this?" screen before anything is spoken or sent |
| SPEAK | Core | Board, Console | Say something with browser speech (no cloud audio for it) |
| PLAY_AUDIO | Core | Board, Console | Play cloud TTS audio (ElevenLabs, cached on the laptop) |
| ACTION_RESULT | Core | Board, Console | A confirmed message, call or room action succeeded or failed |
| METRICS | Core | Console, web dev panel | What a confirmed message cost in clenches and scan steps, and what it would have cost in Day 1 mode |

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
The Core replies to that board only with the current view: SCREEN while scanning or loading, CONFIRM
while confirming, nothing while speaking (the next SCREEN follows when speech ends).

No fields besides `type`.

```json
{"type": "READY"}
```

### AUDIO_DONE

Sent by the board when a SPEAK or PLAY_AUDIO of kind `phrase` or `system` ends: finished, failed
(after the browser-speech fallback), or interrupted by a newer one. Never sent for `echo`.

The Core only reacts to the AUDIO_DONE whose `id` is the phrase it is speaking: it then returns to
home. Any other id (an echo, a system line, an older phrase) is ignored. If the right AUDIO_DONE
does not arrive within 10 s the Core returns to home anyway. With several boards open, the first
matching AUDIO_DONE wins and later ones are ignored.

| Field | Type | Notes |
|---|---|---|
| `id` | string | the `id` of the SPEAK / PLAY_AUDIO that ended |

```json
{"type": "AUDIO_DONE", "id": "3f9c2a71b0de"}
```

## Console -> Core, and Core -> every client

### SETTINGS

Caregiver changes pointing mode, scan speed, language, speak picks or learning. Applies at once, no
restart (PRD P1). The web dev panel also sends it (scan speed slider, EN/ES toggle, Speak picks and
Day 1 mode toggles) on `/ws/input`.

The Core sends the same message the other way, with every field filled in, to each board, console
and input client when it connects, and to all of them after every SETTINGS it receives (the sender
included). Screens show these values instead of assuming defaults.

| Field | Type | Notes |
|---|---|---|
| `pointing_mode` | `"auto"` \| `"scan"` \| `"webcam"` \| `"headtilt"` | Auto is the default |
| `scan_ms` | int | ms per tile in Scan mode, > 0, default 1000 |
| `lang` | `"en"` \| `"es"` (optional) | omit to keep the current language; default `"en"` |
| `speak_picks` | bool (optional) | say each picked tile aloud as it is picked (an `echo`); omit to keep the current value; default from `data/profile.yaml` (true) |
| `learning` | bool (optional) | rank by the patient's history (PRD section 9). `false` = "Day 1 mode": menu.yaml order, the fixed Suggested list, no one-clench shortcut, no Jev, no history for the AI. Omit to keep the current value; default from `data/profile.yaml` (true). A change while scanning goes back to home |

```json
{"type": "SETTINGS", "pointing_mode": "auto", "scan_ms": 1000, "lang": "es", "speak_picks": true, "learning": true}
```

## Core -> Board

### SCREEN

What the board should draw. The board is "dumb": the Core owns the highlight position. Sent after
every change while scanning (level change, highlight move, language change, loading starts), and
once a second during the help countdown.

| Field | Type | Notes |
|---|---|---|
| `screen` | `"menu"` \| `"suggestions"` \| `"help_countdown"` \| `"paused"` \| `"calibrating"` | `suggestions` = the sentences for a picked leaf |
| `tiles` | `{"id": string, "label": string, "kind": TileKind}[]` | at most 6 (PRD D8), see below |
| `highlight` | int \| null | 0-based index into `tiles`, null = nothing highlighted |
| `lang` | `"en"` \| `"es"` | |
| `path` | string[] | breadcrumb labels (current language) from home down to this level; `[]` at home. A step through "Other..." shows as `"Other"` / `"Otro"` |
| `countdown` | int \| null | optional, >= 0. Seconds left before the help alert fires; only set when `screen` is `"help_countdown"`, null (or absent) otherwise |
| `loading` | bool | optional, default false. True while the Core waits for AI options after a pick (at most 4 s); scanning is paused and the board shows "Finding options..." / "Buscando opciones..." |

Tiles:

| `kind` | What | Picking it |
|---|---|---|
| `branch` | a menu category | opens the next level (the home "Suggested" opens the AI's sentences for right now, then its fixed phrases) |
| `leaf` | an option that leads to a sentence, from `data/menu.yaml` or made by the AI | opens the suggestions screen, or the CONFIRM screen with the fixed phrase when there is no AI |
| `suggestion` | a full sentence; `label` is the exact text | opens the CONFIRM screen with exactly that sentence |
| `other` | always the last tile: "Other..." / "Otro...", or "Spell it" / "Deletrear" after two "Other..." picks in a row | "Other...": new options for the same path (AI, else the level's fixed `more` list); "Spell it": says "Spelling is coming soon." for now |

`id` is the dotted menu path (`need.pain.back`). The Core's own tiles end in `.other` / `.spell`
(`other` / `spell` at home). Anything written by the AI starts with `ai:`: an AI option
`ai:need.pain.brazos`, an AI sentence `ai:need.pain.back.a_lot.s1`. The fixed phrase on a
suggestions screen keeps its leaf's id. The AI never chooses the action or the contact: an AI option
takes them from its level, an AI sentence from its leaf.

```json
{"type": "SCREEN", "screen": "menu", "tiles": [{"id": "need.pain.back.a_little", "label": "Un poco", "kind": "leaf"}, {"id": "need.pain.back.a_lot", "label": "Mucho", "kind": "leaf"}, {"id": "need.pain.back.other", "label": "Otro...", "kind": "other"}], "highlight": 1, "lang": "es", "path": ["Necesito", "Dolor", "Espalda"], "countdown": null, "loading": false}
```

**Suggestions screen** (PRD section 5 step 6). Up to 3 AI sentences, then the leaf's fixed phrase
(when the AI did not already write it), then "Other..." (more sentences):

```json
{"type": "SCREEN", "screen": "suggestions", "tiles": [{"id": "ai:people.maria.text.s1", "label": "Mija, estoy bien. Llámame a las seis.", "kind": "suggestion"}, {"id": "ai:people.maria.text.s2", "label": "Mija, todo bien por aquí. Te quiero.", "kind": "suggestion"}, {"id": "people.maria.text", "label": "Mija, estoy bien, llámame a las seis.", "kind": "suggestion"}, {"id": "people.maria.text.other", "label": "Otro...", "kind": "other"}], "highlight": 0, "lang": "es", "path": ["Personas", "María", "Mensaje"], "countdown": null, "loading": false}
```

**Help countdown** (PRD D3, section 5 step 8). A LONG_CLENCH while scanning or on the confirm
screen starts a 5 second countdown. The Core sends this SCREEN with `countdown` 5, 4, 3, 2, 1, one
per second, with no tiles. A DOUBLE_BLINK cancels it and the board goes back to where it was. At
0 the Core calls and messages the profile's help contact (the countdown is the confirmation, so no
CONFIRM screen), says "Calling Maria" / "Llamando a María" (kind `system`) and returns home right
away. Each of the call and the message then reports an ACTION_RESULT. When the countdown starts the
Core also says "Calling for help. Double blink to cancel." / "Pidiendo ayuda. Parpadea dos veces
para cancelar." (kind `system`). System lines never change the session state.

```json
{"type": "SCREEN", "screen": "help_countdown", "tiles": [], "highlight": null, "lang": "es", "path": [], "countdown": 5, "loading": false}
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

### Utterances: SPEAK and PLAY_AUDIO

Everything the board says is one utterance with an `id` and a `kind`:

| `kind` | What | When | Volume | AUDIO_DONE |
|---|---|---|---|---|
| `phrase` | the confirmed sentence | only after a confirming CLENCH on a CONFIRM screen (PRD D5); the Core is SPEAKING until its AUDIO_DONE | 100% | yes |
| `echo` | the label of the tile just picked ("Other" / "Otro" for "Other...") | on every CLENCH pick while scanning, when speak picks is on; never on DOUBLE_BLINK, the confirm clench or a `suggestion` tile (the confirm step says the sentence) | 70% | no |
| `system` | a fixed line from the Core | help countdown start, help alert fired, "Spell it" picked | 100% | yes (ignored by the Core) |

The Core sends PLAY_AUDIO when it has (or can make in time) ElevenLabs audio for the text, and
SPEAK when it cannot (no key, no internet, too slow, service errors): the board then uses browser
speech, so nothing is ever left silent.

Board rules (one audio player, reused):

- A new echo interrupts an older echo. An echo that arrives while a phrase or system line is
  playing is dropped (it never cuts the person's sentence).
- A phrase or system line interrupts whatever is playing. An interrupted phrase or system line
  still gets its AUDIO_DONE.
- If a PLAY_AUDIO file fails to load or play, the board says the same `text` with browser speech,
  then sends AUDIO_DONE.

### SPEAK

Say `text` with the browser's speech synthesis (`en-US` / `es-US` voice when installed).

| Field | Type | Notes |
|---|---|---|
| `id` | string | utterance id, sent back in AUDIO_DONE |
| `kind` | `"phrase"` \| `"echo"` \| `"system"` | see the table above |
| `text` | string | what to say |
| `lang` | `"en"` \| `"es"` | |

```json
{"type": "SPEAK", "id": "3f9c2a71b0de", "kind": "phrase", "text": "My back hurts a lot. Can you help me turn over?", "lang": "en"}
```

### PLAY_AUDIO

Play an mp3 served by the Core from its audio cache (`GET /audio/<sha256>.mp3`, proxied by the web
dev server).

| Field | Type | Notes |
|---|---|---|
| `id` | string | utterance id, sent back in AUDIO_DONE |
| `kind` | `"phrase"` \| `"echo"` \| `"system"` | see the table above |
| `url` | string | path on the Core server |
| `text` | string | what the audio says; spoken with browser speech if the file fails |
| `lang` | `"en"` \| `"es"` | |
| `cached` | bool | true = the file was already on disk, no request was made to ElevenLabs |

```json
{"type": "PLAY_AUDIO", "id": "b41e07c9d2aa", "kind": "echo", "url": "/audio/9b1f0e7c5a2d4e6f8a0b1c3d5e7f9a1b3c5d7e9f0a2b4c6d8e0f1a3b5c7d9e1f.mp3", "text": "Dolor", "lang": "es", "cached": true}
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

## Core -> Console and web dev panel

### METRICS

Sent right after every confirm clench (to `/ws/console` and `/ws/input`, not to the board), so the
demo can show that learning turns into speed (PRD D7, section 12): "Took 2 clenches, 0 s waiting
(Day 1: 5 clenches, 6 s)". Seconds are `scan_steps * scan_ms / 1000`.

| Field | Type | Notes |
|---|---|---|
| `text` | string | the confirmed sentence |
| `selections` | int | clenches since home, >= 1: every pick (going back does not undo one) plus 1 for the confirm |
| `scan_steps` | int | highlight moves the person waited through before those picks, >= 0 |
| `day1_selections` | int | the same message in Day 1 mode: menu.yaml order, no shortcut, the fixed phrase after the AI's sentences when the AI is on |
| `day1_scan_steps` | int | the same, in scan steps. When the message cannot be reached in Day 1 mode (an AI sentence on Suggested, an AI option from "Other..."), both Day 1 fields repeat the real numbers |

```json
{"type": "METRICS", "text": "Mija, estoy bien, llámame a las seis.", "selections": 2, "scan_steps": 0, "day1_selections": 5, "day1_scan_steps": 6}
```
