"""Session state machine (PRD A3.2). Owns where the person is in the menu and, through its
Pointer, the only copy of the highlight. The board just draws what the session sends.

Flow: SCANNING --clench on leaf--> CONFIRMING --clench--> SPEAKING --AUDIO_DONE/timeout--> home.
Nothing is spoken or sent without the confirming clench (PRD D5).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum

from core.clock import Scheduler, TimerHandle
from core.contracts import (
    AudioDone,
    Clench,
    Confirm,
    DoubleBlink,
    Lang,
    LongClench,
    Message,
    Point,
    PointingMode,
    Screen,
    Settings,
    Speak,
    Tile,
)
from core.db import Db
from core.menu import Menu, MenuNode
from core.pointer import DEFAULT_SCAN_MS, make_pointer
from core.profile import Profile

log = logging.getLogger("clench.session")

CLENCH_DEBOUNCE_S = 0.3  # a CLENCH within 300 ms of the last accepted one is ignored
SPEAK_TIMEOUT_S = 10.0  # back to home if the board never sends AUDIO_DONE

Emit = Callable[[Message], None]


class SessionState(str, Enum):
    SCANNING = "SCANNING"
    CONFIRMING = "CONFIRMING"
    SPEAKING = "SPEAKING"
    # TODO(chunk: help alert): HELP_COUNTDOWN, entered on LONG_CLENCH, 5 s cancel window (PRD D3).
    # TODO(chunk: rest pause): PAUSED, entered on eyes closed / no input, left on CLENCH (PRD D13).
    # TODO(chunk: calibration): CALIBRATING and IDLE (PRD A3.2).


class Session:
    def __init__(
        self,
        menu: Menu,
        emit: Emit,
        scheduler: Scheduler,
        *,
        profile: Profile,
        db: Db | None = None,
        lang: Lang | None = None,
        scan_ms: int = DEFAULT_SCAN_MS,
        pointing_mode: PointingMode = "auto",
    ) -> None:
        self._menu = menu
        self._emit = emit
        self._scheduler = scheduler
        self.profile = profile
        self._db = db  # None = nothing is recorded (some tests)
        self.lang: Lang = lang or profile.lang
        self.scan_ms = scan_ms
        self.pointing_mode: PointingMode = pointing_mode
        self.state = SessionState.SCANNING
        self._path: list[MenuNode] = [menu.root]
        self._pending: MenuNode | None = None  # leaf being confirmed or spoken
        self._last_clench: float | None = None
        self._speak_timer: TimerHandle | None = None
        self.pointer = make_pointer(pointing_mode, scheduler, self._on_highlight, scan_ms)

    # --- public ---------------------------------------------------------------

    @property
    def level(self) -> MenuNode:
        return self._path[-1]

    @property
    def highlight(self) -> int:
        return self.pointer.highlight

    def start(self) -> None:
        """Show home and start scanning."""
        self._path = [self._menu.root]
        self._enter_level()

    def stop(self) -> None:
        self.pointer.stop()
        self._cancel_speak_timer()

    def current_view(self) -> Message | None:
        """What a newly connected board should show (reply to READY)."""
        if self.state is SessionState.SCANNING:
            return self._screen()
        if self.state is SessionState.CONFIRMING:
            return self._confirm_msg()
        return None  # SPEAKING: never re-send SPEAK; the next SCREEN follows when speech ends

    def handle(self, msg: Message) -> None:
        match msg:
            case Clench():
                self._on_clench()
            case DoubleBlink():
                self._on_double_blink()
            case LongClench():
                # TODO(chunk: help alert): start HELP_COUNTDOWN.
                log.info("LONG_CLENCH (%.1f s) ignored: help alert comes in a later chunk", msg.duration)
            case AudioDone():
                if self.state is SessionState.SPEAKING:
                    self._finish_speaking("audio done")
            case Settings():
                self._apply_settings(msg)
            case Point():
                if self.state is SessionState.SCANNING:
                    self.pointer.on_point(msg)
            case _:
                log.debug("ignored %s in %s", msg.type, self.state.value)

    # --- gestures -------------------------------------------------------------

    def _on_clench(self) -> None:
        now = self._scheduler.now()
        if self._last_clench is not None and now - self._last_clench < CLENCH_DEBOUNCE_S:
            log.info("CLENCH ignored: within %d ms of the previous one", CLENCH_DEBOUNCE_S * 1000)
            return
        self._last_clench = now
        if self.state is SessionState.SCANNING:
            self._pick()
        elif self.state is SessionState.CONFIRMING:
            self._confirm_pending()
        else:
            log.info("CLENCH ignored while %s", self.state.value)

    def _on_double_blink(self) -> None:
        if self.state is SessionState.SCANNING:
            if len(self._path) > 1:
                self._path.pop()
                self._enter_level()
            else:
                log.info("DOUBLE_BLINK at home: nothing to go back to")
        elif self.state is SessionState.CONFIRMING:
            node = self._pending
            assert node is not None
            log.info("cancelled: %r", node.phrase(self.lang))
            self._record(node, rejected=True, text=node.phrase(self.lang))
            self._pending = None
            self._enter_level()  # back to the level the leaf was on
        else:
            log.info("DOUBLE_BLINK ignored while %s", self.state.value)

    # --- transitions ----------------------------------------------------------

    def _pick(self) -> None:
        children = self.level.children or []
        index = self.pointer.highlight
        if not 0 <= index < len(children):
            log.warning("CLENCH with highlight %d outside %d tiles", index, len(children))
            return
        node = children[index]
        self._record(node)
        if node.is_leaf:
            self._pending = node
            self.state = SessionState.CONFIRMING
            self.pointer.stop()
            self._emit(self._confirm_msg())
        else:
            self._path.append(node)
            self._enter_level()

    def _confirm_pending(self) -> None:
        node = self._pending
        assert node is not None and node.action is not None
        self.state = SessionState.SPEAKING
        text = node.phrase(self.lang)
        self._emit(Speak(text=text, lang=self.lang))
        self._log_action(node, text)
        self._record(node, confirmed=True, text=text)
        self._use_phrase(text)
        self._cancel_speak_timer()
        self._speak_timer = self._scheduler.call_later(
            SPEAK_TIMEOUT_S, lambda: self._finish_speaking("timeout")
        )

    def _finish_speaking(self, reason: str) -> None:
        log.info("speaking done (%s), back to home", reason)
        self._cancel_speak_timer()
        self._pending = None
        self._path = [self._menu.root]
        self._enter_level()

    def _enter_level(self) -> None:
        self.state = SessionState.SCANNING
        self.pointer.on_tiles_changed(len(self.level.children or []))
        self.pointer.start()
        self._emit(self._screen())

    # --- settings and pointer -------------------------------------------------

    def _apply_settings(self, s: Settings) -> None:
        if s.pointing_mode != self.pointing_mode:
            self.pointing_mode = s.pointing_mode
            if s.pointing_mode not in ("auto", "scan"):
                log.info("pointing mode %s is not built yet; scanning instead", s.pointing_mode)
        self.scan_ms = s.scan_ms
        self.pointer.apply_settings(s)
        if s.lang is not None and s.lang != self.lang:
            self.lang = s.lang
            view = self.current_view()
            if view is not None:
                self._emit(view)

    def _on_highlight(self, index: int) -> None:
        if self.state is SessionState.SCANNING:
            self._emit(self._screen())

    # --- messages -------------------------------------------------------------

    def _screen(self) -> Screen:
        prefix = [n.id for n in self._path[1:]]
        tiles = [
            Tile(id=".".join([*prefix, c.id]), label=c.label(self.lang)) for c in self.level.children or []
        ]
        return Screen(
            screen="menu",
            tiles=tiles,
            highlight=self.pointer.highlight if tiles else None,
            lang=self.lang,
            path=[n.label(self.lang) for n in self._path[1:]],
        )

    def _confirm_msg(self) -> Confirm:
        node = self._pending
        assert node is not None and node.action is not None
        return Confirm(text=node.phrase(self.lang), action=node.action)

    def _log_action(self, node: MenuNode, text: str) -> None:
        # TODO(chunk 4): run the real action from the action registry (SMS, call, room control).
        if node.action == "speak":
            log.info("speak: %r", text)
            return
        contact = self._menu.contacts.get(node.contact) if node.contact else None
        target = f" to {contact.label_en} ({contact.relation})" if contact else ""
        log.info("would %s%s: %r (spoken only in this chunk)", node.action, target, text)

    # --- storage --------------------------------------------------------------

    def _record(self, node: MenuNode, *, confirmed: bool = False, rejected: bool = False, text: str | None = None) -> None:
        """Log a pick, confirm or cancel of `node`, a child of the current level. Never raises."""
        if self._db is None:
            return
        ids = [n.id for n in self._path[1:]] + [node.id]
        labels = [n.label(self.lang) for n in self._path[1:]] + [node.label(self.lang)]
        try:
            self._db.log_event(
                node_id=".".join(ids),
                path=labels,
                action=node.action,
                lang=self.lang,
                confirmed=confirmed,
                rejected=rejected,
                text=text,
                contact=node.contact,
            )
        except Exception:
            log.exception("could not record event for %s", ".".join(ids))

    def _use_phrase(self, text: str) -> None:
        if self._db is None:
            return
        try:
            self._db.use_phrase(text, self.lang)
        except Exception:
            log.exception("could not record phrase %r", text)

    def _cancel_speak_timer(self) -> None:
        if self._speak_timer is not None:
            self._speak_timer.cancel()
            self._speak_timer = None
