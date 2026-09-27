"""Chromium integration on the local launcher. Network smoke test is opt-in."""
import asyncio
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from core.computer.browser import ASSETS, Browser
from core.computer.model import Selection, Target
from core.computer.policy import Policy

URL = "http://127.0.0.1:8001/computer/start"


@pytest.fixture
def launcher():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/computer/start":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # Match the constraint that breaks innerHTML-based overlays on YouTube.
            self.send_header("Content-Security-Policy", "require-trusted-types-for 'script'")
            self.end_headers()
            self.wfile.write((ASSETS / "start.html").read_bytes())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 8001), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield URL
    server.shutdown()
    server.server_close()
    thread.join()


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.02)


def test_launcher_scan_real_click_discovery_and_policy(launcher, tmp_path):
    async def run():
        events = []
        browser = Browser(Policy.load(launcher), events.append, headless=True, profile=tmp_path / "profile")
        try:
            await browser.open()
            await browser.render({"blockedWords": browser.policy.blocked_words, "longClenchMs": 1000})
            await until(lambda: any(e.get("targets") for e in events))
            snapshot = next(e for e in reversed(events) if e.get("targets"))
            selection = Selection()
            selection.update([Target.parse(t) for t in snapshot["targets"]], snapshot["height"])
            youtube = next(t for t in snapshot["targets"] if t["label"] == "YouTube")
            band = next(b for b, targets in selection.bands.items() if any(t.id == youtube["id"] for t in targets))
            while selection.items()[selection.index][0] != f"band:{band}":
                selection.tick()
            selection.pick()
            while selection.items()[selection.index][0] != youtube["id"]:
                selection.tick()
            key, label = selection.pick()
            assert label == "YouTube"
            await browser.render({"level": selection.level, "band": selection.band,
                                  "groups": selection.groups(),
                                  "bands": list(selection.bands), "items": selection.items(),
                                  "selected": key, "blockedWords": browser.policy.blocked_words})
            await browser.page.screenshot(path=str(tmp_path / "computer-overlay.png"))
            # Capture the real trusted mouse click without contacting YouTube.
            await browser.page.evaluate("""() => {
              document.querySelector('a').addEventListener('click', e => {
                e.preventDefault(); window.clicked = {trusted:e.isTrusted, label:e.currentTarget.textContent};
              });
            }""")
            await browser.click(key)
            assert await browser.page.evaluate("window.clicked") == {"trusted": True, "label": "YouTube"}
            assert await browser.page.locator("#clench-overlay").count() == 1
            assert await browser.page.evaluate("""() => {
              const original = window.__clench.prepare;
              try { window.__clench.prepare = () => ({label:'Safe',x:1,y:1,width:10,height:10}); } catch {}
              try { window.__clench = {prepare: () => null}; } catch {}
              return window.__clench.prepare === original;
            }""")

            # Visible field is selectable; blocked and covered controls are not.
            await browser.page.evaluate("""() => {
              const add = (tag, label, left, top) => {
                const e = document.createElement(tag); e.setAttribute('aria-label',label);
                Object.assign(e.style,{position:'fixed',left:left+'px',top:top+'px',width:'150px',height:'40px',minHeight:'0'});
                document.body.append(e); return e;
              };
              add('button','Subscribe now',10,90);
              add('button','Covered button',200,90);
              const cover=add('div','Cover',200,90); cover.style.zIndex='100'; cover.style.background='black';
              add('input','Search videos',400,90);
            }""")
            await until(lambda: any(any(t["label"] == "Search videos" for t in e.get("targets", [])) for e in events))
            latest = next(e for e in reversed(events) if e.get("targets"))
            labels = [t["label"] for t in latest["targets"]]
            assert "Subscribe now" not in labels and "Covered button" not in labels
            field = next(t for t in latest["targets"] if t["label"] == "Search videos")
            assert (await browser.click(field["id"]))["text"]
            assert await browser.page.evaluate("document.activeElement.getAttribute('aria-label')") == "Search videos"
            # Final action validation catches a label changed after target discovery.
            await browser.page.evaluate("document.querySelector('a').setAttribute('aria-label','Buy now')")
            assert "error" in await browser.click(key)
            await browser.page.evaluate("document.querySelector('a').removeAttribute('aria-label')")
            await browser.page.evaluate("document.querySelector('a').href='https://example.com/'")
            assert "blocked" in (await browser.click(key))["error"].lower()
            # The network navigation guard also catches script navigations and redirects.
            await browser.page.evaluate("location.href='https://example.com/'")
            await until(lambda: any(e.get("kind") == "blocked" for e in events))
            # Recovery must restore the managed launcher automatically, without a mouse or keyboard.
            await until(lambda: any(e.get("url") == launcher and e.get("documentId") != snapshot["documentId"] for e in events))
            assert browser.page.url == launcher
            # Keyboard events in foreground Chromium drive the same event types.
            await browser.render({"longClenchMs": 1000, "blockedWords": browser.policy.blocked_words})
            count = len(events)
            await browser.page.evaluate("""() => {
              window.clenchBridge({kind:'input',event:'LONG_CLENCH'});
              dispatchEvent(new KeyboardEvent('keydown', {code:'Space'}));
              dispatchEvent(new KeyboardEvent('keyup', {code:'Space'}));
            }""")
            await asyncio.sleep(.05)
            assert not any(e.get("kind") == "input" for e in events[count:])
            assert await browser.page.evaluate("typeof window.clenchTrustedInput") == "undefined"
            await browser.page.keyboard.press("Space")
            await browser.page.keyboard.press("b")
            await browser.page.keyboard.down("Space")
            await until(lambda: any(e.get("event") == "LONG_CLENCH" for e in events))
            await browser.page.keyboard.up("Space")
            assert {e.get("event") for e in events} >= {"CLENCH", "DOUBLE_BLINK", "LONG_CLENCH"}
        finally:
            await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize("size", [(1000, 650), (800, 480), (390, 650)])
