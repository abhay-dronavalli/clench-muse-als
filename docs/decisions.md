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

## 6. Ranking with TypeSafe Jev (planned)

- PRD: section 9 local scoring formula.
- Plan: ranking uses TypeSafe Jev (a structured choice model) when available, with the local scoring
  formula as the fallback. Gemini writes new options and full sentences.

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
  about free-tier rate limits.
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
