# Event contracts

Every message that crosses a process boundary in Clench (PRD A4). The code versions are
`core/contracts.py` (Pydantic) and `web/src/contracts.ts` (TypeScript). **Change all three files
together**; `tests/` checks that they list the same messages and that every example below parses.

General rules:

- Every message is one small JSON object sent over the WebSocket, with a `type` field naming it.
- `t` is a timestamp in float seconds since the Unix epoch.
- `tile` and `highlight` are 0-based indexes into the `tiles` of the SCREEN the board is showing.
  Every SCREEN carries a `seq` that goes up when its tiles change; a POINT names the `seq` its `tile`
  belongs to, so a late POINT can never highlight a tile on a newer screen.
- Unknown fields are rejected. To add a field, change the contract, don't just send it.
- The keyboard stand-in (dev panel in `web/src/dev/`) sends CLENCH, DOUBLE_BLINK and LONG_CLENCH
  exactly as the Sensor Service would. The Core cannot tell them apart (PRD D15).
- Invalid messages are logged and ignored by the Core; the connection stays open.

## WebSocket endpoints (Core, `ws://127.0.0.1:8000`)

The web dev server proxies `/ws/*` to the Core, so the browser connects to `ws://localhost:5173/ws/...`.

| Endpoint | Who connects | Accepted messages | Receives |
|---|---|---|---|
| `/ws/board` | Patient board | READY, RESET, AUDIO_DONE, POINT, FACE_OK | SETTINGS, SCREEN, CONFIRM, SPEAK, PLAY_AUDIO, CLICK, ACTION_RESULT |
| `/ws/console` | Caregiver console | SETTINGS | SETTINGS, METRICS, SHORTCUT_DEBUG and the same Core -> Board messages (mirror) |
| `/ws/input` | Sensor Service, web dev panel | CLENCH, DOUBLE_BLINK, LONG_CLENCH, STATE, SIGNAL, POINT, SETTINGS and RESET (dev panel) | SETTINGS, METRICS, SHORTCUT_DEBUG |

Every client gets the current SETTINGS the moment it connects, and again after every change.

REST (not WebSocket messages; the web dev server proxies `/api` and `/audio` to the Core):

| Route | What |
|---|---|
| `GET /api/head-range` | the saved head range (HeadRange below), or `null` before the first calibration (the board then uses its defaults) |
| `PUT /api/head-range` | save a HeadRange from the calibration overlay; answers with it. 422 when the sides are not around the center |
| `GET /audio/<sha256>.mp3` | cached ElevenLabs audio named in PLAY_AUDIO |
| `GET /computer/start` | local managed-browser launcher: YouTube, Spotify, Google, Exit |

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
| RESET | Board ("Click to start"), web dev panel ("Reset to Home") | Core | Back to Home, highlight on the first tile |
| AUDIO_DONE | Board | Core | A phrase or system line finished (or failed, or was interrupted) |
| SETTINGS | Console, web dev panel / Core | Core / every client | Pointing mode, scan speed, language, speak picks, learning; the Core announces the current values |
| SCREEN | Core | Board, Console | What to draw and which tile is highlighted |
| CONFIRM | Core | Board, Console | "Send this?" screen before anything is spoken or sent |
| SPEAK | Core | Board, Console | Say something with browser speech (no cloud audio for it) |
| PLAY_AUDIO | Core | Board, Console | Play cloud TTS audio (ElevenLabs, cached on the laptop) |
| CLICK | Core | Board, Console | Play the short soft click for a picked "Other..." (in order with the echoes) |
| ACTION_RESULT | Core | Board, Console | A confirmed message, call or room action succeeded or failed |
| METRICS | Core | Console, web dev panel | What a confirmed message cost in clenches and scan steps, and what it would have cost in Day 1 mode |
| SHORTCUT_DEBUG | Core | Console, web dev panel | Why the one-clench Suggested shortcut is on or off, after every Home render |

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

Two blinks within about 700 ms: go up one menu level, answer No, or cancel (help countdown,
confirm screen). Pages opened with "Other..." count as the level they came from, so Home > I need >
Other > Other goes back to Home. Single blinks are never sent (PRD D4).

| Field | Type | Notes |
|---|---|---|
| `t` | float | seconds |

