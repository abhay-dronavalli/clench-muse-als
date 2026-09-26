"""What a message costs the patient (PRD section 12 "steps to send a common message", D7).

For every confirmed message the session counts:
  - selections: every clench pick since home (going back does not undo one), plus 1 for the
    confirm clench;
  - scan steps: the highlight moves the person waited through before those picks.
It also works out what the same message would have cost in Day 1 mode: walk the menu.yaml path to
the leaf it was said under, picking each item where menu.yaml puts it (index = scan steps), through
"Other..." for a `more` item, then (with the AI on) the suggestions screen, where the fixed phrase
comes after the AI's 3 sentences, then the confirm. No shortcut in Day 1 mode.

A message Day 1 mode cannot reach the same way (an AI sentence for right now on Suggested, an AI
option from "Other...") gets no Day 1 cost; the session then reports the real numbers for both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.contracts import Lang
from core.menu import Menu
from core.rank.history import leaf_path
from core.suggest.provider import MAX_SENTENCES, text_key

_SENTENCE_ID = re.compile(r"\.s(\d+)$")  # ai:<leaf path>.s2 = the AI's second sentence


@dataclass
class Effort:
    selections: int = 0
    scan_steps: int = 0


class Tracker:
    """Counts selections and scan steps since the board last went home."""

    def __init__(self) -> None:
        self.effort = Effort()

    def reset(self) -> None:
        self.effort = Effort()

    def step(self) -> None:
        self.effort.scan_steps += 1

    def select(self) -> None:
        self.effort.selections += 1


def day1_cost(menu: Menu, *, event_id: str, item_id: str, text: str, lang: Lang, ai_on: bool) -> Effort | None:
    """What confirming `text`, said under the leaf `event_id`, costs in Day 1 mode. None when the
    menu.yaml path does not lead to a leaf."""
    path = leaf_path(event_id)
    cost = Effort()
    node = menu.root
    for part in path.split(".") if path else []:
        children = node.children or []
        index = next((i for i, c in enumerate(children) if c.id == part), None)
        if index is not None:
            cost.scan_steps += index
            cost.selections += 1
            node = children[index]
            continue
        more = node.more or []
        index = next((i for i, c in enumerate(more) if c.id == part), None)
        if index is None:
            return None
        cost.scan_steps += len(children) + index  # "Other..." is the tile after the level's items
        cost.selections += 2
        node = more[index]
    if not path or not node.is_leaf:
        return None
    if ai_on:  # the suggestions screen: the AI's sentences first, then the fixed phrase
        cost.selections += 1
        if text_key(text) == text_key(node.phrase(lang)):
            cost.scan_steps += MAX_SENTENCES
        else:
            m = _SENTENCE_ID.search(item_id)
            cost.scan_steps += int(m.group(1)) - 1 if m else 0
    cost.selections += 1  # the confirm clench
    return cost
