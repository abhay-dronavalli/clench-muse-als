"""Playwright adapter. Browser work uses a dedicated loop (Windows uvicorn uses SelectorLoop)."""
from __future__ import annotations

import asyncio
import logging
import json
import sys
from pathlib import Path
from threading import Thread

from core.computer.policy import Policy
from core.computer.search import clean_query

log = logging.getLogger("clench.computer")
ASSETS = Path(__file__).parent
PROFILE = ASSETS.parents[1] / "data" / "browser-profile"
FIELD_INFO = """el => {
  const editable = el.isConnected && !el.readOnly && !el.disabled &&
    (el.matches('textarea,input[type=search],input[type=text],input:not([type])') || el.isContentEditable);
  const label = [el.getAttribute('aria-label'),el.getAttribute('title'),el.getAttribute('placeholder'),
    ...Array.from(el.labels||[]).map(l=>l.textContent)].filter(Boolean).join(' ');
  const search = el.matches('input[type=search],[role=searchbox]') || el.closest('[role=search]') ||
    /\\b(search|buscar|búsqueda|busqueda)\\b/i.test(label) ||
    /^(q|query|search|search_query|search-query)$/i.test(el.name || el.id || '');
  return {editable:!!editable, search:!!search, label};
}"""


class Browser:
    def __init__(self, policy: Policy, on_event, *, headless=False, profile=PROFILE):
        self.policy, self.on_event = policy, on_event
        self.headless, self.profile = headless, profile
        self.context = self.page = self.playwright = None
        self.closing = False
        self.overlay = {}
        self.input_contexts = set()
        self.cdp = None
        self.field = None
        self.field_url = None
        self.script = (ASSETS / "bridge.js").read_text(encoding="utf-8").replace(
            "state = {}", "state = " + json.dumps({"blockedWords": policy.blocked_words}))

    async def open(self):
        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.profile), headless=self.headless, no_viewport=True,
            args=["--start-maximized"], accept_downloads=False, service_workers="block",
        )
        self.context.set_default_timeout(3000)
        await self.context.route("**/*", self._route)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        for page in self.context.pages[1:]:
            await page.close()
        self.context.on("page", lambda page: asyncio.create_task(page.close()))
        self.context.on("close", lambda _: self._closed())
        self.page.on("close", lambda _: self._closed())
        self.page.on("crash", lambda _: self._closed())
        self.page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        self.page.on("download", lambda download: asyncio.create_task(download.cancel()))
        self.page.on("domcontentloaded", lambda _: asyncio.create_task(self._loaded()))
        self.cdp = await self.context.new_cdp_session(self.page)
        self.cdp.on("Runtime.executionContextCreated", self._input_context)
        self.cdp.on("Runtime.executionContextDestroyed", lambda e: self.input_contexts.discard(e["executionContextId"]))
        self.cdp.on("Runtime.executionContextsCleared", lambda _: self.input_contexts.clear())
        self.cdp.on("Runtime.bindingCalled", self._trusted_input)
        await self.cdp.send("Page.enable")
        await self.cdp.send("Runtime.enable")
        await self.cdp.send("Runtime.addBinding", {"name": "clenchTrustedInput", "executionContextName": "clench-input"})
        await self.cdp.send("Page.addScriptToEvaluateOnNewDocument", {
            "source": (ASSETS / "input.js").read_text(encoding="utf-8"), "worldName": "clench-input"})
        await self.page.expose_binding("clenchBridge", self._binding)
        await self.page.add_init_script(script=self.script)
        await self.page.goto(self.policy.start_url, wait_until="domcontentloaded", timeout=15000)

    def _closed(self):
        if not self.closing:
            self.on_event({"kind": "closed"})

    async def _route(self, route):
        request = route.request
        if request.is_navigation_request() and (
            not self.policy.allows_url(request.url) or request.frame.page != self.page
        ):
            self.on_event({"kind": "blocked", "message": "Navigation blocked: this site is not allowed."})
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    async def _binding(self, source, payload):
        # Frames and popups cannot inject patient input into the session.
        if (source["page"] != self.page or source["frame"] != self.page.main_frame
                or not self.policy.allows_url(source["frame"].url) or not isinstance(payload, dict)):
            return
        if payload.get("kind") != "targets":
            return
        self.on_event(payload)

    def _input_context(self, event):
        context = event["context"]
        if context.get("name") == "clench-input" and not context.get("auxData", {}).get("isDefault", True):
            self.input_contexts.add(context["id"])

    def _trusted_input(self, event):
        if event.get("name") != "clenchTrustedInput" or event.get("executionContextId") not in self.input_contexts:
            return
        try:
            payload = json.loads(event["payload"])
            name = payload["event"]
        except (ValueError, KeyError, TypeError):
            return
        if name in ("CLENCH", "DOUBLE_BLINK", "LONG_CLENCH", "DEV_TOGGLE", "RESET"):
            self.on_event({"kind": "input", "event": name})
        elif name == "SETTINGS" and isinstance(payload.get("patch"), dict):
            allowed = {"pointing_mode", "scan_ms", "lang", "speak_picks", "learning", "long_clench_ms", "tile_switch_margin", "muse_enabled"}
            if set(payload["patch"]) <= allowed:
                self.on_event({"kind": "settings", "patch": payload["patch"]})

    async def _loaded(self):
        try:
            if self.page and not self.page.is_closed():
                if not self.policy.allows_url(self.page.url):
                    await self.page.goto(self.policy.start_url, wait_until="domcontentloaded")
                # Also restores the overlay on browser error documents after a failed navigation.
                await self.page.evaluate(self.script)
                await self.render(self.overlay)
        except Exception:
            log.debug("document changed during bridge installation", exc_info=True)

    async def render(self, state):
        self.overlay = state
        if self.cdp:
            for context in list(self.input_contexts):
                try:
                    await self.cdp.send("Runtime.evaluate", {"contextId": context,
                        "expression": "globalThis.clenchInputSettings?.(" + json.dumps({k:state.get(k) for k in
                            ("longClenchMs", "settings", "devOpen", "pointer", "trackingStatus", "faceOk", "help")}) + ")"})
                except Exception:
                    pass  # destroyed document; the next context gets current settings
        if self.page and not self.page.is_closed():
            try:
                tiles = await self.page.evaluate("s => window.__clench?.renderAndMeasure(s)", state)
                if tiles is not None:
                    self.on_event({"kind": "layout", "seq": state.get("seq"), "tiles": tiles})
            except Exception:
                pass  # a navigation installs a fresh bridge and replays the latest overlay

    async def click(self, target_id):
        if not self.page or not self.policy.allows_url(self.page.url):
            return {"error": "Page is not allowed."}
        target = await self.page.evaluate("id => window.__clench?.prepare(id)", target_id)
        if not target or not self.policy.allows_label(target["label"]):
            return {"error": "Target changed. Please choose again."}
        if target["href"] and not self.policy.allows_url(target["href"]):
            return {"error": "Navigation blocked: this site is not allowed."}
        if target["exit"] and self.page.url.split("#")[0] != self.policy.start_url:
            return {"error": "Exit is available in Browser menu."}
        if self.field:
            await self.field.dispose()
            self.field = None
        if target["text"]:
            self.field = (await self.page.evaluate_handle("id => window.__clench?.field(id)", target_id)).as_element()
            self.field_url = self.page.url
            if not await self._search_field():
                return {"error": "Choose a search box. Other text editors are not supported."}
        await self.page.mouse.click(target["x"] + target["width"] / 2,
                                    target["y"] + target["height"] / 2)
        if target["exit"]:
            self.on_event({"kind": "exit"})
        return {"text": target["text"]}

    async def submit(self, query):
        text = clean_query(query)
        if not text or not self.policy.allows_label(text):
            return {"error": "Use search words only, without URLs or blocked actions."}
        if not self.field or self.page.url != self.field_url or not self.policy.allows_url(self.page.url):
            return {"error": "The search field changed. Cancel and choose it again."}
        if not await self._search_field():
            return {"error": "This field cannot accept a search. Cancel and choose another."}
        await self.field.fill(text)
        if self.page.url != self.field_url or not await self._search_field():
            return {"error": "The page changed before submission. Choose the search field again."}
        await self.field.press("Enter")
        return {"submitted": True}

    async def _search_field(self):
        if not self.field:
            return False
        info = await self.field.evaluate(FIELD_INFO)
        return info["editable"] and info["search"] and self.policy.allows_label(info["label"])

    async def command(self, key):
        if key in ("down", "up"):
            await self.page.evaluate("d => window.scrollBy(0, d * innerHeight * .8)", 1 if key == "down" else -1)
        elif key == "back":
            await self.page.go_back(wait_until="domcontentloaded", timeout=8000)
        elif key == "home":
            await self.page.goto(self.policy.start_url, wait_until="domcontentloaded", timeout=8000)

    async def close(self):
        self.closing = True
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.playwright:
                await self.playwright.stop()
            self.context = self.page = self.playwright = None


class BrowserWorker:
    """Only Playwright runs on this loop; all patient state and callbacks run on the core loop."""
    def __init__(self, policy, on_event, **browser_options):
        core_loop = asyncio.get_running_loop()
        self.loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        self.browser = Browser(policy, lambda event: core_loop.call_soon_threadsafe(on_event, event), **browser_options)
        self.thread = Thread(target=self.loop.run_forever, name="clench-browser", daemon=True)
        self.thread.start()
        self.closed = False

    async def _call(self, method, *args):
        if self.closed:
            return None
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(getattr(self.browser, method)(*args), self.loop))

    async def open(self):
        return await self._call("open")

    async def render(self, state):
        return await self._call("render", state)

    async def click(self, target_id):
        return await self._call("click", target_id)

    async def submit(self, query):
        return await self._call("submit", query)

    async def command(self, key):
        return await self._call("command", key)

    async def close(self):
        if self.closed:
            return
        try:
            await self._call("close")
        finally:
            self.closed = True
            self.loop.call_soon_threadsafe(self.loop.stop)
            await asyncio.to_thread(self.thread.join, 5)
            if not self.thread.is_alive():
                self.loop.close()
