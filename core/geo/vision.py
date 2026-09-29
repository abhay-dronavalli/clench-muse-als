"""Gemini looks at one live Street View image: ramp, level, steps, steps with a signed accessible route,
or unclear, with the accessibility cues it saw (structured output, with a confidence).

Live only, like core/geo/google.py: the image and the answer are never stored, never scored, and
never spoken. Uses the existing Gemini setup (GEMINI_API_KEY, GEMINI_MODEL); core/suggest is not
changed, only its default model id and thinking setting are reused.
"""

from __future__ import annotations

from typing import Any, Literal

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError, field_validator

from core.suggest.gemini import DEFAULT_MODEL, thinking_config

HTTP_TIMEOUT_MS = 15_000
# Gemini 3 counts thinking tokens against max_output_tokens; 256 cut some answers off mid-JSON.
MAX_OUTPUT_TOKENS = 1024
NOTE_MAX = 200
ATTEMPTS = 2  # a malformed answer is asked once more (the image is already in memory)

SYSTEM = (
    "You check curbside drop-off points for a wheelchair user. You get one street-level photo taken "
    "from the road, facing the building the rider is going to.\n"
    "First list every accessibility cue you can actually see: a ramp or sloped walk to a door, a curb "
    "cut, the blue wheelchair symbol (on a sign, a door, or painted on the ground), automatic or "
    "push-button doors, a level (step-free) entrance, steps, handrails. Then classify the way from the "
    "curb to the building's door:\n"
    "  ramp: a ramp or sloped walk leads to the door.\n"
    "  level: the walk to the door is level, with no steps.\n"
    "  steps: steps lead to the door and nothing visible points to a step-free way.\n"
    "  steps_with_accessible_route: steps lead to the door, but a wheelchair symbol, a ramp, or a "
    "sign shows a step-free way nearby.\n"
    "  unclear: no door or path is visible, or you cannot tell.\n"
    "Only describe what is visible. Never guess. Lower your confidence when the view is far, "
    "blocked, or at an angle."
)

Cue = Literal["ramp", "curb_cut", "wheelchair_symbol", "automatic_door", "level_entrance", "steps", "handrail"]


class RampLabel(BaseModel):
    label: Literal["ramp", "level", "steps", "steps_with_accessible_route", "unclear"]
    confidence: float = Field(ge=0, le=1)
    cues: list[Cue] = Field(default_factory=list, description="Every accessibility cue visible in the photo.")
    note: str = Field(description="One short sentence: what in the photo shows it.")

    @field_validator("note")
    @classmethod
    def _short(cls, v: str) -> str:
        v = " ".join(v.split())
        return v if len(v) <= NOTE_MAX else v[: NOTE_MAX - 1].rstrip() + "…"


class VisionError(RuntimeError):
    pass


class RampClassifier:
    def __init__(self, api_key: str, model: str | None = None, *, client: Any = None) -> None:
        if not api_key and client is None:
            raise VisionError("GEMINI_API_KEY is not set")
        self.model = model or DEFAULT_MODEL
        self._client = client or genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_MS))

    def classify(self, image: bytes, walk_m: float) -> RampLabel:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            temperature=0.0,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            response_schema=RampLabel,
            thinking_config=thinking_config(self.model),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # no tools
        )
        contents = [
            types.Part.from_bytes(data=image, mime_type="image/jpeg"),
            f"The building is about {walk_m:.0f} m from where the photo was taken.",
        ]
        problem = ""
        for _ in range(ATTEMPTS):
            try:
                resp = self._client.models.generate_content(model=self.model, contents=contents, config=config)
            except errors.APIError as e:
                raise VisionError(f"Gemini error {e.code}") from None
            text = getattr(resp, "text", None)
            if not text:
                problem = f"Gemini sent no text ({_finish(resp)})"
                continue
            try:
                return RampLabel.model_validate_json(text)
            except ValidationError as e:
                first = e.errors()[0]
                problem = f"Gemini sent JSON of the wrong shape ({_finish(resp)}; {'.'.join(map(str, first['loc'])) or 'body'}: {first['msg']})"
        raise VisionError(problem)


def _finish(resp: Any) -> str:
    try:
        return f"finish {resp.candidates[0].finish_reason}"
    except (AttributeError, IndexError, TypeError):
        return "no candidates"
