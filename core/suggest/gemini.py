"""Gemini provider (PRD A2, A3.5) through the google-genai SDK.

Structured JSON output: the response schema comes from the Pydantic schema models in provider.py,
and the reply is validated (and cleaned item by item) with Sentences / Options. Low temperature so
suggestions stay steady, and as little thinking as the model allows so an answer comes back well inside the 4 s budget.

Env: GEMINI_API_KEY (required), GEMINI_MODEL (default below; the id was checked against the live
model list, see docs/decisions.md).
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from core.suggest import prompts
from core.suggest.errors import ProviderError
from core.suggest.provider import (
    Bundle,
    BundleSchema,
    LevelContext,
    Options,
    OptionsSchema,
    Sentences,
    SentencesSchema,
    SuggestContext,
)

log = logging.getLogger("clench.suggest")

DEFAULT_MODEL = "gemini-3.8-flash"
TEMPERATURE = 0.2
MAX_OUTPUT_TOKENS = 512
MAX_BUNDLE_TOKENS = 2048  # a whole level: up to 5 leaves x 3 sentences, 5 options, 3 for right now
HTTP_TIMEOUT_MS = 10_000  # hard limit per request; the Suggester gives up after 4 s anyway

# Pause the AI (fixed phrases only) after these errors instead of failing every request.
PAUSE_S = {401: 300.0, 403: 300.0, 429: 60.0}

M = TypeVar("M", bound=BaseModel)


def thinking_config(model: str) -> types.ThinkingConfig:
    """As little thinking as the model allows: Gemini 2.x takes a budget (0 = off); Gemini 3 and later
    take a level, and gemini-3.8-flash rejects MINIMAL, so LOW."""
    if model.startswith("gemini-2"):
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *, client: Any = None) -> None:
        self.model = model
        # Tests pass a stand-in with the same aio.models.generate_content method.
        self._client = client or genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_MS))

    def config(
        self, system: str, schema: type[BaseModel], max_tokens: int = MAX_OUTPUT_TOKENS
    ) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            temperature=TEMPERATURE,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=thinking_config(self.model),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # no tools
        )

    async def compose(self, ctx: SuggestContext) -> Sentences:
        return await self._generate(prompts.compose(ctx), SentencesSchema, Sentences)

    async def more_options(self, ctx: SuggestContext) -> Options:
        return await self._generate(prompts.more_options(ctx), OptionsSchema, Options)

    async def level_bundle(self, ctx: LevelContext) -> Bundle:
        return await self._generate(prompts.level_bundle(ctx), BundleSchema, Bundle, MAX_BUNDLE_TOKENS)

    async def _generate(
        self, prompt: prompts.Prompt, schema: type[BaseModel], result: type[M], max_tokens: int = MAX_OUTPUT_TOKENS
    ) -> M:
        try:
            resp = await self._client.aio.models.generate_content(
                model=self.model, contents=prompt.user, config=self.config(prompt.system, schema, max_tokens)
            )
        except errors.APIError as e:
            code = e.code if isinstance(e.code, int) else None
            raise ProviderError(f"Gemini error {code} {e.status}: {e.message}", pause_s=PAUSE_S.get(code or 0)) from e
        text = getattr(resp, "text", None)
        if not text:
            reason = _finish_reason(resp)
            raise ProviderError(f"Gemini sent no text ({reason})")
        try:
            return result.model_validate_json(text)
        except ValidationError as e:
            raise ProviderError(f"Gemini sent JSON of the wrong shape: {e.error_count()} errors: {text[:200]}") from e

    async def aclose(self) -> None:
        aio = getattr(self._client, "aio", None)
        close = getattr(aio, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:  # closing is best effort at shutdown
                log.debug("gemini client close failed", exc_info=True)


def _finish_reason(resp: Any) -> str:
    try:
        return str(resp.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        feedback = getattr(resp, "prompt_feedback", None)
        return f"prompt feedback: {feedback}" if feedback else "no candidates"
