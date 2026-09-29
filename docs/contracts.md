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
| `/ws/board` | Patient board | READY, RESET, AUDIO_DONE, POINT, FACE_OK, TAP, SETTINGS (the trip layout switch) | SETTINGS, SCREEN, CONFIRM, SPEAK, PLAY_AUDIO, CLICK, CAR_ACTION, CAR_STATE, ACTION_RESULT |
| `/ws/console` | Caregiver console | SETTINGS | SETTINGS, METRICS, SHORTCUT_DEBUG and the same Core -> Board messages (mirror) |
| `/ws/input` | Sensor Service, web dev panel | CLENCH, DOUBLE_BLINK, LONG_CLENCH, STATE, SIGNAL, POINT, SETTINGS and RESET (dev panel) | SETTINGS, METRICS, SHORTCUT_DEBUG |

Every client gets the current SETTINGS the moment it connects, and again after every change.

REST (not WebSocket messages; the web dev server proxies `/api` and `/audio` to the Core):

| Route | What |
|---|---|
| `GET /api/head-range` | the saved head range (HeadRange below), or `null` before the first calibration (the board then uses its defaults) |
| `PUT /api/head-range` | save a HeadRange from the calibration overlay; answers with it. 422 when the sides are not around the center |
| `GET /api/time` | `{"t": <epoch seconds>}`, the Core's clock. A sensor on another device (the tablet) stamps gestures on it, since the Core refuses any gesture more than a second off its own clock |
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
| TAP | Board (touch or mouse) | Core | Pick this tile, or confirm the "Say this?" card, like a CLENCH |
| SETTINGS | Console, web dev panel / Core | Core / every client | Pointing mode, scan speed, language, speak picks, learning; the Core announces the current values |
| SCREEN | Core | Board, Console | What to draw and which tile is highlighted |
| CONFIRM | Core | Board, Console | "Send this?" screen before anything is spoken or sent |
| SPEAK | Core | Board, Console | Say something with browser speech (no cloud audio for it) |
| PLAY_AUDIO | Core | Board, Console | Play cloud TTS audio (ElevenLabs, cached on the laptop) |
| CLICK | Core | Board, Console | Play the short soft click for a picked "Other..." (in order with the echoes) |
| CAR_ACTION | Core | Board, Console | A trip control acts (or Pull over was confirmed): play its animation, input locked meanwhile |
| CAR_STATE | Core | Board, Console | The mock car's telemetry for the trip screen (speed, arrival, battery, temperature, windows, volume) |
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
{"type": "SIGNAL", "t": 1727300010.05, "ch": [12.5, -3.1, 40.2, 8.8], "connected": true, "profile": "taher", "emg": 18.0, "threshold": 35.0, "blocked": null}
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

### TAP

