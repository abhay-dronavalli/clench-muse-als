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

## 4. Each picked word is spoken as it is picked (chunk 4, done)

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

## 5. "Other..." tile on every level (chunk 5, done)

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

## 8. Learning: ranking, stability rule, shortcut and Day 1 mode (chunk 6, done)

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
  menu tiles from it but shows no history sentences.
- The CircuitBreaker from the voice service is shared (it takes a name and fallback text now).
