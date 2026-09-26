"""Ranker: scores candidates against the patient's history (PRD section 9 layer 2, D7).

Reads the last 30 days of outcomes from SQLite and caches them until a new event is logged, so a
seed loaded by scripts/seed_demo.py shows up at the next screen without restarting the core. With no
database (tests) every history part is 0 and orders stay as given.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.contracts import BodyStateLevel, Lang
from core.rank.history import WINDOW_S, HistoryIndex, Outcome
from core.rank.score import (
    DEFAULT_HYSTERESIS,
    Candidate,
    Evidence,
    Scored,
    Weights,
    full_order,
    score_all,
    stable_order,
)

log = logging.getLogger("clench.rank")

SHORTCUT_HISTORY_SHARE = 0.6  # history share that opens the one-clench shortcut on its own
SHORTCUT_JEV_HISTORY_SHARE = 0.4  # ...or this share, when Jev picks the same phrase
SHORTCUT_JEV_CONFIDENCE = 0.45  # ...with at least this confidence
SHORTCUT_MIN_USES = 3.0  # ... and at least this many recency-weighted uses of the phrase
JEV_RECENT = 5
JEV_TOP = 10

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
LANG_NAMES: dict[Lang, str] = {"en": "English", "es": "Spanish"}


class RankHistory(Protocol):
    """What the Ranker reads from the database (core.db.Db implements it)."""

    def last_outcome_id(self) -> int: ...

    def outcomes(self, since: float) -> Sequence[Mapping[str, object]]: ...

    def top_phrases_with_hours(self, lang: Lang, limit: int) -> list[tuple[str, int, list[int]]]: ...

    def recent_messages_with_times(self, lang: Lang, limit: int) -> list[tuple[float, str]]: ...


@dataclass(frozen=True)
class Entry:
    """One thing to rank: a tile (evidence by menu path) or a sentence (evidence by text)."""

    id: str
    path: str | None = None  # dotted menu path: evidence of the node and everything below it
    text: str | None = None  # a sentence: evidence of that exact sentence in `lang`
    urgent: bool = False


class Ranker:
    def __init__(
        self,
        history: RankHistory | None,
        *,
        weights: Weights | None = None,
        hysteresis: float = DEFAULT_HYSTERESIS,
        clock: Callable[[], float] = time.time,
        local_hour: Callable[[float], int] | None = None,
    ) -> None:
        self._history = history
        self.weights = weights or Weights()
        self.hysteresis = hysteresis
        self._clock = clock
        self._local_hour = local_hour or (lambda t: datetime.fromtimestamp(t).hour)
        self._index: HistoryIndex | None = None
        self._index_id: int | None = None

    # --- time -----------------------------------------------------------------

    def now(self) -> float:
        return self._clock()

    def hour(self) -> int:
        return self._local_hour(self._clock())

    # --- history --------------------------------------------------------------

    def last_outcome_id(self) -> int:
        if self._history is None:
            return 0
        try:
            return self._history.last_outcome_id()
        except Exception:
            log.exception("could not read the history; ranking without it")
            return 0

    def index(self) -> HistoryIndex:
        """The history, re-read only when a new event was logged."""
        last = self.last_outcome_id()
        if self._index is None or last != self._index_id:
            outcomes: list[Outcome] = []
            if self._history is not None:
                try:
                    rows = self._history.outcomes(self._clock() - WINDOW_S)
                    outcomes = [
                        Outcome(
                            t=float(r["t"]),  # type: ignore[arg-type]
                            node_id=str(r["node_id"]),
                            text=r["text"],  # type: ignore[arg-type]
                            lang=r["lang"],  # type: ignore[arg-type]
                            confirmed=bool(r["confirmed"]),
                            rejected=bool(r["rejected"]),
                        )
                        for r in rows
                    ]
                except Exception:
                    log.exception("could not read the history; ranking without it")
            self._index = HistoryIndex(outcomes, local_hour=self._local_hour)
            self._index_id = last
        return self._index

    # --- scoring --------------------------------------------------------------

    def score(
        self,
        entries: Sequence[Entry],
        lang: Lang,
        *,
        priors: Mapping[str, float] | None = None,
        state_level: BodyStateLevel | None = None,
    ) -> dict[str, Scored]:
        index = self.index()
        priors = priors or {}
        candidates = []
        for e in entries:
            if e.text is not None:
                ev = index.for_text(e.text, lang)
            elif e.path is not None:
                ev = index.for_path(e.path)
            else:
                ev = Evidence()
            candidates.append(Candidate(e.id, ev, urgent=e.urgent, ai_prior=priors.get(e.id, 0.0)))
        return score_all(candidates, now=self.now(), hour=self.hour(), weights=self.weights, state_level=state_level)

    def order_menu(self, entries: Sequence[Entry], scored: Mapping[str, Scored], *, pinned: int = 0) -> list[str]:
        """Menu level: menu.yaml order unless an item clearly beats the one above it."""
        return stable_order(
            [e.id for e in entries], {k: v.score for k, v in scored.items()}, factor=self.hysteresis, pinned=pinned
        )

    @staticmethod
    def order_full(entries: Sequence[Entry], scored: Mapping[str, Scored]) -> list[str]:
        """Sentence lists: highest score first."""
        return full_order([e.id for e in entries], {k: v.score for k, v in scored.items()})

    @staticmethod
    def history_confidence(top: str, scored: Mapping[str, Scored]) -> float:
        """How sure the history alone is that `top` is wanted right now: its share of everything in
        `scored` said around this hour (recency-weighted, +/- 1 hour), or 0 when it has been used
        fewer than SHORTCUT_MIN_USES times (recency-weighted)."""
        s = scored.get(top)
        if s is None or s.uses < SHORTCUT_MIN_USES:
            return 0.0
        total = sum(v.at_hour for v in scored.values())
        return s.at_hour / total if total > 0 else 0.0

    # --- the short state Jev reads --------------------------------------------

    def jev_state(self, lang: Lang, crumbs: Sequence[str], state_level: BodyStateLevel | None) -> str:
        """A short plain summary of right now. Kept tight on purpose: padding hurts accuracy."""
        now = self._clock()
        when = datetime.fromtimestamp(now)
        lines = [
            f"Now: {WEEKDAYS[when.weekday()]} {when:%H:%M}",
            f"Language: {LANG_NAMES[lang]}",
            f"Screen: {' > '.join(['Home', *crumbs])}",
            f"Body state: {state_level or 'unknown'}",
        ]
        recent: list[tuple[float, str]] = []
        top: list[tuple[str, int, list[int]]] = []
        if self._history is not None:
            try:
                recent = self._history.recent_messages_with_times(lang, JEV_RECENT)
                top = self._history.top_phrases_with_hours(lang, JEV_TOP)
            except Exception:
                log.exception("could not read the history for Jev")
        if recent:
            lines.append("Last messages:")
            lines += [f"- {_ago(now, t)}: {text}" for t, text in recent]
        if top:
            lines.append("Most used phrases (times used, usual time):")
            lines += [f"- {text} ({n}, {usual_hours(hist)})" for text, n, hist in top]
        return "\n".join(lines)


def usual_hours(hist: Sequence[int]) -> str:
    """"around 10:00", or "around 13:00 and 16:00": the busiest hours (at least half of the top)."""
    top = max(hist, default=0)
    if top <= 0:
        return "any time"
    hours = [h for h, n in enumerate(hist) if n * 2 >= top][:2]
    return "around " + " and ".join(f"{h:02d}:00" for h in hours)


def _ago(now: float, t: float) -> str:
    when = datetime.fromtimestamp(t)
    days = (datetime.fromtimestamp(now).date() - when.date()).days
    day = "today" if days == 0 else "yesterday" if days == 1 else WEEKDAYS[when.weekday()]
    return f"{day} {when:%H:%M}"
