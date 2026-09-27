# Decisions log

Running list of the places where the build differs from the PRD (`docs/Clench_PRD.pdf`), newest
chunk last. The PRD stays the source of truth for everything not listed here.

Status: **done** = built, **planned** = agreed, not built yet.

## 1. Keyboard stand-in is a web dev panel (chunk 2, done)

- PRD: A3.1 and A8 list a Python `KeyboardSource` in `sensor/sources/`.
- Now: the stand-in lives in the web app (`web/src/dev/DevPanel.tsx`). Space = CLENCH, hold Space
  1.5 s = LONG_CLENCH, B = DOUBLE_BLINK. It sends the same events on `/ws/input`, so the Core cannot
  tell them from headband events.
- Why: no second program to start during development, it runs on any laptop with a browser, and it
  sits next to the board it drives.

## 2. No SMS: messages go through a Telegram bot, calls through Twilio Voice (chunk 3, done)

- PRD: P0 "Text a family member: a real SMS arrives on a real phone"; actions `send_text` and
  `place_call` through Twilio.
- Now: US carriers block SMS from app numbers that are not registered for A2P 10DLC, which takes
  weeks. Text messages go through a Telegram bot (Bot API `sendMessage`). Calls use Twilio
  Programmable Voice, which does not need SMS registration: the call reads the message aloud twice.
  The action is renamed `send_text` -> `send_message`.
- Why: the demo needs a message that really arrives. Telegram works in minutes and is free.

## 3. Voice output moves to ElevenLabs (chunk 4, done)

- PRD: A2 / A3.2 voice with Google Cloud TTS.
- Now: ElevenLabs (`eleven_flash_v2_5`) through `core/voice.py`, audio cached on the laptop
  (`data/audio_cache/`, `audio_cache` table), browser speech as the automatic fallback with no key,
  no internet, a slow answer or service errors. Every menu label, leaf phrase and help line is made
  in the background at startup (prewarm), so the demo plays from disk.
- Phone calls still use Twilio's own `<Say>` voice. Playing ElevenLabs audio on a call needs
  `<Play>` with a public URL, and the audio lives on the laptop (the Core is not reachable from the
  internet). Hosting it would mean uploading the patient's sentences, against D14.

## 4. Each picked word is spoken as it is picked (chunk 4, done; interrupt rules superseded by #10)

- PRD: D5 says nothing is spoken without the confirm screen.
- Now: auditory feedback ("speak picks", `speak_picks` in `data/profile.yaml`, on by default,
  switchable live with SETTINGS) says each picked tile's label (kind `echo`) as it is picked, before
  the next level shows. D5 still applies to the sentence and to anything sent to a phone. A single
  picked word is the patient's own deliberate choice, so echoing it is allowed. No echo on a double
  blink or on the confirm clench.
- Interrupt rules on the board (one audio player): a new echo interrupts an older echo; a phrase or
  system line interrupts anything; an echo that arrives while a phrase or system line is playing is
  dropped, so it never cuts the person's sentence. Echoes play at 70% volume. The Core also drops an
  echo whose audio arrives after something newer was said, since a late word is only confusing.

## 5. "Other..." tile on every level (chunk 5, done; "Spell it" superseded by #11)

- PRD: section 8 has a "Say something" home tile with "AI phrases from context, Spell it"; D11
  wants spelling reachable as a way out.
- Now: the session adds "Other..." ("Otro...") as the last tile of every level, so `menu.yaml` holds
  at most 5 items per level (D8's 6 tiles still hold). It shows up to 5 new options for the same path
  from the AI. After two "Other..." picks in a row the tile reads "Spell it" ("Deletrear"); for now
  it only says "Spelling is coming soon." (chunk 10 builds spelling).
- Home is Suggested, I need, People, How I feel, Room, Other.... "Say something" is gone: its
  phrases moved into Suggested's fixed list (Thank you / How are you) and the home "Other..."
  fallback (Yes, No, Good morning, Wait a moment).
- Items that no longer fit in 5 moved to a new optional `more` list on their branch, which is what
  "Other..." shows when the AI has nothing: I need > Too hot or cold, Pain > Arms, How I feel > Happy.
  With no AI and no `more` list, "Other..." turns straight into "Spell it" on the same screen.
- Why: one fixed way out on every screen, instead of a category the person has to remember to visit.

## 6. Ranking with TypeSafe Jev as the AI prior (chunk 6, done)

- PRD: section 9 score, whose "AI's guess for this moment" part is left open.
- Now: the local score (`core/rank/score.py`) always runs; TypeSafe Jev, a structured-choice model,
  is its optional AI prior (`core/rank/jev.py`). Each ranking asks Jev ONE Choice question ("Which
  option does the patient most likely want right now?"), criteria = tile ids -> labels or sentences
  (up to 255), state = a short plain summary: time and weekday, language, menu path, body state, the
  last 5 confirmed messages with times, the top 10 phrases with counts and their usual hour. Jev's
  probabilities are the score's AI part (weight 0.3). Gemini still writes new options and sentences.
- Access path, first configured wins, both plain REST through httpx (no SDK needed):
  a) TypeSafe API (`TYPESAFE_API_KEY`): `POST https://api.typesafe.ai/v1/systemone`,
     `Authorization: Bearer <key>`, body `{"model": "jev-latest", "state": ..., "questions": {...}}`,
     reply `{"answers": {"next": {"choice", "probabilities", "confidence"}}}`.
  b) Cloudflare Workers AI (`CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN`):
     `POST https://api.cloudflare.com/client/v4/accounts/<account>/ai/run`, `Authorization: Bearer
     <token>`, body `{"model": "typesafe/jev", "input": {"state": ..., "questions": {...}}}` (from the
     Cloudflare model page, developers.cloudflare.com/ai/models/typesafe/jev, 2026-09-26). The reply
     is read as `{"answers": ...}`, also inside Cloudflare's `{"result": ..., "success": ...}` envelope.
  c) neither: no Jev, AI prior 0.
  Used in this build: the TypeSafe API. `scripts/test_jev.py --send` on 2026-09-26 answered in
  0.4 to 0.5 s (well inside the 1.5 s timeout) and picked the María text for the sample evening.
