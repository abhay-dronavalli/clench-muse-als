"""Every prompt the AI sees, in one place (PRD A3.5: the phrasing rules live in the prompt).

Rules for both methods:
  - Short (about 12 words at most), first person, natural spoken language.
  - Only the requested language.
  - Match the patient's own wording from their history ("Mija" rather than "daughter").
  - more_options stays on topic for the path (under Pain, options are body parts or kinds of pain)
    and never repeats what is already on screen.
The model never decides who a message goes to or what action runs; it only writes labels and text.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.contracts import Lang
from core.suggest.provider import MAX_OPTIONS, MAX_SENTENCES, SuggestContext

LANGUAGE: dict[Lang, str] = {"en": "English", "es": "Spanish (Latin American, as spoken in Miami)"}


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str


def _rules(lang: Lang) -> str:
    return f"""You write for a person who cannot move or speak (for example late-stage ALS). They pick
options on a screen with jaw clenches, and what you write is said aloud in their voice or sent to
their family. They always see it and confirm it before anything is said or sent.

Rules:
- Write ONLY in {LANGUAGE[lang]}. Never mix languages.
- First person, as the patient speaking ("I", "me", "my"), natural everyday spoken language.
- Short: about 12 words at most per sentence. No lists, no emojis, no quotation marks.
- Use the patient's own wording from their history when it fits (for example "Mija" instead of
  "daughter", their greetings, their way of asking). Do not invent facts about them.
- Be warm and plain. Never medical advice, never a diagnosis, never alarming words they did not pick.
- Answer with JSON only, in the requested shape."""


def _history(ctx: SuggestContext) -> str:
    lines = [f"Patient's first name: {ctx.patient_name}", f"Local time: {ctx.hour:02d}:00"]
    if ctx.contacts:
        lines.append("People they talk to: " + ", ".join(ctx.contacts))
    if ctx.top_phrases:
        lines.append("Their most used phrases (their own words):\n" + "\n".join(f"- {p}" for p in ctx.top_phrases))
    if ctx.recent_messages:
        lines.append("Their last confirmed messages, newest first:\n" + "\n".join(f"- {m}" for m in ctx.recent_messages))
    return "\n".join(lines)


def _path(ctx: SuggestContext) -> str:
    return " > ".join(ctx.path) if ctx.path else "(home screen)"


def compose(ctx: SuggestContext) -> Prompt:
    if ctx.path:
        task = f"""They picked this path on the menu: {_path(ctx)}
Write up to {MAX_SENTENCES} different short sentences they might want to say for exactly this choice,
most likely first. Stay on this meaning; vary the detail or the request."""
        if ctx.fixed_phrase:
            task += f'\nThe standard phrase for it is: "{ctx.fixed_phrase}". Do not repeat it; offer alternatives in their style.'
    else:
        task = f"""They are on the home screen. Write up to {MAX_SENTENCES} short sentences they are most likely
to want to say right now, given the time of day and their habits. Most likely first."""
    if ctx.shown:
        task += "\nAlready on screen, do not repeat or rephrase these:\n" + "\n".join(f"- {s}" for s in ctx.shown)
    user = f"""{_history(ctx)}

{task}

Return JSON: {{"sentences": ["...", "..."]}}"""
    return Prompt(system=_rules(ctx.lang), user=user)


def more_options(ctx: SuggestContext) -> Prompt:
    if ctx.path:
        where = f"""They are on this menu level: {_path(ctx)}
Offer up to {MAX_OPTIONS} NEW options that belong on this level and stay on its topic (for example,
under Pain the options are other body parts or kinds of pain; under a person's name they are things
to tell that person). Each option is one more specific choice on this level, not a new category."""
    else:
        where = f"""They are on the home screen. Offer up to {MAX_OPTIONS} NEW things they might want to say, useful
for this time of day (greetings, thanks, yes / no answers, small requests, feelings)."""
    shown = ""
    if ctx.shown:
        shown = "\nAlready on screen, do not repeat or rephrase these:\n" + "\n".join(f"- {s}" for s in ctx.shown)
    user = f"""{_history(ctx)}

{where}{shown}

For each option give:
- "label": the tile text, 1 to 4 words
- "text": the full sentence said aloud when they confirm it, first person, about 12 words at most

Return JSON: {{"options": [{{"label": "...", "text": "..."}}]}}"""
    return Prompt(system=_rules(ctx.lang), user=user)
