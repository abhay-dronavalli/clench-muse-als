"""send_message: a text message through a Telegram bot (Bot API sendMessage).

No SMS: US carriers block SMS from unregistered app numbers (docs/decisions.md #2).
Env: TELEGRAM_BOT_TOKEN, plus the contact's `telegram_chat_env` (e.g. TELEGRAM_CHAT_ID_MARIA).
The text is "<patient name>: <phrase>".
"""

from __future__ import annotations

from typing import Any

import httpx

from core.actions.base import ActionContext, ActionResult, NotConfigured
from core.actions.http import HttpAction, HttpRequest

API = "https://api.telegram.org"


def message_text(ctx: ActionContext) -> str:
    return f"{ctx.patient_name}: {ctx.text}" if ctx.add_sender and ctx.patient_name else ctx.text


class TelegramMessageAction(HttpAction):
    name = "send_message"
    service = "Telegram"

    def build(self, ctx: ActionContext) -> HttpRequest:
        c = ctx.contact
        if c is None:
            raise NotConfigured("Telegram: no contact to message")
        if not c.telegram_chat_env:
            raise NotConfigured(f"Telegram not configured: no telegram_chat_env for {c.id} in contacts.yaml")
        token, chat_id = self.require("TELEGRAM_BOT_TOKEN", c.telegram_chat_env)
        return HttpRequest(
            url=f"{API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message_text(ctx)},
            secrets=[token],
        )

    def describe(self, req: HttpRequest) -> str:
        assert req.json is not None
        return f"chat_id={req.json['chat_id']} text={req.json['text']!r}"

    def result(self, resp: httpx.Response, body: Any) -> ActionResult:
        # {"ok": true, "result": {...}} or {"ok": false, "error_code": 400, "description": "Bad Request: ..."}
        if resp.is_success and isinstance(body, dict) and body.get("ok"):
            return ActionResult(True, "sent")
        description = body.get("description") if isinstance(body, dict) else None
        detail = description or resp.text[:200] or "no details"
        return ActionResult(False, f"Telegram error (HTTP {resp.status_code}): {detail}")
