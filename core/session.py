"""Session state machine (PRD A3.2). Owns where the person is in the menu and, through its
Pointer, the only copy of the highlight. The board just draws what the session sends.

Flow: SCANNING --clench on a leaf--> (suggestions screen) --clench on a sentence--> CONFIRMING
--clench--> SPEAKING --AUDIO_DONE/timeout--> home.
Nothing is spoken or sent without the confirming clench (PRD D5). On that clench the sentence is
spoken on the board and, at the same time, the leaf's action (message, call, room) runs in the
background through the action registry; its outcome comes back as ACTION_RESULT. Every utterance
has an id; SPEAKING only ends on the AUDIO_DONE with the id of the utterance it is waiting for.

Where the person is: a stack of frames. A frame is a menu level, a batch of new options from
"Other...", or the suggestions screen for a picked leaf. DOUBLE_BLINK goes up one menu level: it
pops the "Other..." pages of the current level together with the level itself.

AI (PRD section 5 step 6, D6, decisions.md #5 and #7):
  - Picking a leaf opens the suggestions screen: up to 3 AI sentences, then the leaf's fixed phrase,
    then "Other...". A clench on a sentence opens CONFIRMING with exactly that sentence. With no AI
    (no key, offline, slow, error) the leaf goes straight to CONFIRMING with its fixed phrase.
  - Every level ends with "Other..." ("Otro..."): each pick shows the next page of new options for
    the same path from the AI (or the level's fixed `more` list from menu.yaml when the AI has
    nothing). After 3 pages, or when there is nothing new (or no AI), the next pick loops back to the
    level's own options.
  - The home "Suggested" branch shows the AI's sentences for right now first, then its fixed phrases.
  - The AI only writes labels and text. The action and contact always come from the path: an AI
    option inherits them from its level (MenuNode.inherited), an AI sentence from its leaf.
  - Prefetch: when a frame opens, the session asks for everything the person could pick next
    (sentences for each leaf, the "Other..." batch, the Suggested sentences) in the background.
    Scanning never waits. If a pick needs a result that has not arrived, the session shows the
    loading state (LOADING, scanning paused) for at most 4 s, then falls back.

Learning (PRD section 9, D7, decisions.md "Learning"): with `learning` on, every screen is ordered
by the Ranker (core/rank). Menu levels keep the menu.yaml order unless an item clearly beats the one
above it (stability rule); home Suggested stays first and "Other..." stays last. The suggestions
screen and the Suggested list are fully reordered, and Suggested also offers the sentences the
patient confirms most (each with the action and contact of the leaf it was said under). With
learning off ("Day 1 mode") everything is in menu.yaml order and Suggested is its fixed list.

Speak picks (decisions.md #4): with speak_picks on, every CLENCH pick while scanning says the picked
tile's label (an "echo") before the next view shows; "Other..." is a short soft click (CLICK), no
word. Full sentences (suggestion tiles) are never echoed: the confirm step speaks them.

Pointing (PRD D2, A3.3a): one Pointer slot decides where the highlight is: Scan (timer), Webcam
(the board's POINT messages), Auto (webcam while the board sees a face, scan after 3 s without one)
or Head tilt (scans until the sensor chunk builds it). The pointing mode switches live. Selection is
the same code for every mode; only where the highlight comes from differs. Every SCREEN has a `seq`
that goes up when its tiles change, and a POINT for any other `seq` is ignored, so a late POINT
never lands on a new screen. When the highlight follows the head, a CLENCH picks the tile that was
highlighted `clench_lookback_ms` (250 ms) before it arrived: clenching can move the head.

Going back (docs/decisions.md 17): a DOUBLE_BLINK on a menu or on the "Say this?" screen only opens a
"Go back?" / "Cancel this message?" prompt (BACK_PROMPT) and pauses scanning. A CLENCH within
BACK_CONFIRM_S (3 s) goes back or cancels; doing nothing closes the prompt and nothing changes, and
clenches are then ignored for LATE_CLENCH_S (1 s) so a clench meant for the prompt cannot pick a tile
or send the message. More blinks while it is open are ignored. A LONG_CLENCH still starts help at once.
The help countdown itself is still cancelled by a DOUBLE_BLINK straight away.

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
from collections import deque
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Literal

from core.actions import ActionContext, ActionRegistry, build_registry
from core.clock import Scheduler, TimerHandle
from core.contracts import (
    ActionName,
    AudioDone,
    BodyStateLevel,
    Clench,
    Confirm,
    BackPrompt,
    CarAction,
    CarActionName,
    CarResult,
    CarState,
    TripLayout,
    WindowName,
    DoubleBlink,
    FaceOk,
    Lang,
    LongClench,
    Message,
    Metrics,
    Point,
    JevStatus,
    PointingMode,
    Reset,
    Screen,
    Settings,
    ShortcutDebug,
    State,
    Tap,
    Tile,
    TileKind,
)
from core.contracts import ActionResult as ActionResultMsg
from core.db import Db
from core.menu import MAX_ITEMS, Menu, MenuNode
from core.metrics import Tracker, day1_cost
from core.pointer import DEFAULT_SCAN_MS, make_pointer
from core.profile import Profile
from core.car.link import ActionRequest, CarLink, SupportAnswer, SupportQuestion
from core.car.mock import MockCar
from core.geo.model import Trip as GeoTrip
from core.geo.config import load_geo_config
from core.geo.trip import load_trip
from core.trip import BACK_LABEL, CONFIRM_PHRASE, MUSIC_ON, PLAN_ROOT, SPLIT_TOP, PULL_OVER_S, ROOT as TRIP_ROOT, ROUTINE_S, Car, TripNode
from core.rank import Entry, Ranker
from core.rank.history import Sentence, leaf_path
from core.rank.jev import JevAnswer, JevRanker
from core.rank.ranker import SHORTCUT_HISTORY_SHARE, SHORTCUT_JEV_CONFIDENCE, SHORTCUT_JEV_HISTORY_SHARE
from core.suggest.provider import MAX_SENTENCES, Option, drop_known, text_key
from core.suggest.service import Pending, Suggester
from core.voice import Voice

log = logging.getLogger("clench.session")

CLENCH_DEBOUNCE_S = 0.3  # a CLENCH within 300 ms of the last accepted one is ignored
BACK_CONFIRM_S = 3.0  # the go-back prompt stays open this long; a CLENCH inside it goes back
LATE_CLENCH_S = 1.0  # after the prompt closes on its own, clenches are ignored this long
SPEAK_TIMEOUT_S = 10.0  # back to home if the board never sends AUDIO_DONE
HELP_COUNTDOWN_S = 5  # PRD D3: 5 second cancel window
LOADING_MAX_S = 4.0  # longest the board waits for AI options after a pick
OTHER_PAGES = 3  # "Other..." pages in a row before the next pick loops back to the level's own options
TRAIL_S = 2.0  # how much highlight history the clench look-back keeps
RIDE_MINUTE_S = 60.0  # the mock ride's clock: arrival and battery move once a minute

HELP_LABEL: dict[Lang, str] = {"en": "Help", "es": "Ayuda"}
HELP_MESSAGE: dict[Lang, str] = {"en": "{name} needs help now", "es": "{name} necesita ayuda ahora"}
HELP_SPEECH: dict[Lang, str] = {"en": "Calling {contact}", "es": "Llamando a {contact}"}
HELP_START: dict[Lang, str] = {
    "en": "Calling for help. Double blink to cancel.",
    "es": "Pidiendo ayuda. Parpadea dos veces para cancelar.",
}
OTHER_LABEL: dict[Lang, str] = {"en": "Other...", "es": "Otro..."}
OTHER_WORD: dict[Lang, str] = {"en": "Other", "es": "Otro"}  # the breadcrumb (a pick is a click, no word)

Emit = Callable[[Message], None]
Spawn = Callable[[Coroutine[Any, Any, None]], None]
LANGS: tuple[Lang, ...] = ("en", "es")


def voice_lines(menu: Menu, profile: Profile) -> list[tuple[str, Lang]]:
    """Everything the session can say from the fixed menu, in both languages, for the voice prewarm:
    tile labels first (echoes have the shortest wait), then system lines, then leaf phrases."""
    labels: list[tuple[str, Lang]] = []
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
    return list(dict.fromkeys(labels + system + phrases))


class SessionState(str, Enum):
    SCANNING = "SCANNING"
    LOADING = "LOADING"  # waiting (at most 4 s) for AI options after a pick; scanning paused
    CONFIRMING = "CONFIRMING"
    SPEAKING = "SPEAKING"
    HELP_COUNTDOWN = "HELP_COUNTDOWN"
    ACTING = "ACTING"  # a trip control's confirm animation is playing: input is locked (core/trip.py)
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
    items: list[Item]  # the tiles, in the order shown
    crumb: Item | None = None  # the pick that opened this frame; None at home and for "Other..."
    via_other: bool = False  # opened by "Other...": breadcrumb "Other", left out of the AI path
    others: int = 0  # "Other..." pages in a row that led here; OTHER_PAGES = the next pick loops back
    shown: tuple[str, ...] = ()  # labels / sentences already on screen in this chain (never repeated)
    leaf: Item | None = None  # suggestions screen: the leaf the sentences are for
    ai: bool = False  # holds AI text (one language): dropped when the language changes
    # Ranking: `pool` holds every candidate in its base order (menu.yaml, or AI / history / fixed);
    # `items` is the ranked cut of it. "menu" = stability rule, "full" = by score, "none" = as given.
    pool: list[Item] = field(default_factory=list)
    rank: Literal["none", "menu", "full"] = "none"
    pinned: int = 0  # leading pool items that never move (home Suggested)
    priors: dict[str, float] = field(default_factory=dict)  # Jev probabilities by tile id


@dataclass(frozen=True)
class ShortcutCheck:
    """The one-clench shortcut decision for home Suggested, with what it was based on."""

    top: Item | None  # the history's top phrase
    history: float  # its history share (after recent cancels)
    jev: JevStatus
    jev_pick: Item | None
    jev_confidence: float | None
    ok: bool
    reason: str


def _unique(items: list[Item], lang: Lang) -> list[Item]:
    """`items` without repeats: the first of each id and of each sentence is kept."""
    ids: set[str] = set()
    texts: set[str] = set()
    out = []
    for i in items:
        has_text = i.kind == "suggestion" or (i.node is not None and i.node.is_leaf)
        key = text_key(i.phrase(lang)) if has_text else None
        if i.id in ids or (key is not None and key in texts):
            continue
        ids.add(i.id)
        if key is not None:
            texts.add(key)
        out.append(i)
    return out


PLAN_TIMEOUT_S = 20.0  # a live trip plan slower than this falls back to the demo trip
CORNER_CAR: dict[Lang, str] = {"en": "Car mode", "es": "Modo auto"}
CORNER_HOME: dict[Lang, str] = {"en": "Home", "es": "Inicio"}
CAR_MODE_PHRASE: dict[Lang, str] = {"en": "Start Car mode?", "es": "¿Iniciar el modo auto?"}


def _drop_short(description: str) -> str:
    """ "Main entrance, Jack Kassewitz Building (I)(9) from service road" -> "Main entrance, Jack Kassewitz
    Building": the part before "from", without OSM's parenthesised codes."""
    return re.sub(r"\s*\([^)]*\)", "", description.split(" from ", 1)[0]).strip()


