"""room_control: mock smart-home action (PRD P2). Logs and succeeds; a real one is a stretch goal."""

from __future__ import annotations

import logging

from core.actions.base import Action, ActionContext, ActionResult

log = logging.getLogger("clench.actions")


class RoomControlAction(Action):
    name = "room_control"

    def __init__(self, *, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    async def run(self, ctx: ActionContext) -> ActionResult:
        if self.dry_run:
            log.info("DRY RUN room_control: %r", ctx.text)
            return ActionResult(True, "dry run")
        log.info("room_control (mock): %r", ctx.text)
        return ActionResult(True, "done (mock)")
