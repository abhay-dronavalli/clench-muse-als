"""Nonblocking computer mode coordinator; help never waits for a browser operation."""
from __future__ import annotations

import asyncio
import logging

from core.computer.browser import BrowserWorker
from core.computer.model import MENU, Selection, Target
from core.computer.policy import Policy
from core.contracts import Clench, DoubleBlink, LongClench
from core.pointer.scan import ScanPointer

log = logging.getLogger("clench.computer")


class Computer:
    def __init__(self, scheduler, on_exit, echo, on_input, *, start_url="http://127.0.0.1:8000/computer/start",
                 browser_factory=BrowserWorker):
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
        self.long_clench_ms = settings.long_clench_ms or self.long_clench_ms
        if self.active:
            self._render()

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
            if name == "CLENCH":
                self.on_input(Clench(t=0, strength=1))
            elif name == "DOUBLE_BLINK":
                self.on_input(DoubleBlink(t=0))
            elif name == "LONG_CLENCH":
                self.on_input(LongClench(t=0, duration=self.long_clench_ms / 1000))
        elif kind == "targets":
            try:
                targets = [Target.parse(t) for t in event["targets"][:2000]]
                targets = [t for t in targets if self.policy.allows_label(t.label)]
                navigation = self.document != event["documentId"] or self.url != event["url"]
                self.document, self.url = event["documentId"], event["url"]
                previous_level = self.selection.level
                self.selection.update(targets, max(1, float(event["height"])), navigation=navigation)
                self._refresh(restart=navigation or previous_level != self.selection.level)
            except (ValueError, TypeError, KeyError):
                log.warning("invalid computer target snapshot ignored")

    def _tick(self, index):
        self.selection.index = index
        self._render()

    def _refresh(self, *, restart=False):
        self.scan.place(len(self.selection.items()), self.selection.index)
        if self.ready and not self.busy and self.help is None:
            if restart or not self.scan.running:
                self.scan.start()
        else:
            self.scan.stop()
        self._render()

    def _render(self):
        if not self.browser or not self.active:
            return
        items = self.selection.items()
        state = dict(level=self.selection.level, band=self.selection.band, bands=list(self.selection.bands),
                     groups=self.selection.groups(), page=self.selection.page, busy=self.busy,
                     items=items, selected=items[self.selection.index][0], help=self.help,
                     message=self.message, blockedWords=self.policy.blocked_words, longClenchMs=self.long_clench_ms)
        self._spawn(self.browser.render(state))

    def pick(self):
        if not self.active or not self.ready or self.busy or self.help is not None:
            return
        key, label = self.selection.pick()
        self.echo(label)
        self.message = ""
        if key == "exit":
            self.exit()
        elif key:
            self.busy = True
            self.scan.stop()
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
        except Exception:
            log.exception("computer action failed")
            self.message = "Page did not respond. Use Browser menu to try again or exit."
            self.selection.back_to_bands()
        finally:
            if self.active and generation == self.generation:
                self.busy = False
                self._refresh()

    def back(self):
        if self.active and not self.busy and self.help is None:
            self.selection.back()
            self._refresh(restart=True)

    def set_help(self, seconds):
        self.help = seconds
        self._refresh()

    def exit(self):
        if not self.active:
            return
        self.active = self.ready = self.busy = False
        self.generation += 1
        self.scan.stop()
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
