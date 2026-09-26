"""Action registry tests. No real network: every request goes to an httpx.MockTransport."""

import asyncio
import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest

from core.actions import ActionContext, ActionRegistry, build_registry
from core.actions.base import DEDUPE_S
from core.actions.call import TwilioCallAction, twiml
from core.actions.http import is_e164, mask_phone
from core.actions.message import TelegramMessageAction
from core.contracts import Speak
from core.menu import load_menu

ENV = {
    "TELEGRAM_BOT_TOKEN": "123:SECRET",
    "TELEGRAM_CHAT_ID_MARIA": "555111",
    "TWILIO_ACCOUNT_SID": "AC0000",
    "TWILIO_AUTH_TOKEN": "tok3n",
    "TWILIO_FROM_NUMBER": "+13055550100",
    "CONTACT_MARIA_PHONE": "+13055550123",
}


@pytest.fixture(scope="module")
def maria():
    return load_menu().contacts["maria"]


class Recorder:
    """MockTransport handler that records requests and answers with a canned response."""

    def __init__(self, status: int = 200, body: object = None, exc: Exception | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = body
        self.exc = exc

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return httpx.Response(self.status, json=self.body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def run(coro):
    return asyncio.run(coro)


def ctx(contact, text="Mija, estoy bien, llámame a las seis.", lang="es", **kw) -> ActionContext:
    return ActionContext(text=text, lang=lang, contact=contact, patient_name="Luis", **kw)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


# --- Telegram ----------------------------------------------------------------------------


def test_telegram_request_shape(maria):
    rec = Recorder(200, {"ok": True, "result": {"message_id": 7}})
    action = TelegramMessageAction(ENV, dry_run=False, transport=rec.transport)
    result = run(action.run(ctx(maria)))
    assert (result.ok, result.detail) == (True, "sent")
    (req,) = rec.requests
    assert req.method == "POST"
    assert str(req.url) == "https://api.telegram.org/bot123:SECRET/sendMessage"
    assert json.loads(req.content) == {"chat_id": "555111", "text": "Luis: Mija, estoy bien, llámame a las seis."}


def test_telegram_without_sender_prefix(maria):
    rec = Recorder(200, {"ok": True})
    action = TelegramMessageAction(ENV, dry_run=False, transport=rec.transport)
    run(action.run(ctx(maria, text="Luis necesita ayuda ahora", add_sender=False)))
    assert json.loads(rec.requests[0].content)["text"] == "Luis necesita ayuda ahora"


def test_telegram_error_description_is_surfaced(maria):
    rec = Recorder(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
    result = run(TelegramMessageAction(ENV, dry_run=False, transport=rec.transport).run(ctx(maria)))
    assert not result.ok
    assert result.detail == "Telegram error (HTTP 400): Bad Request: chat not found"


# --- Twilio ------------------------------------------------------------------------------


def test_twilio_request_shape(maria):
    rec = Recorder(201, {"sid": "CA123", "status": "queued"})
    env = {**ENV, "TWILIO_VOICE_ES": "Polly.Mia"}
    result = run(TwilioCallAction(env, dry_run=False, transport=rec.transport).run(ctx(maria)))
    assert (result.ok, result.detail) == (True, "call queued (CA123)")
    (req,) = rec.requests
    assert req.method == "POST"
    assert str(req.url) == "https://api.twilio.com/2010-04-01/Accounts/AC0000/Calls.json"
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"AC0000:tok3n").decode()
    form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
    assert form["To"] == "+13055550123"
    assert form["From"] == "+13055550100"
    say = '<Say language="es-MX" voice="Polly.Mia">Mensaje de Luis: Mija, estoy bien, llámame a las seis.</Say>'
    assert form["Twiml"] == f'<Response>{say}<Pause length="1"/>{say}</Response>'


def test_twiml_is_escaped_and_voice_optional():
    xml = twiml('Tom & "Jerry" <3', "en", None)
    assert xml == (
        '<Response><Say language="en-US">Tom &amp; "Jerry" &lt;3</Say><Pause length="1"/>'
        '<Say language="en-US">Tom &amp; "Jerry" &lt;3</Say></Response>'
    )
    assert 'voice="a&quot;b"' in twiml("hi", "en", 'a"b')


def test_twilio_voice_attribute_left_out_when_unset(maria):
    rec = Recorder(201, {"sid": "CA1"})
    run(TwilioCallAction(ENV, dry_run=False, transport=rec.transport).run(ctx(maria, text="Hi", lang="en")))
    twiml_sent = parse_qs(rec.requests[0].content.decode())["Twiml"][0]
    assert "voice=" not in twiml_sent
    assert '<Say language="en-US">Message from Luis: Hi</Say>' in twiml_sent


def test_twilio_error_code_is_surfaced(maria):
    rec = Recorder(
        400,
        {
            "code": 21219,
            "message": "The number +13055550123 is unverified. Trial accounts cannot send messages to unverified numbers",
            "more_info": "https://www.twilio.com/docs/errors/21219",
            "status": 400,
        },
    )
    result = run(TwilioCallAction(ENV, dry_run=False, transport=rec.transport).run(ctx(maria)))
    assert not result.ok
    assert result.detail.startswith("Twilio error 21219 (HTTP 400): The number +13055550123 is unverified")


def test_twilio_non_json_error(maria):
    def handler(request):
        return httpx.Response(503, text="Service Unavailable")

    action = TwilioCallAction(ENV, dry_run=False, transport=httpx.MockTransport(handler))
    result = run(action.run(ctx(maria)))
    assert (result.ok, result.detail) == (False, "Twilio error (HTTP 503): Service Unavailable")


# --- E.164 -------------------------------------------------------------------------------


@pytest.mark.parametrize("number", ["+13055550123", "+525512345678", "+447911123456"])
def test_e164_valid(number):
    assert is_e164(number)


@pytest.mark.parametrize("number", ["3055550123", "+1 305 555 0123", "+1-305-555-0123", "+0123456789", "+1", ""])
def test_e164_invalid(number):
    assert not is_e164(number)


@pytest.mark.parametrize("key", ["CONTACT_MARIA_PHONE", "TWILIO_FROM_NUMBER"])
def test_twilio_rejects_non_e164_without_calling(maria, key):
    rec = Recorder(201, {"sid": "CA1"})
    env = {**ENV, key: "305-555-0123"}
    result = run(TwilioCallAction(env, dry_run=False, transport=rec.transport).run(ctx(maria)))
    assert not result.ok
    assert result.detail == f"{key} is not an E.164 number: use +13055550123 (no spaces or dashes)"
    assert rec.requests == []


def test_mask_phone():
    assert mask_phone("+13055550123") == "+1******0123"


# --- dry run and missing config ----------------------------------------------------------


@pytest.mark.parametrize("cls", [TelegramMessageAction, TwilioCallAction])
@pytest.mark.parametrize("env", [ENV, {}])
def test_dry_run_makes_no_request(maria, cls, env, caplog):
    rec = Recorder(200, {"ok": True})
    result = run(cls(env, dry_run=True, transport=rec.transport).run(ctx(maria)))
    assert (result.ok, result.detail) == (True, "dry run")
    assert rec.requests == []
    assert "DRY RUN" in caplog.text
    assert "SECRET" not in caplog.text and "tok3n" not in caplog.text  # secrets never logged


def test_missing_telegram_config(maria):
    result = run(TelegramMessageAction({}, dry_run=False).run(ctx(maria)))
    assert (result.ok, result.detail) == (False, "Telegram not configured: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID_MARIA missing")
    env = {k: v for k, v in ENV.items() if k != "TELEGRAM_BOT_TOKEN"}
    result = run(TelegramMessageAction(env, dry_run=False).run(ctx(maria)))
    assert result.detail == "Telegram not configured: TELEGRAM_BOT_TOKEN missing"


def test_missing_twilio_config(maria):
    env = {"TWILIO_ACCOUNT_SID": "AC0000", "TWILIO_FROM_NUMBER": "+13055550100", "TWILIO_AUTH_TOKEN": "  "}
    result = run(TwilioCallAction(env, dry_run=False).run(ctx(maria)))
    assert (result.ok, result.detail) == (False, "Twilio not configured: TWILIO_AUTH_TOKEN, CONTACT_MARIA_PHONE missing")


def test_contact_without_env_names(maria):
    bare = maria.model_copy(update={"phone_env": None, "telegram_chat_env": None})
    assert run(TwilioCallAction(ENV, dry_run=False).run(ctx(bare))).detail == (
        "Twilio not configured: no phone_env for maria in contacts.yaml"
    )
    assert not run(TelegramMessageAction(ENV, dry_run=False).run(ctx(None))).ok


# --- network failures --------------------------------------------------------------------


def test_timeout(maria):
    rec = Recorder(exc=httpx.ReadTimeout("slow"))
    result = run(TelegramMessageAction(ENV, dry_run=False, transport=rec.transport).run(ctx(maria)))
    assert (result.ok, result.detail) == (False, "Telegram timed out after 10 s")


def test_connection_error_hides_token(maria):
    rec = Recorder(exc=httpx.ConnectError("cannot reach https://api.telegram.org/bot123:SECRET/sendMessage"))
    result = run(TelegramMessageAction(ENV, dry_run=False, transport=rec.transport).run(ctx(maria)))
    assert not result.ok
    assert "SECRET" not in result.detail
    assert result.detail.startswith("Telegram unreachable: ConnectError")


# --- registry ----------------------------------------------------------------------------


def test_registry_dedupes_same_action_contact_and_text(maria):
    rec = Recorder(200, {"ok": True})
    clock = FakeClock()
    reg = build_registry(lambda m: None, ENV, dry_run=False, transport=rec.transport, clock=clock)
    assert run(reg.run("send_message", ctx(maria))).detail == "sent"
    clock.t = 10.0
    again = run(reg.run("send_message", ctx(maria)))
    assert (again.ok, again.detail) == (False, "duplicate suppressed")
    assert run(reg.run("send_message", ctx(maria, text="Otra cosa"))).ok  # different text is fine
    clock.t = DEDUPE_S + 0.1
    assert run(reg.run("send_message", ctx(maria))).detail == "sent"  # window has passed
    assert len(rec.requests) == 3


def test_registry_dedupe_is_per_action(maria):
    rec = Recorder(201, {"ok": True, "sid": "CA1"})
    reg = build_registry(lambda m: None, ENV, dry_run=False, transport=rec.transport, clock=FakeClock())
    assert run(reg.run("send_message", ctx(maria))).ok
    assert run(reg.run("place_call", ctx(maria))).ok


def test_speak_is_never_deduped():
    spoken = []
    reg = build_registry(spoken.append, {}, clock=FakeClock())
    for _ in range(3):
        assert run(reg.run("speak", ctx(None, text="Agua"))).ok
    assert spoken == [Speak(text="Agua", lang="es")] * 3


def test_registry_never_raises():
    class Boom(TelegramMessageAction):
        def build(self, ctx):
            raise RuntimeError("bug")

    reg = ActionRegistry([Boom({}, dry_run=False)])
    result = run(reg.run("send_message", ctx(None)))
    assert (result.ok, result.detail) == (False, "send_message failed: RuntimeError")
    assert run(reg.run("launch_rocket", ctx(None))).detail == "unknown action launch_rocket"


def test_room_control_mock():
    reg = build_registry(lambda m: None, {}, dry_run=False)
    assert run(reg.run("room_control", ctx(None, text="Lights on"))).detail == "done (mock)"
    reg = build_registry(lambda m: None, {}, dry_run=True)
    assert run(reg.run("room_control", ctx(None, text="Lights on"))).detail == "dry run"
