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
        {"type": "LONG_CLENCH", "t": 1727300009.9, "duration": 1.6},
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
        {"type": "SIGNAL", "t": 1727300010.05, "ch": [12.5, -3.1, 40.2, 8.8]},
    ),
    "POINT": (
        contracts.Point,
        {"type": "POINT", "source": "webcam", "tile": 3, "t": 1727300011.2},
    ),
    "FACE_OK": (contracts.FaceOk, {"type": "FACE_OK", "ok": False}),
    "READY": (contracts.Ready, {"type": "READY"}),
    "AUDIO_DONE": (contracts.AudioDone, {"type": "AUDIO_DONE"}),
    "SETTINGS": (
        contracts.Settings,
        {"type": "SETTINGS", "pointing_mode": "auto", "scan_ms": 1000, "lang": "es"},
    ),
    "SCREEN": (
        contracts.Screen,
        {
            "type": "SCREEN",
            "screen": "menu",
            "tiles": [
                {"id": "suggested", "label": "Tengo hambre"},
                {"id": "need", "label": "Necesito"},
                {"id": "people", "label": "Personas"},
            ],
            "highlight": 2,
            "lang": "es",
            "path": [],
        },
    ),
    "CONFIRM": (
        contracts.Confirm,
        {
            "type": "CONFIRM",
            "text": "Mija, estoy bien, llámame a las seis.",
            "action": "send_text",
        },
    ),
    "SPEAK": (
        contracts.Speak,
        {"type": "SPEAK", "text": "Tengo hambre. ¿Qué hay de almuerzo?", "lang": "es"},
    ),
    "PLAY_AUDIO": (contracts.PlayAudio, {"type": "PLAY_AUDIO", "url": "/audio/abc123.mp3"}),
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
        {"type": "POINT", "source": "webcam", "tile": -1, "t": 1.0},
        {"type": "CONFIRM", "text": "hi", "action": "delete_everything"},
        {"type": "SPEAK", "text": "hi", "lang": "fr"},
        {"type": "READY", "board": 1},
        {"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 1000, "lang": "de"},
        {
            "type": "SCREEN",
            "screen": "menu",
            "tiles": [{"id": str(i), "label": str(i)} for i in range(7)],
            "highlight": 0,
            "lang": "en",
            "path": [],
        },
    ],
)
def test_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        parse_message(bad)


def test_settings_lang_is_optional():
    msg = parse_message({"type": "SETTINGS", "pointing_mode": "scan", "scan_ms": 800})
    assert msg.lang is None


def test_typescript_contract_has_every_type():
    ts = (ROOT / "web" / "src" / "contracts.ts").read_text(encoding="utf-8")
    ts_types = set(re.findall(r"^\s*type: '([A-Z_]+)'$", ts, flags=re.MULTILINE))
    assert ts_types == set(EXAMPLES)
