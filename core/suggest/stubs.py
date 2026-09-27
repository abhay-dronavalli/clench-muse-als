"""Placeholders for the other providers the PRD names (A3.5: LLM_PROVIDER = gemini | claude | openai).

Not implemented yet. build_provider() logs that and runs with fixed phrases only. To add one:
implement compose(), more_options() and level_bundle() with structured JSON output validated by
Sentences / Options / Bundle (see gemini.py), with the prompts from prompts.py, and return it from build_provider().
"""

from __future__ import annotations

from core.suggest.provider import Bundle, LevelContext, Options, Sentences, SuggestContext
from core.suggest.provider import SearchContext, SearchSuggestions


class _NotBuilt:
    name = "not built"
    model = ""

    async def search_suggestions(self, ctx: SearchContext) -> SearchSuggestions:
        raise NotImplementedError(f"{self.name} provider is not built yet")

    async def compose(self, ctx: SuggestContext) -> Sentences:
        raise NotImplementedError(f"{self.name} provider is not built yet")

    async def more_options(self, ctx: SuggestContext) -> Options:
        raise NotImplementedError(f"{self.name} provider is not built yet")

    async def level_bundle(self, ctx: LevelContext) -> Bundle:
        raise NotImplementedError(f"{self.name} provider is not built yet")

    async def aclose(self) -> None:
        pass


class ClaudeProvider(_NotBuilt):
    """TODO: Anthropic Messages API (env ANTHROPIC_API_KEY, CLAUDE_MODEL)."""

    name = "claude"


class OpenAIProvider(_NotBuilt):
    """TODO: OpenAI Responses API (env OPENAI_API_KEY, OPENAI_MODEL)."""

    name = "openai"
