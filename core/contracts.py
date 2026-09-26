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
ActionName = Literal["speak", "send_message", "place_call", "room_control", "help_alert"]
# phrase = a confirmed sentence (the session waits for its AUDIO_DONE); echo = a picked tile's label
# said as it is picked; system = a fixed line from the Core (help alert). Only phrases change state.
UtteranceKind = Literal["phrase", "echo", "system"]
# branch = opens a smaller menu; leaf = an option that leads to a sentence (menu or AI-made);
# suggestion = a full sentence, picking it opens the confirm screen; other = "Other..." / "Spell it".
TileKind = Literal["branch", "leaf", "suggestion", "other"]


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


# --- Board -> Core ------------------------------------------------------------


class Ready(_Msg):
    """The board connected and is ready to draw. The Core replies with the current view."""

    type: Literal["READY"] = "READY"


class AudioDone(_Msg):
    """Speech (SPEAK) or audio (PLAY_AUDIO) `id` finished, failed or was interrupted on the board."""

    type: Literal["AUDIO_DONE"] = "AUDIO_DONE"
    id: str = Field(min_length=1)  # the SPEAK / PLAY_AUDIO id


# --- Console -> Core, and Core -> every client ----------------------------------


class Settings(_Msg):
    """Caregiver settings change. The Core also sends it, fully filled in, to every client on connect
    and after every change."""

    type: Literal["SETTINGS"] = "SETTINGS"
    pointing_mode: PointingMode
    scan_ms: int = Field(gt=0)
    lang: Lang | None = None  # omit to keep the current language
    speak_picks: bool | None = None  # say each picked tile aloud; omit to keep the current value
    # Rank by the patient's history; false = "Day 1 mode" (menu.yaml order). Omit to keep it.
    learning: bool | None = None


# --- Core -> Board ------------------------------------------------------------


class Tile(_Msg):
    id: str  # dotted menu path; "ai:..." for AI-made options and sentences
    label: str
    kind: TileKind


class Screen(_Msg):
    """What the board should draw. The Core owns the highlight; the board only draws it."""

    type: Literal["SCREEN"] = "SCREEN"
    screen: ScreenName
    tiles: list[Tile] = Field(max_length=6)  # PRD D8: no more than six options
    highlight: int | None = Field(ge=0)  # None = nothing highlighted
    lang: Lang
    path: list[str]  # breadcrumb labels from home down to this level; [] at home
    # Seconds left before the help alert fires; set only when screen="help_countdown" (tiles=[]).
    countdown: int | None = Field(default=None, ge=0)
    # True while the Core waits (at most 4 s) for AI options after a pick; scanning is paused.
    loading: bool = False


class Confirm(_Msg):
    """The "Send this?" screen. Nothing is spoken or sent without a confirming clench (PRD D5)."""

    type: Literal["CONFIRM"] = "CONFIRM"
    text: str
    action: ActionName


class Speak(_Msg):
    """Say `text` with the browser's speech synthesis (no cloud audio for it right now).

    A phrase is only ever sent after a confirming clench (PRD D5).
    """

    type: Literal["SPEAK"] = "SPEAK"
    id: str = Field(min_length=1)  # utterance id, echoed back in AUDIO_DONE
    kind: UtteranceKind
    text: str
    lang: Lang


class PlayAudio(_Msg):
    """Play cloud TTS audio served by the Core (/audio/<hash>.mp3). Same rules as SPEAK."""

    type: Literal["PLAY_AUDIO"] = "PLAY_AUDIO"
    id: str = Field(min_length=1)
    kind: UtteranceKind
    url: str
    text: str  # what the audio says; the board speaks it with browser speech if the file fails
    lang: Lang
    cached: bool  # true = the file was already on disk (no request to the TTS service)


class ActionResult(_Msg):
    """How a confirmed action that leaves the laptop went (message, call, room control)."""

    type: Literal["ACTION_RESULT"] = "ACTION_RESULT"
    action: ActionName
    ok: bool
    detail: str  # "dry run" when ACTIONS_DRY_RUN is on; the service's error message on failure
    contact: str | None  # contact's display name in the current language; None when there is none


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
        Ready,
        AudioDone,
        Settings,
        Screen,
        Confirm,
        Speak,
        PlayAudio,
        ActionResult,
    ],
    Field(discriminator="type"),
]

_message_adapter: TypeAdapter[Message] = TypeAdapter(Message)


def parse_message(data: dict[str, Any]) -> Message:
    """Validate a decoded JSON object and return the matching message model.

    Raises pydantic.ValidationError if `type` is unknown or any field is invalid.
    """
    return _message_adapter.validate_python(data)
