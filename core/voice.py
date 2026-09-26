"""Voice service (PRD A3.2 Voice): everything the board says goes through Voice.speak().

Every utterance gets an id, sent to the board in SPEAK and echoed back in AUDIO_DONE, so the session
only reacts to the phrase it is waiting for.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from core.contracts import Lang, Message, Speak, UtteranceKind

log = logging.getLogger("clench.voice")

Emit = Callable[[Message], None]


def new_utterance_id() -> str:
    return uuid.uuid4().hex[:12]


class Voice:
    def __init__(self, emit: Emit) -> None:
        self._emit = emit

    def speak(self, text: str, lang: Lang, kind: UtteranceKind) -> str:
        """Say `text` on the board and return the utterance id."""
        uid = new_utterance_id()
        self._emit(Speak(id=uid, kind=kind, text=text, lang=lang))
        return uid
