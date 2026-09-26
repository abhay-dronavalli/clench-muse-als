"""Shared plumbing for actions that call a web API (Telegram, Twilio) through httpx.

  - ACTIONS_DRY_RUN (default on): log exactly what would be sent (secrets masked) and return
    ok with detail "dry run". No request is made.
  - Missing or invalid config: ok=False with a clear detail, e.g.
    "Telegram not configured: TELEGRAM_BOT_TOKEN missing".
  - 10 s timeout. HTTP errors carry the status and the service's own error message.
"""

from __future__ import annotations

import logging
import re
from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.actions.base import Action, ActionContext, ActionResult, NotConfigured

log = logging.getLogger("clench.actions")

TIMEOUT_S = 10.0
E164 = re.compile(r"^\+[1-9]\d{7,14}$")


@dataclass
class HttpRequest:
    url: str
    json: dict[str, Any] | None = None
    data: dict[str, str] | None = None
    auth: tuple[str, str] | None = None
    secrets: list[str] = field(default_factory=list)  # masked in logs and error details


def mask(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def mask_phone(number: str) -> str:
    """+13055550123 -> +1*****0123 (logs only)."""
    if len(number) <= 6:
        return number
    return number[:2] + "*" * (len(number) - 6) + number[-4:]


def is_e164(number: str) -> bool:
    return bool(E164.match(number))


class HttpAction(Action):
    service: str  # "Telegram", "Twilio": used in details shown to the caregiver

    def __init__(
        self,
        env: Mapping[str, str],
        *,
        dry_run: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._env = env
        self.dry_run = dry_run
        self._transport = transport  # tests pass httpx.MockTransport

    # --- for subclasses -------------------------------------------------------------

    @abstractmethod
    def build(self, ctx: ActionContext) -> HttpRequest:
        """The request to send. Raises NotConfigured when a key or number is missing or invalid."""

    @abstractmethod
    def describe(self, req: HttpRequest) -> str:
        """What is being sent, for the dry-run log (no secrets)."""

    @abstractmethod
    def result(self, resp: httpx.Response, body: Any) -> ActionResult:
        """Turn the service's response into a result."""

    def env(self, key: str) -> str:
        return self._env.get(key, "").strip()

    def require(self, *keys: str) -> list[str]:
        """Values of `keys`; raises NotConfigured naming every missing one."""
        missing = [k for k in keys if not self.env(k)]
        if missing:
            raise NotConfigured(f"{self.service} not configured: {', '.join(missing)} missing")
        return [self.env(k) for k in keys]

    # --- run ---------------------------------------------------------------------------

    async def run(self, ctx: ActionContext) -> ActionResult:
        who = ctx.contact.id if ctx.contact else None
        try:
            req = self.build(ctx)
        except NotConfigured as e:
            if self.dry_run:
                log.warning("DRY RUN %s to %s: %r (a real send would fail: %s)", self.name, who, ctx.text, e)
                return ActionResult(True, "dry run")
            log.warning("%s to %s not sent: %s", self.name, who, e)
            return ActionResult(False, str(e))
        if self.dry_run:
            log.info("DRY RUN %s to %s: POST %s %s", self.name, who, mask(req.url, req.secrets), self.describe(req))
            return ActionResult(True, "dry run")
        return await self._send(req)

    async def _send(self, req: HttpRequest) -> ActionResult:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_S) as client:
                resp = await client.post(req.url, json=req.json, data=req.data, auth=req.auth)
        except httpx.TimeoutException:
            return ActionResult(False, f"{self.service} timed out after {TIMEOUT_S:.0f} s")
        except httpx.HTTPError as e:
            return ActionResult(False, mask(f"{self.service} unreachable: {e.__class__.__name__}: {e}", req.secrets))
        try:
            body = resp.json()
        except ValueError:
            body = None
        return self.result(resp, body)
