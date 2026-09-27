import json
import re
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from core import contracts
from core.contracts import Message, parse_message

ROOT = Path(__file__).resolve().parent.parent

# One example of each message, matching docs/contracts.md.
EXAMPLES: dict[str, tuple[type, dict]] = {
    "CLENCH": (contracts.Clench, {"type": "CLENCH", "t": 1727300000.12, "strength": 0.83}),
    "DOUBLE_BLINK": (contracts.DoubleBlink, {"type": "DOUBLE_BLINK", "t": 1727300003.4}),
    "LONG_CLENCH": (
        contracts.LongClench,
        {"type": "LONG_CLENCH", "t": 1727300009.9, "duration": 2.6},
    ),
    "STATE": (
        contracts.State,
        {
            "type": "STATE",
            "t": 1727300010.0,
            "level": "elevated",
            "hr": 94.0,
            "motion": 0.7,
            "eyes_closed": False,
        },
    ),
    "SIGNAL": (
        contracts.Signal,
        {"type": "SIGNAL", "t": 1727300010.05, "ch": [12.5, -3.1, 40.2, 8.8],
         "connected": None, "profile": None, "emg": None, "threshold": None, "blocked": None},
    ),
    "POINT": (
        contracts.Point,
        {"type": "POINT", "source": "webcam", "tile": 3, "seq": 42, "t": 1727300011.2},
    ),
    "FACE_OK": (contracts.FaceOk, {"type": "FACE_OK", "ok": False}),
    "READY": (contracts.Ready, {"type": "READY"}),
    "RESET": (contracts.Reset, {"type": "RESET"}),
    "AUDIO_DONE": (contracts.AudioDone, {"type": "AUDIO_DONE", "id": "3f9c2a71b0de"}),
    "TAP": (contracts.Tap, {"type": "TAP", "tile": 3, "seq": 42, "t": 1727300011.2}),
    "SETTINGS": (
        contracts.Settings,
        {
            "type": "SETTINGS", "pointing_mode": "auto", "scan_ms": 1000, "lang": "es", "speak_picks": False,
            "learning": True, "long_clench_ms": 2500, "tile_switch_margin": 0.05, "muse_enabled": None,
        },
    ),
    "SCREEN": (
        contracts.Screen,
        {
            "type": "SCREEN",
            "screen": "menu",
            "seq": 3,
            "tiles": [
                {"id": "suggested", "label": "Sugerencias", "kind": "branch"},
                {"id": "need", "label": "Necesito", "kind": "branch"},
                {"id": "ai:need.pain.brazos", "label": "Brazos", "kind": "leaf"},
                {"id": "ai:suggested.s1", "label": "Tengo hambre. ¿Qué hay de almuerzo?", "kind": "suggestion"},
                {"id": "other", "label": "Otro...", "kind": "other"},
            ],
            "highlight": 2,
            "lang": "es",
            "path": [],
            "countdown": None,
            "loading": False,
            "pointer": "scan",
        },
    ),
    "CONFIRM": (
        contracts.Confirm,
        {
            "type": "CONFIRM",
            "text": "Mija, estoy bien, llámame a las seis.",
            "action": "send_message",
        },
    ),
    "SPEAK": (
        contracts.Speak,
        {"type": "SPEAK", "id": "u1", "kind": "phrase", "text": "Tengo hambre. ¿Qué hay de almuerzo?", "lang": "es"},
    ),
    "PLAY_AUDIO": (
        contracts.PlayAudio,
        {
            "type": "PLAY_AUDIO",
            "id": "u2",
            "kind": "echo",
            "url": "/audio/abc123.mp3",
            "text": "Dolor",
            "lang": "es",
            "cached": True,
        },
    ),
    "CLICK": (contracts.Click, {"type": "CLICK"}),
    "ACTION_RESULT": (
        contracts.ActionResult,
        {"type": "ACTION_RESULT", "action": "send_message", "ok": True, "detail": "sent", "contact": "María"},
    ),
    "BACK_PROMPT": (
        contracts.BackPrompt,
        {"type": "BACK_PROMPT", "open": True, "kind": "menu", "timeout_ms": 3000},
    ),
    "INPUT_EVENT": (
        contracts.InputEvent,
        {
            "type": "INPUT_EVENT",
            "t": 1777001234.5,
            "kind": "CLENCH",
            "source": "muse",
            "accepted": False,
            "reason": "Muse input is paused",
            "strength": 0.62,
            "duration": None,
        },
    ),
    "SHORTCUT_DEBUG": (
        contracts.ShortcutDebug,
        {
            "type": "SHORTCUT_DEBUG",
            "top": "Mija, estoy bien, llámame a las seis.",
            "history_share": 0.72,
            "jev": "answered",
            "jev_pick": "Mija, estoy bien, llámame a las seis.",
            "jev_confidence": 0.52,
            "shortcut": True,
            "reason": "history share 0.72 >= 0.6",
        },
    ),
    "METRICS": (
        contracts.Metrics,
        {"type": "METRICS", "text": "Mija, estoy bien.", "selections": 2, "scan_steps": 0, "day1_selections": 5, "day1_scan_steps": 6},
    ),
}

MESSAGE_CLASSES = get_args(get_args(Message)[0])


