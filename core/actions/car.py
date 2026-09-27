"""pull_over: the trip screen's safety action (core/trip.py). A mock: logs and succeeds, like
room_control. Only ever run after its confirm screen."""

from __future__ import annotations

import logging

from core.actions.base import Action, ActionContext, ActionResult

log = logging.getLogger("clench.actions")


class PullOverAction(Action):
    name = "pull_over"

    def __init__(self, *, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    async def run(self, ctx: ActionContext) -> ActionResult:
        if self.dry_run:
            log.info("DRY RUN pull_over: %r", ctx.text)
            return ActionResult(True, "dry run")
        log.info("pull_over (mock): %r", ctx.text)
        return ActionResult(True, "done (mock)")
