"""Real board-to-core-to-Chromium relay with simulated gaze; no eye tracker or network required."""
import asyncio
import time
import os
import subprocess
from functools import partial
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

from core.computer.browser import Browser
from core.contracts import Clench, Point, Settings
from core.main import create_app
from tests.test_computer_browser import until


def test_board_gaze_reaches_chromium_and_backtick_panel(tmp_path):
    subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "--prefix", "web", "run", "build"], check=True, timeout=90)
    async def run():
        app=create_app(env={"ELEVENLABS_PREWARM":"false","COMPUTER_START_URL":"http://127.0.0.1:8001/computer/start"},
                       audio_dir=tmp_path / "audio",lang="en")
        @app.middleware("http")
        async def strict_site(request, call_next):
            response=await call_next(request)
            if request.url.path=="/computer/start":
                response.headers["Content-Security-Policy"]="require-trusted-types-for 'script'; trusted-types 'none'; connect-src 'none'"
            return response
        # Production assets avoid depending on a second server or on the user's open board.
        app.mount("/",StaticFiles(directory=Path("web/dist"),html=True))
        server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=8001,log_level="warning"))
        task=asyncio.create_task(server.serve())
        board_browser=None
        try:
            await until(lambda: server.started)
            session=app.state.session
            session.handle(Settings(pointing_mode="gaze",scan_ms=500,speak_picks=False))
            c=session.computer
            c.browser_factory=partial(Browser,headless=True,profile=tmp_path / "managed")
            async with async_playwright() as pw:
                board_browser=await pw.chromium.launch(headless=True)
                board=await board_browser.new_page()
                await board.goto("http://127.0.0.1:8001")
                await board.get_by_role("button",name="Clench Click to start Turns on speech. Haga clic para empezar.").click()
                await until(lambda: app.state.session.current_view().screen=="menu")
                await asyncio.sleep(.2)
                screen=session.current_view()
                index=next(i for i,tile in enumerate(screen.tiles) if tile.id=="computer")
                session.handle(Point(source="gaze",tile=index,seq=screen.seq,t=time.time()))
                await asyncio.sleep(.3)
                session.handle(Clench(t=time.time(),strength=1))
                await until(lambda: c.ready and bool(c.rects))
                page=c.browser.page
                await page.set_viewport_size({"width":1000,"height":700})
                await page.evaluate("document.querySelector('a').addEventListener('click',e=>{e.preventDefault();window.picked=e.isTrusted;})")
                await asyncio.sleep(.3)
                band=next(b for b,targets in c.selection.bands.items() if any(t.label=="YouTube" for t in targets))

                async def gaze_at(key):
                    await until(lambda: any(t.id==key for t in c.rects))
                    rect=next(r for r in c.rects if r.id==key)
                    await board.evaluate("""p=>{clearInterval(window.gazeTestTimer);window.gazeTestTimer=setInterval(()=>
                      window.clenchGaze.feed({...p,found:true,confidence:1}),40)}""",
                                        {"x":(rect.left+rect.right)/2,"y":(rect.top+rect.bottom)/2})
                    await until(lambda:c.items()[c.selection.index][0]==key and c.face_ok)
                    await asyncio.sleep(.4)
                await gaze_at(f"band:{band}")
                before=c.seq
                await gaze_at("menu")
                assert c.seq==before  # highlight styling must not move its own targets
                await gaze_at(f"band:{band}")
                rects=list(c.rects)
                await page.evaluate("p=>window.clenchBridge(p)",{"kind":"layout","seq":c.seq,
                    "tiles":[dict(r.model_dump(),left=0,top=0,right=1,bottom=1) for r in rects]})
                await asyncio.sleep(.1)
                assert c.rects==rects  # the site cannot replace driver-measured pointing rectangles
                await page.keyboard.press("Space")
                await until(lambda:c.selection.level=="targets")
                youtube=next(t for t in c.selection.bands[c.selection.band] if t.label=="YouTube")
                await gaze_at(youtube.id)
                await page.keyboard.press("Space")
                await page.wait_for_function("window.picked===true")
                assert c.pointer.source=="gaze"

                # Trusted keyboard toggles the actual isolated-world Dev panel.
                await page.keyboard.press("Backquote")
                await until(lambda:c.dev_open and not c.pointer_running)
                await c.browser.cdp.send("DOM.enable")
                def walk(node):
                    yield node
                    for child in node.get("children",[])+node.get("shadowRoots",[]):
                        yield from walk(child)
                async def panel():
                    async with asyncio.timeout(5):
                        while True:
                            tree=await c.browser.cdp.send("DOM.getDocument",{"depth":-1,"pierce":True})
                            host=next((n for n in walk(tree["root"]) if "clench-dev" in n.get("attributes",[])),None)
                            if host and any(n["nodeName"]=="ASIDE" for n in walk(host)):
                                return host
                            await asyncio.sleep(.02)
                host=await panel()
                async def click_node(node):
                    box=await c.browser.cdp.send("DOM.getBoxModel",{"nodeId":node["nodeId"]})
                    q=box["model"]["content"]
                    await page.mouse.click((q[0]+q[2])/2,(q[1]+q[5])/2)
                def toggle(label):
                    row=next(n for n in walk(host) if n["nodeName"]=="DIV" and any(
                        k["nodeName"]=="SPAN" and any(t.get("nodeValue")==label for t in k.get("children",[])) for k in n.get("children",[])))
                    return next(n for n in row["children"] if n["nodeName"]=="BUTTON")
                await click_node(toggle("Dwell select (gaze)"))
                await until(lambda: c.telemetry and c.telemetry["dwell"])
                await click_node(toggle("Cursor dot"))
                await until(lambda: not c.telemetry["show_cursor"])
                await asyncio.sleep(1.6)
                assert c.selection.level=="targets"  # open panel pauses gaze dwell
                await page.keyboard.press("Backquote")
                await until(lambda:not c.dev_open)
                await page.keyboard.press("KeyB")
                await until(lambda:c.selection.level=="bands")
                await gaze_at("menu")
                await until(lambda:c.selection.level=="menu")  # existing dwell setting now works in Chromium
                await page.keyboard.press("Backquote")
                await until(lambda:c.dev_open)
                host=await panel()
                scan=next(n for n in walk(host) if n["nodeName"]=="BUTTON" and any(k.get("nodeValue")=="Scan" for k in n.get("children",[])))
                await click_node(scan)
                await until(lambda:c.mode=="scan" and session.pointing_mode=="scan")
                await page.screenshot(path=str(tmp_path / "chromium-dev-panel.png"))
                await page.keyboard.press("Backquote")
                await until(lambda:not c.dev_open and c.pointer_running)
                # A page script cannot forge a dev toggle or settings change.
                await page.evaluate("""() => {
                  dispatchEvent(new KeyboardEvent('keydown',{code:'Backquote'}));
                  window.clenchBridge({kind:'settings',patch:{pointing_mode:'gaze'}});
                }""")
                await asyncio.sleep(.1)
                assert not c.dev_open and c.mode=="scan"
                context=next(iter(c.browser.input_contexts))
                await c.browser.cdp.send("Runtime.evaluate",{"contextId":context,"expression":"globalThis.clenchCalibrating=true"})
                await page.keyboard.down("Space")
                await until(lambda:c.help is not None)
                await page.keyboard.up("Space")
                await page.keyboard.press("Escape")
                await until(lambda:c.help is None and not c.calibrating)
                await board.evaluate("clearInterval(window.gazeTestTimer)")
                await board_browser.close()
                board_browser=None
        finally:
            if board_browser:
                await board_browser.close()
            server.should_exit=True
            await asyncio.wait_for(task,10)
    asyncio.run(run())
