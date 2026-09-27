"""Shared event formats (PRD A4). Single source of truth, together with web/src/contracts.ts.

Any change here must update web/src/contracts.ts and docs/contracts.md in the same commit.

All messages are small JSON objects sent over WebSocket. Every message has a `type`
field that names it. Timestamps `t` are float seconds since the Unix epoch.
Tile indexes (`tile`, `highlight`) are 0-based positions in the current SCREEN's `tiles`.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

PointingMode = Literal["auto", "scan", "webcam", "gaze", "headtilt"]
# webcam = the board's head pose; gaze = an eye tracker plugged into the board (docs/eye-tracking.md);
# headtilt = the headband's motion sensor (Sensor Service).
PointSource = Literal["webcam", "gaze", "headtilt"]
# Where the highlight is coming from right now: Auto shows "scan" after falling back (and "gaze" or
# "webcam" while following), and Head tilt shows "scan" until it is built.
ActivePointer = Literal["scan", "webcam", "gaze", "headtilt"]
BodyStateLevel = Literal["calm", "normal", "elevated"]
Lang = Literal["en", "es"]
ScreenName = Literal["menu", "suggestions", "help_countdown", "paused", "calibrating"]
ActionName = Literal["speak", "send_message", "place_call", "room_control", "help_alert"]
# phrase = a confirmed sentence (the session waits for its AUDIO_DONE); echo = a picked tile's label
# said as it is picked; system = a fixed line from the Core (help alert). Only phrases change state.
UtteranceKind = Literal["phrase", "echo", "system"]
# branch = opens a smaller menu; leaf = an option that leads to a sentence (menu or AI-made);
# suggestion = a full sentence, picking it opens the confirm screen; other = "Other..." (the next page of new options).
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
    """Clench held `long_clench_ms` (2.5 s by default): start the help alert countdown."""

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
    """The person is facing tile `tile` of the SCREEN numbered `seq`. Sent when the tile changes and
    once for every new SCREEN. The Core ignores a POINT whose `seq` is not the current screen's."""

    type: Literal["POINT"] = "POINT"
    source: PointSource
    tile: int = Field(ge=0)
    seq: int = Field(ge=0)  # the SCREEN `seq` the tile index belongs to
    t: float


class FaceOk(_Msg):
    """Can the board see the person (the head for webcam pointing, the eyes for gaze)? Auto mode
    falls back to Scan when false for ~3 s."""

    type: Literal["FACE_OK"] = "FACE_OK"
    ok: bool


# --- Board -> Core ------------------------------------------------------------


class Ready(_Msg):
    """The board connected and is ready to draw. The Core replies with the current view."""

    type: Literal["READY"] = "READY"


class Reset(_Msg):
    """Back to Home: clears the screen stack and puts the highlight on the first tile (Suggested).
    Sent by the board when "Click to start" is clicked and by the dev panel's "Reset to Home"."""

    type: Literal["RESET"] = "RESET"


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
    # How long a clench must be held to count as a LONG_CLENCH (help), in ms. Omit to keep it.
    long_clench_ms: int | None = Field(default=None, ge=1000, le=5000)
    # Webcam / gaze pointing: how far (share of a tile's size) the point must be inside a new tile
    # before the highlight moves there, 0 to 0.2. Omit to keep it.
    tile_switch_margin: float | None = Field(default=None, ge=0.0, le=0.2)


# --- Core -> Board ------------------------------------------------------------


class Tile(_Msg):
    id: str  # dotted menu path; "ai:..." for AI-made options and sentences
    label: str
    kind: TileKind


class Screen(_Msg):
    """What the board should draw. The Core owns the highlight; the board only draws it."""

    type: Literal["SCREEN"] = "SCREEN"
    screen: ScreenName
    # Goes up every time the tiles change (not when only the highlight moves); POINT echoes it.
    seq: int = Field(ge=0)
    tiles: list[Tile] = Field(max_length=6)  # PRD D8: no more than six options
    highlight: int | None = Field(ge=0)  # None = nothing highlighted
    lang: Lang
    path: list[str]  # breadcrumb labels from home down to this level; [] at home
    # Seconds left before the help alert fires; set only when screen="help_countdown" (tiles=[]).
    countdown: int | None = Field(default=None, ge=0)
    # True while the Core waits (at most 4 s) for AI options after a pick; scanning is paused.
    loading: bool = False
    # Where the highlight comes from right now (None on the help countdown). "scan" in Auto or Head
    # tilt mode means the fallback is on: the board shows a small "Scanning" badge.
    pointer: ActivePointer | None = None


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


