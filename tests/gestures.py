"""Gestures shared by the session tests."""

from core.contracts import Clench, DoubleBlink
from core.session import CLENCH_DEBOUNCE_S


def go_back(session) -> None:
    """Up one menu level, or cancel the "Say this?" screen.

    A DOUBLE_BLINK only opens the go-back prompt now (docs/decisions.md 17); a CLENCH inside it goes
    back. The clock steps past the clench debounce first, so a pick just before does not swallow it.
    """
    session.handle(DoubleBlink(t=0.0))
    session._scheduler.advance(CLENCH_DEBOUNCE_S + 0.05)  # 0.3 s exactly can round to inside it
    session.handle(Clench(t=0.0, strength=1.0))
