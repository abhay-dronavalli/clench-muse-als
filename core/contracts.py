"""Shared event formats (PRD A4). Single source of truth, together with web/src/contracts.ts.

Any change here must update web/src/contracts.ts and docs/contracts.md in the same commit.

All messages are small JSON objects sent over WebSocket. Every message has a `type`
field that names it. Timestamps `t` are float seconds since the Unix epoch.
Tile indexes (`tile`, `highlight`) are 0-based positions in the current SCREEN's `tiles`.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PointingMode = Literal["auto", "scan", "webcam", "headtilt"]
PointSource = Literal["webcam", "headtilt"]
BodyStateLevel = Literal["calm", "normal", "elevated"]
Lang = Literal["en", "es"]
ScreenName = Literal["menu", "suggestions", "help_countdown", "paused", "calibrating"]
ActionName = Literal["speak", "send_text", "place_call", "room_control", "help_alert"]


class _Msg(BaseModel):
    # Unknown fields are rejected so the Python and TypeScript contracts cannot drift silently.
    model_config = ConfigDict(extra="forbid")


# --- Sensor Service -> Core ---------------------------------------------------


class Clench(_Msg):
    """Short jaw clench: pick the highlighted tile."""

    type: Literal["CLENCH"] = "CLENCH"
    t: float
    strength: float = Field(ge=0.0, le=1.0)


class DoubleBlink(_Msg):
    """Two blinks close together: go back / no / cancel."""

    type: Literal["DOUBLE_BLINK"] = "DOUBLE_BLINK"
    t: float


class LongClench(_Msg):
    """Clench held about 1.5 s: start the help alert countdown."""

    type: Literal["LONG_CLENCH"] = "LONG_CLENCH"
    t: float
    duration: float = Field(gt=0.0)


class State(_Msg):
    """Body state. Only reorders options, never takes action (PRD D10)."""

    type: Literal["STATE"] = "STATE"
    t: float
    level: BodyStateLevel
    hr: float | None  # heart rate in bpm; None when PPG has no reading
    motion: float = Field(ge=0.0)  # restlessness, 0 = still
    eyes_closed: bool


class Signal(_Msg):
    """Thinned signal sample for the caregiver chart only. One value per channel."""

    type: Literal["SIGNAL"] = "SIGNAL"
    t: float
    ch: list[float]


# --- Board -> Core (Webcam mode) / Sensor Service -> Core (Head tilt mode) ----


class Point(_Msg):
    """The person is facing tile `tile`. Sent only when the tile changes."""

    type: Literal["POINT"] = "POINT"
    source: PointSource
    tile: int = Field(ge=0)
    t: float


class FaceOk(_Msg):
    """Webcam face tracking status. Auto mode falls back to Scan when false for ~3 s."""

    type: Literal["FACE_OK"] = "FACE_OK"
    ok: bool


# --- Console -> Core ----------------------------------------------------------


class Settings(_Msg):
    """Caregiver settings change."""

    type: Literal["SETTINGS"] = "SETTINGS"
    pointing_mode: PointingMode
    scan_ms: int = Field(gt=0)


# --- Core -> Board ------------------------------------------------------------


class Tile(_Msg):
    id: str
    label: str


class Screen(_Msg):
    """What the board should draw. The Core owns the highlight; the board only draws it."""

    type: Literal["SCREEN"] = "SCREEN"
    screen: ScreenName
    tiles: list[Tile] = Field(max_length=6)  # PRD D8: no more than six options
    highlight: int | None = Field(ge=0)  # None = nothing highlighted
    lang: Lang


class Confirm(_Msg):
    """The "Send this?" screen. Nothing is spoken or sent without a confirming clench (PRD D5)."""

    type: Literal["CONFIRM"] = "CONFIRM"
    text: str
    action: ActionName


class PlayAudio(_Msg):
    """Play an audio file served by the Core."""

    type: Literal["PLAY_AUDIO"] = "PLAY_AUDIO"
    url: str


# --- Union and helpers --------------------------------------------------------

Message = Annotated[
    Union[
        Clench,
        DoubleBlink,
        LongClench,
        State,
        Signal,
        Point,
        FaceOk,
        Settings,
        Screen,
        Confirm,
        PlayAudio,
    ],
    Field(discriminator="type"),
]

_message_adapter: TypeAdapter[Message] = TypeAdapter(Message)


def parse_message(data: dict[str, Any]) -> Message:
    """Validate a decoded JSON object and return the matching message model.

    Raises pydantic.ValidationError if `type` is unknown or any field is invalid.
    """
    return _message_adapter.validate_python(data)