```json
{"type": "DOUBLE_BLINK", "t": 1727300003.4}
```

### LONG_CLENCH

Clench held `long_clench_ms` (2.5 s by default, `data/profile.yaml`, announced in SETTINGS): start
the 5 second help alert countdown, which a DOUBLE_BLINK cancels. The dev panel's hold-Space uses the
same value.

| Field | Type | Notes |
|---|---|---|
| `t` | float | seconds |
| `duration` | float | seconds held, > 0 |

```json
{"type": "LONG_CLENCH", "t": 1727300009.9, "duration": 2.6}
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

The person is facing a tile. Sent when the tile changes, and once for every new SCREEN (so the Core
knows the tile under the head on the new tiles). The Core still decides what is highlighted (PRD
A3.3): it only uses a POINT while scanning in Webcam mode (source webcam), Gaze mode (source
gaze) or Auto mode (either, once it follows the board), and ignores
any POINT whose `seq` is not the current screen's.

| Field | Type | Notes |
|---|---|---|
| `source` | `"webcam"` \| `"gaze"` \| `"headtilt"` | webcam (head pose) and gaze (an eye tracker, `docs/eye-tracking.md`) come from the board, headtilt from the Sensor Service |
| `tile` | int | 0-based, >= 0, into the tiles of SCREEN `seq` |
| `seq` | int | >= 0, the `seq` of the SCREEN the board measured the tile on |
| `t` | float | seconds |

```json
{"type": "POINT", "source": "webcam", "tile": 3, "seq": 42, "t": 1727300011.2}
```

### FACE_OK

Can the board see the person: the face for head pointing, the eyes when an eye tracker drives the
highlight (gaze). Sent by the board when it changes (after about 300 ms steady), when the board
connects, and as `false` when the camera stops or fails (or the eye tracker stops feeding points in
Gaze mode). In Auto mode the Core falls back
to Scan when the face has been lost for 3 s, and switches back to webcam when a face is seen again
and a POINT arrives. The Core also treats the last board disconnecting as `false`. Only POINT and
FACE_OK leave the browser; video never does (PRD section 11).

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

### RESET

Back to Home: the Core clears the screen stack, leaves the loading, confirm or speaking state, and
shows Home with the highlight on tile 0 (Suggested); in Scan the scan timer restarts from 0. Nothing
is spoken or sent. The board sends it once when "Click to start" is clicked (before READY; not on a
reconnect, so a network blip never moves the person), and the web dev panel's "Reset to Home" button
sends it on `/ws/input`. Ignored during the help countdown: a reloaded board must never cancel a call
for help (a DOUBLE_BLINK does).

No fields besides `type`.

```json
{"type": "RESET"}
```

### AUDIO_DONE

Sent by the board when a SPEAK or PLAY_AUDIO of kind `phrase` or `system` ends: finished, failed
(after the browser-speech fallback), or cleared by a system line. Never sent for `echo`.

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

Caregiver changes pointing mode, scan speed, language, speak picks, learning, the long-clench time or the tile switch margin. Applies at once, no
restart (PRD P1). The web dev panel also sends it (scan speed slider, EN/ES toggle, Speak picks and
Day 1 mode toggles) on `/ws/input`.

The Core sends the same message the other way, with every field filled in, to each board, console
and input client when it connects, and to all of them after every SETTINGS it receives (the sender
included). Screens show these values instead of assuming defaults.

| Field | Type | Notes |
|---|---|---|
| `pointing_mode` | `"auto"` \| `"scan"` \| `"webcam"` \| `"gaze"` \| `"headtilt"` | Auto is the default |
| `scan_ms` | int | ms per tile in Scan mode, > 0, default 1000 |
| `lang` | `"en"` \| `"es"` (optional) | omit to keep the current language; default `"en"` |
| `speak_picks` | bool (optional) | say each picked tile aloud as it is picked (an `echo`); omit to keep the current value; default from `data/profile.yaml` (true) |
| `long_clench_ms` | int (optional) | how long a clench must be held to count as a LONG_CLENCH, 1000 to 5000 ms; omit to keep the current value; default from `data/profile.yaml` (2500). The Sensor Service and the dev panel's hold-Space use it |
| `tile_switch_margin` | float (optional) | webcam / gaze pointing: how far the point must be inside a new tile before the highlight moves there, as a share of that tile's width / height, 0 to 0.2; omit to keep the current value; default from `data/profile.yaml` (0.05). The board applies it; the dev panel has a slider |
| `learning` | bool (optional) | rank by the patient's history (PRD section 9). `false` = "Day 1 mode": menu.yaml order, the fixed Suggested list, no one-clench shortcut, no Jev, no history for the AI. Omit to keep the current value; default from `data/profile.yaml` (true). A change while scanning goes back to home |

```json
{"type": "SETTINGS", "pointing_mode": "auto", "scan_ms": 1000, "lang": "es", "speak_picks": true, "learning": true, "long_clench_ms": 2500, "tile_switch_margin": 0.05}
```

## Core -> Board

### SCREEN

What the board should draw. The board is "dumb": the Core owns the highlight position. Sent after
every change while scanning (level change, highlight move, language change, loading starts,
pointing mode change), and once a second during the help countdown.

| Field | Type | Notes |
|---|---|---|
| `screen` | `"menu"` \| `"suggestions"` \| `"help_countdown"` \| `"paused"` \| `"calibrating"` \| `"computer"` | `suggestions` = the sentences for a picked leaf; `computer` = managed Chromium is open |
| `seq` | int | >= 0. Goes up every time the tiles change (ids, labels or kinds), not when only the highlight moves. POINT echoes it |
| `tiles` | `{"id": string, "label": string, "kind": TileKind}[]` | at most 6 (PRD D8), see below |
| `highlight` | int \| null | 0-based index into `tiles`, null = nothing highlighted |
| `lang` | `"en"` \| `"es"` | |
| `path` | string[] | breadcrumb labels (current language) from home down to this level; `[]` at home. A step through "Other..." shows as `"Other"` / `"Otro"` |
| `countdown` | int \| null | optional, >= 0. Seconds left before the help alert fires; only set when `screen` is `"help_countdown"`, null (or absent) otherwise |
| `loading` | bool | optional, default false. True while the Core waits for AI options after a pick (at most 4 s); scanning is paused and the board shows "Finding options..." / "Buscando opciones..." |
| `pointer` | `"scan"` \| `"webcam"` \| `"gaze"` \| `"headtilt"` \| null | optional. Where the highlight comes from right now; null on the help countdown. `"scan"` while the pointing mode is Auto or Head tilt means the fallback is on, and the board shows a small "Scanning" / "Escaneando" badge |

Tiles:

| `kind` | What | Picking it |
|---|---|---|
| `branch` | a menu category | opens the next level (the home "Suggested" opens the AI's sentences for right now, then its fixed phrases) |
| `leaf` | an option that leads to a sentence, from `data/menu.yaml` or made by the AI | opens the suggestions screen, or the CONFIRM screen with the fixed phrase when there is no AI |
| `suggestion` | a full sentence; `label` is the exact text | opens the CONFIRM screen with exactly that sentence |
| `other` | always the last tile: "Other..." / "Otro..." | the next page of new options for the same path (AI, else the level's fixed `more` list). After 3 pages, or when there is nothing new (or no AI), the next pick loops back to the level's own options |

`id` is the dotted menu path (`need.pain.back`). The Core's own tile ends in `.other`
(`other` at home). Anything written by the AI starts with `ai:`: an AI option
`ai:need.pain.brazos`, an AI sentence `ai:need.pain.back.a_lot.s1`. The fixed phrase on a
suggestions screen keeps its leaf's id. The AI never chooses the action or the contact: an AI option
takes them from its level, an AI sentence from its leaf.

```json
{"type": "SCREEN", "screen": "menu", "seq": 7, "tiles": [{"id": "need.pain.back.a_little", "label": "Un poco", "kind": "leaf"}, {"id": "need.pain.back.a_lot", "label": "Mucho", "kind": "leaf"}, {"id": "need.pain.back.other", "label": "Otro...", "kind": "other"}], "highlight": 1, "lang": "es", "path": ["Necesito", "Dolor", "Espalda"], "countdown": null, "loading": false, "pointer": "webcam"}
```

**Suggestions screen** (PRD section 5 step 6). Up to 3 AI sentences, then the leaf's fixed phrase
(when the AI did not already write it), then "Other..." (more sentences):

```json
{"type": "SCREEN", "screen": "suggestions", "seq": 12, "tiles": [{"id": "ai:people.maria.text.s1", "label": "Mija, estoy bien. Llámame a las seis.", "kind": "suggestion"}, {"id": "ai:people.maria.text.s2", "label": "Mija, todo bien por aquí. Te quiero.", "kind": "suggestion"}, {"id": "people.maria.text", "label": "Mija, estoy bien, llámame a las seis.", "kind": "suggestion"}, {"id": "people.maria.text.other", "label": "Otro...", "kind": "other"}], "highlight": 0, "lang": "es", "path": ["Personas", "María", "Mensaje"], "countdown": null, "loading": false, "pointer": "scan"}
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
{"type": "SCREEN", "screen": "help_countdown", "seq": 12, "tiles": [], "highlight": null, "lang": "es", "path": [], "countdown": 5, "loading": false, "pointer": null}
```

**Pointing** (PRD D2, A3.3a). The Core has one pointer slot, set live by SETTINGS `pointing_mode`:

| Mode | Highlight comes from | `pointer` |
|---|---|---|
| `scan` | the Core's scan timer, one tile every `scan_ms` | `"scan"` |
| `webcam` | the board's POINT messages (head turns); no timer. On new tiles the highlight stays at the same index until the board's POINT for the new `seq` arrives | `"webcam"` |
| `gaze` | the board's POINT messages from an eye tracker (`docs/eye-tracking.md`); otherwise like `webcam` | `"gaze"` |
| `auto` | starts scanning; follows the board when FACE_OK is true and a POINT arrives; back to scanning after 3 s without a face. The board sends gaze POINTs while an eye tracker sees the eyes, head (webcam) POINTs otherwise: gaze if available, else head, else scan | `"scan"`, `"gaze"` or `"webcam"` |
| `headtilt` | not built yet (needs the headband motion data): scans, and logs that it does | `"scan"` |

Picking is always a CLENCH on the highlighted tile. When the highlight follows the head or the eyes, the Core
picks the tile that was highlighted about 250 ms before the CLENCH arrived (`clench_lookback_ms` in
`data/profile.yaml`), because clenching the jaw can move the head slightly (PRD 3a "freeze on clench").

### Computer mode

Computer mode uses SCREEN with `screen="computer"`, empty `tiles` and `path`, null
`highlight` and `pointer`. The board remains connected and keeps its existing audio queue and
dev input panel. READY returns this screen while Chromium is open. No COMPUTER_STATE message is
needed: browser targets and overlay state stay inside the core's Playwright connection.

```json
{"type":"SCREEN","screen":"computer","seq":20,"tiles":[],"highlight":null,"lang":"en","path":[],"countdown":null,"loading":false,"pointer":null}
```

In computer mode CLENCH selects a band, target or browser-menu item. DOUBLE_BLINK goes from
targets/menu to bands; at bands it does nothing. A text placeholder has one Cancel choice.
LONG_CLENCH starts the same five-second help countdown, mirrored as a red banner in Chromium.
After cancel or firing, the session resumes its browser selection. Closing Chromium during the
countdown does not cancel help; the session returns Home when the countdown ends. RESET closes
computer mode except during help. SETTINGS.scan_ms applies to the computer scan independently of
the board's pointing mode. Echoes remain SPEAK/PLAY_AUDIO kind `echo` and respect speak_picks.

The page bridge uses `page.add_init_script` and `page.expose_binding` for target snapshots;
there is no page WebSocket. It cannot submit gestures. Foreground Space/B/hold input uses a
Chromium isolated-world binding that accepts only trusted keyboard events and forwards the same
CLENCH/DOUBLE_BLINK/LONG_CLENCH types to Session.handle. The headband and board dev panel still
use `/ws/input` unchanged.

### CONFIRM (communication board)

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
| `echo` | the label of the tile just picked | on every CLENCH pick while scanning, when speak picks is on; never on DOUBLE_BLINK, the confirm clench, a `suggestion` tile (the confirm step says the sentence) or "Other..." (a CLICK instead) | 70% | no |
| `system` | a fixed line from the Core | help countdown start, help alert fired | 100% | yes (ignored by the Core) |

The Core sends PLAY_AUDIO when it has (or can make in time) ElevenLabs audio for the text, and
SPEAK when it cannot (no key, no internet, too slow, service errors): the board then uses browser
speech, so nothing is ever left silent. An echo is never held back: when its audio is not cached
the Core sends SPEAK at once (in pick order) and makes the audio in the background for next time.

Board rules (one queue, one audio player):

- Echoes play in order, one after another, never cutting each other off: every picked word is
  heard, including the last pick before the suggestions or confirm screen.
- A phrase waits for the echoes queued before it, then plays.
- A system line clears the queue and plays at once, interrupting whatever is playing. A cleared or
  interrupted phrase or system line still gets its AUDIO_DONE.
- At most 6 items wait; on overflow the oldest waiting echo is dropped (and logged).
- An echo whose PLAY_AUDIO has not started playing within 300 ms is said with browser speech
  instead, in its place in the queue.
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

### CLICK

A picked "Other..." says no word: the board plays a short soft click instead (made with WebAudio,
no audio file). It joins the sound queue like an echo, in pick order, at echo volume. Sent only when
speak picks is on. Never answered with AUDIO_DONE. No fields besides `type`.

```json
{"type": "CLICK"}
```

### HeadRange (REST body, not a message)

Sent by the board's calibration overlay with `PUT /api/head-range` and returned by `GET`. Degrees of
head yaw and pitch as the board measures them from MediaPipe's face transformation matrix. The board
maps yaw from `left_yaw` (x = 0, the left edge) through `center_yaw` (x = 0.5) to `right_yaw` (x = 1),
and pitch from `up_pitch` (top) through `center_pitch` to `down_pitch` (bottom), piece by piece, so an
uneven range works. Left and right must lie on opposite sides of the center, at least 2 degrees
away; the same for up and down.

| Field | Type |
|---|---|
| `center_yaw`, `center_pitch` | float, looking at the middle of the screen |
| `left_yaw`, `right_yaw` | float, looking at the left / right edge |
| `up_pitch`, `down_pitch` | float, looking at the top / bottom edge |

For example `{"center_yaw": 0.5, "center_pitch": -2.0, "left_yaw": -18.0, "right_yaw": 17.0,
"up_pitch": 9.0, "down_pitch": -12.0}`.

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

### SHORTCUT_DEBUG

Sent after every Home render (right after its SCREEN), and again when Jev's answer arrives while
Home is still showing, to `/ws/console` and `/ws/input` only. It says whether picking Suggested would
go straight to the confirm screen, and why. The shortcut fires when learning is on and EITHER the
history share of the top phrase is at least 0.6, OR the history share is at least 0.4 AND Jev picks
the same phrase with confidence at least 0.45. Jev never blocks a shortcut the history alone
qualifies for. A cancel of the phrase in the last 24 h scales both numbers down.

| Field | Type | Notes |
|---|---|---|
| `top` | string \| null | the history's top Suggested phrase (history and fixed phrases, no AI sentences); null in Day 1 mode or with no phrases |
| `history_share` | float | 0 to 1: its share of everything confirmed around this hour (+/- 1 h, recency-weighted); 0 with fewer than 3 recency-weighted uses |
| `jev` | `"off"` \| `"waiting"` \| `"answered"` | off = no Jev key, paused, or Day 1 mode; waiting = no answer yet |
| `jev_pick` | string \| null | the phrase Jev picked; null unless `jev` is `"answered"` |
| `jev_confidence` | float \| null | 0 to 1, Jev's confidence in its pick |
| `shortcut` | bool | picking Suggested opens the confirm screen with `top` |
| `reason` | string | e.g. `"history share 0.72 >= 0.6"`, `"history share 0.50 >= 0.4 and Jev agrees (0.52 >= 0.45)"`, `"history share 0.50 < 0.6 and no Jev answer yet"`, `"learning off (Day 1 mode)"` |

```json
{"type": "SHORTCUT_DEBUG", "top": "Mija, estoy bien, llámame a las seis.", "history_share": 0.72, "jev": "answered", "jev_pick": "Mija, estoy bien, llámame a las seis.", "jev_confidence": 0.52, "shortcut": true, "reason": "history share 0.72 >= 0.6"}
```
