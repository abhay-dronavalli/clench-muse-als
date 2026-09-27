"""Computer selection and session safety with a fake browser; never launches Chromium."""
import asyncio
from functools import partial

import pytest

from core.clock import ManualScheduler
from core.computer.model import Selection, Target, reading_order, split_bands
from core.computer.policy import Policy
from core.computer.service import Computer
from core.contracts import Clench, DoubleBlink, LongClench, Reset, Screen, Settings, Speak, ActionResult
from core.menu import load_menu
from core.profile import load_profile
from core.session import Session, SessionState


def target(i, x=10, y=100):
    return Target(str(i), f"Target {i}", x, y, 40, 20)


def test_bands_and_reading_order():
    targets = [target(1, 200, 20), target(2, 10, 23), target(3, 5, 270), target(4, 5, 999)]
    assert [t.id for t in reading_order(targets)] == ["2", "1", "3", "4"]
    bands = split_bands(targets, 1000)
    assert list(bands) == [0, 1, 3]
    assert [t.id for t in bands[0]] == ["2", "1"]


def test_video_card_controls_stay_in_the_same_band():
    targets = [Target("thumbnail", "Video", 10, 110, 300, 280, band_y=250),
               Target("menu", "Action menu", 600, 100, 40, 40, band_y=250),
               target("search", 300, 10)]
    bands = split_bands(targets, 800)
    assert set(t.id for t in bands[1]) == {"thumbnail", "menu"}
    assert [t.id for t in bands[0]] == ["search"]


def test_bands_targets_pagination_back_and_menu():
    s = Selection()
    s.update([target(i, x=i * 50) for i in range(17)], 1000)
    assert s.items() == [("band:0", "Group 1"), ("menu", "Browser menu")]
    assert s.pick() == ("", "Group 1")
    assert len(s.items()) == 8 and s.items()[-1][0] == "more"
    s.index = 7
    s.pick()
    assert s.page == 1 and s.items()[0][0] == "7"
    s.index = 7
    s.pick()
    assert s.page == 2 and s.items()[0][0] == "14"
    s.index = 3
    s.pick()
    assert s.page == 0
    s.back()
    assert s.level == "bands"
    s.back()
    assert s.level == "bands"
    s.tick()
    s.pick()
    assert s.level == "menu" and len(s.items()) == 5
    s.index = 4
    assert s.pick() == ("exit", "Exit")


def test_snapshot_preserves_identity_and_navigation_resets():
    s = Selection()
    s.update([target(1), target(2, 80)], 1000)
    s.pick()
    s.index = 1
    s.update([target(0, 0), target(1), target(2, 80)], 1000)
    assert s.items()[s.index][0] == "2"
    s.update([target(1)], 1000)
    assert s.level == "bands"  # disappearing selection never silently picks a neighbor
    s.pick()
    s.update([target(1)], 1000, navigation=True)
    assert s.level == "bands" and s.index == 0


def test_live_target_survives_band_and_pagination_changes():
    s = Selection()
    s.update([target(i, i * 50) for i in range(10)], 1000)
    s.pick()
    s.index = 6
    s.update([target(-1, 0)] + [target(i, (i + 1) * 50) for i in range(10)], 1000)
    assert s.page == 1 and s.items()[s.index][0] == "6"
    s.update([target(6, 300, 600)], 1000)
    assert s.level == "targets" and s.band == 2 and s.items()[s.index][0] == "6"
    assert s.groups() == {"2": ["6"]}
    s.back()
    assert s.items()[0] == ("band:2", "Group 1")


@pytest.mark.parametrize("url", ["https://youtube.com/watch?v=a", "https://www.youtube.com/", "https://accounts.google.com/", "https://open.spotify.com/", "http://127.0.0.1:8001/computer/start#x"])
def test_allowed_domains(url):
    assert Policy.load("http://127.0.0.1:8001/computer/start").allows_url(url)


@pytest.mark.parametrize("url", ["https://youtube.com.evil.test", "https://evilyoutube.com", "https://youtube.com@evil.test", "http://youtube.com", "javascript:alert(1)", "file:///x", "https://accounts.spotify.com", "http://127.0.0.1:8001/health", "http://127.0.0.1:8000/computer/start", "https://youtube.com:444/", "https://youtube.com:bad/", "http://127.0.0.1:8001/computer/start?redirect=evil"])
def test_blocked_domains(url):
    assert not Policy.load("http://127.0.0.1:8001/computer/start").allows_url(url)


@pytest.mark.parametrize("label", ["Buy now", "SUBSCRIBE", "Delete video", "Purchase", "Pay now", "Comprar", "Pagar"])
def test_blocked_labels(label):
    assert not Policy.load("http://localhost/computer/start").allows_label(label)


class FakeBrowser:
    def __init__(self, policy, on_event):
        self.on_event = on_event
        self.closed = False
        self.clicks = []
        self.overlays = []
        self.result = {}

    async def open(self):
        self.on_event({"kind": "targets", "documentId": "doc1", "url": "http://127.0.0.1:8000/computer/start",
                       "height": 1000, "targets": [vars(target(1)), vars(target(2, 80))]})

    async def close(self):
        self.closed = True

    async def render(self, state):
        self.overlays.append(state)

    async def click(self, key):
        self.clicks.append(key)
        return self.result

    async def command(self, key):
        self.clicks.append(key)


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


def make(browser=FakeBrowser):
    menu = load_menu()
    clock, sent = ManualScheduler(start=100), []
    session = Session(menu, sent.append, clock, profile=load_profile(menu.contacts), lang="en", pointing_mode="scan",
                      computer_factory=partial(Computer, browser_factory=browser))
    session.start()
    clock.advance(4)
    session.handle(Clench(t=0, strength=1))
    return session, clock, sent