A touch or mouse press on the board: a caregiver helping, or testing without a headband (the tablet
has no keyboard for the stand-in). On a tile it picks that tile, exactly as a CLENCH would with that
tile highlighted (no clench look-back: the finger says which tile). On the "Say this?" sentence it
confirms, exactly as a CLENCH would; with `cancel` (a Cancel button on that screen, e.g. Pull over's)
it cancels the confirm screen at once. Everything else about a CLENCH applies (the 300 ms debounce,
the confirm step: a tap on a tile never speaks or sends).

The Core ignores a TAP whose `seq` is not the current screen's, a tile TAP when the board is not
scanning, a card TAP when nothing is waiting for confirmation, and any TAP while the go-back prompt
is open or the help countdown runs (a stray touch can neither answer the prompt nor stop a call for
help).

| Field | Type | Notes |
|---|---|---|
| `tile` | int or null | 0-based tile index on SCREEN `seq`; null = the "Say this?" card |
| `seq` | int or null | the SCREEN `seq` the tile belongs to; null with a null `tile` (both or neither) |
| `cancel` | bool | the Cancel button of the confirm screen (tile and seq null); default false |
| `t` | float | epoch seconds when tapped |

```json
{"type": "TAP", "tile": 3, "seq": 42, "cancel": false, "t": 1727300011.2}
```

```json
{"type": "TAP", "tile": null, "seq": null, "cancel": false, "t": 1727300015.0}
```

```json
{"type": "TAP", "tile": null, "seq": null, "cancel": true, "t": 1727300016.0}
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
| `trip` | bool (optional) | trip mode (`core/trip.py`): the board shows the trip screen (car controls, SCREEN `screen: "trip"`) instead of the menus. Session-only, default false; the dev panel's Start trip / End trip. A change while scanning switches at once, on the first tile |
| `trip_layout` | `"car"` \| `"split"` \| `"map"` (optional) | the trip screen's layout: the 3D car; the route map on the left half with the car and the tiles on the right (the trip menu's top level then shows only Windows, Pull over and Support); or the map above the tiles. Session-only, default `"car"`; the board's Car / Split / Map switch sends it on `/ws/board` |
| `learning` | bool (optional) | rank by the patient's history (PRD section 9). `false` = "Day 1 mode": menu.yaml order, the fixed Suggested list, no one-clench shortcut, no Jev, no history for the AI. Omit to keep the current value; default from `data/profile.yaml` (true). A change while scanning goes back to home |
| `onboarding` | bool (optional) | setup owns ordinary input: CLENCH, TAP and DOUBLE_BLINK cannot operate the hidden board. LONG_CLENCH remains available and DOUBLE_BLINK can still cancel help. Default false; cleared when the last board disconnects. Omit to keep it |

`trip_layout` is the preferred layout. While SCREEN `pointer` is `"scan"`, the trip uses the Car
layout and all six top-level controls, including when the preference is Split or Map. The Core
expands the actual tile list, preserves the highlighted control by id, and advances `seq` when the
tiles change. Once pointing resumes, the preferred layout returns at the current menu depth.

```json
{"type": "SETTINGS", "pointing_mode": "auto", "scan_ms": 1000, "lang": "es", "speak_picks": true, "learning": true, "long_clench_ms": 2500, "tile_switch_margin": 0.05, "muse_enabled": false, "onboarding": false, "trip": false, "trip_layout": "car"}
```

## Core -> Board

### SCREEN

What the board should draw. The board is "dumb": the Core owns the highlight position. Sent after
every change while scanning (level change, highlight move, language change, loading starts,
pointing mode change), and once a second during the help countdown.

| Field | Type | Notes |
|---|---|---|
| `screen` | `"menu"` \| `"suggestions"` \| `"help_countdown"` \| `"paused"` \| `"calibrating"` \| `"trip"` \| `"computer"` \| `"player"` | `suggestions` = the sentences for a picked leaf; `trip` = the trip menu (trip mode): up to 6 tiles, no "Other...", `path` = the trip levels opened (empty at its top); `computer` = managed Chromium is open; `player` = a video or playlist playing on the board, its controls as the tiles (no "Other...") |
| `seq` | int | >= 0. Goes up every time the tiles change (ids, labels or kinds), not when only the highlight moves. POINT echoes it |
| `tiles` | `{"id": string, "label": string, "kind": TileKind, "image"?: string}[]` | at most 6 (PRD D8), see below. `image` (an https URL, at most 500 characters) is only on media tiles (a video thumbnail, a playlist cover); every other tile has no `image` key |
| `highlight` | int \| null | 0-based index into `tiles`, null = nothing highlighted |
| `lang` | `"en"` \| `"es"` | |
| `path` | string[] | breadcrumb labels (current language) from home down to this level; `[]` at home. A step through "Other..." shows as `"Other"` / `"Otro"` |
| `countdown` | int \| null | optional, >= 0. Seconds left before the help alert fires; only set when `screen` is `"help_countdown"`, null (or absent) otherwise |
| `loading` | bool | optional, default false. True while the Core waits for AI options after a pick (at most 4 s); scanning is paused and the board shows "Finding options..." / "Buscando opciones..." |
| `pointer` | `"scan"` \| `"webcam"` \| `"gaze"` \| `"headtilt"` \| null | optional. Where the highlight comes from right now; null on the help countdown. `"scan"` while the pointing mode is Auto, Gaze or Head tilt means the fallback is on, and the board shows a small "Scanning" / "Escaneando" badge |

Tiles:

| `kind` | What | Picking it |
|---|---|---|
| `branch` | a menu category | opens the next level (the home "Suggested" opens the AI's sentences for right now, then its fixed phrases) |
| `leaf` | an option that leads to a sentence, from `data/menu.yaml` or made by the AI | opens the suggestions screen, or the CONFIRM screen with the fixed phrase when there is no AI |
| `suggestion` | a full sentence; `label` is the exact text | opens the CONFIRM screen with exactly that sentence |
| `car` | a trip menu level or control, trip screen only (`trip.windows`, `trip.windows.down.front_left`, `trip.temperature.warmer`, ...; the tree is in `core/trip.py`) | a level opens; a routine control acts at once (CAR_ACTION, input locked for its `ms`, same level after); `pull_over` and `support` open the CONFIRM screen |
| `back` | the trip menu's Back tile, last on every level below the top | up one level |
| `other` | always the last tile: "Other..." / "Otro..." | the next page of new options for the same path (AI, else the level's fixed `more` list). After 3 pages, or when there is nothing new (or no AI), the next pick loops back to the level's own options |

`id` is the dotted menu path (`need.pain.back`). The Core's own tile ends in `.other`
(`other` at home). Anything written by the AI starts with `ai:`: an AI option
`ai:need.pain.brazos`, an AI sentence `ai:need.pain.back.a_lot.s1`. The fixed phrase on a
suggestions screen keeps its leaf's id. The AI never chooses the action or the contact: an AI option
takes them from its level, an AI sentence from its leaf.

```json
{"type": "SCREEN", "screen": "menu", "seq": 7, "tiles": [{"id": "need.pain.back.a_little", "label": "Un poco", "kind": "leaf"}, {"id": "need.pain.back.a_lot", "label": "Mucho", "kind": "leaf"}, {"id": "need.pain.back.other", "label": "Otro...", "kind": "other"}], "highlight": 1, "lang": "es", "path": ["Necesito", "Dolor", "Espalda"], "countdown": null, "loading": false, "pointer": "webcam", "prompt": null, "corner": null}
```

**Suggestions screen** (PRD section 5 step 6). Up to 3 AI sentences, then the leaf's fixed phrase
(when the AI did not already write it), then "Other..." (more sentences):

```json
{"type": "SCREEN", "screen": "suggestions", "seq": 12, "tiles": [{"id": "ai:people.maria.text.s1", "label": "Mija, estoy bien. Llámame a las seis.", "kind": "suggestion"}, {"id": "ai:people.maria.text.s2", "label": "Mija, todo bien por aquí. Te quiero.", "kind": "suggestion"}, {"id": "people.maria.text", "label": "Mija, estoy bien, llámame a las seis.", "kind": "suggestion"}, {"id": "people.maria.text.other", "label": "Otro...", "kind": "other"}], "highlight": 0, "lang": "es", "path": ["Personas", "María", "Mensaje"], "countdown": null, "loading": false, "pointer": "scan", "prompt": null, "corner": null}
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
{"type": "SCREEN", "screen": "help_countdown", "seq": 12, "tiles": [], "highlight": null, "lang": "es", "path": [], "countdown": 5, "loading": false, "pointer": null, "prompt": null, "corner": null}
```

**Pointing** (PRD D2, A3.3a). The Core has one pointer slot, set live by SETTINGS `pointing_mode`:

| Mode | Highlight comes from | `pointer` |
|---|---|---|
| `scan` | the Core's scan timer, one tile every `scan_ms` | `"scan"` |
| `webcam` | the board's POINT messages (head turns); no timer. On new tiles the highlight stays at the same index until the board's POINT for the new `seq` arrives | `"webcam"` |
| `gaze` | starts scanning, follows only gaze POINTs once FACE_OK is true; returns to scanning after 3 s without eyes | `"gaze"` or `"scan"` |
| `auto` | starts scanning; follows the board when FACE_OK is true and a POINT arrives; back to scanning after 3 s without a face. The board sends gaze POINTs while an eye tracker sees the eyes, head (webcam) POINTs otherwise: gaze if available, else head, else scan | `"scan"`, `"gaze"` or `"webcam"` |
| `headtilt` | not built yet (needs the headband motion data): scans, and logs that it does | `"scan"` |

Picking is always a CLENCH on the highlighted tile. When the highlight follows the head or the eyes, the Core
picks the tile that was highlighted about 250 ms before the CLENCH arrived (`clench_lookback_ms` in
`data/profile.yaml`), because clenching the jaw can move the head slightly (PRD 3a "freeze on clench").

### Computer mode

Computer mode uses SCREEN with `screen="computer"`, empty `tiles` and `path`, null
`highlight` and `pointer`. The board remains connected and keeps its existing audio queue and
dev input panel. READY returns this screen and COMPUTER_STATE while Chromium is open. The latter
relays viewport rectangles so the board's existing head/eye tracker can point at browser choices.

```json
{"type":"SCREEN","screen":"computer","seq":20,"tiles":[],"highlight":null,"lang":"en","path":[],"countdown":null,"loading":false,"pointer":null,"prompt":null,"corner":null}
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

### Media on the board: SCREEN player and MEDIA

Home › Computer opens YouTube, Spotify and Web browser (Web browser is computer mode above). The
YouTube and Spotify levels list videos and playlists from `data/menu.yaml` (`media: {provider, id,
image?}`); their tiles carry `image`, and their "Other..." pages only through the level's `more`
list (the AI writes nothing for app or media levels):

```json
{"type":"SCREEN","screen":"menu","seq":31,"tiles":[{"id":"computer.youtube.lofi","label":"Lofi radio","kind":"branch","image":"https://i.ytimg.com/vi/jfKfPfyJRdk/hqdefault.jpg"},{"id":"computer.youtube.other","label":"Other...","kind":"other"}],"highlight":0,"lang":"en","path":["Computer","YouTube"],"countdown":null,"loading":false,"pointer":"scan","prompt":null,"corner":null}
```

Picking a video or playlist sends MEDIA `play` and opens the player screen: the board embeds the
provider's own player in its page (the person's own logins apply). Nothing is said or sent to
anyone, so there is no CONFIRM step. The tiles are the controls: Pause (Play when paused), Restart,
Volume − and Volume + (YouTube only) and Back; no "Other...", no corner button.

