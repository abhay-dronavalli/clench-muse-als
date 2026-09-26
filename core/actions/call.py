"""place_call: a phone call through Twilio Programmable Voice that reads the message aloud twice.

Plain REST through httpx (no Twilio SDK): POST /2010-04-01/Accounts/{SID}/Calls.json with basic
auth, To, From and inline TwiML. Voice calls do not need SMS (A2P 10DLC) registration.
Env: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, the contact's `phone_env`
(e.g. CONTACT_MARIA_PHONE), and optionally TWILIO_VOICE_ES / TWILIO_VOICE_EN (e.g. Polly.Mia).
Numbers must be E.164 (+13055550123).
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

import httpx

from core.actions.base import ActionContext, ActionResult, NotConfigured
from core.actions.http import HttpAction, HttpRequest, is_e164, mask_phone
from core.contracts import Lang

API = "https://api.twilio.com/2010-04-01"
SAY_LANG: dict[Lang, str] = {"en": "en-US", "es": "es-MX"}
FROM_PATIENT: dict[Lang, str] = {"en": "Message from {name}: {text}", "es": "Mensaje de {name}: {text}"}


def call_message(ctx: ActionContext) -> str:
    if ctx.add_sender and ctx.patient_name:
        return FROM_PATIENT[ctx.lang].format(name=ctx.patient_name, text=ctx.text)
    return ctx.text


def _attr(value: str) -> str:
    return '"' + escape(value, {'"': "&quot;"}) + '"'


def twiml(message: str, lang: Lang, voice: str | None) -> str:
    """Say the message, pause a second, say it again. Text and attributes are XML-escaped."""
    attrs = f" language={_attr(SAY_LANG[lang])}"
    if voice:
        attrs += f" voice={_attr(voice)}"
    say = f"<Say{attrs}>{escape(message)}</Say>"
    return f'<Response>{say}<Pause length="1"/>{say}</Response>'


class TwilioCallAction(HttpAction):
    name = "place_call"
    service = "Twilio"

    def build(self, ctx: ActionContext) -> HttpRequest:
        c = ctx.contact
        if c is None:
            raise NotConfigured("Twilio: no contact to call")
        if not c.phone_env:
            raise NotConfigured(f"Twilio not configured: no phone_env for {c.id} in contacts.yaml")
        sid, token, from_number, to_number = self.require(
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", c.phone_env
        )
        for key, number in (("TWILIO_FROM_NUMBER", from_number), (c.phone_env, to_number)):
            if not is_e164(number):
                raise NotConfigured(f"{key} is not an E.164 number: use +13055550123 (no spaces or dashes)")
        voice = self.env(f"TWILIO_VOICE_{ctx.lang.upper()}") or None
        return HttpRequest(
            url=f"{API}/Accounts/{sid}/Calls.json",
            data={"To": to_number, "From": from_number, "Twiml": twiml(call_message(ctx), ctx.lang, voice)},
            auth=(sid, token),
            secrets=[token],
        )

    def describe(self, req: HttpRequest) -> str:
        assert req.data is not None
        return f"To={mask_phone(req.data['To'])} From={mask_phone(req.data['From'])} Twiml={req.data['Twiml']}"

    def result(self, resp: httpx.Response, body: Any) -> ActionResult:
        # Success: 201 {"sid": "CA...", "status": "queued", ...}
        # Error:   {"code": 21219, "message": "The number ... is unverified. ...", "status": 400, ...}
        if resp.is_success:
            sid = body.get("sid") if isinstance(body, dict) else None
            return ActionResult(True, f"call queued ({sid})" if sid else "call queued")
        if isinstance(body, dict) and (body.get("code") or body.get("message")):
            return ActionResult(
                False, f"Twilio error {body.get('code')} (HTTP {resp.status_code}): {body.get('message')}"
            )
        return ActionResult(False, f"Twilio error (HTTP {resp.status_code}): {resp.text[:200] or 'no details'}")