def test_computer_help_cancel_fire_and_exit_preserve_session():
    async def run():
        s, clock, sent = make()
        await settle()
        c = s.computer
        assert s.state is SessionState.COMPUTER
        assert s.current_view().screen == "computer" and not s.current_view().tiles
        assert not s.pointer.running
        clock.advance(.35)
        s.handle(Clench(t=0, strength=1))
        assert c.selection.level == "targets"
        s.handle(LongClench(t=0, duration=2.5))
        assert s.state is SessionState.HELP_COUNTDOWN and c.help == 5 and not c.scan.running
        clock.advance(2)
        s.handle(Reset())
        assert s.state is SessionState.HELP_COUNTDOWN
        s.handle(DoubleBlink(t=0))
        assert s.state is SessionState.COMPUTER and c.selection.level == "targets" and c.help is None
        s.handle(LongClench(t=0, duration=2.5))
        clock.advance(5)
        await settle()
        assert s.state is SessionState.COMPUTER and c.selection.level == "targets"
        assert {m.action for m in sent if isinstance(m, ActionResult)} == {"place_call", "send_message"}
        assert any(isinstance(m, Speak) and m.kind == "system" for m in sent)
        s.handle(DoubleBlink(t=0))
        assert c.selection.level == "bands"
        c.selection.index = len(c.selection.items()) - 1
        c.pick()
        c.selection.index = 4
        browser = c.browser
        c.pick()
        await settle()
        assert s.state is SessionState.SCANNING and browser.closed and s.highlight == 0
        assert s.current_view().path == []
        await c.aclose()
        s.stop()
    asyncio.run(run())


def test_window_close_does_not_cancel_help_and_reset_closes_browser():
    async def run():
        s, clock, sent = make()
        await settle()
        s.handle(LongClench(t=0, duration=3))
        s.computer._event({"kind": "closed"})
        assert s.state is SessionState.HELP_COUNTDOWN
        clock.advance(5)
        await settle()
        assert s.state is SessionState.SCANNING
        assert len([m for m in sent if isinstance(m, ActionResult)]) == 2
        await s.computer.aclose()
        s.stop()
        s, clock, sent = make()
        await settle()
        b = s.computer.browser
        s.handle(Reset())
        await settle()
        assert b.closed and s.state is SessionState.SCANNING
        await s.computer.aclose()
        s.stop()
    asyncio.run(run())


def test_text_cancel_echo_toggle_settings_and_frequent_dom_updates():
    async def run():
        s, clock, sent = make()
        await settle()
        c = s.computer
        c.pick()
        c.browser.result = {"text": True}
        c.pick()
        await settle()
        assert c.selection.level == "text" and c.items()[-1] == ("cancel", "Cancel")
        c.selection.index = len(c.items()) - 1
        c.pick()
        assert c.selection.level == "targets"
        s.handle(Settings(pointing_mode="webcam", scan_ms=500, speak_picks=False, long_clench_ms=1000))
        before = len([m for m in sent if isinstance(m, Speak)])
        c.back()
        c.pick()
        assert len([m for m in sent if isinstance(m, Speak)]) == before
        assert c.scan.scan_ms == 500 and c.long_clench_ms == 1000
        clock.advance(1)
        assert c.selection.index == 0 and not c.scan.running  # webcam now waits for a real point
        s.handle(Settings(pointing_mode="scan", scan_ms=500))
        for _ in range(6):
            clock.advance(.1)
            c._refresh()
        assert c.selection.index == 1  # target refreshes cannot starve the scan timer
        await c.aclose()
        s.stop()
    asyncio.run(run())


@pytest.mark.parametrize("failure", [RuntimeError("missing Chromium"), TimeoutError("launch timed out")])
def test_launch_failure_returns_home(failure):
    class Broken(FakeBrowser):
        async def open(self):
            raise failure
    async def run():
        s, _, _ = make(Broken)
        await settle()
        assert s.state is SessionState.SCANNING and not s.computer.active
        await s.computer.aclose()
        s.stop()
    asyncio.run(run())


def test_browser_timeout_cannot_block_help():
    class Slow(FakeBrowser):
        async def click(self, key):
            await asyncio.sleep(60)
    async def run():
        s, clock, _ = make(Slow)
        await settle()
        s.computer.pick()
        s.computer.pick()
        await settle()
        assert s.computer.browser.overlays[-1]["busy"] is True
        s.handle(LongClench(t=0, duration=3))
        clock.advance(5)
        assert s.state is SessionState.COMPUTER
        # Cancel the outstanding fake operation when closing, without waiting for its timeout.
        for task in list(s.computer.tasks):
            task.cancel()
        await s.computer.aclose()
        s.stop()
    asyncio.run(run())


def test_restart_waits_for_profile_release_and_ignores_old_browser():
    async def run():
        release = asyncio.Event()
        opened = []
        class SlowClose(FakeBrowser):
            async def open(self):
                opened.append(self)
                await super().open()
            async def close(self):
                await release.wait()
                await super().close()
        s, _, _ = make(SlowClose)
        await settle()
        c, first = s.computer, opened[0]
        c.exit()
        c.start(s.settings())
        await settle()
        assert len(opened) == 1
        first.on_event({"kind": "closed"})
        assert c.active
        release.set()
        await settle()
        assert len(opened) == 2 and first.closed and c.ready
        await c.aclose()
        s.stop()
    asyncio.run(run())