def _short_reason(reason: str) -> str:
    """The drop-off reason's first three points, and its unknowns: one short line."""
    main, _, unknown = reason.partition("; unknown: ")
    points = [p.strip() for p in main.split(",")][:3]
    text = ", ".join(points)
    return text + (f". Unknown: {unknown}." if unknown else ".")


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
        learning: bool | None = None,
        ranker: Ranker | None = None,
        jev: JevRanker | None = None,
        car_link: CarLink | None = None,
        geo_trip: GeoTrip | None = None,
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
        self.learning = profile.learning if learning is None else learning
        self.long_clench_ms = profile.long_clench_ms
        self.muse_enabled = False
        self.onboarding = False
        self.trip = False  # trip mode: the trip screen instead of the menus (core/trip.py)
        # A ride is under way. Leaving Car mode (the Home corner) only leaves the screen: the ride goes on,
        # and the Car mode corner on Home brings the rider straight back to it.
        self.ride_active = False
        self.trip_layout: TripLayout = "car"  # car / split (map beside the car: fewer top tiles) / map
        self._trip_path: list[TripNode] = [PLAN_ROOT]  # the trip menu levels, top first
        # The car (core/car): the in-process mock unless main passes another link. The trip tiles
        # (routes, drop-off) come from the committed open-data result (core/geo).
        self.geo_trip = geo_trip if geo_trip is not None else load_trip()
        routes = {t.route_id: t.label for t in self.geo_trip.ride.tiles} if self.geo_trip and self.geo_trip.ride else {}
        self.car_link: CarLink = car_link or MockCar(scheduler, routes=routes)
        self.car_link.subscribe(self)
        self._car_requests = 0
        self._confirmed_requests: set[str] = set()  # answers to these are always said
        self._car_confirm: TripNode | None = None  # the HIGH-safety control on the confirm screen
        self._question: SupportQuestion | None = None  # an open Support question (it takes the screen)
        self._question_timer: TimerHandle | None = None
        self.input_connected: Callable[[], bool] = lambda: True  # main: is a board / input connected
        self._planning: str | None = None  # the destination being planned live (core/geo/plan.py)
        # Car mode: Plan a trip until a route is confirmed (and after "Change trip"), then the ride controls.
        self.ride_started = False
        self._changing_trip = False
        from core.geo.plan import load_places

        self.place_trips = load_places()  # saved places' committed trips: every pick is instant
        self._plan_note: str | None = None  # why the last plan fell back to the demo trip
        self._acting_timer: TimerHandle | None = None
        self._ride_timer: TimerHandle | None = None
        self.tile_switch_margin = profile.tile_switch_margin
        self.ranker = ranker or Ranker(db, weights=profile.ranking.weights, hysteresis=profile.ranking.hysteresis)
        self.jev = jev  # None = no Jev: the AI prior is 0
        self.state_level: BodyStateLevel | None = None  # from STATE; only reorders (PRD D10)
        self._moves = 0  # gestures so far; a late Jev answer only re-ranks if this has not changed
        self.suggester.use_history = self.learning
        self.state = SessionState.SCANNING
        self._stack: list[Frame] = [self._home()]
        self._pending: Item | None = None  # item being confirmed or spoken
        self._last_clench: float | None = None
        self._back: Literal["menu", "confirm"] | None = None  # the go-back prompt, while it is open
        self._back_timer: TimerHandle | None = None
        self._late_until = float("-inf")  # clenches before this are ignored (the prompt just closed)
        self._speak_timer: TimerHandle | None = None
        self._speaking_id: str | None = None  # the utterance SPEAKING waits for
        self._help_timer: TimerHandle | None = None
        self._help_left = 0  # seconds left in the help countdown
        self._help_from = SessionState.SCANNING  # where a cancelled countdown goes back to
        self._waiting: object | None = None  # token of the AI result LOADING waits for
        # The Suggested tile when CONFIRMING came from the one-clench shortcut: a double blink then
        # opens the Suggested list instead of going home.
        self._shortcut_from: Item | None = None
        self._effort = Tracker()  # clenches and scan steps since home (METRICS)
        self._loading_timer: TimerHandle | None = None
        # SCREEN seq: goes up whenever the tiles change; POINT must name the current one.
        self._seq = 0
        self._tiles_key: tuple[tuple[str, str, str], ...] | None = None
        # (time, seq, highlight) each time the highlight changed, for the clench look-back.
        self._trail: deque[tuple[float, int, int]] = deque()
        self._lookback_s = profile.clench_lookback_ms / 1000
        self.face_ok = False  # last FACE_OK from the board (handed to a new pointer on a mode switch)
        self.pointer = make_pointer(pointing_mode, scheduler, self._on_highlight, scan_ms, self._on_pointer_source)

    # --- public ---------------------------------------------------------------

    @property
    def frame(self) -> Frame:
        return self._stack[-1]

    @property
    def highlight(self) -> int:
        return self.pointer.highlight

    @property
    def seq(self) -> int:
        """The `seq` of the current tiles (the last SCREEN sent)."""
        return self._seq

    @property
    def speaking_id(self) -> str | None:
        """Id of the utterance SPEAKING is waiting for; None when not speaking."""
        return self._speaking_id

    def tiles(self) -> list[Tile]:
        """The tiles of the current frame, "Other..." last."""
        return self._screen().tiles

    def start(self) -> None:
        """Show home and start scanning."""
        self._go_home()

    def stop(self) -> None:
        self.pointer.close()
        self._cancel_speak_timer()
        self._cancel_help_timer()
        self._cancel_back_timer()
        self._cancel_acting()
        self._cancel_ride()
        self._cancel_wait()
        for task in list(self._tasks):
            task.cancel()

    @property
    def car(self) -> Car:
        """The mock car's telemetry (tests, and the trip screen's older callers)."""
        return self.car_link.car  # type: ignore[attr-defined]

    def current_view(self) -> Message | None:
        """What a newly connected board should show (reply to READY)."""
        if self.state in (SessionState.SCANNING, SessionState.LOADING, SessionState.ACTING):
            return self._screen()
        if self.state is SessionState.CONFIRMING:
            return self._confirm_msg()
        if self.state is SessionState.HELP_COUNTDOWN:
            return self._help_screen()
        return None  # SPEAKING: never re-send SPEAK; the next SCREEN follows when speech ends

    def settings(self) -> Settings:
        """The current settings, as the Core announces them to every client."""
        return Settings(
            pointing_mode=self.pointing_mode,
            scan_ms=self.scan_ms,
            lang=self.lang,
            speak_picks=self.speak_picks,
            learning=self.learning,
            long_clench_ms=self.long_clench_ms,
            muse_enabled=self.muse_enabled,
            onboarding=self.onboarding,
            tile_switch_margin=self.tile_switch_margin,
            trip=self.trip,
            trip_layout=self.trip_layout,
        )

    def handle(self, msg: Message) -> None:
        if self.onboarding and isinstance(msg, (Clench, Tap, DoubleBlink)):
            if not (isinstance(msg, DoubleBlink) and self.state is SessionState.HELP_COUNTDOWN):
                log.info("%s ignored: onboarding owns ordinary input", msg.type)
                return
        if isinstance(msg, (Clench, DoubleBlink, LongClench, Tap)):
            self._moves += 1
        match msg:
            case Clench():
                self._on_clench()
            case Tap():
                self._on_tap(msg)
            case DoubleBlink():
                self._on_double_blink()
            case LongClench():
                if self.state in (
                    SessionState.SCANNING, SessionState.LOADING, SessionState.CONFIRMING, SessionState.ACTING
                ):
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
            case Reset():
                self._reset()
            case Point():
                if msg.seq != self._seq:
                    log.debug("POINT for screen %d ignored: the board shows screen %d now", msg.seq, self._seq)
                elif self.state is SessionState.SCANNING:
                    self.pointer.on_point(msg)
            case FaceOk():
                if msg.ok != self.face_ok:
                    log.info("webcam %s", "sees a face" if msg.ok else "lost the face")
                self.face_ok = msg.ok
                self.pointer.on_face(msg.ok)
            case State():
                self.state_level = msg.level  # used from the next screen on; never takes action
            case _:
                log.debug("ignored %s in %s", msg.type, self.state.value)

    # --- gestures -------------------------------------------------------------

    def _on_tap(self, msg: Tap) -> None:
        """A touch on the board: a CLENCH aimed at one tile (or at the confirm card). Everything a
        CLENCH must pass (debounce, the go-back prompt's aftermath) applies; the help countdown and the
        go-back prompt ignore tile taps, so a stray touch can neither cancel help nor answer the prompt.
        Their large Cancel / Stay here buttons send TAP `cancel`: a deliberate touch, so nothing depends
        on blinks (the tablet has no DOUBLE_BLINK yet)."""
        if msg.cancel and self.state is SessionState.HELP_COUNTDOWN:
            log.info("TAP on Cancel: the help countdown is cancelled")
            self._cancel_help()
            return
        if msg.cancel and self._back is not None:
            log.info("TAP on Stay here: the go-back prompt is closed, staying")
            kind = self._back
            self._close_back()
            if kind == "menu" and self.state is SessionState.SCANNING:
                self._resume()
            return
        if self._back is not None:
            log.info("TAP ignored: the go-back prompt is open (a clench answers it)")
            return
        if msg.cancel:
            if self.state is SessionState.CONFIRMING:
                log.info("TAP on Cancel: the confirm screen is cancelled")
                self._cancel_confirm()
            else:
                log.info("TAP on Cancel ignored while %s", self.state.value)
            return
        if msg.tile is None:
            if self.state is SessionState.CONFIRMING:
                self._on_clench()
            else:
                log.info("TAP on the confirm card ignored while %s", self.state.value)
            return
        if msg.seq != self._seq:
            log.info("TAP for screen %s ignored: the board shows screen %d now", msg.seq, self._seq)
        elif self.state is not SessionState.SCANNING:
            log.info("TAP on tile %d ignored while %s", msg.tile, self.state.value)
        else:
            self._on_clench(index=msg.tile)

    def _on_clench(self, index: int | None = None) -> None:
        """Pick (or confirm, or answer the go-back prompt). `index`: a TAP's tile instead of the highlight."""
        now = self._scheduler.now()
        if self._last_clench is not None and now - self._last_clench < CLENCH_DEBOUNCE_S:
            log.info("CLENCH ignored: within %d ms of the previous one", CLENCH_DEBOUNCE_S * 1000)
            return
        self._last_clench = now
        if self._back is not None:
            kind = self._back
            log.info("CLENCH confirms the go-back prompt (%s)", kind)
            self._close_back()
            if kind == "menu":
                self._up_one_level()
            else:
                self._cancel_confirm()
            return
        if now < self._late_until:
            log.info("CLENCH ignored: the go-back prompt just closed")
            return
        if self.state is SessionState.SCANNING:
            self._effort.select()
            self._pick(index)
        elif self.state is SessionState.CONFIRMING:
            self._confirm_pending()
        else:
            log.info("CLENCH ignored while %s", self.state.value)

    def _on_double_blink(self) -> None:
        if self._back is not None and self.state is not SessionState.HELP_COUNTDOWN:
            log.info("DOUBLE_BLINK ignored: the go-back prompt is already open")
            return
        if self.state is SessionState.SCANNING:
            if self._depth() > 1:
                self._open_back("menu")
            else:
                log.info("DOUBLE_BLINK at home: nothing to go back to")
        elif self.state is SessionState.LOADING:
            log.info("DOUBLE_BLINK: stopped waiting for AI options")
            self._cancel_wait()
            self._resume()
        elif self.state is SessionState.CONFIRMING:
            self._open_back("confirm")
        elif self.state is SessionState.HELP_COUNTDOWN:
            self._cancel_help()  # at once, by choice: stopping a false alarm must stay one gesture
        else:
            log.info("DOUBLE_BLINK ignored while %s", self.state.value)

    # --- the go-back prompt ---------------------------------------------------

    def _open_back(self, kind: Literal["menu", "confirm"]) -> None:
        """Ask before going back: blinks are easy to do by accident, and twice. Scanning pauses so the
        highlight cannot move under the prompt."""
        self._back = kind
        if kind == "menu":
            self.pointer.stop()
        log.info("DOUBLE_BLINK: go-back prompt (%s) for %.0f s; a clench confirms", kind, BACK_CONFIRM_S)
        self._back_timer = self._scheduler.call_later(BACK_CONFIRM_S, self._back_timed_out)
        self._emit(BackPrompt(open=True, kind=kind, timeout_ms=round(BACK_CONFIRM_S * 1000)))

    def _back_timed_out(self) -> None:
        """Nothing happened: stay. Doing nothing is how the person says no."""
        self._back_timer = None
        kind = self._back or "menu"
        self._back = None
        self._late_until = self._scheduler.now() + LATE_CLENCH_S
        log.info("go-back prompt (%s) closed: no clench, staying", kind)
        self._emit(BackPrompt(open=False, kind=kind, timeout_ms=0))
        if kind == "menu" and self.state is SessionState.SCANNING:
            self._resume()

    def _close_back(self) -> None:
        """Close the prompt without its own consequence (confirmed, help, reset, a new screen)."""
        if self._back is None:
            return
        kind = self._back
        self._back = None
        self._cancel_back_timer()
        self._emit(BackPrompt(open=False, kind=kind, timeout_ms=0))

    def _cancel_back_timer(self) -> None:
        if self._back_timer is not None:
            self._back_timer.cancel()
            self._back_timer = None

    def _cancel_confirm(self) -> None:
        """The "Say this?" screen was cancelled: nothing is said or sent."""
        item = self._pending
        assert item is not None
        text = item.phrase(self.lang)
        log.info("cancelled: %r", text)
        self._record(item, rejected=True, text=text)
        self._pending = None
        branch, self._shortcut_from = self._shortcut_from, None
        if branch is not None:
            # The one-clench guess was wrong: show the whole Suggested list instead of going home.
            self._open_suggested_list(branch)
        else:
            self._enter_frame()  # back to the screen the sentence was picked on

    # --- picking --------------------------------------------------------------

    def _pick_index(self) -> int:
        """The tile a CLENCH picks. Scanning: the highlighted one. Following the head: the one that was
        highlighted `clench_lookback_ms` before the clench arrived, on this same screen (the first
        highlight of the screen when it is newer than that), because clenching can move the head."""
        index = self.pointer.highlight
        if self.pointer.source == "scan" or self._lookback_s <= 0:
            return index
        target = self._scheduler.now() - self._lookback_s
        here = [(t, h) for t, seq, h in self._trail if seq == self._seq]
        before = [h for t, h in here if t <= target]
        picked = before[-1] if before else here[0][1] if here else index
        if picked != index:
            log.info(
                "clench look-back: tile %d (highlighted %d ms before the clench), not %d",
                picked, self._lookback_s * 1000, index,
            )
        return picked

    def _pick(self, index: int | None = None) -> None:
        frame = self.frame
        if index is None:
            index = self._pick_index()
        if self._question is not None:
            self._pick_answer(index)
            return
        corner = self._corner()
        if corner is not None and index == self._grid_count():
            self._pick_corner(corner)
            return
        if self.trip:
            self._pick_trip(index)
            return
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
                top = self._shortcut(item)
                if top is not None:
                    log.info("shortcut: straight to the confirm screen with %r", top.phrase(self.lang))
                    self._shortcut_from = item
                    self._confirm(top)
                else:
                    self._open_suggested_list(item)
            else:
                self._push(self._menu_frame(item.node, item.id, item))
        elif self.suggester.available:
            self._wait(self._leaf_request(item), lambda sentences: self._open_suggestions(item, sentences))
        else:
            self._confirm(item)  # no AI: straight to the fixed phrase, as before

    def _pick_other(self, frame: Frame) -> None:
        self._log_event(node_id=_join(frame.prefix, "other"), path=self._crumbs() + [OTHER_WORD[self.lang]], action=None)
        if self.speak_picks:
            self._voice.click()  # a short soft click, no word, in order with the echoes
        if frame.others >= OTHER_PAGES:
            self._loop_back(f"after {OTHER_PAGES} pages")
        elif self.suggester.available:
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
        self._shortcut_from = None
        text = item.phrase(self.lang)
        ctx = self._context(text, item.contact)
        if item.kind == "corner":  # "Start Car mode?" confirmed: nothing to say or send
            self._pending = None
            self._set_trip(True)
            if self.state is SessionState.CONFIRMING:  # _change_trip leaves a confirm screen for us to close
                self._go_home(first_tile=True)
            return
        self._record(item, confirmed=True, text=text)
        if item.kind in ("car", "answer"):
            # A HIGH-safety trip request or a Support answer: confirmed, so now it goes to the car
            # (no sentence history). The rider's sentence is said in the car; the car's answer follows.
            if item.kind == "car":
                node, self._car_confirm = self._car_confirm, None
                assert node is not None and node.car_id is not None
                self._send_car(node.car_id, confirmed=True)
            else:
                self._send_answer(item.id.rsplit(".", 1)[0].removeprefix("trip.support."), item.id.removeprefix("trip.support."))
            self._speak_phrase(text)
            return
        else:
            self._use_phrase(text)
            self._effort.select()
            self._emit(self._metrics(item, text))
        self._speak_phrase(text)  # always said aloud in the room
        if item.action != "speak":
            self._run_action(item.action, ctx)  # and sent, at the same time

    def _metrics(self, item: Item, text: str) -> Metrics:
        """What this message took, and what it would have taken in Day 1 mode."""
        done = self._effort.effort
        day1 = day1_cost(
            self._menu, event_id=item.event_id, item_id=item.id, text=text, lang=self.lang, ai_on=self.suggester.available
        ) or done
        log.info(
            "took %d clenches, %d scan steps (Day 1: %d clenches, %d scan steps): %r",
            done.selections, done.scan_steps, day1.selections, day1.scan_steps, text,
        )
        return Metrics(
            text=text,
            selections=max(done.selections, 1),
            scan_steps=done.scan_steps,
            day1_selections=max(day1.selections, 1),
            day1_scan_steps=day1.scan_steps,
        )

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
        self._go_home()

    # --- trip ---------------------------------------------------------------

    def _trip_prefix(self) -> str:
        return ".".join(n.key for n in self._trip_path)

    def _trip_tiles(self) -> list[Tile]:
        """The trip menu level's tiles, then Back below the top level."""
        prefix = self._trip_prefix()
        tiles = [Tile(id=f"{prefix}.{n.key}", label=n.label(self.lang), kind="car") for n in self._trip_children()]
        if len(self._trip_path) > 1:
            tiles.append(Tile(id=f"{prefix}.back", label=BACK_LABEL[self.lang], kind="back"))
        return tiles

    def _pick_trip(self, index: int) -> None:
        """A trip tile: a level opens, Back goes up, a LOW-safety comfort control is sent to the car at
        once behind a short input lock (the level stays, so it can be repeated), a HIGH-safety request
        (pull over, Support, drop-off, route) opens the confirm screen first."""
        children = self._trip_children()
        if len(self._trip_path) > 1 and index == len(children):
            self._echo(BACK_LABEL[self.lang])
            self._trip_path.pop()
            self._enter_frame()
            return
        if not 0 <= index < len(children):
            log.warning("trip pick %d outside %d tiles", index, self._tile_count())
            return
        node = children[index]
        node_id = f"{self._trip_prefix()}.{node.key}"
        label = node.label(self.lang)
        self._echo(label)
        if node.is_level:
            self._trip_path.append(node)
            self._enter_frame()
            return
        assert node.car_id is not None
        if node.car_id == "change_trip":
            self._changing_trip = True
            self._trip_path = [PLAN_ROOT]
            self._enter_frame()
            return
        if node.car_id.startswith("plan:"):
            place = next(p for p in load_geo_config().places if p.key == node.car_id.removeprefix("plan:"))
            self._trip_path.pop()  # back to the Trip level, which shows the planning
            self._enter_frame()
            self.plan_trip(place.query, place.label_en if self.lang == "en" else place.label_es)
            return
        if node.confirm:
            self._car_confirm = node
            self._confirm(
                Item(kind="car", id=node_id, event_id=node_id, text=self._car_phrase(node),
                     ai_label=label, action=self._car_action_name(node.car_id))
            )
            return
        log.info("trip: %s (LOW, sent at once)", node.car_id)
        crumbs = [n.label(self.lang) for n in self._trip_path] + [label]
        self._log_event(node_id=node_id, path=crumbs, action=None)
        self._send_car(node.car_id, confirmed=False)
        assert node.action is not None
        self._act(node.action, node.window, ROUTINE_S)

    # --- the car link -----------------------------------------------------------

    def _ride_nodes(self) -> list[TripNode]:
        """The Trip level: the route tiles and the drop-off, from the committed open-data result."""
        t = self.geo_trip
        nodes: list[TripNode] = []
        if t is not None and t.ride is not None:
            for i, tile in enumerate(t.ride.tiles[:2]):
                nodes.append(TripNode(f"route_{i + 1}", tile.label, tile.label, car_id=f"route:{tile.route_id}", confirm=True))
        if t is not None and t.dropoff is not None and t.dropoff.request is not None:
            short = _drop_short(t.dropoff.request.description)
            nodes.append(TripNode("dropoff", f"Drop off at {short}?", f"¿Bajar en {short}?", car_id="dropoff", confirm=True))
        nodes.append(TripNode("plan", "Plan a trip", "Planear un viaje", dynamic="plan"))
        return nodes

    def _plan_nodes(self) -> list[TripNode]:
        return [TripNode(p.key, p.label_en, p.label_es, car_id=f"plan:{p.key}") for p in load_geo_config().places][:5]

    def plan_trip(self, query: str, label: str) -> None:
        """Plan a new trip live (layers 1 and 2) in a worker thread. On success its routes and drop-off
        replace the Trip level's; after PLAN_TIMEOUT_S or a failure the demo trip stays, and the rider
        is told why. MDC Kendall is the committed demo trip itself (no live planning needed)."""
        if self._planning is not None:
            log.info("already planning a trip to %s; %r ignored", self._planning, label)
            return
        self._planning = label
        self._plan_note = None
        self._voice.speak(("Planning your trip to " if self.lang == "en" else "Planeando tu viaje a ") + label + ".", self.lang, "system")
        self._redraw_trip()
        self._spawn(self._plan_task(query, label))

    async def _plan_task(self, query: str, label: str) -> None:
        trip = None
        why = ""
        try:
            demo = load_trip()
            if demo is not None and demo.dropoff is not None and query == next((p.query for p in load_geo_config().places if p.key == "mdc"), None):
                trip = demo  # MDC Kendall: the committed demo trip
            else:
                from core.geo.plan import plan_trip

                work = asyncio.ensure_future(asyncio.to_thread(plan_trip, query, label))
                try:
                    trip = await asyncio.wait_for(asyncio.shield(work), PLAN_TIMEOUT_S)
                except asyncio.TimeoutError:
                    # Keep the demo trip now; if the plan still finishes, offer it then (and it is cached).
                    work.add_done_callback(lambda f: self._late_plan(f, label))
                    raise
                if trip.dropoff is None or trip.ride is None:
                    why = "; ".join(trip.notes) or "the map services did not answer"
                    trip = None
        except asyncio.TimeoutError:
            why = f"it took more than {PLAN_TIMEOUT_S:.0f} seconds"
        except Exception as e:  # a planning failure must never break the session
            log.exception("trip planning failed")
            why = type(e).__name__
        self._planning = None
        if trip is not None and trip.dropoff is not None and trip.ride is not None:
            self.geo_trip = trip
            if isinstance(self.car_link, MockCar):
                self.car_link.routes = {t.route_id: t.label for t in trip.ride.tiles}
            text = f"Your trip to {label} is planned." if self.lang == "en" else f"Tu viaje a {label} está listo."
        else:
            self._plan_note = (f"Could not plan {label} ({why}); showing the demo trip." if self.lang == "en"
                               else f"No se pudo planear {label} ({why}); se muestra el viaje de demostración.")
            text = self._plan_note
        log.info("trip plan: %s", text)
        self._voice.speak(text, self.lang, "system")
        self._redraw_trip()

    def _late_plan(self, work: "asyncio.Future[GeoTrip]", label: str) -> None:
        """A plan that missed PLAN_TIMEOUT_S finished after all: use it, and say so (unless another
        plan started meanwhile)."""
        if self._planning is not None or work.cancelled() or work.exception() is not None:
            return
        trip = work.result()
        if trip.dropoff is None or trip.ride is None:
            return
        self.geo_trip = trip
        self._plan_note = None
        if isinstance(self.car_link, MockCar):
            self.car_link.routes = {t.route_id: t.label for t in trip.ride.tiles}
        text = f"Your trip to {label} is ready now." if self.lang == "en" else f"Tu viaje a {label} ya está listo."
        log.info("trip plan (late): %s", text)
        self._voice.speak(text, self.lang, "system")
        self._redraw_trip()

    def _redraw_trip(self) -> None:
        if self.trip and self.state in (SessionState.SCANNING, SessionState.ACTING) and self._question is None:
            self._emit(self._screen())

    def _routes_prompt(self, key: str) -> str:
        """The routes screen: where the rider is dropped off and why, in two short lines."""
        trip = self.place_trips[key]
        req = trip.dropoff.request if trip.dropoff else None
        if req is None:
            if self.lang == "en":
                return "No accessible drop-off is mapped here. The car chooses where to stop.\nTimes without traffic."
            return "No hay un punto de bajada accesible en el mapa. El auto elige dónde parar.\nTiempos sin tráfico."
        why = _short_reason(req.reason)
        if self.lang == "en":
            return f"Drop-off: {_drop_short(req.description)}\n{why}\nTimes without traffic."
        return f"Bajada: {_drop_short(req.description)}\n{why}\nTiempos sin tráfico."

    def _ride_prompt(self) -> str | None:
        """What the Trip level says above its tiles: the drop-off's reason and each route's trade-off."""
        t = self.geo_trip
        if self._planning is not None:
            return ("Planning your trip to " if self.lang == "en" else "Planeando tu viaje a ") + self._planning + "…"
        if t is None:
            return self._plan_note
        parts = [self._plan_note] if self._plan_note else []
        if t.ride is not None:
            parts += [f"{tile.label}: {tile.detail}" for tile in t.ride.tiles[:2]]
            parts.append(t.duration_note)
        if t.dropoff is not None and t.dropoff.request is not None:
            parts.append(f"Drop-off: {t.dropoff.request.reason}")
        return " · ".join(parts) or None

    def _car_phrase(self, node: TripNode) -> str:
        """The sentence on the confirm screen, said in the car once confirmed."""
        cid = node.car_id or ""
        if cid.startswith("go:"):
            key = cid.split(":")[1]
            trip = self.place_trips[key]
            place = next(p for p in load_geo_config().places if p.key == key)
            name = place.label_en if self.lang == "en" else place.label_es
            drop = _drop_short(trip.dropoff.request.description) if trip.dropoff and trip.dropoff.request else None
            if self.lang == "en":
                return f"Go to {name}? {node.label_en}, without traffic. " + (f"Drop-off: {drop}." if drop else "Drop-off: the car chooses.")
            return f"¿Ir a {name}? {node.label_en}, sin tráfico. " + (f"Bajada: {drop}." if drop else "Bajada: la elige el auto.")
        if cid in CONFIRM_PHRASE:
            return CONFIRM_PHRASE[cid][self.lang]
        if cid == "dropoff":
            short = node.label_en.removeprefix("Drop off at ").removesuffix("?")
            return f"Please drop me off at {short}." if self.lang == "en" else f"Por favor, déjame en {short}."
        return f"Please take this route: {node.label_en}." if self.lang == "en" else f"Por favor, toma esta ruta: {node.label_en}."

    @staticmethod
    def _car_action_name(car_id: str) -> ActionName:
        if car_id == "pull_over":
            return "pull_over"
        if car_id == "contact_support":
            return "support"
        if car_id == "dropoff":
            return "dropoff"
        return "route"

    def _send_car(self, car_id: str, *, confirmed: bool) -> None:
        """Send a request to the car. HIGH-safety ids arrive here only from _confirm_pending."""
        self._car_requests += 1
        rid = f"r-{self._car_requests}"
        self.car_link.set_language(self.lang)
        if confirmed:
            self._confirmed_requests.add(rid)
        now = time.time()
        if car_id == "dropoff":
            assert self.geo_trip is not None and self.geo_trip.dropoff is not None and self.geo_trip.dropoff.request is not None
            self.car_link.request(self.geo_trip.dropoff.request.model_copy(update={"request_id": rid}))
            return
        if car_id.startswith("go:"):
            _, key, route_id = car_id.split(":", 2)
            trip = self.place_trips[key]
            self.geo_trip = trip
            if isinstance(self.car_link, MockCar):
                self.car_link.routes = {t.route_id: t.label for t in trip.ride.tiles} if trip.ride else {}
            if trip.dropoff is not None and trip.dropoff.request is not None:
                self._car_requests += 1
                drop_id = f"r-{self._car_requests}"
                self._confirmed_requests.add(drop_id)
                self.car_link.request(trip.dropoff.request.model_copy(update={"request_id": drop_id}))
            car_id = f"route:{route_id}"
            self.ride_started = True
            self._changing_trip = False
            if isinstance(self.car_link, MockCar):
                self.car_link.depart()
        if car_id.startswith("route:") and self.geo_trip is not None and self.geo_trip.ride is not None:
            tile = next((t for t in self.geo_trip.ride.tiles if f"route:{t.route_id}" == car_id), None)
            if tile is not None and tile.ride_profile is not None:
                self.car_link.request(tile.ride_profile)  # the preference behind it (no answer expected)
        self.car_link.request(ActionRequest(request_id=rid, action_id=car_id, confirmed_at=now if confirmed else None))

    def on_car_state(self, state: CarState) -> None:
        if self.trip:
            self._emit(state)

    def on_car_result(self, result: CarResult) -> None:
        """The car answered. Shown always; said for a confirmed request, or when a comfort control
        was DELAYED or REJECTED (the rider must hear why nothing happened)."""
        self._emit(result)
        confirmed = result.request_id in self._confirmed_requests
        if confirmed or result.status in ("DELAYED", "REJECTED"):
            self._voice.speak(result.message, self.lang, "system")
        if result.status in ("COMPLETED", "REJECTED"):
            self._confirmed_requests.discard(result.request_id)
        if result.action_id == "pull_over" and result.status == "COMPLETED":
            self._emit(CarAction(action="pull_over", ms=round(PULL_OVER_S * 1000)))  # the scene eases to a stop

    # --- Support questions ------------------------------------------------------

    def on_support_question(self, question: SupportQuestion) -> None:
        """Support asks something: the question takes the screen as soon as the rider is scanning
        (not in the middle of a confirm, a sentence, or a help countdown). Help works as always."""
        log.info("Support asks %s: %r", question.question_id, question.text)
        self._close_question()
        self._question = question
        self._question_timer = self._scheduler.call_later(question.timeout_seconds, self._question_timed_out)
        prefix = "Support asks: " if self.lang == "en" else "Soporte pregunta: "
        self._voice.speak(prefix + question.text, self.lang, "system")
        if self.state in (SessionState.SCANNING, SessionState.LOADING, SessionState.ACTING):
            self._cancel_wait()
            self._cancel_acting()
            self._enter_frame(first_tile=True)

    def _pick_answer(self, index: int) -> None:
        q = self._question
        assert q is not None
        if not 0 <= index < len(q.options):
            return
        option = q.options[index]
        self._echo(option.label)
        item_id = f"trip.support.{q.question_id}.{option.id.rsplit('.', 1)[-1]}"
        self._confirm(Item(kind="answer", id=item_id, event_id=item_id, text=f"{option.label}.", ai_label=option.label, action="support_answer"))

    def _send_answer(self, question_id: str, answer_key: str) -> None:
        q = self._question
        if q is None or q.question_id != question_id:
            log.info("Support answer for %s dropped: the question is closed", question_id)
            return
        option = next((o for o in q.options if o.id.rsplit(".", 1)[-1] == answer_key.rsplit(".", 1)[-1]), None)
        self.car_link.answer(SupportAnswer(question_id=q.question_id, answered_at=time.time(), option_id=option.id if option else None))
        self._close_question()

    def _question_timed_out(self) -> None:
        """No answer in time: the car hears no_response, and whether input was connected."""
        self._question_timer = None
        q = self._question
        if q is None:
            return
        log.info("Support question %s: no response", q.question_id)
        self.car_link.answer(SupportAnswer(question_id=q.question_id, answered_at=time.time(), no_response_input_connected=self.input_connected()))
        self._question = None
        if self.state is SessionState.CONFIRMING and self._pending is not None and self._pending.kind == "answer":
            self._pending = None  # too late to answer: back to the trip screen, nothing sent
            self._close_back()
            self._enter_frame()
        elif self.state in (SessionState.SCANNING, SessionState.ACTING):
            self._cancel_acting()
            self._enter_frame()

    def _close_question(self) -> None:
        self._question = None
        if self._question_timer is not None:
            self._question_timer.cancel()
            self._question_timer = None

    def _act(self, action: CarActionName, window: WindowName | None, seconds: float) -> None:
        """Play a control's confirm animation with input locked: no pick, tap or pointing until it ends
        (a stray clench must not land on whatever is under the tiles when they come back)."""
        self.state = SessionState.ACTING
        self.pointer.stop()
        self._emit(CarAction(action=action, window=window, ms=round(seconds * 1000)))
        self._cancel_acting()
        self._acting_timer = self._scheduler.call_later(seconds, self._acting_done)

    def _acting_done(self) -> None:
        self._acting_timer = None
        if self.state is SessionState.ACTING:
            self._resume()  # same tiles, the highlight where it was

    def _cancel_acting(self) -> None:
        if self._acting_timer is not None:
            self._acting_timer.cancel()
            self._acting_timer = None

    def _change_trip(self) -> None:
        """Trip mode on or off. A new trip starts a fresh (mock) ride. While scanning, the board switches
        screens at once (first tile); otherwise (confirming, speaking, help) it applies from the next screen."""
        log.info("trip mode %s", "on: the trip screen" if self.trip else "off: the menus" + (" (the ride goes on)" if self.ride_active else " (ride ended)"))
        if self.trip:
            # Car mode always opens in Split (decisions #30); switching to Car lasts until Car mode is left.
            self.trip_layout = "split"
        if self.trip and self.ride_active:
            self._emit(self.car_link.state())  # back to the ride under way
        elif self.trip:
            self.ride_active = True
            self.ride_started = False
            self.car_link.start_ride()  # a fresh (mock) ride; its CAR_STATE comes back through on_car_state
            self._ride_timer = self._scheduler.call_later(RIDE_MINUTE_S, self._ride_tick)
        if self.state in (SessionState.SCANNING, SessionState.LOADING, SessionState.ACTING):
            self._cancel_wait()
            self._cancel_acting()
            self._tiles_key = None
            self._go_home(first_tile=True)
        else:
            self._stack = [self._home()]
            self._trip_path = [self._trip_home()]

    def _ride_tick(self) -> None:
        """Another minute of the mock ride: arrival closer, a little battery used."""
        self._ride_timer = None
        if not self.ride_active:
            return
        self.car_link.tick()
        self._ride_timer = self._scheduler.call_later(RIDE_MINUTE_S, self._ride_tick)

    def _cancel_ride(self) -> None:
        if self._ride_timer is not None:
            self._ride_timer.cancel()
            self._ride_timer = None

    def _tile_count(self) -> int:
        """Tiles on the current screen: a Support question's options, the trip level's (Back included),
        or the frame's items plus "Other...", then the corner button when there is one."""
        return self._grid_count() + (1 if self._corner() is not None else 0)

    def _grid_count(self) -> int:
        if self._question is not None:
            return len(self._question.options)
        if self.trip:
            return len(self._trip_children()) + (1 if len(self._trip_path) > 1 else 0)
        return len(self.frame.items) + 1

    def _corner(self) -> Tile | None:
        """The corner button outside the grid: "Home" in Car mode (leaves the screen, not the ride),
        "Car mode" on the Home screen. None on a Support question and below Home."""
        if self._question is not None:
            return None
        if self.trip:
            return Tile(id="corner.home", label=CORNER_HOME[self.lang], kind="corner")
        if len(self._stack) == 1 and self.frame.kind == "menu":
            return Tile(id="corner.car_mode", label=CORNER_CAR[self.lang], kind="corner")
        return None

    def _pick_corner(self, corner: Tile) -> None:
        self._echo(corner.label)
        if corner.id == "corner.car_mode":
            # Entering Car mode starts or resumes a ride: a HIGH-safety step, so it confirms first.
            self._confirm(Item(kind="corner", id=corner.id, event_id="trip.car_mode", text=CAR_MODE_PHRASE[self.lang],
                               ai_label=corner.label, action="car_mode"))
            return
        log.info("Car mode: back to Home (the ride goes on)")
        self._set_trip(False)

    def _trip_home(self) -> TripNode:
        return TRIP_ROOT if self.ride_started and not self._changing_trip else PLAN_ROOT

    def car_start_ride(self) -> None:
        """The car started the ride (/car-sim Start ride): the board shows Car mode. The car's action,
        not the rider's request, so there is no confirm screen; help works as always."""
        log.info("the car started the ride")
        self._set_trip(True)

    def car_end_ride(self) -> None:
        """The car ended the ride (/car-sim End ride): back to Home; the next ride starts fresh."""
        log.info("the car ended the ride")
        self.ride_active = False
        self.ride_started = False
        self._changing_trip = False
        self._cancel_ride()
        self._set_trip(False)

    def _set_trip(self, on: bool) -> None:
        """Show or leave the car screen, as the dev panel's SETTINGS `trip` does, and tell every client."""
        if on == self.trip:
            return
        self.trip = on
        self._change_trip()
        self._emit(self.settings())

    def _trip_children(self) -> list[TripNode]:
        """The trip level's controls; at the top of the split layout only the most important ones."""
        level = self._trip_path[-1]
        if level.dynamic == "ride":
            return self._ride_nodes()
        if level.dynamic == "plan":
            return [TripNode(p.key, p.label_en, p.label_es, dynamic=f"routes:{p.key}")
                    for p in load_geo_config().places if p.key in self.place_trips][:5]
        if level.dynamic and level.dynamic.startswith("routes:"):
            key = level.dynamic.removeprefix("routes:")
            trip = self.place_trips[key]
            assert trip.ride is not None
            return [TripNode(f"route_{i + 1}", t.label, t.label, car_id=f"go:{key}:{t.route_id}", confirm=True)
                    for i, t in enumerate(trip.ride.tiles[:3])]
        if self.trip_layout == "split" and self.pointer.source != "scan" and len(self._trip_path) == 1:
            return [n for n in level.children if n.key in SPLIT_TOP]
        music_off = self.car_link.state().music_playing is False
        return [TripNode(*MUSIC_ON[:3], action=MUSIC_ON[3], car_id=MUSIC_ON[4]) if n.key == "music" and music_off else n  # type: ignore[arg-type]
                for n in level.children]

    def _depth(self) -> int:
        """How deep the person is: 1 at home (or the top of the trip menu, or a Support question)."""
        if self._question is not None:
            return 1
        return len(self._trip_path) if self.trip else len(self._stack)

    # --- frames ---------------------------------------------------------------

    def _reset(self) -> None:
        """RESET ("Click to start", the dev panel): Home from its first tile, whatever was going on,
        except a help countdown (a reloaded board must never cancel a call for help)."""
        if self.state is SessionState.HELP_COUNTDOWN:
            log.warning("RESET ignored: a help countdown is running (a double blink cancels it)")
            return
        log.info("RESET: back to home (was %s)", self.state.value)
        self._close_back()
        self._cancel_wait()
        self._cancel_speak_timer()
        self._speaking_id = None
        self._pending = None
        self._shortcut_from = None
        self._tiles_key = None  # a new seq even if home is already showing: old POINTs are void
        self._go_home(first_tile=True)

    def _home(self) -> Frame:
        return self._menu_frame(self._menu.root, "", None)

    def _go_home(self, *, first_tile: bool = False) -> None:
        self._stack = [self._home()]
        self._trip_path = [self._trip_home()]  # in trip mode: Plan a trip, or the ride controls once under way
        self._effort.reset()  # metrics count from home
        self._enter_frame(first_tile=first_tile)

    def _menu_item(self, node: MenuNode, prefix: str) -> Item:
        tile_id = _join(prefix, node.id)
        kind: TileKind = "leaf" if node.is_leaf else "branch"
        return Item(kind=kind, id=tile_id, event_id=tile_id, node=node, action=node.action, contact=node.contact)

    def _menu_frame(self, node: MenuNode, prefix: str, crumb: Item | None) -> Frame:
        items = [self._menu_item(c, prefix) for c in node.children or []]
        # The AI's "right now" branch (home Suggested) is always first.
        first = [i for i in items if i.node is not None and i.node.ai_now]
        pool = first + [i for i in items if i not in first]
        frame = Frame(
            kind="menu", level=node, prefix=prefix, items=pool, crumb=crumb, pool=pool, rank="menu", pinned=len(first)
        )
        self._rank(frame)
        return frame

    def _suggested_pool(self, item: Item, sentences: list[str] | None) -> list[Item]:
        """Home "Suggested" candidates in their base order: the AI's sentences for right now, the
        patient's most used sentences, then the fixed phrases; each sentence once. Day 1 mode: the
        fixed phrases only."""
        node = item.node
        assert node is not None
        fixed = [self._menu_item(c, item.id) for c in node.children or []]
        if not self.learning:
            return fixed
        ai = [self._sentence(s, i, f"ai:{item.id}", "speak", None) for i, s in enumerate((sentences or [])[:MAX_SENTENCES])]
        history = [
            h
            for n, s in enumerate(self.ranker.index().sentences(self.lang, self.ranker.now(), MAX_ITEMS))
            if (h := self._history_item(s, n)) is not None
        ]
        return _unique(ai + history + fixed, self.lang)

    def _open_suggested(self, item: Item, sentences: list[str] | None) -> None:
        """Home "Suggested": the AI's sentences for right now and the patient's most used sentences,
        then the fixed phrases, best first (learning on). Day 1 mode: the fixed list."""
        node = item.node
        assert node is not None
        pool = self._suggested_pool(item, sentences)
        frame = Frame(
            kind="menu",
            level=node,
            prefix=item.id,
            items=pool,
            crumb=item,
            pool=pool,
            rank="full",
            ai=any(i.text is not None for i in pool),  # AI or history text: in one language only
        )
        self._rank(frame)
        self._push(frame)
        top = frame.items[0] if frame.items else None
        if top is not None and top.kind == "suggestion":
            self._voice.warm(top.phrase(self.lang), self.lang)

    def _history_item(self, s: Sentence, n: int) -> Item | None:
        """A sentence from the history as a Suggested tile. Its action and contact come from the menu
        path it was confirmed under, never from the text: the leaf's, or for an AI option or AI
        sentence below a level, what that level passes on. None when the path left the menu."""
        path = leaf_path(s.node_id)
        nodes = self._menu.chain(path)
        if not nodes:
            return None
        node = nodes[-1]
        if len(nodes) == len(path.split(".")) and node.is_leaf:
            action, contact = node.action, node.contact
            fixed = text_key(node.phrase(self.lang)) == text_key(s.text)
        else:
            action, contact = node.inherited()
            fixed = False
        if fixed:  # the leaf's own phrase keeps the leaf's id
            return Item(kind="suggestion", id=path, event_id=path, text=s.text, action=action, contact=contact)
        return Item(
            kind="suggestion", id=f"ai:{path}.h{n + 1}", event_id=f"ai:{path}", text=s.text, action=action, contact=contact
        )

    def _open_suggestions(self, leaf: Item, sentences: list[str] | None) -> None:
        """The suggestions screen for `leaf`: AI sentences, its fixed phrase, "Other...". No
        sentences (AI off, slow, failed or empty): straight to the confirm screen with the fixed phrase."""
        if not sentences:
            self._confirm(leaf)
            return
        base = leaf_path(leaf.id)
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
        pool = ai + [fixed]
        frame = Frame(
            kind="suggestions",
            level=self.frame.level,
            prefix=leaf.id,
            items=pool,
            crumb=leaf,
            pool=pool,
            rank="full",
            leaf=leaf,
            ai=True,
        )
        self._rank(frame)
        self._push(frame)
        # Only the top sentence is made in advance (ElevenLabs quota); others when confirmed.
        self._voice.warm(frame.items[0].phrase(self.lang), self.lang)

    def _open_other(self, frame: Frame, result: list[Any] | None) -> None:
        """The next "Other..." page for `frame`, or back to the level's own options when there is
        nothing new."""
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
            self._loop_back("nothing new")
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

    def _loop_back(self, reason: str) -> None:
        """"Other..." has shown all it has: back to the level's own options (the page the first
        "Other..." was picked on), from its first tile."""
        while len(self._stack) > 1 and self.frame.via_other:
            self._stack.pop()
        log.info("Other... (%s): back to the options of %s", reason, " > ".join(self._crumbs()) or "home")
        self._enter_frame()

    def _up_one_level(self) -> None:
        """DOUBLE_BLINK: up one menu level. Pages opened with "Other..." belong to the level they came
        from, so they are all left together with it (Home > I need > Other > Other goes to Home)."""
        if self.trip:
            if len(self._trip_path) > 1:
                self._trip_path.pop()
            self._enter_frame()
            return
        while len(self._stack) > 1 and self.frame.via_other:
            self._stack.pop()
        if len(self._stack) > 1:
            self._stack.pop()
        self._enter_frame()

    # --- ranking --------------------------------------------------------------

    def _rank(self, frame: Frame) -> None:
        """Set frame.items (and what is shown) from frame.pool: ranked with learning on, in the pool's
        own order in Day 1 mode."""
        pool = frame.pool
        if self.learning and frame.rank != "none" and pool:
            entries = [self._entry(i, by_text=frame.rank == "full") for i in pool]
            scored = self.ranker.score(entries, self.lang, priors=frame.priors, state_level=self.state_level)
            if frame.rank == "menu":
                order = self.ranker.order_menu(entries, scored, pinned=frame.pinned)
            else:
                order = self.ranker.order_full(entries, scored)
            by_id = {i.id: i for i in pool}
            pool = [by_id[i] for i in order]
        frame.items = pool[:MAX_ITEMS]
        frame.shown = tuple(i.label(self.lang) for i in frame.items)

    def _entry(self, item: Item, *, by_text: bool) -> Entry:
        """How the Ranker looks `item` up: in a sentence list by its sentence, on a menu level by its
        path (a branch collects everything below it)."""
        path = leaf_path(item.event_id if item.kind == "suggestion" else item.id)
        urgent = self._urgent(path)
        sentence = item.kind == "suggestion" or (item.node is not None and item.node.is_leaf) or item.text is not None
        if by_text and sentence:
            return Entry(item.id, text=item.phrase(self.lang), urgent=urgent)
        return Entry(item.id, path=path, urgent=urgent)

    def _jev_on(self) -> bool:
        return self.learning and self.jev is not None and self.jev.available

    def _jev_key(self, items: list[Item]) -> tuple[Any, ...]:
        return (tuple(i.id for i in items), self.ranker.hour(), self.ranker.last_outcome_id(), self.lang)

    def _jev_request(self, items: list[Item], crumbs: list[str] | None = None) -> Pending[JevAnswer]:
        """Jev's probabilities for `items` right now (cached on ids, hour, last outcome, language)."""
        assert self.jev is not None
        criteria = {i.id: i.phrase(self.lang) if i.kind == "suggestion" else i.label(self.lang) for i in items}
        state = self.ranker.jev_state(self.lang, self._crumbs() if crumbs is None else crumbs, self.state_level)
        return self.jev.rank(self._jev_key(items), criteria, state)

    # --- one-clench shortcut ----------------------------------------------------

    def _suggested_branch(self) -> Item | None:
        return next((i for i in self._stack[0].items if i.node is not None and i.node.ai_now), None)

    def _shortcut(self, branch: Item) -> Item | None:
        """The phrase to confirm straight away when Suggested is picked, or None."""
        check = self._shortcut_check(branch)
        log.info("shortcut %s: %s", "on" if check.ok else "off", check.reason)
        return check.top if check.ok else None

    def _shortcut_check(self, branch: Item) -> ShortcutCheck:
        """Is the history's top Suggested phrase (history and fixed phrases, no AI sentences, so the
        guess is the patient's own habit) a confident guess? Only with learning on, and when
          - its history share is >= 0.6 (Ranker.history_confidence), or
          - its history share is >= 0.4 and Jev picks the same phrase with confidence >= 0.45.
        Jev only helps: it never blocks a shortcut the history alone qualifies for (decisions.md
        "Shortcut"). A cancel of the phrase in the last 24 h lowers both numbers as it lowers the
        score. The confirm clench is still required (PRD D5)."""
        if not self.learning:
            return ShortcutCheck(None, 0.0, "off", None, None, False, "learning off (Day 1 mode)")
        pool = self._suggested_pool(branch, None)
        if not pool:
            return ShortcutCheck(None, 0.0, "off", None, None, False, "no Suggested phrases")
        entries = [self._entry(i, by_text=True) for i in pool]
        scored = self.ranker.score(entries, self.lang, state_level=self.state_level)  # history only
        top_id = self.ranker.order_full(entries, scored)[0]
        top = next(i for i in pool if i.id == top_id)
        keep = 1.0 - scored[top_id].reject
        history = self.ranker.history_confidence(top_id, scored) * keep
        jev: JevStatus = "off"
        answer: JevAnswer | None = None
        if self._jev_on():
            assert self.jev is not None
            answer = self.jev.cached(self._jev_key(pool))
            jev = "answered" if answer is not None else "waiting"
        pick = next((i for i in pool if answer is not None and i.id == answer.choice), None)
        confidence = answer.confidence if answer is not None else None
        agrees = answer is not None and answer.choice == top_id
        share = f"history share {history:.2f}"
        if history >= SHORTCUT_HISTORY_SHARE:
            ok, reason = True, f"{share} >= {SHORTCUT_HISTORY_SHARE}"
        elif history < SHORTCUT_JEV_HISTORY_SHARE:
            ok, reason = False, f"{share} < {SHORTCUT_JEV_HISTORY_SHARE}"
        elif answer is None:
            ok = False
            reason = f"{share} < {SHORTCUT_HISTORY_SHARE} and " + ("Jev is off" if jev == "off" else "no Jev answer yet")
        elif not agrees:
            ok, reason = False, f"{share} < {SHORTCUT_HISTORY_SHARE} and Jev picks another phrase"
        elif answer.confidence * keep >= SHORTCUT_JEV_CONFIDENCE:
            ok = True
            reason = (
                f"{share} >= {SHORTCUT_JEV_HISTORY_SHARE} and Jev agrees "
                f"({answer.confidence * keep:.2f} >= {SHORTCUT_JEV_CONFIDENCE})"
            )
        else:
            ok = False
            reason = f"{share} < {SHORTCUT_HISTORY_SHARE} and Jev is unsure ({answer.confidence * keep:.2f} < {SHORTCUT_JEV_CONFIDENCE})"
        return ShortcutCheck(top, history, jev, pick, confidence, ok, reason)

    def _shortcut_debug(self) -> ShortcutDebug:
        """The SHORTCUT_DEBUG line for consoles and the dev panel: the shortcut as it stands now."""
        branch = self._suggested_branch()
        if branch is None:
            check = ShortcutCheck(None, 0.0, "off", None, None, False, "no Suggested tile")
        else:
            check = self._shortcut_check(branch)
        return ShortcutDebug(
            top=check.top.phrase(self.lang) if check.top is not None else None,
            history_share=round(min(max(check.history, 0.0), 1.0), 3),
            jev=check.jev,
            jev_pick=check.jev_pick.phrase(self.lang) if check.jev_pick is not None else None,
            jev_confidence=round(min(max(check.jev_confidence, 0.0), 1.0), 3) if check.jev_confidence is not None else None,
            shortcut=check.ok,
            reason=check.reason,
        )

    def suggested_preview(self) -> tuple[list[str], str | None]:
        """The home Suggested phrases as they rank right now (history and fixed phrases, no AI
        sentences), and the phrase the one-clench shortcut would confirm (None = no shortcut).
        For scripts/seed_demo.py and, later, the caregiver console."""
        branch = self._suggested_branch()
        if branch is None or branch.node is None:
            return [], None
        pool = self._suggested_pool(branch, None)
        frame = Frame(kind="menu", level=branch.node, prefix=branch.id, items=pool, pool=pool, rank="full")
        self._rank(frame)
        top = self._shortcut(branch)
        return [i.phrase(self.lang) for i in frame.items], top.phrase(self.lang) if top is not None else None

    def _prefetch_shortcut(self) -> Pending[JevAnswer] | None:
        """At home, ask Jev about the Suggested phrases now, so the shortcut can use its answer."""
        branch = self._suggested_branch()
        if branch is None or not self._jev_on():
            return None
        pool = self._suggested_pool(branch, None)
        return self._jev_request(pool, [branch.label(self.lang)]) if len(pool) >= 2 else None

    def _announce_shortcut(self, pending: Pending[JevAnswer] | None) -> None:
        """After a Home render: the SHORTCUT_DEBUG line, and again when Jev answers if home is still
        showing."""
        self._emit(self._shortcut_debug())
        if pending is not None and not pending.done:
            home = self._stack[0]

            def arrived(answer: JevAnswer | None) -> None:
                if answer is not None and self._stack == [home] and self.state is SessionState.SCANNING:
                    self._emit(self._shortcut_debug())

            pending.on_done(arrived)

    def _open_suggested_list(self, branch: Item) -> None:
        """The whole Suggested list: with the AI's sentences for right now (waiting for them at most
        4 s), or the fixed list in Day 1 mode."""
        if not self.learning:
            self._open_suggested(branch, None)
            return
        self._wait(self._now_request(), lambda sentences: self._open_suggested(branch, sentences))

    def _ask_jev(self, frame: Frame) -> None:
        """Ask Jev about this frame's candidates, in the background. The scanner never waits: the
        screen shows the history ranking now, and Jev's answer re-ranks it quietly (same highlight
        position, no echo) only if the person has not done anything since. A cached answer is used at
        once, before the screen is drawn."""
        if not self._jev_on() or frame.rank == "none" or len(frame.pool) < 2:
            return
        pending = self._jev_request(frame.pool)
        if pending.done:
            if pending.result is not None:
                frame.priors = dict(pending.result.probabilities)
                self._rank(frame)
            return
        moves = self._moves

        def arrived(answer: JevAnswer | None) -> None:
            if answer is None:
                return
            frame.priors = dict(answer.probabilities)  # kept for when the person comes back here
            if self.frame is not frame or self.state is not SessionState.SCANNING or self._moves != moves:
                return
            before = [i.id for i in frame.items]
            self._rank(frame)
            if [i.id for i in frame.items] != before:
                log.info("Jev re-ranked %s", " > ".join(self._crumbs()) or "home")
                self._emit(self._screen())

        pending.on_done(arrived)

    def _urgent(self, path: str) -> bool:
        """Urgent (pain, bathroom, help): the item, a level above it, or something below it says so."""
        nodes = self._menu.chain(path)
        return any(n.urgent for n in nodes) or (bool(nodes) and nodes[-1].has_urgent)

    def _enter_frame(self, *, first_tile: bool = False) -> None:
        """Show the top frame and prefetch what could be picked next. Scanning starts on the first
        tile with a full scan step; the head keeps its tile until its next POINT, unless `first_tile`
        (RESET) puts every pointer on tile 0."""
        self._close_back()
        self.state = SessionState.SCANNING
        if self.trip or self._question is not None:
            if self.trip:
                self._stack = [self._home()]  # the menus wait underneath, at home
            count = self._tile_count()
            self.pointer.on_tiles_changed(count)
            if first_tile:
                self.pointer.place(count, 0)
            self.pointer.start()
            self._emit(self._screen())
            return
        self._ask_jev(self.frame)  # an answer already cached re-ranks here, before the screen is drawn
        home = len(self._stack) == 1
        shortcut_jev = self._prefetch_shortcut() if home else None
        count = self._tile_count()
        self.pointer.on_tiles_changed(count)
        if first_tile:
            self.pointer.place(count, 0)
        self.pointer.start()  # the scan timer restarts from 0
        self._emit(self._screen())
        if home:
            self._announce_shortcut(shortcut_jev)
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
            if frame.others < OTHER_PAGES:
                self._other_request(frame)  # more sentences: one request
            return
        self.suggester.level_bundle(
            self._ai_path(),
            self.lang,
            leaves=tuple((i.label(self.lang), i.phrase(self.lang)) for i in frame.items if i.kind == "leaf"),
            now=self.learning and any(i.kind == "branch" and i.node is not None and i.node.ai_now for i in frame.items),
            other_shown=frame.shown if frame.others < OTHER_PAGES else None,
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
        self._close_back()  # the help alert wins over a go-back prompt, at once
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
        self._shortcut_from = None
        self._go_home()  # straight home: a system line never holds the session

    def _help_screen(self) -> Screen:
        return Screen(
            screen="help_countdown",
            seq=self._seq,
            tiles=[],
            highlight=None,
            lang=self.lang,
            path=[],
            countdown=self._help_left,
        )

    def _cancel_help_timer(self) -> None:
        if self._help_timer is not None:
            self._help_timer.cancel()
            self._help_timer = None

    # --- settings and pointer -------------------------------------------------

    def _apply_settings(self, s: Settings) -> None:
        mode_changed = s.pointing_mode != self.pointing_mode
        if mode_changed:
            self._switch_pointer(s.pointing_mode)
        self.scan_ms = s.scan_ms
        if s.speak_picks is not None:
            self.speak_picks = s.speak_picks
        if s.long_clench_ms is not None:
            self.long_clench_ms = s.long_clench_ms
        if s.muse_enabled is not None:
            self.muse_enabled = s.muse_enabled
        if s.onboarding is not None:
            self.onboarding = s.onboarding
        if s.tile_switch_margin is not None:
            self.tile_switch_margin = s.tile_switch_margin
        trip_changed = s.trip is not None and s.trip != self.trip
        if s.trip is not None:
            self.trip = s.trip
        layout_changed = s.trip_layout is not None and s.trip_layout != self.trip_layout
        if s.trip_layout is not None:
            self.trip_layout = s.trip_layout
        self.pointer.apply_settings(s)
        lang_changed = s.lang is not None and s.lang != self.lang
        if s.lang is not None:
            self.lang = s.lang
        learning_changed = s.learning is not None and s.learning != self.learning
        if s.learning is not None:
            self.learning = s.learning
            self.suggester.use_history = s.learning
        self._emit(self.settings())  # every client sees the real values, whoever changed them
        if trip_changed:
            self._change_trip()
        elif layout_changed and self.trip and self.state is SessionState.SCANNING:
            self._enter_frame()  # the top level's tiles change with the layout
        elif learning_changed:
            self._change_learning()
        elif lang_changed:
            self._change_language()
        elif mode_changed and self.state is SessionState.SCANNING:
            self._emit(self._screen())  # same tiles; the highlight's source (and badge) changed

    def _switch_pointer(self, mode: PointingMode) -> None:
        """Swap the pointer live, mid-screen: the new one takes over the same tiles with the highlight
        where it was, and runs only if the session is scanning."""
        old = self.pointer
        old.close()
        new = make_pointer(mode, self._scheduler, self._on_highlight, self.scan_ms, self._on_pointer_source)
        new.place(self._tile_count(), old.highlight)
        new.on_face(self.face_ok)
        self.pointer = new
        self.pointing_mode = mode
        log.info("pointing mode %s: highlight from %s", mode, new.source)
        if self.state is SessionState.SCANNING:
            new.start()

    def _change_learning(self) -> None:
        """Day 1 mode on or off. While scanning, the board goes back to home so the before / after
        shows at once; otherwise (confirming, speaking, help) it applies from the next screen."""
        log.info("learning %s", "on" if self.learning else "off: Day 1 mode (menu.yaml order, fixed Suggested list)")
        if self.state in (SessionState.SCANNING, SessionState.LOADING):
            self._cancel_wait()
            self._go_home()
        else:
            self._stack = [self._home()]  # where CONFIRMING's cancel and SPEAKING's end return to

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
            self.pointer.on_tiles_changed(self._tile_count())
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
            if self.pointer.source == "scan":
                self._effort.step()  # waiting through the scan; a head turn is not waiting
            self._emit(self._screen())

    def _on_pointer_source(self) -> None:
        """Auto switched between webcam and scan: redraw so the board shows (or hides) the badge."""
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
        return Tile(id=_join(frame.prefix, "other"), label=OTHER_LABEL[self.lang], kind="other")

    def _screen(self) -> Screen:
        frame = self.frame
        prompt = None
        if self._question is not None:
            q = self._question
            tiles = [Tile(id=f"trip.support.{o.id}", label=o.label, kind="answer") for o in q.options]
            prompt = ("Support asks: " if self.lang == "en" else "Soporte pregunta: ") + q.text
        elif self.trip:
            tiles = self._trip_tiles()
            level = self._trip_path[-1]
            if level.dynamic == "plan":
                prompt = "Plan a trip" if self.lang == "en" else "Planear un viaje"
            elif level.dynamic and level.dynamic.startswith("routes:"):
                prompt = self._routes_prompt(level.dynamic.removeprefix("routes:"))
        else:
            tiles = [Tile(id=i.id, label=i.label(self.lang), kind=i.kind) for i in frame.items]
            tiles.append(self._other_tile(frame))
        corner_tile = self._corner()
        key = tuple((t.id, t.label, t.kind) for t in tiles + ([corner_tile] if corner_tile is not None else []))
        if key != self._tiles_key:  # new tiles: a new seq, so a POINT for the old ones is ignored
            if self.trip:
                # Preserve controls by id when loss of gaze expands Split to six tiles.
                old = self._tiles_key or ()
                index = self.pointer.highlight
                selected = old[index][0] if 0 <= index < len(old) else None
                corner = self._corner()
                ids = [t.id for t in tiles] + ([corner.id] if corner is not None else [])  # the corner is the last index
                self.pointer.place(len(ids), ids.index(selected) if selected in ids else 0)
            self._tiles_key = key
            self._seq += 1
        highlight = self.pointer.highlight
        self._note_highlight(highlight)
        return Screen(
            screen="support_question" if self._question is not None else "trip" if self.trip else "suggestions" if frame.kind == "suggestions" else "menu",
            seq=self._seq,
            tiles=tiles,
            highlight=highlight,
            lang=self.lang,
            path=[] if self._question is not None else [n.label(self.lang) for n in self._trip_path[1:]] if self.trip else self._crumbs(),
            loading=self.state is SessionState.LOADING,
            pointer=self.pointer.source,
            prompt=prompt,
            corner=self._corner(),
        )

    def _note_highlight(self, highlight: int) -> None:
        """Remember when the highlight changed (for the clench look-back), keeping about TRAIL_S of it
        plus the entry still in effect before that."""
        now = self._scheduler.now()
        trail = self._trail
        if not trail or trail[-1][1:] != (self._seq, highlight):
            trail.append((now, self._seq, highlight))
        while len(trail) > 1 and trail[1][0] <= now - TRAIL_S:
            trail.popleft()

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
