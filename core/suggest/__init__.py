"""AI provider layer (PRD A3.5): LLMProvider interface, Gemini and fake providers, the Suggester.

LLM_PROVIDER picks the provider: gemini (default) | fake | claude | openai. With no key, or a
provider that is not built yet, the app runs with no AI: fixed phrases only, one log line saying so.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.suggest.provider import LLMProvider

__all__ = ["build_provider"]


def build_provider(env: Mapping[str, str]) -> tuple[LLMProvider | None, str]:
    """(provider, "") or (None, why there is no AI)."""
    name = env.get("LLM_PROVIDER", "").strip().lower() or "gemini"
    if name == "fake":
        from core.suggest.fake import FakeProvider

        return FakeProvider(), ""
    if name == "gemini":
        key = env.get("GEMINI_API_KEY", "").strip()
        if not key:
            return None, "GEMINI_API_KEY missing"
        from core.suggest.gemini import DEFAULT_MODEL, GeminiProvider  # imported only when used

        return GeminiProvider(key, env.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL), ""
    if name in ("claude", "openai"):
        return None, f"LLM_PROVIDER={name} is not built yet (see core/suggest/stubs.py)"
    if name in ("none", "off"):
        return None, f"LLM_PROVIDER={name}"
    return None, f"unknown LLM_PROVIDER={name!r} (use gemini, fake, claude or openai)"
