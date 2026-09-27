"""Ask Jev (TypeSafe) to rank the home Suggested phrases for a sample moment, and print the
probabilities, the confidence and how long it took, for whichever access path .env configures:
TYPESAFE_API_KEY (TypeSafe API) first, else CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN
(Cloudflare Workers AI, model typesafe/jev).

Does nothing real without --send: it only prints what it would ask.

    uv run python scripts/test_jev.py --send

Prints OK with the answer, or FAILED: <exact error> (e.g. "TypeSafe API error (HTTP 401): ..." = wrong
key; "Cloudflare Workers AI error (HTTP 403)" = the token lacks Workers AI access).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

from core.config import load_env  # noqa: E402
from core.menu import load_menu  # noqa: E402
from core.rank.jev import JevRanker, jev_access  # noqa: E402

# A sample moment: Saturday evening, Luis texts his daughter most evenings around now.
STATE = """Now: Saturday 17:40
Language: Spanish
Screen: Home > Sugerencias
Body state: normal
Last messages:
- yesterday 17:12: Mija, estoy bien, llámame a las seis.
- yesterday 16:05: Quiero agua, por favor.
- yesterday 08:20: Tengo hambre. ¿Me das algo de comer?
- Thursday 17:31: Mija, estoy bien, llámame a las seis.
- Thursday 15:10: Me duele mucho la espalda. ¿Me ayudas a voltearme?
Most used phrases (times used):
- Mija, estoy bien, llámame a las seis. (12)
- Quiero agua, por favor. (10)
- Tengo hambre. ¿Me das algo de comer? (7)
- Por favor, prende la televisión. (4)
- Gracias por todo hoy. Te quiero. (3)"""


def candidates() -> dict[str, str]:
    """The home Suggested candidates in Spanish: the fixed phrases, plus the text to María."""
    menu = load_menu()
    suggested = menu.find("suggested")
    maria = menu.find("people.maria.text")
    assert suggested is not None and maria is not None
    out = {"people.maria.text": maria.phrase("es")}
    out.update({f"suggested.{c.id}": c.phrase("es") for c in suggested.children or []})
    return out


async def run(jev: JevRanker) -> int:
    criteria = candidates()
    try:
        answer = await jev.ask(criteria, STATE)
    except Exception as e:
        print(f"FAILED: {e}")
        return 1
    finally:
        await jev.aclose()
    print(f"OK: {jev.access.name} answered in {answer.latency_s:.2f} s")
    print(f"   choice: {answer.choice} (confidence {answer.confidence:.2f})")
    for option, p in sorted(answer.probabilities.items(), key=lambda kv: -kv[1]):
        print(f"   {p:5.2f}  {option:22s} {criteria[option]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank the home Suggested phrases with Jev for a sample moment.")
    parser.add_argument("--send", action="store_true", help="really call Jev (otherwise only print)")
    args = parser.parse_args(argv)
    access, why = jev_access(load_env())
    if not args.send:
        where = access.name if access is not None else f"nothing configured ({why})"
        print(f"Would ask Jev through {where} which of these the patient most likely wants:")
        for option, text in candidates().items():
            print(f"  {option:22s} {text}")
        print("with this state:\n  " + STATE.replace("\n", "\n  "))
        print("Nothing sent. Add --send to really call it.")
        return 0
    if access is None:
        print(f"FAILED: no Jev access: {why} (see .env.example)")
        return 1
    return asyncio.run(run(JevRanker(access)))


if __name__ == "__main__":
    sys.exit(main())
