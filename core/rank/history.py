"""What the ranking knows about the past: every confirmed send and cancelled confirm screen of the
last 30 days (events table), indexed two ways:

  - by menu path, for tiles. A branch collects the evidence of every leaf below it, so its score
    is the sum of its descendants' leaf scores (before scaling). "ai:" is dropped from node ids, so
    an AI sentence confirmed under People > Maria > Text counts for that leaf.
  - by sentence text (case, spaces and end punctuation ignored) in one language, for sentence lists.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from core.contracts import Lang
from core.rank.score import Evidence, Use, recency_count
from core.suggest.provider import text_key

WINDOW_S = 30 * 86_400.0  # older history is not read


def leaf_path(node_id: str) -> str:
    """The menu path an event belongs to: its node id without the "ai:" prefix."""
    return node_id.removeprefix("ai:")


@dataclass(frozen=True)
class Outcome:
    t: float
    node_id: str
    text: str | None
    lang: Lang
    confirmed: bool
    rejected: bool

    @property
    def path(self) -> str:
        return leaf_path(self.node_id)


@dataclass(frozen=True)
class Sentence:
    """A sentence the patient has confirmed, as the home Suggested list offers it again."""

    text: str
    node_id: str  # as logged, e.g. people.maria.text or ai:people.maria.text
    uses: float  # recency-weighted


class HistoryIndex:
    def __init__(self, outcomes: Iterable[Outcome], *, local_hour: Callable[[float], int] | None = None) -> None:
        self._hour = local_hour or (lambda t: datetime.fromtimestamp(t).hour)
        self.outcomes = list(outcomes)

    @property
    def empty(self) -> bool:
        return not self.outcomes

    def _evidence(self, rows: Iterable[Outcome]) -> Evidence:
        ev = Evidence()
        for o in rows:
            if o.confirmed:
                ev.uses.append(Use(o.t, self._hour(o.t)))
            elif o.rejected:
                ev.rejections.append(o.t)
        return ev

    def for_path(self, path: str) -> Evidence:
        """Evidence for the menu item at dotted `path` and everything below it."""
        below = path + "."
        return self._evidence(o for o in self.outcomes if o.path == path or o.path.startswith(below))

    def for_text(self, text: str, lang: Lang) -> Evidence:
        key = text_key(text)
        return self._evidence(o for o in self.outcomes if o.lang == lang and o.text and text_key(o.text) == key)

    def sentences(self, lang: Lang, now: float, limit: int) -> list[Sentence]:
        """The most used confirmed sentences in `lang` (recency-weighted), most used first. Each
        keeps the node it was last confirmed under, which gives its action and contact."""
        groups: dict[str, list[Outcome]] = {}
        for o in self.outcomes:
            if o.confirmed and o.text and o.lang == lang:
                groups.setdefault(text_key(o.text), []).append(o)
        out = []
        for rows in groups.values():
            last = max(rows, key=lambda o: o.t)
            ev = self._evidence(rows)
            out.append(Sentence(text=last.text or "", node_id=last.node_id, uses=recency_count(ev.uses, now)))
        out.sort(key=lambda s: -s.uses)
        return out[:limit]