- Never blocks: the screen shows the history ranking at once. Jev's answer re-ranks it quietly (same
  highlight position, no echo) only if the person has done nothing since; otherwise it is kept for
  when they come back to that screen. Cached 10 minutes on (candidate ids, hour, last outcome id,
  language); circuit breaker as for the voice (off 5 min after 401/402/403 or 3 failures in a row).
  Day 1 mode never calls Jev.

## 7. AI layer: Gemini writes sentences and new options (chunk 5, done)

- PRD: A3.5 has one method, `rank_and_suggest(ctx)` returning ranked option ids plus up to 3
  sentences.
- Now: `LLMProvider` (`core/suggest/provider.py`) has two methods, both returning validated Pydantic
  models: `compose(ctx)` (up to 3 sentences for a leaf, or for "right now" with an empty path) and
  `more_options(ctx)` (up to 5 new options, each a tile label plus the sentence it says). Ranking is
  left to the learning chunk (#6). `LLM_PROVIDER` = gemini (default) | fake | claude | openai;
  claude and openai are stubs that log "not built yet" and run with fixed phrases.
- Model: `gemini-3.8-flash`, the newest stable Flash model in the live model list
  (`client.models.list()` on 2026-09-26; `gemini-3.5-flash`, `gemini-flash-latest` and previews were
  also listed). Structured JSON output (response schema from Pydantic), temperature 0.2, thinking
  level LOW (the model rejects MINIMAL). `scripts/test_gemini.py --send` measured 1.0 to 1.5 s per
  request, well inside the 4 s budget.
- Suggestions screen (PRD section 5 step 6): picking a leaf shows the AI's sentences, then the
  fixed phrase, then "Other...". With no AI, a timeout or an error the leaf goes straight to the
  confirm screen with its fixed phrase, as before (D6). D5 is unchanged: a sentence is only said after
  the confirm clench.
- Safety: the AI only writes labels and text. An AI option takes action and contact from its level
  (the action and the contact every leaf below it shares, otherwise speak and no contact: People >
  Maria gives speak to Maria, Room gives room_control). An AI sentence takes them from its leaf. Extra
  fields in the AI's JSON are dropped by the models.

## Smaller choices (chunk 3)

- The help countdown travels in SCREEN (`screen: "help_countdown"`, optional `countdown` field)
  instead of a new HELP message, because the PRD already lists `help_countdown` as a screen.
- The `contacts` table stores the names of the env vars that hold a contact's phone number and
  Telegram chat id (`phone_env`, `telegram_chat_env`), not the values (PRD A7 has `phone`). Numbers
  live only in `.env`.
- `events` has three columns more than PRD A7: `lang`, plus `text` and `contact` on confirmed sends,
  so the caregiver history and the learning chunk can read them without walking the menu.
  `node_id` is the dotted menu path (`need.pain.back.a_lot`), because ids are only unique among
  siblings; `path` is the breadcrumb labels as a JSON list. The help alert is `node_id = "help"`.
- The contacts' "Call" phrases were rewritten for the person being called ("Mija, quiero verte. Por
  favor ven cuando puedas.") since the call now reads them aloud to the contact.
- ACTION_RESULT's `contact` is the display name in the current language ("María"), so the board can
  show it without knowing the contacts.
- The help alert message and the "Calling Maria" speech use the board's current language.
- Dry run never fails on missing config: it logs what would be sent plus a warning about what a real
  send would lack, and the board shows the gray demo toast. A fresh clone runs with no `.env`.
- Dedupe counts every attempt, successful or not, and also applies in dry run, so demo mode behaves
  like the real thing. A suppressed repeat returns `ok=false, "duplicate suppressed"`.

## Smaller choices (chunk 4)

- Utterance ids are 12 hex characters from a random UUID. `AUDIO_DONE` names the id; the session
  only leaves SPEAKING on the id of the phrase it is waiting for.
- PLAY_AUDIO has one field more than planned: `cached`, so the dev panel can show "ElevenLabs
  (cached)" versus "ElevenLabs" without asking the Core.
- System lines never hold the session. When the help alert fires, the board goes home at once while
  "Calling Maria" plays, instead of waiting in SPEAKING as in chunk 3. The toasts still show the
  call and the message. The countdown start line ("Calling for help. Double blink to cancel.") plays
  over the red countdown screen. Only a phrase shows the speaking screen.
- The ElevenLabs request also sends `language_code` ("en" / "es") for the models that accept it
  (Flash and Turbo v2.5), so one-word labels such as "Dolor" get the right accent. Output is
  `mp3_44100_128`.
- A cache hit is decided by the file on disk; the `audio_cache` row is the record (PRD A7 columns plus
  `created_at`; `voice` holds the voice id, the model is part of the hash).
- Two requests for the same text share one ElevenLabs call (prewarm and a live pick, or a repeat).
  Prewarm sends one request at a time, labels first, and stops if the circuit breaker opens.
- The circuit breaker counts a 401, a 402 or any error whose status or message says "quota" as fatal
  (off for 5 minutes at once); other errors, including network errors, open it after 3 in a row. A
  success resets the count. A request that only missed the live timeout is not an error.
- The dev panel's Speak picks toggle starts at On (the profile default) because the Core does not
  report settings back to the board yet. (Fixed in chunk 5: the Core now announces SETTINGS.)

## Smaller choices (chunk 5)

- The core sends SETTINGS to every client on connect and after every change (also to `/ws/input`,
  which used to receive nothing), so the dev panel shows real values and waits for them instead of
  assuming defaults.
- `scripts/test_*.py` only print what they would do unless run with `--send`.
- What the AI sees (D14): path labels, language, local hour, patient name, the leaf's fixed phrase,
  the 20 most used phrases in that language, the last 5 confirmed sentences in that language (the
  help alert's fixed line is left out) and the contacts' first names. Plus `shown`, the labels or
  sentences already on screen, which "not repeating the ones already shown" needs.
- Validation drops items one by one (empty, over 90 characters, duplicates ignoring case and end
  punctuation); an option label also has to be at most 40 characters (a tile is read from a bed). A
  response that is not the right JSON at all counts as an error.
- Cache key: (method, path, language, hour, fixed phrase, what is shown), 10 minutes. Failures are
  not cached. One request is shared by everyone asking the same thing at once.
- A rate limit (HTTP 429) pauses the AI for 1 minute and a bad key (401/403) for 5 minutes, with one
  log line, so every level does not wait on requests that will fail. While paused the app behaves as
  with no key.
- Prefetch asks, per level, for each leaf's sentences, the level's "Other..." batch and (at home)
  the Suggested sentences. That is up to 6 requests per level; see "Problems" in the chunk 5 report
  about free-tier rate limits. (Chunk 6: now one request per level, see "Smaller choices (chunk 6)".)
- The loading wait uses the session timer (4 s) on top of the Suggester's own 4 s limit. Clenches are
  ignored while loading; a double blink stops waiting and resumes scanning; a long clench starts the
  help countdown.
- "Other..." on a suggestions screen asks `compose` again with the shown sentences excluded, so it
  brings more sentences rather than options.
- Home "Suggested" with AI: up to 3 AI sentences (picking one goes straight to the confirm screen),
  then its fixed items that the AI did not already say, 5 at most. Without AI it is the fixed list.
- ElevenLabs quota: when a suggestions screen (or the AI part of Suggested) opens, only the top
  sentence is synthesized in the background. Any sentence gets synthesized once it is on the confirm
  screen (before the confirm clench, so it is usually cached when said).
- Echoes: "Other..." is said as "Other" / "Otro"; sentences on the suggestions screen are never
  echoed; "Spell it" is not echoed, its system line says it all.
- Ids: AI options are `ai:<level path>.<slug of the label>` (`ai:need.pain.brazos`), AI sentences
  `ai:<leaf path>.s1`..`s3`; a confirmed AI sentence is logged with node_id `ai:<leaf path>` and the
  sentence in `text`. "Other..." and "Spell it" picks are logged as `<level path>.other` / `.spell`.
  The breadcrumb shows each "Other..." step as "Other" / "Otro".
- A language switch drops every screen holding AI text (it cannot be translated) and goes back to the
  menu level below it. A sentence already on the confirm screen stays as it is.
- After "Spell it" appears in place (nothing new to show), the highlight stays on it, so one clench
  picks it.

## 8. Learning: ranking, stability rule, shortcut and Day 1 mode (chunk 6, done; shortcut rule superseded by #12)

- PRD: section 9 layer 2 (score, weights 0.4 / 0.2 / 0.1 / 0.3), D7 (most likely first), "Showing
  it in the demo" (Day 1 vs a simulated week).
- Score (`core/rank/score.py`): use (recency-weighted, half-life 3 days) + time of day (hour
  histogram, +/- 1 hour at half weight, also recency-weighted) + body state (urgent items when the
  state is elevated; 0 until the sensor chunk sends STATE) + AI prior (Jev) - recent rejections
  (confirm screens cancelled in the last 24 h, 1 - 0.5^n). The PRD gives no rejection weight; it is
  0.3, like the AI's. Weights live in `data/profile.yaml` and are normalized. Each part is scaled to
  0..1 across the candidates being ranked (use and time divided by the best one), so the weights
  compare like with like. Evidence is every confirmed send and cancelled confirm of the last 30 days.
- A branch scores as the sum of its descendants' leaf evidence (a node id without its `ai:` prefix,
  so an AI sentence confirmed under People > Maria > Text counts for that leaf). Tiles are looked up
  by path (the same in both languages), sentences by their text in the current language.
- Stability rule (motor memory matters for AAC users): menu levels keep the menu.yaml order; an item
  moves above the one before it only when its score is at least 1.5x that one's (`hysteresis` in the
  profile) and at least 0.02 higher. The anchor is always menu.yaml, not the last order shown, so
  the same history always gives the same menu. Home Suggested is pinned first, "Other..." is never an
  item (always last). Full reordering on the suggestions screen and in the Suggested list.