```json
{"type":"SCREEN","screen":"player","seq":32,"tiles":[{"id":"computer.youtube.lofi.player.pause","label":"Pause","kind":"leaf"},{"id":"computer.youtube.lofi.player.restart","label":"Restart","kind":"leaf"},{"id":"computer.youtube.lofi.player.volume_down","label":"Volume −","kind":"leaf"},{"id":"computer.youtube.lofi.player.volume_up","label":"Volume +","kind":"leaf"},{"id":"computer.youtube.lofi.player.back","label":"Back","kind":"leaf"}],"highlight":0,"lang":"en","path":["Computer","YouTube","Lofi radio"],"countdown":null,"loading":false,"pointer":"scan","prompt":null,"corner":null}
```

MEDIA (Core -> Board) plays or controls it:

| Field | Type | Notes |
|---|---|---|
| `action` | `"play"` \| `"pause"` \| `"resume"` \| `"restart"` \| `"volume_down"` \| `"volume_up"` \| `"stop"` | |
| `provider` | `"youtube"` \| `"spotify"` \| null | `play` only (required there, null otherwise) |
| `id` | string \| null | `play` only: the video / playlist id, `[A-Za-z0-9_-]{6,40}` |
| `title` | string \| null | `play` only: what is playing, at most 200 characters |