def test_overlay_layout_and_stable_dom(launcher, tmp_path, size):
    async def run():
        events = []
        browser = Browser(Policy.load(launcher), events.append, headless=True, profile=tmp_path / "layout")
        try:
            await browser.open()
            await browser.page.set_viewport_size({"width": size[0], "height": size[1]})
            # Test-only access to the otherwise closed shadow root; production stays closed.
            await browser.page.add_init_script("""const attach = Element.prototype.attachShadow;
              Element.prototype.attachShadow = function(options) {
                const root = attach.call(this, options);
                if (this.id === 'clench-overlay') window.overlayUnderTest = root;
                return root;
              };""")
            await browser.page.reload()
            assert await browser.page.evaluate("() => !!window.overlayUnderTest")
            await until(lambda: any(e.get("targets") for e in events))
            await browser.page.evaluate("window.__clench.discover()")
            await asyncio.sleep(.1)
            snapshot = next(e for e in reversed(events) if e.get("targets"))
            selection = Selection()
            selection.update([Target.parse(t) for t in snapshot["targets"]], snapshot["height"])

            async def render():
                await browser.render(dict(level=selection.level, band=selection.band,
                    groups=selection.groups(), items=selection.items(),
                    selected=selection.items()[selection.index][0]))
                await browser.page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")

            await render()
            await browser.page.evaluate("""() => {
              window.overlayNodes = [...window.overlayUnderTest.querySelectorAll('*')];
              window.overlayAdds = 0;
              new MutationObserver(records => { window.overlayAdds += records.reduce((n,r)=>n+[...r.addedNodes].filter(e=>e.nodeType===1).length,0); })
                .observe(window.overlayUnderTest,{subtree:true,childList:true});
            }""")
            selection.pick()
            await render()
            geometry = await browser.page.evaluate("""() => {
              const r = window.overlayUnderTest;
              return {outline:r.querySelector('.outline').getBoundingClientRect().toJSON(),
                      dock:r.querySelector('.dock').getBoundingClientRect().toJSON()};
            }""")
            target = next(t for t in snapshot["targets"] if t["id"] == selection.items()[selection.index][0])
            outline, dock = geometry["outline"], geometry["dock"]
            assert outline["top"] <= target["y"] and outline["bottom"] >= target["y"] + target["height"]
            assert dock["top"] >= outline["bottom"] or dock["bottom"] <= outline["top"]
            for _ in range(8):
                selection.tick()
                await render()
            assert await browser.page.evaluate("window.overlayAdds") == 0
            assert await browser.page.evaluate("window.overlayNodes.every(e => e.isConnected)")
            selection.back()
            selection.index = len(selection.items()) - 1
            selection.pick()
            await render()
            assert await browser.page.evaluate("""() => {
              const r=window.overlayUnderTest, p=r.querySelector('.panel').getBoundingClientRect();
              const options=[...r.querySelectorAll('.option')].filter(e=>e.style.display!=='none');
              return p.top>=0 && p.bottom<=innerHeight && options.length===5 &&
                options.every(e=>e.getBoundingClientRect().bottom<=p.bottom);
            }""")
            # Controls fixed at the bottom remain discoverable; the dock moves away.
            await browser.page.evaluate("""() => {
              const button=document.createElement('button'); button.textContent='Bottom control';
              Object.assign(button.style,{position:'fixed',bottom:'8px',left:'20px',height:'44px',minHeight:'0',width:'180px',fontSize:'18px'});
              document.body.append(button);window.__clench.discover();
            }""")
            await until(lambda: any(any(t["label"] == "Bottom control" for t in e.get("targets", [])) for e in events))
            snapshot = next(e for e in reversed(events) if e.get("targets"))
            button = next(t for t in snapshot["targets"] if t["label"] == "Bottom control")
            await browser.render(dict(level="targets", band=3, groups={"3": [button["id"]]},
                                      items=[[button["id"], button["label"]]], selected=button["id"]))
            await browser.page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            assert await browser.page.evaluate("() => window.overlayUnderTest.querySelector('.dock').getBoundingClientRect().top === 0")
            assert await browser.page.evaluate("window.__clench.prepare", button["id"]) is not None
        finally:
            await browser.close()
    asyncio.run(run())