- Suggested with learning on: the AI's sentences for right now, the patient's 5 most used sentences
  (each with the action and contact of the menu leaf it was confirmed under, never from the text; a
  sentence under an AI option takes what its level passes on), then the fixed phrases, one of each
  sentence, ranked, 5 shown. A history sentence that is its leaf's fixed phrase keeps the leaf's id
  (`people.maria.text`); others are `ai:<leaf path>.h1`...
- One-clench shortcut: with learning on, picking Suggested goes straight to the confirm screen with
  the top learned phrase (history and fixed phrases, no AI sentences, so the guess is the patient's
  own habit) when it is a confident guess. "History score share" is read as: of everything confirmed
  around this hour (+/- 1 h, recency-weighted) among the candidates, the share that was this phrase,
  and the phrase needs at least 3 recency-weighted uses (about 4 days of daily use). A cancel of it in
  the last 24 h scales the confidence down as it does the score (one cancel halves it). A double blink
  on the shortcut's confirm screen opens the full Suggested list instead of going home. D5 unchanged.
- Shortcut with Jev on: the prompt asked for Jev confidence >= 0.8. Measured on the live API with a
  seeded week, Jev picks the right phrase but its calibrated confidence stays at 0.44 to 0.6, so that
  rule alone would switch the shortcut off whenever Jev is configured. Now: with Jev on, Jev must pick
  the same top phrase (a veto), and then Jev confidence >= 0.8 OR the history share >= 0.6 opens the
  shortcut. Without a Jev answer yet, the history rule alone decides.