class Click(_Msg):
    """Play the short soft click (a picked "Other..."; no word is said for it). It goes into the
    board's sound queue in order with the echoes. Only sent when speak picks is on."""

    type: Literal["CLICK"] = "CLICK"


class ActionResult(_Msg):
    """How a confirmed action that leaves the laptop went (message, call, room control)."""

    type: Literal["ACTION_RESULT"] = "ACTION_RESULT"
    action: ActionName
    ok: bool
    detail: str  # "dry run" when ACTIONS_DRY_RUN is on; the service's error message on failure
    contact: str | None  # contact's display name in the current language; None when there is none


# --- Core -> Console and web dev panel ------------------------------------------


class Metrics(_Msg):
    """What one confirmed message cost (PRD section 12, D7), and what it would have cost in Day 1
    mode (menu.yaml order, no shortcut). Sent after every confirm, to consoles and input clients."""

    type: Literal["METRICS"] = "METRICS"
    text: str  # the confirmed sentence
    selections: int = Field(ge=1)  # clenches since home, the confirm clench included
    scan_steps: int = Field(ge=0)  # highlight moves waited through before the picks
    day1_selections: int = Field(ge=1)
    day1_scan_steps: int = Field(ge=0)


JevStatus = Literal["off", "waiting", "answered"]


class ShortcutDebug(_Msg):
    """Why the one-clench Suggested shortcut is on or off right now. Sent after every Home render (and
    again when Jev's answer arrives while Home shows), to consoles and input clients only."""

    type: Literal["SHORTCUT_DEBUG"] = "SHORTCUT_DEBUG"
    top: str | None  # the history's top Suggested phrase; None in Day 1 mode or with no phrases
    history_share: float = Field(ge=0.0, le=1.0)  # its share of what was said around this hour
    jev: JevStatus  # off = not configured, paused or Day 1 mode; waiting = no answer yet
    jev_pick: str | None  # the phrase Jev picked (None unless jev = "answered")
    jev_confidence: float | None = Field(ge=0.0, le=1.0)  # Jev's confidence in its pick
    shortcut: bool  # picking Suggested goes straight to the confirm screen with `top`
    reason: str  # e.g. "history share 0.72 >= 0.6"


# --- Board <-> Core over REST (not a WebSocket message) ---------------------------

MIN_HEAD_SPAN_DEG = 2.0  # a calibrated side closer than this to the center is a failed calibration


class HeadRange(_Msg):
    """The person's comfortable head range from the calibration overlay, in degrees of head yaw and
    pitch as the board measures them (GET / PUT /api/head-range). Saved in the database profile
    (PRD A7 `head_range_json`). Left and right lie on opposite sides of the center, as do up and down;
    the sign convention is the board's, so the Core only checks that shape."""

    center_yaw: float
    center_pitch: float
    left_yaw: float
    right_yaw: float
    up_pitch: float
    down_pitch: float

    @model_validator(mode="after")
    def _around_center(self) -> "HeadRange":
        for name, a, b, c in (
            ("yaw", self.left_yaw, self.center_yaw, self.right_yaw),
            ("pitch", self.up_pitch, self.center_pitch, self.down_pitch),
        ):
            if (a - b) * (c - b) >= 0 or min(abs(a - b), abs(c - b)) < MIN_HEAD_SPAN_DEG:
                raise ValueError(f"{name}: each side must be at least {MIN_HEAD_SPAN_DEG} degrees from the center")
        return self


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
        Reset,
        AudioDone,
        Settings,
        Screen,
        Confirm,
        Speak,
        PlayAudio,
        Click,
        ActionResult,
        Metrics,
        ShortcutDebug,
    ],
    Field(discriminator="type"),
]

_message_adapter: TypeAdapter[Message] = TypeAdapter(Message)


def parse_message(data: dict[str, Any]) -> Message:
    """Validate a decoded JSON object and return the matching message model.

    Raises pydantic.ValidationError if `type` is unknown or any field is invalid.
    """
    return _message_adapter.validate_python(data)
