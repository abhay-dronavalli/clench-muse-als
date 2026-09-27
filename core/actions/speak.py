"""speak: say the sentence aloud on the patient board through the voice service."""

from __future__ import annotations

from core.actions.base import Action, ActionContext, ActionResult
from core.voice import Voice


class SpeakAction(Action):
    name = "speak"
    dedupe = False

    def __init__(self, voice: Voice) -> None:
        self._voice = voice

    async def run(self, ctx: ActionContext) -> ActionResult:
        # Local: nothing leaves the laptop, so dry run does not apply.
        self._voice.speak(ctx.text, ctx.lang, "phrase")
        return ActionResult(True, "spoken")
