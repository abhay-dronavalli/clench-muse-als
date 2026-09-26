"""Send ONE real Telegram test message to a contact (default: maria), using .env.

Ignores ACTIONS_DRY_RUN on purpose: this script exists to test the real thing.

    uv run python scripts/test_telegram.py            # to Maria
    uv run python scripts/test_telegram.py carlos     # to another contact in data/contacts.yaml

Needs TELEGRAM_BOT_TOKEN and the contact's chat id (e.g. TELEGRAM_CHAT_ID_MARIA) in .env. The
contact must have pressed Start in a chat with the bot first, or Telegram answers "chat not found".
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

from core.actions import ActionContext  # noqa: E402
from core.actions.message import TelegramMessageAction, message_text  # noqa: E402
from core.config import load_env  # noqa: E402
from core.menu import load_menu  # noqa: E402
from core.profile import load_profile  # noqa: E402


def main() -> int:
    contact_id = sys.argv[1] if len(sys.argv) > 1 else "maria"
    menu = load_menu()
    contact = menu.contacts.get(contact_id)
    if contact is None:
        print(f"FAILED: no contact '{contact_id}' in data/contacts.yaml ({', '.join(menu.contacts)})")
        return 1
    profile = load_profile(menu.contacts)
    ctx = ActionContext(
        text="Prueba de Clench: si ves esto, los mensajes funcionan.",
        lang="es",
        contact=contact,
        patient_name=profile.name,
    )
    print(f"Sending a real Telegram message to {contact.label_es} ({contact.telegram_chat_env}):")
    print(f"  {message_text(ctx)}")
    result = asyncio.run(TelegramMessageAction(load_env(), dry_run=False).run(ctx))
    if result.ok:
        print(f"OK: {result.detail}. Check {contact.label_es}'s Telegram.")
        return 0
    print(f"FAILED: {result.detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
