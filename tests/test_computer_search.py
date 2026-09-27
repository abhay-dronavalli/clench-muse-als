import asyncio

import pytest

from core.computer.search import SearchPanel, clean_query
from tests.test_computer import FakeBrowser, make, settle


def test_panel_order_and_three_page_loop():
    queries = [f"Song {i}" for i in range(15)]
    panel = SearchPanel(queries, "es")
    assert [text for _, text in panel.items()] == queries[:5] + ["Otro...", "Teclado", "Cancelar"]
    panel.more(queries)
    assert panel.pages[panel.page] == queries[5:10]
    panel.more(queries)
    assert panel.pages[panel.page] == queries[10:]
    panel.more(queries)
    assert panel.page == 0
    panel.more(queries)
    assert panel.page == 1 and len(panel.pages) == 3


@pytest.mark.parametrize("query", ["https://youtube.com", "google.com", "www.example.com", "javascript:alert(1)", "foo/bar", "a" * 41, ""])
def test_search_rejects_urls_and_invalid_text(query):
    assert clean_query(query) is None


class SearchBrowser(FakeBrowser):
    async def submit(self, query):
        self.clicks.append(("submit", query))
        return {"submitted": True}


async def open_panel(browser=SearchBrowser):
    session, clock, sent = make(browser)
    await settle()
    computer = session.computer
    computer.pick()
    computer.browser.result = {"text": True}
    computer.pick()
    await settle()
    assert computer.search is not None
    return session, clock, sent


def test_pick_submits_cancel_and_back_do_not():
    async def run():
        for cancel in ("back", "cancel", "pick"):
            session, _, _ = await open_panel()
            computer = session.computer
            query = computer.items()[0][1]
            if cancel == "back":
                computer.back()
            else:
                computer.selection.index = len(computer.items()) - 1 if cancel == "cancel" else 0
                computer.pick()
            await settle()
            assert computer.search is None
            assert (("submit", query) in computer.browser.clicks) == (cancel == "pick")
            assert computer.selection.level == ("bands" if cancel == "pick" else "targets")
            await computer.aclose()
            session.stop()
    asyncio.run(run())


def test_help_interrupts_pending_search_and_preserves_panel():
    class Slow(SearchBrowser):
        async def submit(self, query):
            await asyncio.sleep(60)
            return await super().submit(query)

    async def run():
        session, _, _ = await open_panel(Slow)
        computer = session.computer
        computer.pick()
        await settle()
        computer.set_help(5)
        await settle()
        assert computer.search is not None and not computer.busy and not computer.scan.running
        assert not any(isinstance(c, tuple) for c in computer.browser.clicks)
        computer.set_help(None)
        assert computer.scan.running
        await computer.aclose()
        session.stop()
    asyncio.run(run())
