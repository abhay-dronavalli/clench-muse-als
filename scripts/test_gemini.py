"""Ask the real AI provider from .env for suggestions on "I need > Pain", in Spanish and English,
and print them with how long each took.

Does nothing real without --send: it only prints what it would ask.

    uv run python scripts/test_gemini.py --send

Needs GEMINI_API_KEY in .env (LLM_PROVIDER defaults to gemini; GEMINI_MODEL defaults to the model in
core/suggest/gemini.py). Prints each result, or FAILED: <exact error> (e.g. "Gemini error 400
INVALID_ARGUMENT: API key not valid" = wrong key).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

from core.config import load_env  # noqa: E402
from core.contracts import Lang  # noqa: E402
from core.suggest import build_provider  # noqa: E402
from core.suggest.provider import LLMProvider, SuggestContext  # noqa: E402

PATHS: dict[Lang, tuple[str, ...]] = {"es": ("Necesito", "Dolor"), "en": ("I need", "Pain")}
SHOWN: dict[Lang, tuple[str, ...]] = {
    "es": ("Cabeza", "Espalda", "Estómago", "Pecho", "Piernas"),
    "en": ("Head", "Back", "Stomach", "Chest", "Legs"),
}
CONTACTS: dict[Lang, tuple[str, ...]] = {"es": ("María", "Carlos", "Enfermera"), "en": ("Maria", "Carlos", "Nurse")}


def contexts() -> list[SuggestContext]:
    hour = time.localtime().tm_hour
    return [
        SuggestContext(path=PATHS[lang], lang=lang, hour=hour, patient_name="Luis", contacts=CONTACTS[lang], shown=SHOWN[lang])
        for lang in ("es", "en")
    ]


async def run(provider: LLMProvider) -> int:
    failed = 0
    try:
        for ctx in contexts():
            label = " > ".join(ctx.path)
            for method in ("compose", "more_options"):
                start = time.perf_counter()
                try:
                    if method == "compose":
                        items = [s for s in (await provider.compose(ctx)).sentences]
                    else:
                        items = [f"{o.label}: {o.text}" for o in (await provider.more_options(ctx)).options]
                except Exception as e:
                    failed += 1
                    print(f"FAILED {method} ({ctx.lang}) {label} after {time.perf_counter() - start:.2f} s: {e}")
                    continue
                print(f"OK {method} ({ctx.lang}) {label}: {len(items)} in {time.perf_counter() - start:.2f} s")
                for item in items:
                    print(f"   - {item}")
    finally:
        await provider.aclose()
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask the AI provider for suggestions on I need > Pain.")
    parser.add_argument("--send", action="store_true", help="really call the provider (otherwise only print)")
    args = parser.parse_args(argv)
    if not args.send:
        print("Would ask the AI provider from .env, for I need > Pain in Spanish and English:")
        for ctx in contexts():
            print(f"  compose and more_options ({ctx.lang}): {' > '.join(ctx.path)}")
        print("Nothing sent. Add --send to really call it.")
        return 0
    provider, why = build_provider(load_env())
    if provider is None:
        print(f"FAILED: no AI provider: {why} (see .env.example)")
        return 1
    print(f"Provider: {provider.name}, model {provider.model}")
    return asyncio.run(run(provider))


if __name__ == "__main__":
    sys.exit(main())