- Jev state: besides the fields the prompt listed, each top phrase carries its usual hour ("(13,
  around 10:00)"). One short line per phrase, and it raised Jev's confidence in the right phrase in a
  live check (0.44 -> 0.52).
- Day 1 mode = SETTINGS `learning: false` (default from `data/profile.yaml`, broadcast like the other
  settings, a dev panel toggle): menu.yaml order, Suggested is its fixed list (no AI sentences, no
  history sentences), no shortcut, no Jev, and the AI is told nothing about the history. Switching it
  while scanning goes back to home, so the before and after show at once. The value is not written
  back to profile.yaml (like speak picks).
- The metric: the PRD says "5 steps became 1" (D7, section 13). With the confirm clench required
  (D5) the best case is 2: Suggested, then confirm. So the demo number is "5 -> 2": People > María >
  Text > (fixed phrase after the AI's 3 sentences) > confirm is 5 clenches and 6 scan steps in Day 1
  mode with the AI on (4 clenches, 3 steps without AI), and 2 clenches, 0 steps after the week.
  METRICS (new message, consoles and dev panel only) reports both after every confirm.

## Smaller choices (chunk 6)

- One Gemini request per level: `level_bundle(ctx)` returns the sentences for every leaf on the level,
  its "Other..." options and (at home) the Suggested sentences in one structured reply; each part is
  cached under the key compose() / more_options() use, so picks find it (or share the request in
  flight). Only missing parts are asked; a single missing part goes as a plain compose /
  more_options. A leaf the model leaves out is not cached (its pick asks again). The suggestions
  screen's "Other..." still uses compose (one request). The bundle may use up to 2,048 output tokens.
  Requests in the last minute are logged at debug level (`clench.suggest`).
- The history cache and the Jev cache key use the id of the last confirmed send or cancel (not of any
  event), so plain picks do not invalidate them. A seed loaded while the core runs shows up at the
  next screen without a restart.
- `urgent: true` is on I need > Pain, I need > Bathroom and the Nurse contact (help); a branch counts
  as urgent when anything in it is. There is no "Can't breathe" item yet. STATE's level is stored and
  used from the next screen on; it only reorders (D10).
- Metrics: selections count every accepted clench pick since home (a pick undone by a double blink
  still counts: it was effort), scan steps every highlight move while scanning. The Day 1 cost walks
  the menu.yaml path of the leaf the message was said under, "Other..." counting as the tile after the
  level's items for a `more` item. A message Day 1 mode cannot reach the same way (an AI sentence on
  Suggested, an AI option from "Other...") reports its real numbers for both.
