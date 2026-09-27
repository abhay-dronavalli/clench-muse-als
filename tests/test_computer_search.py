import asyncio

import pytest

from core.computer.search import SearchPanel, clean_query
from tests.test_computer import FakeBrowser, make, settle
from core.computer.search import fallback_queries, unique_queries
from core.computer.policy import Policy
from core.suggest.fake import FakeProvider
from core.suggest.provider import SearchSuggestions
from core.suggest.service import Suggester
from core.suggest.errors import ProviderError
from core.db import Db
from core.rank import Ranker
from core.contracts import Settings


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
    computer.url = "https://www.youtube.com/"
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


@pytest.mark.parametrize("site", ["youtube", "spotify", "google"])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_fallbacks_are_safe_and_fill_three_pages(site, lang):
    values = fallback_queries(site, lang)
    assert len(unique_queries(values, Policy.load("http://localhost/computer/start"))) == len(values) >= 15


def test_provider_validation_and_cache_exclusions_privacy():
    assert SearchSuggestions(queries=["https://youtube.com", "Celia Cruz", "celia cruz", "", 42]).queries == ["Celia Cruz"]
    async def run():
        provider = FakeProvider()
        service = Suggester(provider, patient_name="Luis", local_hour=lambda: 19)
        first = service.search_suggestions("youtube", "es", recent_searches=("boleros",))
        assert service.search_suggestions("youtube", "es", recent_searches=("boleros",)) is first
        await settle()
        assert first.done and len(first.result) == 5
        cached = service.search_suggestions("youtube", "es", recent_searches=("boleros",))
        assert cached.done and len(provider.calls) == 1
        other = service.search_suggestions("youtube", "es", shown=tuple(first.result))
        await settle()
        assert not set(other.result) & set(first.result)
        ctx = provider.calls[0][1]
        assert set(vars(ctx)) == {"site", "lang", "hour", "patient_name", "recent_searches", "top_phrases", "shown"}
        assert ctx.recent_searches == ("boleros",) and ctx.hour == 19
        service.use_history = False
        service.search_suggestions("youtube", "es", recent_searches=("private query",))
        await settle()
        assert provider.calls[-1][1].recent_searches == ()
        await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "key", "offline"])
def test_search_failure_resolves_to_fallback_and_is_cached(failure):
    class Broken(FakeProvider):
        async def search_suggestions(self, ctx):
            self.calls.append(("search_suggestions", ctx))
            if failure == "timeout":
                await asyncio.sleep(60)
            raise ProviderError("unavailable", pause_s=300 if failure == "key" else None)
    async def run():
        provider = Broken()
        service = Suggester(provider, patient_name="Luis", timeout=.01)
        pending = service.search_suggestions("google", "es")
        await asyncio.sleep(.03)
        assert pending.done and pending.result is None
        assert service.search_suggestions("google", "es").done
        assert len(provider.calls) == 1
        await service.aclose()
    asyncio.run(run())


def test_late_ai_does_not_replace_visible_choices():
    async def run():
        session, _, _ = await open_panel()
        computer = session.computer
        provider = FakeProvider()
        service = Suggester(provider, patient_name="Luis")
        computer.suggester = service
        before = computer.items()
        computer._prefetch()
        await settle()
        assert computer.items() == before
        await computer.aclose()
        await service.aclose()
        session.stop()
    asyncio.run(run())


def test_gemini_search_uses_structured_output_without_tools():
    import json
    from types import SimpleNamespace
    from core.suggest.gemini import GeminiProvider
    from core.suggest.provider import SearchContext
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text='{"queries":["Celia Cruz","https://evil.test"]}')
    async def run():
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
        provider = GeminiProvider("test", client=client)
        result = await provider.search_suggestions(SearchContext("youtube", "es", 20, "Luis"))
        assert result.queries == ["Celia Cruz"]
        assert json.loads(calls[0]["contents"])["site"] == "youtube"
        assert calls[0]["config"].automatic_function_calling.disable is True
        assert calls[0]["config"].response_schema.model_fields.keys() == {"queries"}
    asyncio.run(run())


def test_learning_uses_existing_score_with_site_language_and_time():
    now = 2_000_000_000
    db = Db(":memory:", clock=lambda: now)
    db.sync_profile("Luis", "es", [])
    for _ in range(4):
        db.log_search("youtube", "Celia Cruz", "es", hour=19)
    db.log_search("youtube", "boleros", "es", hour=9)
    ranker = Ranker(db, clock=lambda: now, local_hour=lambda _: 19)
    rows = db.searches("youtube", "es")
    assert ranker.order_searches(["boleros", "Celia Cruz", "Miami Heat"], rows)[0] == "Celia Cruz"
    assert db.searches("spotify", "es") == db.searches("youtube", "en") == []
    assert db.top_phrases("es", 20) == [] and db.events() == []
    from core.rank.score import Weights
    time_ranker = Ranker(db, weights=Weights(use=0, time=1, state=0, ai=0, reject=0),
                         clock=lambda: now, local_hour=lambda _: 9)
    assert time_ranker.order_searches(["Celia Cruz", "boleros"], rows)[0] == "boleros"
    db.clear_history()
    assert db.searches("youtube", "es") == []
    db.close()


def test_only_successful_search_is_logged_and_day_one_ignores_history():
    async def run():
        db = Db(":memory:")
        db.sync_profile("Luis", "en", [])
        db.log_search("youtube", "orchids", "en")
        for fail in (True, False):
            class Adapter(SearchBrowser):
                async def submit(self, query):
                    return {"error": "detached"} if fail else await super().submit(query)
            session, _, _ = await open_panel(Adapter)
            c = session.computer
            c.history, c.ranker = db, Ranker(db)
            c.configure(Settings(pointing_mode="scan", scan_ms=1000, learning=True))
            assert c._queries()[0] == "orchids"
            c.configure(Settings(pointing_mode="scan", scan_ms=1000, learning=False))
            assert "orchids" not in c._queries()
            query = c.items()[0][1]
            before = len(db.searches("youtube", "en"))
            c.pick()
            await settle()
            assert len(db.searches("youtube", "en")) == before + (not fail)
            if not fail:
                assert query in db.recent_searches("youtube", "en")
            await c.aclose()
            session.stop()
        db.close()
    asyncio.run(run())


def test_language_change_refreshes_panel_and_launcher_does_not_prefetch():
    async def run():
        session, _, _ = await open_panel()
        c = session.computer
        provider = FakeProvider()
        service = Suggester(provider, patient_name="Luis")
        c.suggester = service
        c.configure(Settings(pointing_mode="scan", scan_ms=1000, lang="es"))
        assert c.items()[-3:] == [("other", "Otro..."), ("keyboard", "Teclado"), ("cancel", "Cancelar")]
        assert c.selection.index == 0
        await settle()
        before = len(provider.calls)
        for url in (c.policy.start_url, "https://accounts.google.com/", "https://mail.google.com/"):
            c.url = url
            c._prefetch()
        await settle()
        assert len(provider.calls) == before
        await c.aclose()
        await service.aclose()
        session.stop()
    asyncio.run(run())
