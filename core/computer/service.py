"""Nonblocking computer mode coordinator; help never waits for a browser operation."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from core.computer.browser import BrowserWorker
from core.computer.model import MENU, Selection, Target
from core.computer.policy import Policy
from core.computer.search import SearchPanel, unique_queries, fallback_queries, site_for, clean_query
from core.contracts import Clench, DoubleBlink, LongClench, Settings, Reset, Point, ComputerState, ComputerTile, ComputerControl
from core.pointer import make_pointer
from core.pointer.scan import ScanPointer

log = logging.getLogger("clench.computer")


class Computer:
    def __init__(self, scheduler, on_exit, echo, on_input, *, start_url="http://127.0.0.1:8000/computer/start",
                 browser_factory=BrowserWorker, suggester=None, history=None, ranker=None, emit=lambda _: None, lookback_ms=250):
        self.policy = Policy.load(start_url)
        self.on_exit, self.echo, self.on_input = on_exit, echo, on_input
        self.browser_factory = browser_factory
        self.selection = Selection()
        self.scan = ScanPointer(scheduler, self._tick)
        self.browser = None
        self.active = self.ready = self.busy = False
        self.help = None
        self.long_clench_ms = 2500
        self.message = ""
        self.document = self.url = None
        self.generation = 0
        self.tasks = set()
        self.open_task = None
        self.close_task = None
        self.search = None
        self.lang = "es"
        self.suggester = suggester
        self.history, self.ranker = history, ranker
        self.learning = True
        self.submit_task = None
        self.render_task = None
        self.pending_render = None
        self.scheduler, self.emit = scheduler, emit
        self.pointer = self.scan
        self.pointer_running = False
        self.mode = "scan"
        self.settings = Settings(pointing_mode="scan", scan_ms=1000)
        self.dev_open = False
        self.face_ok = False
        self.tracking_status = "off"
        self.watchdog = None
        self.seq = 0
        self.layout_key = None
        self.rects = []
        self.last_view = None
        self.lookback = lookback_ms / 1000
        self.trail = deque(maxlen=100)
        self.telemetry = None
        self.calibrating = False

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def start(self, settings):
        if self.active:
            return
        self.active = True
        self.generation += 1
        self.selection = Selection()
        self.document = self.url = None
        self.help = None
        self.search = None
        self.dev_open = False
        self.calibrating = False
        self.face_ok = False
        self.pointer.on_face(False)
        self.layout_key = None
        self.message = "Opening browser…"
        self.configure(settings)
        self.open_task = self._spawn(self._open(self.generation))

    async def _open(self, generation):
        try:
            if self.close_task:
                await asyncio.shield(self.close_task)
            if not self.active or generation != self.generation:
                return
            self.browser = self.browser_factory(self.policy, lambda e: self._event(e) if generation == self.generation else None)
            await asyncio.wait_for(self.browser.open(), 25)
            if self.active and generation == self.generation:
                self.ready = True
                self.message = ""
                self._refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("computer browser could not start; returning to Home")
            self.exit()

    def configure(self, settings):
        self.scan.apply_settings(settings)
        self.settings = Settings(**{**self.settings.model_dump(), **settings.model_dump(exclude_none=True)})
        if settings.pointing_mode != self.mode:
            if self.watchdog:
                self.watchdog.cancel()
                self.watchdog = None
            self.pointer.close()
            self.pointer_running = False
            self.mode = settings.pointing_mode
            self.pointer = make_pointer(self.mode, self.scheduler, self._tick, settings.scan_ms, self._source_changed)
            if self.mode == "scan":
                self.scan = self.pointer
            self.face_ok = False
            self.tracking_status = "starting" if self.mode in ("auto", "webcam", "gaze") else "off"
            self.trail.clear()
        self.pointer.apply_settings(settings)
        lang_changed = settings.lang is not None and settings.lang != self.lang
        self.lang = settings.lang or self.lang
        if settings.learning is not None:
            self.learning = settings.learning
            if self.suggester:
                self.suggester.use_history = settings.learning
        self.long_clench_ms = settings.long_clench_ms or self.long_clench_ms
        if self.active:
            if lang_changed and self.search and not self.busy:
                self.search = SearchPanel(self._queries(), self.lang)
                self.selection.index = 0
                self._prefetch(self.search.shown)
            self._refresh(restart=lang_changed)

    def _event(self, event):
        if not self.active:
            return
        kind = event.get("kind")
        if kind == "closed":
            self.exit()
        elif kind == "exit" and self.help is None:
            self.exit()
        elif kind == "blocked":
            self.message = event["message"]
            self._render()
        elif kind == "input":
            name = event.get("event")
            if name == "DEV_TOGGLE":
                self.dev_open = not self.dev_open
                self._refresh(restart=True)
            elif name == "RESET":
                self.on_input(Reset())
            elif name == "CLENCH":
                if self.dev_open and self.help is None:
                    self.dev_open = False
                    self._refresh(restart=True)
                self.on_input(Clench(t=0, strength=1))
            elif name == "DOUBLE_BLINK":
                self.on_input(DoubleBlink(t=0))
            elif name == "LONG_CLENCH":
                self.on_input(LongClench(t=0, duration=self.long_clench_ms / 1000))
        elif kind == "settings":
            try:
                self.on_input(Settings(**{**self.settings.model_dump(), **event["patch"]}))
            except (ValueError, TypeError, KeyError):
                log.warning("invalid Chromium settings ignored")
        elif kind == "control":
            command = ComputerControl(**event["control"])
            if command.action in ("calibrate", "calibration_done"):
                self.calibrating = command.action == "calibrate"
                self._refresh()
            self.emit(command)
        elif kind == "layout":
            self._layout(event)
        elif kind == "targets":
            try:
                if self.dev_open and self.url == event.get("url") and self.document == event.get("documentId"):
                    return  # controls hidden by the caregiver panel have not disappeared from the page
                targets = [Target.parse(t) for t in event["targets"][:2000]]
                targets = [t for t in targets if self.policy.allows_label(t.label)]
                navigation = self.document != event["documentId"] or self.url != event["url"]
                self.document, self.url = event["documentId"], event["url"]
                previous_level = self.selection.level
                if navigation:
                    self.search = None
                    self._prefetch()
                if not self.search:
                    self.selection.update(targets, max(1, float(event["height"])), navigation=navigation)
                self._refresh(restart=navigation or previous_level != self.selection.level)
            except (ValueError, TypeError, KeyError):
                log.warning("invalid computer target snapshot ignored")

    def _tick(self, index):
        self.selection.index = index
        self.trail.append((self.scheduler.now(), self.seq, index))
        self._render()

    def _source_changed(self):
        self.selection.index = self.pointer.highlight
        self.trail.clear()
        self.trail.append((self.scheduler.now(), self.seq, self.selection.index))
        self._render()

    def _stop_pointer(self):
        self.pointer.stop()
        self.pointer_running = False

    def on_point(self, msg):
        if not self.active or msg.seq != self.seq or not -1 <= time.time() - msg.t <= 2:
            return
        if self.mode not in ("auto", msg.source):
            return
        if msg.tile is not None and msg.tile >= len(self.rects):
            return
        if self.watchdog:
            self.watchdog.cancel()
        self.watchdog = self.scheduler.call_later(1, self._tracking_lost)
        changed = (self.face_ok, self.tracking_status) != (msg.found, msg.status)
        self.face_ok, self.tracking_status = msg.found, msg.status
        self.pointer.on_face(msg.found)
        if msg.found and msg.tile is not None and not self.view().paused:
            self.pointer.on_point(Point(source=msg.source, tile=msg.tile, seq=msg.seq, t=msg.t))
        if changed:
            self._render()
        if msg.pick and msg.source == "gaze" and msg.found and msg.tile is not None and not self.view().paused:
            self.on_input(Clench(t=msg.t, strength=1))

    def _tracking_lost(self):
        self.watchdog = None
        self.face_ok = False
        self.tracking_status = "no_tracker" if self.mode == "gaze" else "lost"
        self.pointer.on_face(False)
        self._render()

    def on_telemetry(self, msg):
        if self.active:
            self.telemetry = msg.model_dump()
            self._render()

    def view(self):
        return ComputerState(active=self.active, seq=self.seq, tiles=self.rects if self.active else [],
                             highlight=self.selection.index if self.rects and self.active else None,
                             paused=not self.ready or self.busy or self.help is not None or self.dev_open or self.calibrating,
                             pointer=self.pointer.source)

    def _layout(self, event):
        if event.get("seq") != self.seq:
            return
        try:
            rects = [ComputerTile(**r) for r in event["tiles"]]
            if [(r.id, r.label) for r in rects] != self.items():
                return
            if rects != self.rects:
                if self.rects:
                    self.seq += 1
                    self.trail.clear()
                self.rects = rects
                self._render()
        except (ValueError, TypeError, KeyError):
            log.debug("invalid Chromium layout ignored")

    def _refresh(self, *, restart=False):
        key = (tuple(self.items()), self.document, self.url, self.help is not None, self.busy, self.dev_open,
               self.search.mode if self.search else self.selection.level)
        if key != self.layout_key:
            self.layout_key = key
            self.seq += 1
            self.rects = []
            self.trail.clear()
        self.pointer.place(len(self.items()), self.selection.index)
        if self.ready and not self.busy and self.help is None and not self.dev_open and not self.calibrating:
            if restart or not self.pointer_running:
                self.pointer.start()
                self.pointer_running = True
        else:
            self._stop_pointer()
        self._render()

    def items(self):
        return self.search.items() if self.search else self.selection.items()

    def _render(self):
        if not self.browser or not self.active:
            return
        items = self.items()
        view = self.view()
        if view != self.last_view:
            self.last_view = view
            self.emit(view)
        state = dict(level=self.search.mode if self.search else self.selection.level,
                     telemetry=self.telemetry,
                     seq=self.seq, settings=self.settings.model_dump(), devOpen=self.dev_open,
                     pointer=self.pointer.source, trackingStatus=self.tracking_status, faceOk=self.face_ok,
                     lang=self.lang, searchPage=self.search.page if self.search else 0,
                     draft=self.search.keyboard.draft if self.search else "",
                     keyboardRow=self.search.keyboard.row if self.search else None,
                     band=self.selection.band, bands=list(self.selection.bands),
                     groups=self.selection.groups(), page=self.selection.page, busy=self.busy,
                     items=items, selected=items[self.selection.index][0], help=self.help,
                     message=self.message, blockedWords=self.policy.blocked_words, longClenchMs=self.long_clench_ms)
        self.pending_render = (self.browser, state)
        if self.render_task is None:
            self.render_task = self._spawn(self._render_latest())

    async def _render_latest(self):
        # A quick sequence of edits must not queue stale highlights behind the live selection.
        try:
            while self.pending_render:
                browser, state = self.pending_render
                self.pending_render = None
                if browser is self.browser and self.active:
                    await browser.render(state)
        except Exception:
            log.debug("computer overlay render interrupted", exc_info=True)
        finally:
            self.render_task = None

    def pick(self):
        if not self.active or not self.ready or self.busy or self.help is not None:
            return
        if self.dev_open or self.calibrating:
            return
        if self.pointer.source in ("webcam", "gaze") and not self.face_ok:
            return
        if self.pointer.source == "webcam" and self.lookback:
            cutoff = self.scheduler.now() - self.lookback
            older = [index for at, seq, index in self.trail if seq == self.seq and at <= cutoff]
            if older:
                self.selection.index = older[-1]
        if self.search:
            self._pick_search()
            return
        key, label = self.selection.pick()
        self.echo(label)
        self.message = ""
        if key == "exit":
            self.exit()
        elif key:
            self.busy = True
            self._stop_pointer()
            self._render()
            self._spawn(self._act(key, self.generation, self.document, self.url))
        else:
            self._refresh(restart=True)

    async def _act(self, key, generation, document, url):
        try:
            if self.help is not None or not self.active or generation != self.generation:
                return
            if key in {k for k, _ in MENU}:
                await asyncio.wait_for(self.browser.command(key), 9)
                if generation == self.generation:
                    self.selection.back_to_bands()
            else:
                result = await asyncio.wait_for(self.browser.click(key), 4)
                if generation != self.generation or not self.active:
                    return
                if result and result.get("error"):
                    self.message = result["error"]
                    self.selection.back_to_bands()
                elif result and result.get("text") and (document, url) == (self.document, self.url):
                    self.selection.level, self.selection.index = "text", 0
                    self.search = SearchPanel(self._queries(), self.lang)
                    self._prefetch(self.search.shown)
        except Exception:
            log.exception("computer action failed")
            self.message = "Page did not respond. Use Browser menu to try again or exit."
            self.selection.back_to_bands()
        finally:
            if self.active and generation == self.generation:
                self.busy = False
                self._refresh()

    def back(self):
        if self.dev_open and self.help is None:
            self.dev_open = False
            self._refresh(restart=True)
            return
        if self.active and not self.busy and self.help is None:
            if self.search:
                if self.search.mode == "keyboard":
                    self.search.mode = "search"
                else:
                    self.search = None
                    self.selection.back()
                self.selection.index = 0
            else:
                self.selection.back()
            self._refresh(restart=True)

    def _pick_search(self):
        key, label = self.items()[self.selection.index]
        self.message = ""
        if self.search.mode == "keyboard":
            query = self.search.keyboard.pick(key)
            self.selection.index = 0
            if query is not None:
                self._begin_search(query)
            else:
                self.echo(label)
                self._refresh(restart=True)
            return
        self.echo(label)
        if key == "cancel":
            self.back()
        elif key == "other":
            new_page = self.search.page == len(self.search.pages) - 1 and len(self.search.pages) < 3
            self.search.more(self._queries(self.search.shown) if new_page else [])
            if len(self.search.pages) < 3:
                self._prefetch(self.search.shown)
            self.selection.index = 0
            self._refresh(restart=True)
        elif key == "keyboard":
            self.search.mode = "keyboard"
            self.search.keyboard.row = None
            self.search.keyboard.candidates = unique_queries(list(self.search.shown) + self._queries(), self.policy)
            self.selection.index = 0
            self._refresh(restart=True)
        elif key.startswith("query:"):
            self._begin_search(label, echo=False)

    def _begin_search(self, query, *, echo=True):
        query = clean_query(query)
        if not query or not self.policy.allows_label(query):
            self.message = "Usa palabras de búsqueda, sin enlaces ni acciones bloqueadas." if self.lang == "es" else "Use search words, without links or blocked actions."
            self._refresh(restart=True)
            return
        if echo:
            self.echo(query)
        self.busy = True
        self._stop_pointer()
        self._render()
        self.submit_task = self._spawn(self._submit_search(query, self.generation, site_for(self.url or ""), self.lang))

    async def _submit_search(self, query, generation, site, lang):
        try:
            if self.help is not None or not self.active or generation != self.generation:
                return
            result = await asyncio.wait_for(self.browser.submit(query), 5)
            if generation != self.generation or not self.active:
                return
            if result and result.get("error"):
                self.message = result["error"]
            elif result and result.get("submitted"):
                if self.history and site:
                    try:
                        self.history.log_search(site, query, lang)
                    except Exception:
                        log.exception("could not save computer search history")
                self.search = None
                self.selection.back_to_bands()
            else:
                self.message = "Search was not submitted. Try again or cancel."
        except Exception:
            log.exception("computer search failed")
            self.message = "Search could not be submitted. Try again or cancel."
        finally:
            if generation == self.generation and self.active:
                self.busy = False
                self._refresh(restart=True)

    def _prefetch(self, shown=()):
        site = site_for(self.url or "")
        if self.suggester and site:
            recent = self.history.recent_searches(site, self.lang) if self.history and self.learning else ()
            return self.suggester.search_suggestions(site, self.lang, shown=shown, recent_searches=recent)
        return None

    def _queries(self, shown=()):
        pending = self._prefetch(shown)
        values = pending.result if pending and pending.done and pending.result else []
        site = site_for(self.url or "") or "google"
        rows = self.history.searches(site, self.lang, self.ranker.now() - 30 * 86400) if self.history and self.ranker and self.learning else []
        queries = unique_queries(values + fallback_queries(site, self.lang) + [row["query"] for row in rows], self.policy)
        return self.ranker.order_searches(queries, rows) if self.ranker and self.learning else queries

    def set_help(self, seconds):
        self.help = seconds
        if seconds is not None and self.submit_task and not self.submit_task.done():
            self.submit_task.cancel()
        self._refresh()

    def exit(self):
        if not self.active:
            return
        self.active = self.ready = self.busy = False
        self.pending_render = None
        self.search = None
        if self.submit_task and not self.submit_task.done():
            self.submit_task.cancel()
        self.generation += 1
        self.pointer.close()
        self.pointer_running = False
        if self.watchdog:
            self.watchdog.cancel()
            self.watchdog = None
        self.emit(self.view())
        browser, self.browser = self.browser, None
        if self.open_task and not self.open_task.done() and self.open_task != asyncio.current_task():
            self.open_task.cancel()
        if browser:
            self.close_task = self._spawn(browser.close())
        self.on_exit()

    async def aclose(self):
        self.exit()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