- `scripts/seed_demo.py` needs no `--send`: it only writes the local database. `--load` writes the
  same rows the core writes (a pick per level, then the confirmed send and the phrase count), from a
  fixed random seed (same week every time). `--focus-hour H` sends the María text twice at H every
  day and moves any other habit within an hour of H two hours away, so the text is the top Suggested
  phrase and the shortcut is on at H. The seed is Spanish (Luis's language); an English board ranks
  menu tiles from it but shows no history sentences. (Chunk 7: the seed is bilingual now, see
  "Smaller choices (chunk 7)".)
- The CircuitBreaker from the voice service is shared (it takes a name and fallback text now).

## 9. Webcam pointing: POINT carries the screen, and a clench looks back (chunk 7, done)

- PRD: D2 / A3.3a (pointing modes; the board "maps the angle to a tile, smooths it and sends POINT
  only when the tile changes"; calibrate center and edges; sticky edges; "freeze on clench: lock the
  highlight for about 300 ms"), A6 (head turn to highlight under 150 ms), A7 (`head_range_json`),
  section 11 (video never leaves the laptop, a light while the camera is on).
- SCREEN has a `seq` that goes up whenever its tiles change (ids, labels or kinds; not when only the
  highlight moves), and POINT names the `seq` its tile belongs to. The Core ignores any other `seq`,
  so a POINT measured on the old tiles can never highlight a tile on a new screen. The board sends a
  POINT once for every new `seq`, then only when the tile changes.
- SCREEN also has an optional `pointer` ("scan" | "webcam" | "headtilt", null on the help countdown):
  where the highlight comes from right now. The board shows the "Scanning" badge when it is "scan" in
  Auto or Head tilt mode.
- Freeze on clench is built as a look-back instead of a lock: the session keeps the last 2 s of
  highlight changes, and when the highlight follows the head a CLENCH picks the tile that was
  highlighted 250 ms before it arrived (`clench_lookback_ms` in `data/profile.yaml`, 0 = off). A lock
  would have to start before the clench is known; the look-back gets the same result after the fact.
  It is measured from when the CLENCH reaches the Core (localhost, a few ms), not from the event's
  `t`, so a sensor clock that disagrees cannot pick a tile from another screen. It never reaches past
  the current screen (a screen younger than 250 ms picks the tile it started with). Scan mode picks
  the highlighted tile, as before. The prompt asked for 250 ms; the PRD says about 300 ms.
- The board maps the head pose to a point on the screen, not to a fixed grid: calibrated yaw to x and
  pitch to y, piece by piece either side of the center (so an uneven range works), then the tile
  whose box on screen holds the point, or the nearest. So it works for any layout, including long
  sentence tiles.
- Sticky edges: a new tile is taken only once the point is 15% of that tile's own width / height
  inside it. A point in a gap between tiles never moves the highlight; a point past the grid's edge
  moves to the nearest tile only if even its shrunk box is closer than the current tile.
- The head range is saved in the database profile (`profile.head_range_json`, PRD A7) through REST
  (`GET` / `PUT /api/head-range`, a `HeadRange` model in both contract files) rather than in
  `data/profile.yaml`: the YAML is hand-written and in git (writing it back would drop its comments),
  and a calibration belongs to the person and seat on this laptop, like the history. Before the first
  calibration the board uses defaults for a laptop camera above the screen (18 degrees either side,
  center 8 degrees down, 12 up or down).

## Smaller choices (chunk 7)

- Auto starts scanning, and switches to webcam only when FACE_OK is true and a POINT arrives (as the
  prompt says), so the highlight moves at once even while the camera is starting or refused. It falls
  back after 3 s without a face, from the tile that was highlighted. A face back within 3 s cancels
  the fallback. The session remembers the last FACE_OK and hands it to a new pointer when the mode
  changes. When the last board disconnects the Core treats it as FACE_OK false.
- Webcam mode never falls back (that is what Auto is for): with no face the highlight stays where it
  is, and a camera problem says "Set Pointing mode to Auto or Scan" on the board.
- On new tiles the Webcam pointer keeps the same index (the head has not moved, and the 3x2 grid
  puts it in the same place) until the board's POINT for the new `seq` corrects it, instead of
  jumping to tile 0 for a moment.
- Head tilt is a ScanPointer that logs one warning when chosen; POINT with source "headtilt" is not
  used yet. The badge reads "Head tilt is not ready yet: scanning".
- Head turns are not "scan steps": METRICS counts only highlight moves made by the scan timer.
- A mode change applies at once while scanning (same tiles, same highlight, a new SCREEN with the new
  `pointer`); while confirming, speaking or on the help countdown it applies from the next screen.
- Head pose: yaw and pitch come from the third column of MediaPipe's facial transformation matrix
  (read column-major, as MediaPipe's own three.js samples do). Smoothing: exponential, 35% of each
  new frame at about 25 frames a second. The GPU delegate is tried first; the CPU is used if it fails
  to start or a frame fails on it.
- FACE_OK is debounced 300 ms (a blink or a hand does not flip Auto), sent again on every reconnect
  (the Core may have restarted), and sent as false when the camera stops or fails. POINT goes out
  only while a face is reported, never during calibration.
- Calibration: 1.5 s "get ready", then 1.5 s per dot (center, left, right, top, bottom), keeping the
  frames after the first 0.5 s of each step; the median of each step is the range. It fails (nothing
  saved, the overlay says why) when a step saw fewer than 5 face frames or a side is not at least 2
  degrees from the center on the opposite side; the Core checks the same rule before saving.
- The MediaPipe wasm is copied from `node_modules/@mediapipe/tasks-vision` (the same version as the
  JS) and the model is downloaded from Google's model storage by `web/scripts/mediapipe-assets.mjs`
  after `npm install` and before `dev` / `build`; both land in `web/public/mediapipe/`, git-ignored.
  A failed download never fails the npm command; the board then says face tracking could not start.
- The cursor dot toggle is a per-browser convenience (localStorage), not a Core setting.
- vitest 5 was added for the web's pure functions (`web/src/facetrack/*.test.ts`); MediaPipe itself is
  not in unit tests. npm 10.9 crashes resolving vitest 5's optional peers, so it was installed with
  npm 11 (`npx npm@11 install -D vitest`); `npm install` inside `web/` and `npm --prefix web ci`
  work from the lockfile with npm 10.9. (`npm --prefix web install`, the old setup line, fails with
  ENOENT on npm 10.9 even before this chunk; CLAUDE.md now says `npm --prefix web ci`.)
- Demo seed: every pattern has `phrase_en` and `phrase_es` (both checked against the menu), and
  `--load` writes each simulated use once in each language by default (`--lang es|en|both`). Writing
  both languages doubles the evidence per path, but every score part is scaled across the candidates
  being ranked, so menus, Suggested and the shortcut come out the same as a one-language week.

## 10. Echo queue instead of interrupts (chunk 7.5, done; supersedes the interrupt rules in #4)

- Tested by a person: fast picks cut earlier words off (a new echo interrupted the old one), and the
  Core dropped a late echo when something newer had been said, so some picked words were never heard.
- Now the board has one sound queue (`web/src/board/queue.ts`): echoes and the "Other..." click play
  in order, one after another, never cutting each other off. A phrase waits for the echoes queued
  before it. A system line (help) clears the queue and plays at once. At most 6 items wait; on
  overflow the oldest waiting echo is dropped (a phrase only if nothing else waits) and logged.
- An echo is never held back by the Core: when its audio is not cached the Core sends SPEAK at once
  (browser voice, in pick order) and makes the ElevenLabs audio in the background for next time,
  instead of waiting up to 2.5 s (which is what let a later cached word overtake it). The prompt's
  "if cached audio isn't ready within 300 ms" is applied on the board: a PLAY_AUDIO echo that has not
  started playing 300 ms after its turn comes is said by the browser voice in its place.
- Removed: "a new echo interrupts an older echo", "an echo during a phrase or system line is
  dropped", and the Core's "late echo is dropped".

## 11. "Other..." pages loop; no "Spell it" (chunk 7.5, done; supersedes "Spell it" in #5)

- Each "Other..." pick shows the next page of new options. After 3 AI pages, or when there is
  nothing new (the AI returns nothing and the level's `more` list is used up, or there is no AI),
  the next pick loops back to the level's own options (the page the first "Other..." was picked
  on), from its first tile. The `more` list still counts as a page when the AI has nothing, so with
  no key Home > Other... shows Yes / No / Good morning / Wait a moment and the next pick goes back
  to Home. "Spell it", its system line and the `.spell` tile id are gone; spelling (PRD D11) is left
  for its own chunk.
- A DOUBLE_BLINK always goes up one menu level: "Other..." pages count as the level they came from,
  so Home > I need > Other > Other goes to Home. A suggestions screen is one level (above its leaf's
  level).
- "Other..." says no word: a CLICK message (Core -> Board) plays a short soft click made with
  WebAudio (a 60 ms sine blip, 1.6 to 0.8 kHz, at echo volume), no audio file. A new message rather
  than a new utterance kind, since it has no text and no AUDIO_DONE. Sent only with speak picks on.

## 12. Suggested shortcut: Jev helps, never gates (chunk 7.5, done; supersedes the Jev rule in #8)

- The shortcut fires when learning is on and EITHER the top phrase's history share is >= 0.6, OR
  the share is >= 0.4 AND Jev picks the same phrase with confidence >= 0.45. Jev disagreeing never
  blocks what the history alone allows. Jev alone (little history) is no longer enough.
- The "top phrase" is ranked by the history only (no Jev prior), so "Jev's pick matches the history
  top phrase" compares two independent guesses. A cancel in the last 24 h scales both numbers down.
- SHORTCUT_DEBUG (new message, consoles and the dev panel only) goes out after every Home SCREEN and
  again when Jev's answer arrives while Home shows: top phrase, history share, Jev status / pick /
  confidence, and yes/no with a reason. The dev panel shows it as one line.
- ACTIONS_DRY_RUN changes nothing here: a test runs the same week and two shortcut sends with dry run
  on and off (Telegram mocked) and gets the same confirms, METRICS and events.

## 13. RESET, and every new screen starts on its first tile (chunk 7.5, done)

- RESET (board or dev panel -> Core): Home, stack cleared, highlight on tile 0 (Suggested), scan timer
  from 0. The board sends it once when "Click to start" is clicked (before READY), not on a
  reconnect, so a network blip never moves the person. The dev panel has "Reset to Home".
- RESET leaves loading, confirming and speaking (nothing is said or sent), but is ignored during the
  help countdown: a reloaded board must never cancel a call for help; a double blink does.
- RESET always sends a new SCREEN `seq` (even when Home already shows), so a POINT for the old screen
  cannot move the highlight, and it puts the head / gaze pointers on tile 0 too until their next POINT.
- Scan mode already started every new screen on tile 0 with a full scan step; this is now tested. A
  Jev re-rank of the same screen and returning from the help countdown keep the highlight (same
  screen).

## 14. Long clench 2.5 s and a 5% tile switch margin, both in the profile (chunk 7.5, done)

- `long_clench_ms` (default 2500, 1000 to 5000) and `tile_switch_margin` (default 0.05, 0 to 0.2) are
  in `data/profile.yaml` and announced in SETTINGS (optional fields, settable live), because the dev
  panel's hold-Space and the board's sticky edges must use the Core's value. The 5 s help countdown
  is unchanged. The dev panel has a 0 to 20% margin slider (sent after the slider settles, like scan
  speed). The Core does not check LONG_CLENCH's `duration`; the Sensor Service will use the value.

## 15. Gaze slot for eye tracking (chunk 7.5, done)

- `web/src/facetrack` now has pluggable screen-point sources (`source.ts`): `head` (MediaPipe head
  pose, as before) and `gaze` (`gaze.ts`, fed by other code with x, y in 0..1, found, confidence
  0..1; also reachable as `window.clenchGaze` for a separately loaded tracker). `usePointing.ts`
  replaces `useHeadPointing.ts` and turns the active source into POINT / FACE_OK the same way.
- New pointing mode `gaze` (contracts, `core/pointer/gaze.py`): behaves like Webcam but follows POINT
  with source `"gaze"`. POINT `source` and SCREEN `pointer` gain `"gaze"`.
- Auto: gaze if available, else head, else scan. "Available" = a gaze sample in the last 500 ms with
  found and confidence >= 0.5. The board decides between gaze and head (it has both); the Core's
  Auto follows either POINT source once the person is seen and shows which in SCREEN `pointer`.
  The head camera stays on in Auto while gaze drives, so the head takes over at once.
- FACE_OK now means "the active source sees the person" (eyes for gaze). In Gaze mode an eye tracker
  that stops feeding counts as not seen, and the board says "No eye tracker connected".
- The interface is documented for the teammate's tracker (`kushagra/`) in `docs/eye-tracking.md`.

## Smaller choices (chunk 7.5)

- `web/vite.config.ts` reads `CORE_URL` for its proxy target, so a second core (8100) and web app
  (5273) can run next to the usual ones for checks.
- The dev panel grew (Reset, margin slider, eye tracker and Shortcut lines); it now scrolls when the
  screen is short instead of running off the top.
- `test_a_recent_cancel_turns_the_shortcut_off` only passed before 18:30 on 2026-09-26 (the cancel is
  logged at the wall clock, the ranker's clock was fixed at 18:30 that day); it now moves the cancel
  next to the ranker's clock.
# Computer mode, part 1 (2026-09-27)

- The user's part-1 design replaces PRD B2/B4's task-running AI with direct band/target scanning.
  Home's Room tile becomes Computer / Computadora. `computer: true` is a menu entry with no
  action or phrase; it cannot become an AI-confirmed action. Room action code is retained and
  tested with a custom menu fixture. The demo seed's obsolete TV habit becomes the existing Tired
  phrase so seed validation still works; old shared databases are not edited.
- Core-owned Playwright Chromium uses a separate persistent `data/browser-profile/`, maximized
  with native viewport sizing. Browser work runs on a dedicated event-loop thread because Windows
  uvicorn's selector loop cannot start Playwright's subprocess. Core gestures, scan timers and help
  remain on the core loop. Relaunch waits for the old profile owner to close.
- The viewport's four equal horizontal bands are assigned by each target's center. Only occupied
  bands scan, followed by Browser menu. Targets read top-to-bottom, with tops within 12 pixels
  grouped left-to-right. Eight slots include More: seven targets per page when pagination is
  needed, with the last page looping to the first. This is the user's explicit computer-mode
  exception to the communication board's six-tile rule; SCREEN still has at most six tiles.
- Element ids are stable for the life of a DOM element/document. Discovery runs after load,
  scrolling, resize and throttled DOM changes; a periodic check catches SPA URL changes. A vanished
  highlighted element returns to bands rather than silently selecting a neighbor. DOM refreshes
  preserve scan progress. Navigation resets to bands. Text fields focus with a real click and
  offer the part-2 placeholder and Cancel.
- Overlay DOM is built with createElement in a Shadow DOM, without innerHTML or a page WebSocket.
  add_init_script + expose_binding carry target snapshots. The exposed page binding rejects
  gestures: trusted Space/B/hold input uses a separate Chromium isolated world and native CDP
  binding, so a site's JavaScript cannot manufacture a help request. Headband/dev-panel events
  continue through the existing Session handler. Hold always means help; the requested overlay
  wording "hold = back" conflicts with this safety path, so it says "Hold = help" instead.
- Help keeps the existing five-second confirmation and system audio. Computer scanning pauses;
  the overlay shows a red countdown. Firing and cancel both return to the browser selection. A
  browser closed during help never cancels the countdown and returns Home after it ends. Pick
  labels use the board's existing echo queue and speak_picks setting. No page content goes to AI.
- Navigation allows HTTPS youtube.com/google.com (including subdomains), exactly
  open.spotify.com, and the configured local launcher path/origin. Credentials, unusual ports,
  other local paths, other protocols, popups and downloads are rejected. CDN/media subresources
  are allowed so videos can load; service workers are disabled so they cannot bypass routing.
  Labels use a conservative case-insensitive substring denylist, including Spanish equivalents,
  both during discovery and immediately before clicking. This is a prototype policy, not a
  semantic classifier for every possible transaction. Login is a caregiver setup action in the
  separate persistent profile, outside active patient mode; the active allowlist is not widened
  for Spotify's account-domain redirects.
- Contracts add only SCREEN.screen="computer". Targets and scan state stay inside core/computer;
  the board keeps its socket, dev panel and audio queue mounted. The launcher route is
  /computer/start; COMPUTER_START_URL defaults to port 8000 and must be 8001 for isolated checks.
- Local Chromium integration runs by default on 8001 using temporary profiles and a Trusted Types
  CSP. It captures a trusted click without contacting YouTube, checks blocking/recovery and
  keyboard input isolation. Actual YouTube smoke tests require CLENCH_NETWORK_TESTS=1.

## Computer overlay usability repair (2026-09-27)

- Playwright MCP inspection reproduced outlines slicing launcher buttons, dimming half a chosen
  row, a status strip covering YouTube search, duplicate controls, and thumbnail labels of "true".
  The overlay now uses a single yellow active outline around the actual group or target, muted
  teal outlines for the current target page's alternatives, and a navy control dock. The dock
  shows the current label, scan position and gesture hints rather than every long label at once.
  Browser menu and the text placeholder have their own readable panels.
- Design tokens: navy #142b38, white #f5fafb, yellow #ffda60, teal #6caea8 and help red #ae1737.
  Segoe UI keeps the local Windows UI familiar. Yellow identifies the current selectable choice;
  there are no animated sweeps or pulses. Group/target outlines and the dim cutout use real bounds,
  not viewport quarter edges. The launcher fits short windows with responsive button heights.
- Four horizontal buckets remain. Occupied buckets are labelled Group 1 onward without gaps.
  Controls within one YouTube video card use its thumbnail center for bucket assignment, so a
  video title, thumbnail and action menu do not fall into different groups. Duplicate links to
  the same video in that card share one thumbnail target when the thumbnail is visible. YouTube
  marks these duplicate thumbnail links aria-hidden for screen readers; this specific link is
  still eligible when visually visible. Hidden ancestors, inert state and hit testing still apply.
- The dock normally stays at the bottom and moves to the top for a bottom-edge selection. This
  keeps fixed page/video controls reachable without excluding the bottom of the viewport. Error
  messages move with it. Menu panels also fit short windows. Help remains the existing countdown.
- Shadow DOM nodes are created once and updated in an animation frame; scanning no longer tears
  down the overlay or initiates another full target discovery. DOM/scroll discovery remains
  throttled. Live target identity survives moving between bands or pagination pages. Navigation
  and a vanished target get a fresh scan interval; slow actions visibly pause selection.
- Short video titles are display labels only. All available action labels, including associated
  video title attributes, are checked at discovery and again before clicking. The injected
  denylist survives an empty render during navigation. No board event contract changed; group
  membership and the optional card band position stay internal to the browser adapter.
- Regression checks cover persistent overlay nodes, complete target outlines, dock avoidance,
  menu bounds at 1000x650 / 800x480 / 390x650, duplicate video controls, blocked labels hidden by
  friendly titles, and selection stability after layout shifts. The real YouTube visual checks
  use Playwright MCP; automated network tests remain opt-in.

## Patient feedback and follow-up order (2026-09-27)

User testing of the managed Chromium browser found these remaining gaps:

- Backtick does not open the Dev panel inside Chromium. Add access to the Dev panel and its
  settings in the foreground managed browser, including the backtick shortcut.
- Chromium currently uses scanning only. Extend patient pointing to head/webcam, gaze and
  eye-tracker input so these modes can select website controls as they do on the board. Preserve
  clench selection, back and the independent help path. Do not present an unconnected eye tracker
  as working gaze input.
- The user dislikes the Group 1 / Group 2 interaction and the current overlay appearance. The
  earlier visual repair is not accepted as the final UI. Revisit the grouping interaction and
  redesign the browser UI after the functional work.

Requested order: finish Computer mode Part 2 (smart search suggestions and keyboard; full Part 2
instructions still pending), then implement the foreground Dev panel and pointing support, then
the broader UI changes. These are recorded follow-ups, not implemented capabilities. Defer the UI
redesign now; the immediate request is to retain this feedback for work after Part 2.
