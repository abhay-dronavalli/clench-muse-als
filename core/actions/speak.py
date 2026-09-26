"""speak: say the sentence aloud on the patient board (SPEAK; the board uses browser speech)."""

from __future__ import annotations

from collections.abc import Callable

from core.actions.base import Action, ActionContext, ActionResult
from core.contracts import Message, Speak


class SpeakAction(Action):
    name = "speak"
    dedupe = False

    def __init__(self, emit: Callable[[Message], None]) -> None:
        self._emit = emit

    async def run(self, ctx: ActionContext) -> ActionResult:
        # Local: nothing leaves the laptop, so dry run does not apply.
        self._emit(Speak(text=ctx.text, lang=ctx.lang))
        return ActionResult(True, "spoken")
