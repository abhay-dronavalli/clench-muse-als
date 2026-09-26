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

## 3. Voice output moves to ElevenLabs (planned)

- PRD: A2 / A3.2 voice with Google Cloud TTS.
- Plan: ElevenLabs (`eleven_flash_v2_5`), audio cached on the laptop, browser speech as the offline
  fallback.

## 4. Each picked word is spoken as it is picked (planned)

- PRD: D5 says nothing is spoken without the confirm screen.
- Plan: auditory feedback, on by default and switchable in settings, speaks each picked tile's label
  as it is picked. D5 still applies to AI-written sentences and to anything sent to a phone. A single
  picked word is the patient's own deliberate choice, so echoing it is allowed.

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