```json
{"type":"MEDIA","action":"play","provider":"youtube","id":"jfKfPfyJRdk","title":"Lofi radio"}
```

```json
{"type":"MEDIA","action":"pause","provider":null,"id":null,"title":null}
```

A control tile sends its MEDIA action and a new SCREEN (Pause <-> Play). Back, a double blink
(up one level), RESET, Home, Car mode or anything else that leaves the player screen sends `stop`.
A LONG_CLENCH pauses it (`pause`) so the help lines are heard, and the board hides the player
during the countdown; after a cancel the player screen comes back (the Core marks it paused).

### CONFIRM (communication board)

The "Send this?" screen. Nothing is spoken or sent until the person clenches here; a
DOUBLE_BLINK cancels (PRD D5).

| Field | Type | Notes |
|---|---|---|
| `text` | string | the exact sentence that will be spoken or sent |
| `action` | `"speak"` \| `"send_message"` \| `"place_call"` \| `"room_control"` \| `"help_alert"` \| `"pull_over"` \| `"support"` | from the action registry (`pull_over`: the trip screen's Pull over, "Pull over here?"; `support`: its Support, "Call rider support?") |

```json
{"type": "CONFIRM", "text": "Mija, estoy bien, llámame a las seis.", "action": "send_message"}
```

### BACK_PROMPT

A DOUBLE_BLINK on a menu, or on the "Say this?" screen, does not go back by itself any more: blinks
are easy to do by accident, and twice. The Core pauses scanning and sends `open: true`; the board shows
"Go back?" (or "Cancel this message?") with a bar running down over `timeout_ms`. A CLENCH inside that
time goes back (or cancels the message). Doing nothing is "no": the prompt closes, nothing changes,
and CLENCH is ignored for the next second so a clench meant for the prompt cannot pick a tile or send
the message. More DOUBLE_BLINKs while it is open are ignored. LONG_CLENCH still starts the help
countdown at once, and the help countdown is still cancelled by a DOUBLE_BLINK straight away. The
Core sends `open: false` whenever the prompt closes (confirmed, timed out, help, reset, new screen).
Sent to boards and consoles.

| Field | Type | Notes |
|---|---|---|
| `open` | bool | true = show the prompt, false = hide it |
| `kind` | `"menu"` \| `"confirm"` | menu = up one level; confirm = cancel the "Say this?" screen |
| `timeout_ms` | int | how long it stays open (3000); 0 when closing |

```json
{"type": "BACK_PROMPT", "open": true, "kind": "menu", "timeout_ms": 3000}
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

### CAR_ACTION

A trip control acted (Windows > Up / Down > a window, Temperature > Warmer / Cooler, Music > Louder /
Softer, Slow down), or Pull over was confirmed. The board plays that control's confirm animation for
`ms` (the other tiles fade, the picked one stays and grows a little, the tablet's 3D car shows the
control's line particles; Pull over: the drive eases to a stop and the screen takes a warm tint, no
particles). For a routine control the Core locks input for the same `ms`: CLENCH, TAP and POINT are
ignored, the highlight stays put, and the same screen comes back when it ends (so a control can be
repeated). LONG_CLENCH still starts the help countdown. Pull over's is sent with its confirm, before
the confirmed sentence is spoken. The controls are mocks: nothing leaves the laptop for a routine one.

| Field | Type | Notes |
|---|---|---|
| `action` | `"window_up"` \| `"window_down"` \| `"warmer"` \| `"cooler"` \| `"louder"` \| `"softer"` \| `"slow_down"` \| `"pull_over"` \| `"support"` | which control (`support` is never sent: it only confirms and calls) |
| `window` | `"front_left"` \| `"front_right"` \| `"rear_left"` \| `"rear_right"` \| `"all"` \| null | which window, for `window_up` / `window_down`; null otherwise |
| `ms` | int | how long the animation (and, for routine controls, the input lock) lasts, > 0 |

```json
{"type": "CAR_ACTION", "action": "window_down", "window": "front_left", "ms": 900}
```

### CAR_STATE

The (mock) car's telemetry for the trip screen's status strip: sent when trip mode starts, after
every control that changes it, once a minute as the ride goes on (arrival, battery), and to a board
that connects during a trip. The tablet's 3D scene drives at `speed_mph` (0 after Pull over).

| Field | Type | Notes |
|---|---|---|
| `speed_mph` | int | >= 0; Slow down takes 5 off (never below 10), Pull over stops the car |
| `eta_min` | int | minutes to arrival, >= 0 |
| `battery_pct` | int | 0 to 100 |
| `cabin_temp_f` | int | °F, 60 to 85; Warmer / Cooler move it by 1 |
| `windows` | object | `front_left`, `front_right`, `rear_left`, `rear_right`: % open, 0 (up) to 100 (down), 25 per Up / Down |
| `volume` | int | music volume 0 to 10; Louder / Softer move it by 1 |

```json
{"type": "CAR_STATE", "speed_mph": 32, "eta_min": 14, "battery_pct": 78, "cabin_temp_f": 72, "windows": {"front_left": 25, "front_right": 0, "rear_left": 0, "rear_right": 0}, "volume": 4, "phase": "EN_ROUTE", "music_playing": true, "on_highway": false}
```

### SCREEN: the corner button (Car mode)

On Home the Core adds a corner button outside the six-tile grid, "Car mode" (`corner`, kind `corner`,
tile index `len(tiles)`). Gaze, head, taps and the scan reach it like any tile; picking it opens the
confirm screen (`action` `car_mode`, "Start Car mode?"). In Car mode the corner is "Home": it leaves the
car screen without confirm and without ending the ride; the Car mode button brings the rider back to it.

```json
{"type": "SCREEN", "screen": "menu", "seq": 3, "tiles": [{"id": "suggested", "label": "Suggested", "kind": "branch"}], "highlight": 1, "lang": "en", "path": [], "countdown": null, "loading": false, "pointer": "gaze", "prompt": null, "corner": {"id": "corner.car_mode", "label": "Car mode", "kind": "corner"}}
```

### SCREEN: a Support question

When the car's Support team asks the rider something (core/car, proto `SupportQuestion`), the trip
screen gives way to `support_question`: the question in `prompt`, its options as `answer` tiles. Picking
one opens the confirm screen (`action` `support_answer`); nothing is sent before the confirm. With no
answer before the question's timeout the Core sends `SupportAnswer.no_response`. Help works as usual.

```json
{"type": "SCREEN", "screen": "support_question", "seq": 40, "tiles": [{"id": "support.q1.yes", "label": "Yes", "kind": "answer"}, {"id": "support.q1.no", "label": "No", "kind": "answer"}, {"id": "support.q1.not_sure", "label": "Not sure", "kind": "answer"}], "highlight": 0, "lang": "en", "path": [], "countdown": null, "loading": false, "pointer": "scan", "prompt": "Support asks: Are you hurt?", "corner": null}
```

### CAR_RESULT

The car's answer to a trip request (proto `ActionResult`), to boards, consoles and `/car-sim`. Low-safety
comfort controls (temperature, music, volume, windows) are sent without a confirm screen, as in
decisions #21; the Core says `message` only when such a control is `DELAYED` or `REJECTED`. For
confirmed high-safety requests (pull over, Support, drop-off, route) every answer is said.

```json
{"type": "CAR_RESULT", "request_id": "r-12", "action_id": "pull_over", "status": "DELAYED", "message": "We're on the highway. Pulling over at the next safe spot, in about 2 minutes.", "expected_in_seconds": 120, "rtt_ms": 152}
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

## Core <-> car simulator (`/ws/car-sim`)

`/car-sim` (a page for a second laptop) shows the mock car (core/car) and plays the car's side. It
receives `CAR_STATE`, `CAR_RESULT` and `CAR_LOG`, and sends `CAR_SIM`.

### CAR_LOG

One request or answer crossing the car link, with the round trip for answers.

```json
{"type": "CAR_LOG", "t": 1790000000.5, "direction": "to_car", "kind": "ActionRequest", "summary": "pull_over (confirmed, clench)", "request_id": "r-12", "rtt_ms": null}
```

### CAR_SIM

`ask` sends a Support question to the rider; `set` changes the mock car's situation (on the highway,
the ride's phase); `plan` plans a trip to the address in `text` (a caregiver typing any destination):
layers 1 and 2 are computed live, or the demo trip stays after 20 s or a failure. `start_ride` starts
a ride and shows Car mode on the board (the car's action, not a rider request, so no confirm);
`end_ride` ends the ride and returns the board to Home. A new ride is `BOARDING` (parked, 0 mph) until
the rider confirms a route; then `EN_ROUTE`.

```json
{"type": "CAR_SIM", "command": "ask", "text": "Are you hurt?", "options": ["Yes", "No", "Not sure"], "timeout_s": 30, "urgent": true, "on_highway": null, "phase": null}
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

### INPUT_EVENT

Sent to `/ws/console` and `/ws/input` only, once for every input gesture the Core receives from the
headband or the keyboard stand-in, whether or not it acted on it. The board's input log (press `/`)
shows these: someone checking whether the headband is picking up a clench, a long clench or a double
blink has to be able to see the gestures the Core REFUSED, and why it refused them.

| Field | Type | Notes |
|---|---|---|
| `t` | float | when the gesture happened (the sender's clock) |
| `kind` | `"CLENCH"` \| `"LONG_CLENCH"` \| `"DOUBLE_BLINK"` | which gesture |
| `source` | `"muse"` \| `"dev"` | the headband Sensor Service, or the keyboard stand-in |
| `accepted` | bool | false = the Core ignored it |
| `reason` | string \| null | why it was ignored; null when accepted |
| `strength` | float \| null | 0 to 1, CLENCH only |
| `duration` | float \| null | seconds held, LONG_CLENCH only |

```json
{"type": "INPUT_EVENT", "t": 1777001234.5, "kind": "CLENCH", "source": "muse", "accepted": false, "reason": "Muse input is paused", "strength": 0.62, "duration": null}
```

## Muse integration: clench and double blink

`/ws/sensor` accepts CLENCH, LONG_CLENCH, DOUBLE_BLINK and SIGNAL from one headless Muse service.
It receives SETTINGS, including `long_clench_ms` and the optional `muse_enabled`
boolean. Core starts with Muse paused (`false`); omitted settings preserve the value.
Web controls on the board and console enable/pause it via `/ws/console`.
Keyboard `/ws/input` remains independent. The service detects CLENCH and LONG_CLENCH with the
person's calibrated jaw threshold and DOUBLE_BLINK with MNE on AF7/AF8 (no eye threshold needed).
DOUBLE_BLINK's `t` is when it was sent, not the blink itself (MNE commits blinks at least 0.5 s late).

SIGNAL adds optional nullable fields: `connected` (boolean), `profile` (string),
`emg` (finite nonnegative microvolts), `threshold` (finite positive microvolts),
and `blocked` (string explanation, null when ready). Existing senders may omit them.
Muse sends telemetry at 4 Hz; `ch` contains four channel standard deviations in
TP9/AF7/AF8/TP10 order, or [] when unavailable, never raw waveform arrays.
Core forwards it to boards and consoles, caches it for new consoles, and sends
`connected: false` when the service socket closes. The web marks samples older
than two seconds disconnected. Core ignores commands with stale telemetry,
a blocked signal, no patient board, or event timestamps outside the past second, and reports every
one of them as an INPUT_EVENT with `accepted: false`.

A service disconnect or the last board disconnect pauses Muse (`muse_enabled: false`). A brief
headband dropout does NOT: the gate above already refuses commands while the signal is disconnected,
so pausing on every Bluetooth reconnect only flapped the switch and made the caregiver re-enable the
input every few seconds. Muse pauses on headband loss only once it has lasted
`MUSE_LOSS_GRACE_S` (10 s).
The next session must be enabled again, and the detector requires 600 ms of
released jaw before accepting a new gesture. Commands are never queued for replay
across a lost connection. A short clench emits on release; a long one emits help
at the configured duration and suppresses its short-clench release.

B remains the keyboard/caregiver Back/Cancel stand-in while deliberate eye input
is redesigned. No real blink triggers app navigation in this integration.

## Computer pointing

`COMPUTER_STATE` (Core -> board/console) carries `active`, independent monotonic `seq`, `tiles`
(at most nine `{id,label,left,top,right,bottom}` rectangles normalized to Chromium's viewport),
`highlight` (index or null), `paused`, and `pointer` (the existing active-pointer enum). It is
sent for rendered browser layouts, selection/settings changes and exit, and replayed on READY.
Its sequence changes when choices or geometry change. It does not change the board SCREEN seq.

```json
{"type":"COMPUTER_STATE","active":true,"seq":7,"tiles":[{"id":"menu","label":"Browser menu","left":0.75,"top":0.8,"right":0.98,"bottom":0.95}],"highlight":0,"paused":false,"pointer":"gaze"}
```

`COMPUTER_POINT` (board -> Core, `/ws/board` only) carries that `seq`, `tile` (0..8 or null),
`source` (`webcam` or `gaze`), `found`, `status` (`tracking`, `no_tracker`, `lost`, `camera_error`,
`starting`, `off`) and finite Unix-second `t`. The board sends a heartbeat at least every 250 ms
while samples arrive; only locally filtered target indices and status leave it, never video.
Stale sequences, invalid indices and points during help/busy/dev-panel states cannot select.
One second without samples marks tracking lost; Auto then uses its existing 3-second fallback.
The same clench/back/help events perform actions. The board's optional gaze dwell setting also
applies to computer choices; help, busy states and the open Dev panel pause dwell.
For a dwell pick, `pick: true` on COMPUTER_POINT keeps the action tied to that layout sequence;
the core ignores stale picks and uses the existing clench handler for a valid gaze pick.

```json
{"type":"COMPUTER_POINT","seq":7,"tile":0,"source":"gaze","found":true,"status":"tracking","t":1790474400.25,"pick":false}
```

`COMPUTER_TELEMETRY` (board -> Core, `/ws/board` only) mirrors the cursor and local controls at
10 Hz during computer mode. It carries normalized nullable `x`/`y`, `show_cursor`, `dwell`,
`progress` (0..1), `camera` (on/off/starting/error), nullable numeric `yaw`/`pitch`,
`eye_connected`, nullable saved `head_range` and nullable `voice_source`. No video is included.
The core forwards it to the existing DevPanel component in Chromium's isolated world.

```json
{"type":"COMPUTER_TELEMETRY","x":0.3,"y":0.5,"show_cursor":true,"dwell":false,"progress":0,"camera":"off","yaw":null,"pitch":null,"eye_connected":true,"head_range":null,"voice_source":"Browser"}
```

`COMPUTER_CONTROL` (Core -> board, originating only in the trusted Chromium controls) requests
the board's existing local control: `cursor`, `dwell`, `retry`, `calibrate`, `calibration_done`
or `head_range`. `value` is the toggle value; `head_range` is required by the save action.
Calibration pauses board pointing while the existing calibration component runs in Chromium;
the board saves through its existing API and acknowledges through telemetry.

```json
{"type":"COMPUTER_CONTROL","action":"cursor","value":true,"head_range":null}
```
