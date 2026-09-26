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

## 5. "Other..." tile on every level (planned)

- Plan: every menu level ends with an "Other..." tile that asks the AI for new options on the same
  path. Picking it twice in a row offers "Spell it". Levels therefore hold at most 5 content tiles
  plus "Other...".

## 6. Ranking with TypeSafe Jev (planned)

- PRD: section 9 local scoring formula.
- Plan: ranking uses TypeSafe Jev (a structured choice model) when available, with the local scoring
  formula as the fallback. Gemini writes new options and full sentences.

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
  report settings back to the board yet.
