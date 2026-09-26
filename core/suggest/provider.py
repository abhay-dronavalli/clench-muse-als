"""LLMProvider interface and the validated models every provider returns (PRD A3.5, D5, D6, D14).

A provider does three jobs, all in the patient's own words and in one language:
  - compose(ctx): up to 3 short first-person sentences for a picked leaf (or, with an empty path,
    for "right now" on the home Suggested tile).
  - more_options(ctx): up to 5 NEW options for the current level (the "Other..." tile), each a short
    tile label plus the full sentence it says.
  - level_bundle(ctx): both of the above for a whole menu level in ONE request: the sentences for
    every leaf on it, its "Other..." options and (at home) the Suggested sentences for right now.
    The session prefetches with it, so opening a level costs at most one request.

The AI only ever supplies labels and text. Who a message goes to and which action runs always come
from the menu path, never from the model (see core/suggest/service.py and the session). Models
ignore any extra field a provider sends back ("action", "contact", ...), so there is nowhere for
that to leak in.

Validation is forgiving per item: a bad item (empty, too long, a duplicate, the wrong shape) is
dropped and the rest are kept; only a response that is not the right JSON at all fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, field_validator

from core.contracts import Lang

MAX_SENTENCES = 3
MAX_OPTIONS = 5
MAX_TEXT = 90  # characters; anything longer is dropped (a sentence should be about 12 words)
MAX_LABEL = 40  # characters; a tile label is a few words, read from a bed


@dataclass(frozen=True)
class SuggestContext:
    """Everything a provider is told, and nothing else (PRD D14: only the current request, a short
    summary of habits, never the full history)."""

    path: tuple[str, ...]  # breadcrumb labels in `lang`; () = home, "what would I say right now"
    lang: Lang
    hour: int  # local hour, 0-23
    patient_name: str
    fixed_phrase: str | None = None  # the leaf's hand-written phrase, if there is one
    top_phrases: tuple[str, ...] = ()  # the patient's 20 most used phrases in `lang`
    recent_messages: tuple[str, ...] = ()  # last 5 confirmed sentences in `lang`, newest first
    contacts: tuple[str, ...] = ()  # first names of the contacts, in `lang`
    shown: tuple[str, ...] = ()  # labels / sentences already on screen: never repeat these


def _key(text: str) -> str:
    return " ".join(text.casefold().split()).rstrip(".!?¡¿")


def _clean_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > limit:
        return None
    return text


def clean_sentences(value: Any) -> list[str]:
    """Up to 3 sentences: empty, too long, non-string and duplicate items dropped."""
    if not isinstance(value, list):
        raise ValueError("sentences must be a list")
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item, MAX_TEXT)
        if text is None or _key(text) in seen:
            continue
        seen.add(_key(text))
        out.append(text)
    return out[:MAX_SENTENCES]


class Sentences(BaseModel):
    """compose() result: up to 3 sentences, cleaned."""

    model_config = ConfigDict(extra="ignore")

    sentences: list[str]

    @field_validator("sentences", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> list[str]:
        return clean_sentences(value)


class Option(BaseModel):
    """One new tile: a short label and the full sentence it says after the confirm."""

    model_config = ConfigDict(extra="ignore")  # an "action" or "contact" from the model is dropped

    label: str
    text: str


def clean_options(value: Any) -> list[Option]:
    """Up to 5 options: items with a bad label or text, and duplicates, dropped."""
    if not isinstance(value, list):
        raise ValueError("options must be a list")
    out: list[Option] = []
    seen_labels: set[str] = set()
    seen_texts: set[str] = set()
    for item in value:
        if isinstance(item, Option):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"), MAX_LABEL)
        text = _clean_text(item.get("text"), MAX_TEXT)
        if label is None or text is None or _key(label) in seen_labels or _key(text) in seen_texts:
            continue
        seen_labels.add(_key(label))
        seen_texts.add(_key(text))
        out.append(Option(label=label, text=text))
    return out[:MAX_OPTIONS]


class Options(BaseModel):
    """more_options() result: up to 5 options, cleaned."""

    model_config = ConfigDict(extra="ignore")

    options: list[Option]

    @field_validator("options", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> list[Option]:
        return clean_options(value)


@dataclass(frozen=True)
class LevelLeaf:
    """One leaf of the level a bundle is for. `id` is the short key the model answers with."""

    id: str  # "L1", "L2", ...
    label: str
    fixed_phrase: str | None = None


@dataclass(frozen=True)
class LevelContext:
    """level_bundle() request: a menu level, with the same history summary as SuggestContext (D14)."""

    path: tuple[str, ...]  # breadcrumb labels of the level; () = home
    lang: Lang
    hour: int
    patient_name: str
    leaves: tuple[LevelLeaf, ...] = ()  # sentences wanted for each of these
    now: bool = False  # also the home Suggested sentences for right now
    options: bool = False  # also the level's "Other..." options
    top_phrases: tuple[str, ...] = ()
    recent_messages: tuple[str, ...] = ()
    contacts: tuple[str, ...] = ()
    shown: tuple[str, ...] = ()  # labels on the level: new options never repeat these


class LeafSentences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    sentences: list[str]

    @field_validator("sentences", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> list[str]:
        return clean_sentences(value)


class Bundle(BaseModel):
    """level_bundle() result. A part that was not asked for (or is missing) is empty. A leaf entry
    of the wrong shape is dropped and the rest are kept."""

    model_config = ConfigDict(extra="ignore")

    leaves: list[LeafSentences] = []
    now: list[str] = []
    options: list[Option] = []

    @field_validator("leaves", mode="before")
    @classmethod
    def _clean_leaves(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("leaves must be a list")
        return [
            v
            for v in value
            if isinstance(v, LeafSentences)
            or (isinstance(v, dict) and isinstance(v.get("id"), str) and isinstance(v.get("sentences"), list))
        ]

    @field_validator("now", mode="before")
    @classmethod
    def _clean_now(cls, value: Any) -> list[str]:
        return [] if value is None else clean_sentences(value)

    @field_validator("options", mode="before")
    @classmethod
    def _clean_options(cls, value: Any) -> list[Option]:
        return [] if value is None else clean_options(value)

    def for_leaf(self, leaf_id: str) -> list[str] | None:
        """The sentences for `leaf_id`, or None when the model left it out."""
        return next((leaf.sentences for leaf in self.leaves if leaf.id == leaf_id), None)


def drop_known(items: list[str], known: tuple[str, ...] | list[str]) -> list[str]:
    """`items` without anything already in `known` (case, spaces and end punctuation ignored)."""
    keys = {_key(k) for k in known}
    return [i for i in items if _key(i) not in keys]


# --- JSON schemas sent to the model --------------------------------------------------
# Plain schemas (no validators) for structured output; the models above do the cleaning.


class SentencesSchema(BaseModel):
    sentences: list[str]


class OptionSchema(BaseModel):
    label: str
    text: str


class OptionsSchema(BaseModel):
    options: list[OptionSchema]


class LeafSchema(BaseModel):
    id: str
    sentences: list[str]


class BundleSchema(BaseModel):
    leaves: list[LeafSchema]
    now: list[str]
    options: list[OptionSchema]


class LLMProvider(Protocol):
    name: str
    model: str

    async def compose(self, ctx: SuggestContext) -> Sentences:
        """Up to 3 short first-person sentences for ctx.path. Raises on any failure."""
        ...

    async def more_options(self, ctx: SuggestContext) -> Options:
        """Up to 5 new options for the level at ctx.path, none of them in ctx.shown. Raises on any failure."""
        ...

    async def level_bundle(self, ctx: LevelContext) -> Bundle:
        """In one request: up to 3 sentences for each of ctx.leaves, plus (if asked) up to 3 for right
        now and up to 5 new options for the level. Raises on any failure."""
        ...

    async def aclose(self) -> None: ...
