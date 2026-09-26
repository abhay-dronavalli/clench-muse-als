"""Session state machine (PRD A3.2). Owns where the person is in the menu and, through its
Pointer, the only copy of the highlight. The board just draws what the session sends.

Flow: SCANNING --clench on leaf--> CONFIRMING --clench--> SPEAKING --AUDIO_DONE/timeout--> home.
Nothing is spoken or sent without the confirming clench (PRD D5). On that clench the sentence is
spoken on the board and, at the same time, the leaf's action (message, call, room) runs in the
background through the action registry; its outcome comes back as ACTION_RESULT.

Help alert (PRD D3): LONG_CLENCH while SCANNING or CONFIRMING --> HELP_COUNTDOWN, 5 s, one SCREEN
per second. DOUBLE_BLINK cancels back to where the person was. At 0 the help contact gets a call
and a message (the countdown is the confirmation), the board says "Calling Maria", then home.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

from core.actions import ActionContext, ActionRegistry, build_registry
from core.clock import Scheduler, TimerHandle
from core.contracts import (
    ActionName,
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
    Tile,
)
from core.contracts import ActionResult as ActionResultMsg
from core.db import Db
from core.menu import Menu, MenuNode
from core.pointer import DEFAULT_SCAN_MS, make_pointer
from core.profile import Profile

log = logging.getLogger("clench.session")

CLENCH_DEBOUNCE_S = 0.3  # a CLENCH within 300 ms of the last accepted one is ignored
SPEAK_TIMEOUT_S = 10.0  # back to home if the board never sends AUDIO_DONE
HELP_COUNTDOWN_S = 5  # PRD D3: 5 second cancel window

HELP_LABEL: dict[Lang, str] = {"en": "Help", "es": "Ayuda"}
HELP_MESSAGE: dict[Lang, str] = {"en": "{name} needs help now", "es": "{name} necesita ayuda ahora"}
HELP_SPEECH: dict[Lang, str] = {"en": "Calling {contact}", "es": "Llamando a {contact}"}

Emit = Callable[[Message], None]
Spawn = Callable[[Coroutine[Any, Any, None]], None]


class SessionState(str, Enum):
    SCANNING = "SCANNING"
    CONFIRMING = "CONFIRMING"
    SPEAKING = "SPEAKING"
    HELP_COUNTDOWN = "HELP_COUNTDOWN"
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
        actions: ActionRegistry | None = None,
        spawn: Spawn | None = None,
        db: Db | None = None,
        lang: Lang | None = None,
        scan_ms: int = DEFAULT_SCAN_MS,
        pointing_mode: PointingMode = "auto",
    ) -> None:
        self._menu = menu
        self._emit = emit
        self._scheduler = scheduler
        self.profile = profile
        # Default: dry-run actions with no keys, so nothing can leave the laptop by accident.
        self._actions = actions or build_registry(emit, {}, dry_run=True)
        self._spawn = spawn or self._spawn_task  # tests pass a runner that finishes at once
        self._tasks: set[asyncio.Task[None]] = set()
        self._db = db  # None = nothing is recorded (some tests)
        self.lang: Lang = lang or profile.lang
        self.scan_ms = scan_ms
        self.pointing_mode: PointingMode = pointing_mode
        self.state = SessionState.SCANNING
        self._path: list[MenuNode] = [menu.root]
        self._pending: MenuNode | None = None  # leaf being confirmed or spoken
        self._last_clench: float | None = None
        self._speak_timer: TimerHandle | None = None
        self._help_timer: TimerHandle | None = None
        self._help_left = 0  # seconds left in the help countdown
        self._help_from = SessionState.SCANNING  # where a cancelled countdown goes back to
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
        self._cancel_help_timer()
        for task in list(self._tasks):
            task.cancel()

    def current_view(self) -> Message | None:
        """What a newly connected board should show (reply to READY)."""
        if self.state is SessionState.SCANNING:
            return self._screen()
        if self.state is SessionState.CONFIRMING:
            return self._confirm_msg()
        if self.state is SessionState.HELP_COUNTDOWN:
            return self._help_screen()
        return None  # SPEAKING: never re-send SPEAK; the next SCREEN follows when speech ends

    def handle(self, msg: Message) -> None:
        match msg:
            case Clench():
                self._on_clench()
            case DoubleBlink():
                self._on_double_blink()
            case LongClench():
                if self.state in (SessionState.SCANNING, SessionState.CONFIRMING):
                    self._start_help()
                else:
                    log.info("LONG_CLENCH (%.1f s) ignored while %s", msg.duration, self.state.value)
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
        elif self.state is SessionState.HELP_COUNTDOWN:
            self._cancel_help()
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
        text = node.phrase(self.lang)
        ctx = self._context(text, node.contact)
        self._record(node, confirmed=True, text=text)
        self._use_phrase(text)
        self._speak(ctx)  # always said aloud in the room
        if node.action != "speak":
            self._run_action(node.action, ctx)  # and sent, at the same time

    def _speak(self, ctx: ActionContext) -> None:
        """Say `ctx.text` on the board; back to home on AUDIO_DONE or after SPEAK_TIMEOUT_S."""
        self.state = SessionState.SPEAKING
        self._run_action("speak", ctx, report=False)
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

    # --- help alert -----------------------------------------------------------

    def _start_help(self) -> None:
        self._help_from = self.state
        self.state = SessionState.HELP_COUNTDOWN
        self.pointer.stop()
        self._help_left = HELP_COUNTDOWN_S
        log.warning("LONG_CLENCH: help alert in %d s unless cancelled with a double blink", HELP_COUNTDOWN_S)
        self._emit(self._help_screen())
        self._help_timer = self._scheduler.call_later(1.0, self._help_tick)

    def _help_tick(self) -> None:
        self._help_timer = None
        self._help_left -= 1
        if self._help_left > 0:
            self._emit(self._help_screen())
            self._help_timer = self._scheduler.call_later(1.0, self._help_tick)
        else:
            self._fire_help()

    def _cancel_help(self) -> None:
        self._cancel_help_timer()
        log.info("help alert cancelled with %d s left", self._help_left)
        self._log_event(node_id="help", path=[HELP_LABEL[self.lang]], action="help_alert", rejected=True)
        if self._help_from is SessionState.CONFIRMING:
            self.state = SessionState.CONFIRMING
            self._emit(self._confirm_msg())
        else:
            # Same level, same tile: the highlight carries on from where it stopped.
            self.state = SessionState.SCANNING
            self.pointer.start()
            self._emit(self._screen())

    def _fire_help(self) -> None:
        contact = self._menu.contacts[self.profile.help_contact]
        text = HELP_MESSAGE[self.lang].format(name=self.profile.name)
        log.warning("HELP ALERT: calling and messaging %s: %r", contact.id, text)
        self._log_event(
            node_id="help",
            path=[HELP_LABEL[self.lang]],
            action="help_alert",
            confirmed=True,
            text=text,
            contact=contact.id,
        )
        self._pending = None
        ctx = self._context(text, contact.id, add_sender=False)  # the text already names the patient
        self._run_action("place_call", ctx)
        self._run_action("send_message", ctx)
        self._speak(self._context(HELP_SPEECH[self.lang].format(contact=contact.label(self.lang)), None))

    def _help_screen(self) -> Screen:
        return Screen(
            screen="help_countdown", tiles=[], highlight=None, lang=self.lang, path=[], countdown=self._help_left
        )

    def _cancel_help_timer(self) -> None:
        if self._help_timer is not None:
            self._help_timer.cancel()
            self._help_timer = None

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

    # --- actions --------------------------------------------------------------

    def _context(self, text: str, contact_id: str | None, *, add_sender: bool = True) -> ActionContext:
        contact = self._menu.contacts.get(contact_id) if contact_id else None
        return ActionContext(
            text=text, lang=self.lang, contact=contact, patient_name=self.profile.name, add_sender=add_sender
        )

    def _run_action(self, name: ActionName, ctx: ActionContext, *, report: bool = True) -> None:
        """Run an action in the background. With `report`, send ACTION_RESULT when it finishes."""
        contact_label = ctx.contact.label(ctx.lang) if ctx.contact else None

        async def run() -> None:
            result = await self._actions.run(name, ctx)
            target = f" to {ctx.contact.id}" if ctx.contact else ""
            if result.ok:
                log.info("%s%s: %s (%r)", name, target, result.detail, ctx.text)
            else:
                log.warning("%s%s FAILED: %s (%r)", name, target, result.detail, ctx.text)
            if report:
                self._emit(ActionResultMsg(action=name, ok=result.ok, detail=result.detail, contact=contact_label))

        self._spawn(run())

    def _spawn_task(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)  # keep a reference so the task is not garbage-collected mid-send
        task.add_done_callback(self._tasks.discard)

    # --- storage --------------------------------------------------------------

    def _record(self, node: MenuNode, *, confirmed: bool = False, rejected: bool = False, text: str | None = None) -> None:
        """Log a pick, confirm or cancel of `node`, a child of the current level."""
        ids = [n.id for n in self._path[1:]] + [node.id]
        labels = [n.label(self.lang) for n in self._path[1:]] + [node.label(self.lang)]
        self._log_event(
            node_id=".".join(ids),
            path=labels,
            action=node.action,
            confirmed=confirmed,
            rejected=rejected,
            text=text,
            contact=node.contact,
        )

    def _log_event(
        self,
        *,
        node_id: str,
        path: list[str],
        action: str | None,
        confirmed: bool = False,
        rejected: bool = False,
        text: str | None = None,
        contact: str | None = None,
    ) -> None:
        """Append an events row. Never raises: a storage problem must not stop the person talking."""
        if self._db is None:
            return
        try:
            self._db.log_event(
                node_id=node_id,
                path=path,
                action=action,
                lang=self.lang,
                confirmed=confirmed,
                rejected=rejected,
                text=text,
                contact=contact,
            )
        except Exception:
            log.exception("could not record event for %s", node_id)

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