def test_video_discovery_labels_duplicates_and_final_policy(launcher, tmp_path):
    async def run():
        events = []
        browser = Browser(Policy.load(launcher), events.append, headless=True, profile=tmp_path / "video")
        try:
            await browser.open()
            await browser.page.set_viewport_size({"width": 1000, "height": 700})
            await browser.page.evaluate("""() => {
              document.body.replaceChildren();
              const card=document.createElement('ytd-video-renderer');
              const thumbnail=document.createElement('a');thumbnail.id='thumbnail';
              thumbnail.setAttribute('aria-hidden','true'); // YouTube hides this duplicate from screen readers.
              thumbnail.href='https://www.youtube.com/watch?v=test&tracking=one';thumbnail.setAttribute('aria-label','true');
              Object.assign(thumbnail.style,{position:'fixed',left:'20px',top:'100px',width:'400px',height:'220px',minHeight:'0'});
              const title=document.createElement('a');title.id='video-title';title.textContent='A peaceful forest';
              title.href='https://www.youtube.com/watch?v=test&tracking=two';
              Object.assign(title.style,{position:'fixed',left:'450px',top:'100px',width:'450px',height:'60px',minHeight:'0'});
              const wrapper=document.createElement('ytd-thumbnail');wrapper.append(thumbnail);
              card.append(wrapper,title);document.body.append(card);window.__clench.discover();
            }""")
            await until(lambda: any(any(t["label"] == "A peaceful forest" for t in e.get("targets", [])) for e in events))
            snapshot = next(e for e in reversed(events) if e.get("targets"))
            assert len(snapshot["targets"]) == 1
            video = snapshot["targets"][0]
            assert video["label"] == "A peaceful forest" and video["band_y"] == 210
            # A friendly title must never hide a denied action label, even after discovery.
            await browser.page.evaluate("document.querySelector('#thumbnail').setAttribute('aria-label','Buy now')")
            assert "error" in await browser.click(video["id"])
            await browser.page.evaluate("window.__clench.discover()")
            await asyncio.sleep(.1)
            latest = next(e for e in reversed(events) if e.get("kind") == "targets")
            assert video["id"] not in [t["id"] for t in latest["targets"]]
            # A blocked title is also checked through the thumbnail's associated title.
            await browser.page.evaluate("""() => {
              document.querySelector('#thumbnail').setAttribute('aria-label','true');
              document.querySelector('#video-title').setAttribute('aria-label','Subscribe now');
              window.__clench.discover();
            }""")
            await asyncio.sleep(.1)
            latest = next(e for e in reversed(events) if e.get("kind") == "targets")
            assert latest["targets"] == []
        finally:
            await browser.close()
    asyncio.run(run())


def test_worker_and_controller_round_trip(launcher, tmp_path):
    from functools import partial
    from core.clock import ManualScheduler
    from core.computer.browser import BrowserWorker
    from core.computer.service import Computer
    from core.contracts import Settings
    async def run():
        exited, echo, inputs = [], [], []
        computer = Computer(ManualScheduler(), lambda: exited.append(True), echo.append, inputs.append,
                            start_url=launcher, browser_factory=partial(BrowserWorker, headless=True, profile=tmp_path / "worker-profile"))
        try:
            computer.start(Settings(pointing_mode="scan", scan_ms=1000))
            await until(lambda: computer.ready and bool(computer.selection.bands))
            computer.pick()
            assert computer.selection.level == "targets"
            computer.set_help(5)
            assert not computer.scan.running
            computer.set_help(None)
            assert computer.scan.running
            computer.back()
            computer.selection.index = len(computer.selection.items()) - 1
            computer.pick()
            computer.selection.index = 4
            computer.pick()
            assert exited == [True]
        finally:
            await computer.aclose()
        assert computer.close_task.done()
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get("CLENCH_NETWORK_TESTS") != "1", reason="real network smoke test is opt-in")
def test_youtube_network(launcher, tmp_path):
    async def run():
        events = []
        browser = Browser(Policy.load(launcher), events.append, headless=True, profile=tmp_path / "profile")
        try:
            await browser.open()
            await browser.page.goto("https://www.youtube.com/", wait_until="domcontentloaded", timeout=20000)
            await until(lambda: any(e.get("url", "").startswith("https://www.youtube.com") and e.get("targets") for e in events))
        finally:
            await browser.close()
    asyncio.run(run())