def test_every_message_has_an_example():
    assert {cls.model_fields["type"].default for cls in MESSAGE_CLASSES} == set(EXAMPLES)


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_round_trip(name):
    cls, example = EXAMPLES[name]
    msg = parse_message(example)
    assert type(msg) is cls
    assert msg.model_dump(mode="json") == example
    # Through a JSON string too, as it travels over the WebSocket.
    assert parse_message(json.loads(msg.model_dump_json())) == msg


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "NOPE", "t": 1.0},
        {"t": 1.0},
        {"type": "SETTINGS", "pointing_mode": "eyegaze", "scan_ms": 1000},
        {"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 0},
        {"type": "CLENCH", "t": 1.0, "strength": 0.5, "extra": 1},
        {"type": "CLENCH", "t": 1.0, "strength": 1.5},
        {"type": "STATE", "t": 1.0, "level": "panic", "hr": None, "motion": 0, "eyes_closed": False},
        {"type": "POINT", "source": "webcam", "tile": -1, "seq": 0, "t": 1.0},
        {"type": "POINT", "source": "webcam", "tile": 1, "t": 1.0},  # seq is required
        {"type": "POINT", "source": "webcam", "tile": 1, "seq": -1, "t": 1.0},
        {"type": "CONFIRM", "text": "hi", "action": "delete_everything"},
        {"type": "SPEAK", "id": "u1", "kind": "phrase", "text": "hi", "lang": "fr"},
        {"type": "SPEAK", "text": "hi", "lang": "en"},  # id and kind are required
        {"type": "SPEAK", "id": "u1", "kind": "shout", "text": "hi", "lang": "en"},
        {"type": "AUDIO_DONE"},  # id is required
        {"type": "AUDIO_DONE", "id": ""},
        {"type": "PLAY_AUDIO", "id": "u1", "kind": "echo", "url": "/audio/a.mp3", "text": "hi", "lang": "en"},
        {"type": "ACTION_RESULT", "action": "send_text", "ok": True, "detail": "", "contact": None},
        {"type": "SCREEN", "screen": "help_countdown", "seq": 0, "tiles": [], "highlight": None, "lang": "en", "path": [], "countdown": -1},
        {"type": "SCREEN", "screen": "menu", "tiles": [], "highlight": None, "lang": "en", "path": []},  # seq is required
        {"type": "SCREEN", "screen": "menu", "seq": 1, "tiles": [], "highlight": None, "lang": "en", "path": [], "pointer": "eyes"},
        {"type": "ACTION_RESULT", "action": "place_call", "ok": True, "detail": ""},  # contact is required
        {"type": "READY", "board": 1},
        {"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 1000, "lang": "de"},
        {"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 1000, "learning": "day1"},
        {"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 1000, "long_clench_ms": 500},
        {"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 1000, "tile_switch_margin": 0.5},
        {"type": "METRICS", "text": "x", "selections": 0, "scan_steps": 0, "day1_selections": 1, "day1_scan_steps": 0},
        {"type": "METRICS", "text": "x", "selections": 2, "scan_steps": 0, "day1_selections": 5},
        {
            "type": "SCREEN",
            "screen": "menu",
            "seq": 1,
            "tiles": [{"id": str(i), "label": str(i), "kind": "leaf"} for i in range(7)],
            "highlight": 0,
            "lang": "en",
            "path": [],
        },
        {"type": "SCREEN", "screen": "menu", "seq": 1, "tiles": [{"id": "a", "label": "A"}], "highlight": 0, "lang": "en", "path": []},
        {
            "type": "SCREEN",
            "screen": "menu",
            "seq": 1,
            "tiles": [{"id": "a", "label": "A", "kind": "folder"}],
            "highlight": 0,
            "lang": "en",
            "path": [],
        },
        {"type": "SCREEN", "screen": "menu", "seq": 1, "tiles": [], "highlight": None, "lang": "en", "path": [], "loading": "maybe"},
    ],
)
def test_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        parse_message(bad)


def test_screen_loading_and_pointer_are_optional():
    msg = parse_message({"type": "SCREEN", "screen": "menu", "seq": 0, "tiles": [], "highlight": None, "lang": "en", "path": []})
    assert msg.loading is False
    assert msg.pointer is None


HEAD_RANGE = {"center_yaw": 0.5, "center_pitch": -2.0, "left_yaw": -18.0, "right_yaw": 17.0, "up_pitch": 9.0, "down_pitch": -12.0}


def test_head_range_round_trips():
    assert contracts.HeadRange.model_validate(HEAD_RANGE).model_dump() == HEAD_RANGE
    # The sign convention is the board's: a mirrored range is just as valid.
    flipped = {**HEAD_RANGE, "left_yaw": 17.0, "right_yaw": -18.0}
    contracts.HeadRange.model_validate(flipped)


@pytest.mark.parametrize(
    "change",
    [
        {"left_yaw": 5.0},  # left and right on the same side of the center
        {"right_yaw": 1.5},  # a side closer than 2 degrees to the center
        {"up_pitch": -2.0},  # up at the center
        {"down_pitch": 20.0},  # up and down on the same side
        {"extra": 1},
    ],
)
def test_head_range_rejects_a_failed_calibration(change):
    with pytest.raises(ValidationError):
        contracts.HeadRange.model_validate({**HEAD_RANGE, **change})


def test_settings_lang_speak_picks_and_learning_are_optional():
    msg = parse_message({"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 800})
    assert msg.lang is None
    assert msg.speak_picks is None
    assert msg.learning is None


def test_typescript_contract_has_every_type():
    ts = (ROOT / "web" / "src" / "contracts.ts").read_text(encoding="utf-8")
    ts_types = set(re.findall(r"^\s*type: '([A-Z_]+)'$", ts, flags=re.MULTILINE))
    assert ts_types == set(EXAMPLES)
