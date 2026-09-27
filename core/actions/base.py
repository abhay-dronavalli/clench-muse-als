"""Action interface and registry (PRD A3.2). The session asks the registry to run an action by
name and never talks to a service directly.

Safety rules shared by every action:
  - Nothing runs without a confirm step (PRD D5); the session only calls the registry after the
    confirming clench, or when the help countdown reaches zero (the countdown is the confirmation).
  - The same action to the same contact with the same text within 30 s is skipped, so a bug can
    never loop messages or calls.
  - An action never raises into the session: every failure becomes ActionResult(ok=False, detail).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import ClassVar

from core.contracts import ActionName, Lang
from core.menu import Contact

log = logging.getLogger("clench.actions")

DEDUPE_S = 30.0


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    detail: str


@dataclass(frozen=True)
class ActionContext:
    text: str  # the confirmed sentence, in `lang`
    lang: Lang
    contact: Contact | None = None
    patient_name: str = ""
    # Prefix the text with who it is from ("Luis: ..."). False when the text already says it
    # (the help alert: "Luis necesita ayuda ahora").
    add_sender: bool = True


class NotConfigured(Exception):
    """A key, id or phone number the action needs is missing or invalid in .env / contacts.yaml."""


class Action(ABC):
    name: ClassVar[ActionName]
    needs_confirm: ClassVar[bool] = True  # every action so far; the registry is also for Part 3
    dedupe: ClassVar[bool] = True  # False for speak: saying the same thing twice is fine

    @abstractmethod
    async def run(self, ctx: ActionContext) -> ActionResult: ...


class ActionRegistry:
    def __init__(
        self,
        actions: Iterable[Action],
        *,
        clock: Callable[[], float] = time.monotonic,
        dedupe_s: float = DEDUPE_S,
    ) -> None:
        self._actions: dict[str, Action] = {a.name: a for a in actions}
        self._clock = clock
        self._dedupe_s = dedupe_s
        self._recent: dict[tuple[str, str | None, str], float] = {}

    def __contains__(self, name: str) -> bool:
        return name in self._actions

    def get(self, name: str) -> Action | None:
        return self._actions.get(name)

    async def run(self, name: str, ctx: ActionContext) -> ActionResult:
        action = self._actions.get(name)
        if action is None:
            return ActionResult(False, f"unknown action {name}")
        who = ctx.contact.id if ctx.contact else None
        if action.dedupe and self._is_duplicate((name, who, ctx.text)):
            log.warning("%s to %s suppressed: same text sent less than %d s ago: %r", name, who, self._dedupe_s, ctx.text)
            return ActionResult(False, "duplicate suppressed")
        try:
            return await action.run(ctx)
        except Exception as e:  # never let an action take the session down
            log.exception("%s to %s crashed", name, who)
            return ActionResult(False, f"{name} failed: {e.__class__.__name__}")

    def _is_duplicate(self, key: tuple[str, str | None, str]) -> bool:
        now = self._clock()
        self._recent = {k: t for k, t in self._recent.items() if now - t < self._dedupe_s}
        if key in self._recent:
            return True
        self._recent[key] = now
        return False
