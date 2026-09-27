"""Place ONE real Twilio test call to a contact (default: maria), using .env.

Does nothing real without --send: it only prints what it would do. With --send it ignores
ACTIONS_DRY_RUN on purpose, since this script exists to test the real thing.

    uv run python scripts/test_call.py --send            # call Maria
    uv run python scripts/test_call.py carlos --send     # another contact in data/contacts.yaml
    uv run python scripts/test_call.py maria en --send   # say it in English

Needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER and the contact's number (e.g.
CONTACT_MARIA_PHONE) in .env, all E.164 like +13055550123. On a Twilio trial account the number
called must be verified in the Twilio console (otherwise error 21219), and the call starts with a
short trial message before yours.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

from core.actions import ActionContext  # noqa: E402
from core.actions.call import TwilioCallAction, call_message  # noqa: E402
from core.config import load_env  # noqa: E402
from core.contracts import Lang  # noqa: E402
from core.menu import load_menu  # noqa: E402
from core.profile import load_profile  # noqa: E402

TEXT: dict[Lang, str] = {
    "es": "Esta es una llamada de prueba de Clench. Si la escuchas, las llamadas funcionan.",
    "en": "This is a Clench test call. If you can hear it, calls work.",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Place one real Twilio test call.")
    parser.add_argument("contact", nargs="?", default="maria", help="contact id (default: maria)")
    parser.add_argument("lang", nargs="?", default="es", choices=["es", "en"], help="language (default: es)")
    parser.add_argument("--send", action="store_true", help="really place the call (otherwise only print)")
    args = parser.parse_args(argv)
    contact_id = args.contact
    lang: Lang = args.lang
    menu = load_menu()
    contact = menu.contacts.get(contact_id)
    if contact is None:
        print(f"FAILED: no contact '{contact_id}' in data/contacts.yaml ({', '.join(menu.contacts)})")
        return 1
    profile = load_profile(menu.contacts)
    ctx = ActionContext(text=TEXT[lang], lang=lang, contact=contact, patient_name=profile.name)
    if not args.send:
        print(f"Would place a real Twilio call to {contact.label_es} ({contact.phone_env}), said twice:")
        print(f"  {call_message(ctx)}")
        print("No call placed. Add --send to really call.")
        return 0
    print(f"Placing a real Twilio call to {contact.label_es} ({contact.phone_env}), said twice:")
    print(f"  {call_message(ctx)}")
    result = asyncio.run(TwilioCallAction(load_env(), dry_run=False).run(ctx))
    if result.ok:
        print(f"OK: {result.detail}. {contact.label_es}'s phone should ring in a few seconds.")
        return 0
    print(f"FAILED: {result.detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
