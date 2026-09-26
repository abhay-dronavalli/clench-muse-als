"""Session state machine (PRD A3.2). Owns where the person is in the menu and, through its
Pointer, the only copy of the highlight. The board just draws what the session sends.

Flow: SCANNING --clench on a leaf--> (suggestions screen) --clench on a sentence--> CONFIRMING
--clench--> SPEAKING --AUDIO_DONE/timeout--> home.
Nothing is spoken or sent without the confirming clench (PRD D5). On that clench the sentence is
spoken on the board and, at the same time, the leaf's action (message, call, room) runs in the
background through the action registry; its outcome comes back as ACTION_RESULT. Every utterance
has an id; SPEAKING only ends on the AUDIO_DONE with the id of the utterance it is waiting for.

Where the person is: a stack of frames. A frame is a menu level, a batch of new options from
"Other...", or the suggestions screen for a picked leaf. DOUBLE_BLINK pops one frame.

AI (PRD section 5 step 6, D6, decisions.md #5 and #7):
  - Picking a leaf opens the suggestions screen: up to 3 AI sentences, then the leaf's fixed phrase,
    then "Other...". A clench on a sentence opens CONFIRMING with exactly that sentence. With no AI
    (no key, offline, slow, error) the leaf goes straight to CONFIRMING with its fixed phrase.
  - Every level ends with "Other..." ("Otro..."): new options for the same path from the AI (or the
    level's fixed `more` list from menu.yaml when the AI has nothing). After 2 "Other..." picks in a
    row the tile reads "Spell it" ("Deletrear"), which for now only says spelling is coming soon.
  - The home "Suggested" branch shows the AI's sentences for right now first, then its fixed phrases.
  - The AI only writes labels and text. The action and contact always come from the path: an AI
    option inherits them from its level (MenuNode.inherited), an AI sentence from its leaf.
  - Prefetch: when a frame opens, the session asks for everything the person could pick next
    (sentences for each leaf, the "Other..." batch, the Suggested sentences) in the background.
    Scanning never waits. If a pick needs a result that has not arrived, the session shows the
    loading state (LOADING, scanning paused) for at most 4 s, then falls back.

Speak picks (decisions.md #4): with speak_picks on, every CLENCH pick while scanning says the picked
tile's label (an "echo") before the next view shows; "Other..." is said as "Other" / "Otro". Full
sentences (suggestion tiles) are never echoed: the confirm step speaks them.

Help alert (PRD D3): LONG_CLENCH while SCANNING, LOADING or CONFIRMING --> HELP_COUNTDOWN, 5 s, one
SCREEN per second, and the board says "Calling for help. Double blink to cancel.". DOUBLE_BLINK
cancels back to where the person was. At 0 the help contact gets a call and a message (the countdown
is the confirmation), the board says "Calling Maria" and the session goes home at once. Echoes and
system lines never change the session state; only a confirmed phrase makes it wait (SPEAKING).
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Literal

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
    TileKind,
)
from core.contracts import ActionResult as ActionResultMsg
from core.db import Db
from core.menu import MAX_ITEMS, Menu, MenuNode
from core.pointer import DEFAULT_SCAN_MS, make_pointer
from core.profile import Profile
from core.suggest.provider import MAX_SENTENCES, Option, drop_known
from core.suggest.service import Pending, Suggester
from core.voice import Voice

log = logging.getLogger("clench.session")

CLENCH_DEBOUNCE_S = 0.3  # a CLENCH within 300 ms of the last accepted one is ignored
SPEAK_TIMEOUT_S = 10.0  # back to home if the board never sends AUDIO_DONE
HELP_COUNTDOWN_S = 5  # PRD D3: 5 second cancel window
LOADING_MAX_S = 4.0  # longest the board waits for AI options after a pick
SPELL_AFTER = 2  # "Other..." picks in a row before the tile becomes "Spell it"

HELP_LABEL: dict[Lang, str] = {"en": "Help", "es": "Ayuda"}
HELP_MESSAGE: dict[Lang, str] = {"en": "{name} needs help now", "es": "{name} necesita ayuda ahora"}
HELP_SPEECH: dict[Lang, str] = {"en": "Calling {contact}", "es": "Llamando a {contact}"}
HELP_START: dict[Lang, str] = {
    "en": "Calling for help. Double blink to cancel.",
    "es": "Pidiendo ayuda. Parpadea dos veces para cancelar.",
}
OTHER_LABEL: dict[Lang, str] = {"en": "Other...", "es": "Otro..."}
OTHER_WORD: dict[Lang, str] = {"en": "Other", "es": "Otro"}  # the echo and the breadcrumb
SPELL_LABEL: dict[Lang, str] = {"en": "Spell it", "es": "Deletrear"}
SPELL_SOON: dict[Lang, str] = {"en": "Spelling is coming soon.", "es": "Deletrear llegará pronto."}

Emit = Callable[[Message], None]
Spawn = Callable[[Coroutine[Any, Any, None]], None]
LANGS: tuple[Lang, ...] = ("en", "es")


def voice_lines(menu: Menu, profile: Profile) -> list[tuple[str, Lang]]:
    """Everything the session can say from the fixed menu, in both languages, for the voice prewarm:
    tile labels first (echoes have the shortest wait), then system lines, then leaf phrases."""
    labels: list[tuple[str, Lang]] = [(OTHER_WORD[lang], lang) for lang in LANGS]
    phrases: list[tuple[str, Lang]] = []

    def walk(node: MenuNode) -> None:
        for child in (node.children or []) + (node.more or []):
            labels.extend((child.label(lang), lang) for lang in LANGS)
            if child.is_leaf:
                phrases.extend((child.phrase(lang), lang) for lang in LANGS)
            else:
                walk(child)

    walk(menu.root)
    contact = menu.contacts[profile.help_contact]
    system = [(HELP_START[lang], lang) for lang in LANGS]
    system += [(HELP_SPEECH[lang].format(contact=contact.label(lang)), lang) for lang in LANGS]
    system += [(SPELL_SOON[lang], lang) for lang in LANGS]
    return list(dict.fromkeys(labels + system + phrases))


class SessionState(str, Enum):
    SCANNING = "SCANNING"
    LOADING = "LOADING"  # waiting (at most 4 s) for AI options after a pick; scanning paused
    CONFIRMING = "CONFIRMING"
    SPEAKING = "SPEAKING"
    HELP_COUNTDOWN = "HELP_COUNTDOWN"
    # TODO(chunk: rest pause): PAUSED, entered on eyes closed / no input, left on CLENCH (PRD D13).
    # TODO(chunk: calibration): CALIBRATING and IDLE (PRD A3.2).


@dataclass(frozen=True)
class Item:
    """One tile of a frame. The "Other..." tile is not an Item; it is added when the screen is drawn.

    A menu item keeps its MenuNode, so it follows the language. An AI item holds text in the one
    language it was written in. Action and contact are set when the item is made, from the path.
    """

    kind: TileKind  # branch | leaf | suggestion
    id: str  # tile id: the dotted menu path, "ai:..." for AI-made items
    event_id: str  # node_id in the events table; "ai:<leaf path>" for an AI sentence
    node: MenuNode | None = None
    text: str | None = None  # AI option: its sentence; AI suggestion: the sentence
    ai_label: str | None = None  # AI option: its tile label
    action: ActionName | None = None
    contact: str | None = None

    def phrase(self, lang: Lang) -> str:
        """The sentence confirmed for this item."""
        if self.node is not None and self.node.is_leaf:
            return self.node.phrase(lang)
        assert self.text is not None
        return self.text

    def label(self, lang: Lang) -> str:
        if self.kind == "suggestion":
            return self.phrase(lang)
        if self.node is not None:
            return self.node.label(lang)
        assert self.ai_label is not None
        return self.ai_label


@dataclass
class Frame:
    """One screen of the stack: a menu level, an "Other..." batch or a suggestions screen."""

    kind: Literal["menu", "suggestions"]
    level: MenuNode  # the menu level this frame belongs to (inherited action / contact, `more`)
    prefix: str  # dotted id prefix for this frame's own tiles ("" at home)
    items: list[Item]
    crumb: Item | None = None  # the pick that opened this frame; None at home and for "Other..."
    via_other: bool = False  # opened by "Other...": breadcrumb "Other", left out of the AI path
    others: int = 0  # "Other..." picks in a row that led here; SPELL_AFTER = "Spell it"
    shown: tuple[str, ...] = ()  # labels / sentences already on screen in this chain (never repeated)
    leaf: Item | None = None  # suggestions screen: the leaf the sentences are for
    ai: bool = False  # holds AI text (one language): dropped when the language changes


def _join(prefix: str, part: str) -> str:
    return f"{prefix}.{part}" if prefix else part


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


class Session:
    def __init__(
        self,
        menu: Menu,
        emit: Emit,
        scheduler: Scheduler,
        *,
        profile: Profile,
        actions: ActionRegistry | None = None,
        voice: Voice | None = None,
        suggester: Suggester | None = None,
        spawn: Spawn | None = None,
        db: Db | None = None,
        lang: Lang | None = None,
        scan_ms: int = DEFAULT_SCAN_MS,
        pointing_mode: PointingMode = "auto",
        speak_picks: bool | None = None,
    ) -> None:
        self._menu = menu
        self._emit = emit
        self._scheduler = scheduler
        self.profile = profile
        self._voice = voice or Voice(emit)  # default: browser speech only
        # Default: dry-run actions with no keys, so nothing can leave the laptop by accident.
        self._actions = actions or build_registry(self._voice, {}, dry_run=True)
        # Default: no AI, fixed phrases only.
        self.suggester = suggester or Suggester(None, patient_name=profile.name, off_reason="no provider")
        self._spawn = spawn or self._spawn_task  # tests pass a runner that finishes at once
        self._tasks: set[asyncio.Task[None]] = set()
        self._db = db  # None = nothing is recorded (some tests)
        self.lang: Lang = lang or profile.lang
        self.scan_ms = scan_ms
        self.pointing_mode: PointingMode = pointing_mode
        self.speak_picks = profile.speak_picks if speak_picks is None else speak_picks
        self.state = SessionState.SCANNING
        self._stack: list[Frame] = [self._home()]
        self._pending: Item | None = None  # item being confirmed or spoken
        self._last_clench: float | None = None
        self._speak_timer: TimerHandle | None = None
        self._speaking_id: str | None = None  # the utterance SPEAKING waits for
        self._help_timer: TimerHandle | None = None
        self._help_left = 0  # seconds left in the help countdown
        self._help_from = SessionState.SCANNING  # where a cancelled countdown goes back to
        self._waiting: object | None = None  # token of the AI result LOADING waits for
        self._loading_timer: TimerHandle | None = None
        self.pointer = make_pointer(pointing_mode, scheduler, self._on_highlight, scan_ms)

    # --- public ---------------------------------------------------------------

    @property
    def frame(self) -> Frame:
        return self._stack[-1]

    @property
    def highlight(self) -> int:
        return self.pointer.highlight

    @property
    def speaking_id(self) -> str | None:
        """Id of the utterance SPEAKING is waiting for; None when not speaking."""
        return self._speaking_id

    def tiles(self) -> list[Tile]:
        """The tiles of the current frame, "Other..." last."""
        return self._screen().tiles

    def start(self) -> None:
        """Show home and start scanning."""
        self._stack = [self._home()]
        self._enter_frame()

    def stop(self) -> None:
        self.pointer.stop()
        self._cancel_speak_timer()
        self._cancel_help_timer()
        self._cancel_wait()
        for task in list(self._tasks):
            task.cancel()

    def current_view(self) -> Message | None:
        """What a newly connected board should show (reply to READY)."""
        if self.state in (SessionState.SCANNING, SessionState.LOADING):
            return self._screen()
        if self.state is SessionState.CONFIRMING:
            return self._confirm_msg()
        if self.state is SessionState.HELP_COUNTDOWN:
            return self._help_screen()
        return None  # SPEAKING: never re-send SPEAK; the next SCREEN follows when speech ends

    def settings(self) -> Settings:
        """The current settings, as the Core announces them to every client."""
        return Settings(
            pointing_mode=self.pointing_mode, scan_ms=self.scan_ms, lang=self.lang, speak_picks=self.speak_picks
        )

    def handle(self, msg: Message) -> None:
        match msg:
            case Clench():
                self._on_clench()
            case DoubleBlink():
                self._on_double_blink()
            case LongClench():
                if self.state in (SessionState.SCANNING, SessionState.LOADING, SessionState.CONFIRMING):
                    self._start_help()
                else:
                    log.info("LONG_CLENCH (%.1f s) ignored while %s", msg.duration, self.state.value)
            case AudioDone():
                if self.state is SessionState.SPEAKING and msg.id == self._speaking_id:
                    self._finish_speaking("audio done")
                else:
                    log.debug("AUDIO_DONE %s ignored (waiting for %s)", msg.id, self._speaking_id)
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
            if len(self._stack) > 1:
                self._stack.pop()
                self._enter_frame()
            else:
                log.info("DOUBLE_BLINK at home: nothing to go back to")
        elif self.state is SessionState.LOADING:
            log.info("DOUBLE_BLINK: stopped waiting for AI options")
            self._cancel_wait()
            self._resume()
        elif self.state is SessionState.CONFIRMING:
            item = self._pending
            assert item is not None
            text = item.phrase(self.lang)
            log.info("cancelled: %r", text)
            self._record(item, rejected=True, text=text)
            self._pending = None
            self._enter_frame()  # back to the screen the sentence was picked on
        elif self.state is SessionState.HELP_COUNTDOWN:
            self._cancel_help()
        else:
            log.info("DOUBLE_BLINK ignored while %s", self.state.value)

    # --- picking --------------------------------------------------------------

    def _pick(self) -> None:
        frame = self.frame
        index = self.pointer.highlight
        if index == len(frame.items):
            self._pick_other(frame)
            return
        if not 0 <= index < len(frame.items):
            log.warning("CLENCH with highlight %d outside %d tiles", index, len(frame.items) + 1)
            return
        item = frame.items[index]
        self._record(item)
        if item.kind == "suggestion":
            self._confirm(item)  # no echo: the confirm step speaks the whole sentence
            return
        self._echo(item.label(self.lang))  # before the next view shows
        if item.kind == "branch":
            assert item.node is not None
            if item.node.ai_now:
                self._wait(self._now_request(), lambda sentences: self._open_suggested(item, sentences))
            else:
                self._push(self._menu_frame(item.node, item.id, item))
        elif self.suggester.available:
            self._wait(self._leaf_request(item), lambda sentences: self._open_suggestions(item, sentences))
        else:
            self._confirm(item)  # no AI: straight to the fixed phrase, as before

    def _pick_other(self, frame: Frame) -> None:
        crumbs = self._crumbs()
        if frame.others >= SPELL_AFTER:
            log.info("Spell it picked: not built yet")
            self._log_event(node_id=_join(frame.prefix, "spell"), path=crumbs + [SPELL_LABEL[self.lang]], action=None)
            self._voice.speak(SPELL_SOON[self.lang], self.lang, "system")
            return  # nothing opens; scanning goes on
        self._log_event(node_id=_join(frame.prefix, "other"), path=crumbs + [OTHER_WORD[self.lang]], action=None)
        self._echo(OTHER_WORD[self.lang])
        if self.suggester.available:
            self._wait(self._other_request(frame), lambda result: self._open_other(frame, result))
        else:
            self._open_other(frame, None)

    def _confirm(self, item: Item) -> None:
        self._pending = item
        self.state = SessionState.CONFIRMING
        self.pointer.stop()
        self._emit(self._confirm_msg())
        self._voice.warm(item.phrase(self.lang), self.lang)  # likely said next: make its audio now

    def _confirm_pending(self) -> None:
        item = self._pending
        assert item is not None and item.action is not None
        text = item.phrase(self.lang)
        ctx = self._context(text, item.contact)
        self._record(item, confirmed=True, text=text)
        self._use_phrase(text)
        self._speak_phrase(text)  # always said aloud in the room
        if item.action != "speak":
            self._run_action(item.action, ctx)  # and sent, at the same time

    def _speak_phrase(self, text: str) -> None:
        """Say the confirmed sentence; back to home on its AUDIO_DONE or after SPEAK_TIMEOUT_S."""
        self.state = SessionState.SPEAKING
        self._speaking_id = self._voice.speak(text, self.lang, "phrase")
        self._cancel_speak_timer()
        self._speak_timer = self._scheduler.call_later(
            SPEAK_TIMEOUT_S, lambda: self._finish_speaking("timeout")
        )

    def _finish_speaking(self, reason: str) -> None:
        log.info("speaking done (%s), back to home", reason)
        self._cancel_speak_timer()
        self._speaking_id = None
        self._pending = None
        self._stack = [self._home()]
        self._enter_frame()

    # --- frames ---------------------------------------------------------------

    def _home(self) -> Frame:
        return self._menu_frame(self._menu.root, "", None)

    def _menu_item(self, node: MenuNode, prefix: str) -> Item:
        tile_id = _join(prefix, node.id)
        kind: TileKind = "leaf" if node.is_leaf else "branch"
        return Item(kind=kind, id=tile_id, event_id=tile_id, node=node, action=node.action, contact=node.contact)

    def _menu_frame(self, node: MenuNode, prefix: str, crumb: Item | None) -> Frame:
        items = [self._menu_item(c, prefix) for c in node.children or []]
        return Frame(
            kind="menu",
            level=node,
            prefix=prefix,
            items=items,
            crumb=crumb,
            shown=tuple(i.label(self.lang) for i in items),
        )

    def _open_suggested(self, item: Item, sentences: list[str] | None) -> None:
        """Home "Suggested": the AI's sentences for right now, then the fixed phrases not already there."""
        node = item.node
        assert node is not None
        ai = [self._sentence(s, i, f"ai:{item.id}", "speak", None) for i, s in enumerate((sentences or [])[:MAX_SENTENCES])]
        said = [a.phrase(self.lang) for a in ai]
        fixed = [
            self._menu_item(c, item.id)
            for c in node.children or []
            if not c.is_leaf or drop_known([c.phrase(self.lang)], said)  # not already said by the AI
        ]
        items = ai + fixed[: MAX_ITEMS - len(ai)]
        frame = Frame(
            kind="menu",
            level=node,
            prefix=item.id,
            items=items,
            crumb=item,
            shown=tuple(i.label(self.lang) for i in items),
            ai=bool(ai),
        )
        self._push(frame)
        if ai:
            self._voice.warm(ai[0].phrase(self.lang), self.lang)

    def _open_suggestions(self, leaf: Item, sentences: list[str] | None) -> None:
        """The suggestions screen for `leaf`: AI sentences, its fixed phrase, "Other...". No
        sentences (AI off, slow, failed or empty): straight to the confirm screen with the fixed phrase."""
        if not sentences:
            self._confirm(leaf)
            return
        base = leaf.id.removeprefix("ai:")
        ai = [self._sentence(s, i, f"ai:{base}", leaf.action, leaf.contact) for i, s in enumerate(sentences[:MAX_SENTENCES])]
        fixed = Item(
            kind="suggestion",
            id=leaf.id,
            event_id=leaf.event_id,
            node=leaf.node,
            text=leaf.text,
            action=leaf.action,
            contact=leaf.contact,
        )
        items = ai + [fixed]
        frame = Frame(
            kind="suggestions",
            level=self.frame.level,
            prefix=leaf.id,
            items=items,
            crumb=leaf,
            shown=tuple(i.label(self.lang) for i in items),
            leaf=leaf,
            ai=True,
        )
        self._push(frame)
        # Only the top sentence is made in advance (ElevenLabs quota); others when confirmed.
        self._voice.warm(ai[0].phrase(self.lang), self.lang)

    def _open_other(self, frame: Frame, result: list[Any] | None) -> None:
        """The next "Other..." batch for `frame`, or "Spell it" when there is nothing new."""
        items: list[Item] = []
        if frame.kind == "suggestions":
            leaf = frame.leaf
            assert leaf is not None
            base = leaf.id.removeprefix("ai:")
            items = [self._sentence(s, i, f"ai:{base}", leaf.action, leaf.contact) for i, s in enumerate(result or [])]
        else:
            items = [self._ai_option(frame, o, i) for i, o in enumerate(result or [])]
            if len({i.id for i in items}) < len(items):  # two labels with the same slug
                items = [replace(it, id=f"{it.id}_{n + 1}", event_id=f"{it.id}_{n + 1}") for n, it in enumerate(items)]
            if not items:  # the AI had nothing: the level's fixed extras from menu.yaml
                items = [
                    self._menu_item(n, frame.prefix)
                    for n in frame.level.more or []
                    if drop_known([n.label(self.lang)], frame.shown)
                ]
        items = items[:MAX_ITEMS]
        if not items:
            log.info("Other...: nothing new here; the tile now offers Spell it")
            frame.others = SPELL_AFTER
            self._resume()  # same screen, the last tile now reads "Spell it"
            return
        self._push(
            Frame(
                kind=frame.kind,
                level=frame.level,
                prefix=frame.prefix,
                items=items,
                via_other=True,
                others=frame.others + 1,
                shown=frame.shown + tuple(i.label(self.lang) for i in items),
                leaf=frame.leaf,
                ai=frame.ai or any(i.node is None for i in items),
            )
        )

    def _sentence(self, text: str, i: int, event_id: str, action: ActionName | None, contact: str | None) -> Item:
        return Item(
            kind="suggestion", id=f"{event_id}.s{i + 1}", event_id=event_id, text=text, action=action, contact=contact
        )

    def _ai_option(self, frame: Frame, option: Option, i: int) -> Item:
        """An AI option on `frame`'s level. The AI gave the label and text; action and contact come
        from the level (never from the AI)."""
        action, contact = frame.level.inherited()
        tile_id = "ai:" + _join(frame.prefix, _slug(option.label) or f"option{i + 1}")
        return Item(kind="leaf", id=tile_id, event_id=tile_id, text=option.text, ai_label=option.label, action=action, contact=contact)

    def _push(self, frame: Frame) -> None:
        self._stack.append(frame)
        self._enter_frame()

    def _enter_frame(self) -> None:
        """Show the top frame from its first tile and prefetch what could be picked next."""
        self.state = SessionState.SCANNING
        self.pointer.on_tiles_changed(len(self.frame.items) + 1)
        self.pointer.start()
        self._emit(self._screen())
        self._prefetch()

    def _resume(self) -> None:
        """Back to scanning the same frame, the highlight where it was."""
        self.state = SessionState.SCANNING
        self.pointer.start()
        self._emit(self._screen())

    # --- AI requests ----------------------------------------------------------

    def _ai_path(self) -> tuple[str, ...]:
        """Breadcrumb labels the AI sees: every pick down to here, without the "Other" steps."""
        return tuple(f.crumb.label(self.lang) for f in self._stack[1:] if f.crumb is not None and not f.via_other)

    def _leaf_request(self, item: Item) -> Pending[list[str]]:
        return self.suggester.compose(self._ai_path() + (item.label(self.lang),), self.lang, fixed_phrase=item.phrase(self.lang))

    def _now_request(self) -> Pending[list[str]]:
        return self.suggester.compose((), self.lang)

    def _other_request(self, frame: Frame) -> Pending[Any]:
        if frame.kind == "suggestions":
            assert frame.leaf is not None
            return self.suggester.compose(
                self._ai_path(), self.lang, fixed_phrase=frame.leaf.phrase(self.lang), shown=frame.shown
            )
        return self.suggester.more_options(self._ai_path(), self.lang, shown=frame.shown)

    def _prefetch(self) -> None:
        """Ask in the background for everything the person could pick next, in one request for the
        whole level (level_bundle; nothing when it is all cached). Never waits."""
        if not self.suggester.available:
            return
        frame = self.frame
        if frame.kind == "suggestions":
            if frame.others < SPELL_AFTER:
                self._other_request(frame)  # more sentences: one request
            return
        self.suggester.level_bundle(
            self._ai_path(),
            self.lang,
            leaves=tuple((i.label(self.lang), i.phrase(self.lang)) for i in frame.items if i.kind == "leaf"),
            now=any(i.kind == "branch" and i.node is not None and i.node.ai_now for i in frame.items),
            other_shown=frame.shown if frame.others < SPELL_AFTER else None,
        )

    def _wait(self, pending: Pending[Any], then: Callable[[Any], None]) -> None:
        """Run `then(result)` now if the result is here; otherwise show the loading state (scanning
        paused) until it arrives, for at most LOADING_MAX_S, then `then(None)` (fixed phrases)."""
        if pending.done:
            then(pending.result)
            return
        token = object()
        self._waiting = token
        self.state = SessionState.LOADING
        self.pointer.stop()
        self._emit(self._screen())

        def finish(result: Any) -> None:
            if self._waiting is not token:
                return  # cancelled, timed out, or the person moved on
            self._cancel_wait()
            then(result)

        def give_up() -> None:
            if self._waiting is token:
                log.info("AI options not here after %.0f s: fixed phrases instead", LOADING_MAX_S)
                finish(None)

        pending.on_done(finish)
        self._loading_timer = self._scheduler.call_later(LOADING_MAX_S, give_up)

    def _cancel_wait(self) -> None:
        self._waiting = None
        if self._loading_timer is not None:
            self._loading_timer.cancel()
            self._loading_timer = None

    # --- help alert -----------------------------------------------------------

    def _start_help(self) -> None:
        if self.state is SessionState.LOADING:
            self._cancel_wait()  # the help alert wins; a cancel goes back to scanning this screen
            self.state = SessionState.SCANNING
        self._help_from = self.state
        self.state = SessionState.HELP_COUNTDOWN
        self.pointer.stop()
        self._help_left = HELP_COUNTDOWN_S
        log.warning("LONG_CLENCH: help alert in %d s unless cancelled with a double blink", HELP_COUNTDOWN_S)
        self._voice.speak(HELP_START[self.lang], self.lang, "system")
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
            self._resume()  # same screen, same tile: the highlight carries on from where it stopped

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
        self._voice.speak(HELP_SPEECH[self.lang].format(contact=contact.label(self.lang)), self.lang, "system")
        self._stack = [self._home()]
        self._enter_frame()  # straight home: a system line never holds the session

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
        if s.speak_picks is not None:
            self.speak_picks = s.speak_picks
        self.pointer.apply_settings(s)
        lang_changed = s.lang is not None and s.lang != self.lang
        if s.lang is not None:
            self.lang = s.lang
        self._emit(self.settings())  # every client sees the real values, whoever changed them
        if lang_changed:
            self._change_language()

    def _change_language(self) -> None:
        """Menu labels follow the language. AI text was written in the old one, so screens holding
        it are dropped: the person is taken back to the menu level below them."""
        first_ai = next((i for i, f in enumerate(self._stack) if f.ai), None)
        if first_ai is not None:
            self._stack = self._stack[:first_ai]
            log.info("language changed: AI options dropped, back to %s", self._crumbs() or "home")
            if self.state is SessionState.LOADING:
                self._cancel_wait()
            if self.state in (SessionState.SCANNING, SessionState.LOADING):
                self._enter_frame()
                return
            self.pointer.on_tiles_changed(len(self.frame.items) + 1)
        elif self.state is SessionState.LOADING:
            self._cancel_wait()
            self._resume()
            return
        # What the AI must not repeat, now in the new language (only menu items are left).
        for below, f in zip([None, *self._stack], self._stack):
            before = below.shown if f.via_other and below is not None else ()
            f.shown = before + tuple(i.label(self.lang) for i in f.items)
        view = self.current_view()
        if view is not None:
            self._emit(view)
        if self.state is SessionState.SCANNING:
            self._prefetch()

    def _on_highlight(self, index: int) -> None:
        if self.state is SessionState.SCANNING:
            self._emit(self._screen())

    # --- messages -------------------------------------------------------------

    def _crumbs(self) -> list[str]:
        """Breadcrumb labels from home down to the current frame."""
        return [
            OTHER_WORD[self.lang] if f.via_other else f.crumb.label(self.lang)
            for f in self._stack[1:]
            if f.via_other or f.crumb is not None
        ]

    def _other_tile(self, frame: Frame) -> Tile:
        if frame.others >= SPELL_AFTER:
            return Tile(id=_join(frame.prefix, "spell"), label=SPELL_LABEL[self.lang], kind="other")
        return Tile(id=_join(frame.prefix, "other"), label=OTHER_LABEL[self.lang], kind="other")

    def _screen(self) -> Screen:
        frame = self.frame
        tiles = [Tile(id=i.id, label=i.label(self.lang), kind=i.kind) for i in frame.items]
        tiles.append(self._other_tile(frame))
        return Screen(
            screen="suggestions" if frame.kind == "suggestions" else "menu",
            tiles=tiles,
            highlight=self.pointer.highlight,
            lang=self.lang,
            path=self._crumbs(),
            loading=self.state is SessionState.LOADING,
        )

    def _confirm_msg(self) -> Confirm:
        item = self._pending
        assert item is not None and item.action is not None
        return Confirm(text=item.phrase(self.lang), action=item.action)

    def _echo(self, text: str) -> None:
        if self.speak_picks:
            self._voice.speak(text, self.lang, "echo")

    # --- actions --------------------------------------------------------------

    def _context(self, text: str, contact_id: str | None, *, add_sender: bool = True) -> ActionContext:
        contact = self._menu.contacts.get(contact_id) if contact_id else None
        return ActionContext(
            text=text, lang=self.lang, contact=contact, patient_name=self.profile.name, add_sender=add_sender
        )

    def _run_action(self, name: ActionName, ctx: ActionContext) -> None:
        """Run an action in the background and send ACTION_RESULT when it finishes."""
        contact_label = ctx.contact.label(ctx.lang) if ctx.contact else None

        async def run() -> None:
            result = await self._actions.run(name, ctx)
            target = f" to {ctx.contact.id}" if ctx.contact else ""
            if result.ok:
                log.info("%s%s: %s (%r)", name, target, result.detail, ctx.text)
            else:
                log.warning("%s%s FAILED: %s (%r)", name, target, result.detail, ctx.text)
            self._emit(ActionResultMsg(action=name, ok=result.ok, detail=result.detail, contact=contact_label))

        self._spawn(run())

    def _spawn_task(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)  # keep a reference so the task is not garbage-collected mid-send
        task.add_done_callback(self._tasks.discard)

    # --- storage --------------------------------------------------------------

    def _record(self, item: Item, *, confirmed: bool = False, rejected: bool = False, text: str | None = None) -> None:
        """Log a pick, confirm or cancel of `item`, a tile of the current frame (or the one being confirmed)."""
        # A sentence's breadcrumb already ends with its leaf; the sentence itself goes in `text`.
        labels = self._crumbs() + ([] if item.kind == "suggestion" else [item.label(self.lang)])
        self._log_event(
            node_id=item.event_id,
            path=labels,
            action=item.action,
            confirmed=confirmed,
            rejected=rejected,
            text=text,
            contact=item.contact,
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
