"""Action registry (PRD A3.2): speak, send_message, place_call, room_control. Every action needs a
confirm step (PRD D5). The help alert is not an action of its own: when its countdown ends, the
session runs place_call and send_message for the help contact.

Real sends happen only when ACTIONS_DRY_RUN=false in .env; otherwise every send is only logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

import httpx

from core.actions.base import Action, ActionContext, ActionRegistry, ActionResult, NotConfigured
from core.actions.call import TwilioCallAction
from core.actions.message import TelegramMessageAction
from core.actions.room import RoomControlAction
from core.actions.speak import SpeakAction
from core.contracts import Message

__all__ = [
    "Action",
    "ActionContext",
    "ActionRegistry",
    "ActionResult",
    "NotConfigured",
    "build_registry",
]


def build_registry(
    emit: Callable[[Message], None],
    env: Mapping[str, str],
    *,
    dry_run: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ActionRegistry:
    """All actions. `emit` sends SPEAK to the board; `env` holds the keys (see .env.example)."""
    return ActionRegistry(
        [
            SpeakAction(emit),
            TelegramMessageAction(env, dry_run=dry_run, transport=transport),
            TwilioCallAction(env, dry_run=dry_run, transport=transport),
            RoomControlAction(dry_run=dry_run),
        ],
        clock=clock,
    )
