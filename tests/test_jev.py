"""Jev prior: request shape for both access paths, parsing, timeout, cache, breaker, and the session's
quiet re-rank. No real network: httpx.MockTransport stands in for TypeSafe and Cloudflare."""

import asyncio
import json

import httpx
import pytest

from core.clock import ManualScheduler
from core.contracts import Clench, DoubleBlink, Screen
from core.menu import load_menu
from core.profile import load_profile
from core.rank.jev import (
    CLOUDFLARE_MODEL,
    QUESTION,
    CloudflareAccess,
    JevRanker,
    TypeSafeAccess,
    build_jev,
    jev_access,
)
from core.session import Session
from tests.gestures import go_back

CRITERIA = {"suggested.water": "I'd like some water, please.", "people.maria.text": "Honey, I'm okay, call me at six."}


def reply(probs: dict[str, float], choice: str, confidence: float = 0.9, *, envelope: bool = False) -> dict:
    body = {
        "model": "jev-1.13.0",
        "answers": {"next": {"type": "choice", "choice": choice, "probabilities": probs, "confidence": confidence}},
        "usage": {"input_tokens": 120, "output_tokens": 0},
    }
    return {"result": body, "success": True, "errors": [], "messages": []} if envelope else body


class Loop:
    def __init__(self):
        self.jobs = []

    def spawn(self, coro):
        self.jobs.append(coro)

    def run(self):
        while self.jobs:
            jobs, self.jobs = self.jobs, []
            for job in jobs:
                asyncio.run(job)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(access, handler, *, timeout=1.5):
    requests = []

    async def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        result = handler(request)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    loop, clock = Loop(), Clock()
    jev = JevRanker(access, spawn=loop.spawn, clock=clock, transport=httpx.MockTransport(record), timeout=timeout)
    return jev, loop, clock, requests


GOOD = lambda request: httpx.Response(200, json=reply({"suggested.water": 0.2, "people.maria.text": 0.8}, "people.maria.text"))  # noqa: E731


def test_typesafe_request_shape():
    jev, loop, _, requests = make(TypeSafeAccess("ts-key"), GOOD)
    p = jev.rank(("k",), CRITERIA, "Now: Saturday 18:00")
    loop.run()
    (req,) = requests
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
    assert req.headers["authorization"] == "Bearer ts-key"
    body = json.loads(req.content)
    assert body["model"] == "jev-latest" and body["state"] == "Now: Saturday 18:00"
    assert body["questions"] == {"next": {"type": "choice", "instructions": QUESTION, "criteria": CRITERIA}}
    assert p.result.choice == "people.maria.text" and p.result.confidence == 0.9
    assert p.result.probabilities == {"suggested.water": 0.2, "people.maria.text": 0.8}


def test_cloudflare_request_shape_and_envelope():
    handler = lambda request: httpx.Response(200, json=reply({"suggested.water": 0.7, "people.maria.text": 0.3}, "suggested.water", envelope=True))  # noqa: E731
    jev, loop, _, requests = make(CloudflareAccess("acct123", "cf-token"), handler)
    p = jev.rank(("k",), CRITERIA, "state")
    loop.run()
    (req,) = requests
    assert str(req.url) == "https://api.cloudflare.com/client/v4/accounts/acct123/ai/run"
    assert req.headers["authorization"] == "Bearer cf-token"
    body = json.loads(req.content)
    assert body == {"model": CLOUDFLARE_MODEL, "input": {"state": "state", "questions": {"next": {"type": "choice", "instructions": QUESTION, "criteria": CRITERIA}}}}
    assert p.result.choice == "suggested.water"
    # Without the envelope too (as the Cloudflare model page shows it).
    handler2 = lambda request: httpx.Response(200, json=reply({"suggested.water": 0.4, "people.maria.text": 0.6}, "people.maria.text"))  # noqa: E731
    jev, loop, _, _ = make(CloudflareAccess("a", "t"), handler2)
    p = jev.rank(("k",), CRITERIA, "state")
    loop.run()
    assert p.result.choice == "people.maria.text"


def test_access_order_from_env():
    assert jev_access({})[0] is None
    assert isinstance(jev_access({"TYPESAFE_API_KEY": "k", "CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "t"})[0], TypeSafeAccess)
    assert isinstance(jev_access({"CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "t"})[0], CloudflareAccess)
    assert jev_access({"CLOUDFLARE_ACCOUNT_ID": "a"})[0] is None  # both are needed
    jev, why = build_jev({})
    assert jev is None and "TYPESAFE_API_KEY" in why


def test_unknown_ids_are_dropped_and_bad_answers_fail_softly():
    handler = lambda request: httpx.Response(200, json=reply({"suggested.water": 1.4, "made.up": 0.5}, "made.up"))  # noqa: E731
    jev, loop, _, _ = make(TypeSafeAccess("k"), handler)
    p = jev.rank(("k",), CRITERIA, "s")
    loop.run()
    assert p.result.probabilities == {"suggested.water": 1.0} and p.result.choice is None
    jev, loop, _, _ = make(TypeSafeAccess("k"), lambda r: httpx.Response(200, json={"answers": {}}))
    p = jev.rank(("k",), CRITERIA, "s")
    loop.run()
    assert p.done and p.result is None


def test_cache_makes_no_second_request():
    jev, loop, clock, requests = make(TypeSafeAccess("k"), GOOD)
    jev.rank(("ids", 18, 7), CRITERIA, "s")
    same = jev.rank(("ids", 18, 7), CRITERIA, "s")  # in flight: shared
    loop.run()
    hit = jev.rank(("ids", 18, 7), CRITERIA, "s")
    assert hit.done and hit.result is same.result and len(requests) == 1
    jev.rank(("ids", 18, 8), CRITERIA, "s")  # a new outcome was logged: asked again
    loop.run()
    assert len(requests) == 2
    clock.t += 601
    assert jev.cached(("ids", 18, 7)) is None


def test_timeout_falls_back_to_no_prior():
    async def slow(request):
        await asyncio.sleep(1.0)
        return GOOD(request)

    jev, loop, _, _ = make(TypeSafeAccess("k"), slow, timeout=0.05)
    p = jev.rank(("k",), CRITERIA, "s")
    loop.run()
    assert p.done and p.result is None
    assert jev.available  # one timeout does not switch it off


def test_breaker_opens_on_bad_key_and_after_three_failures(caplog):
    jev, loop, clock, requests = make(TypeSafeAccess("bad"), lambda r: httpx.Response(401, text="invalid key"))
    jev.rank(("a",), CRITERIA, "s")
    loop.run()
    assert not jev.available and "switched off" in caplog.text and "(paused)" in jev.describe()
    assert jev.rank(("b",), CRITERIA, "s").result is None and loop.jobs == []  # nothing is sent while off
    clock.t += 301
    assert jev.available

    jev, loop, _, requests = make(TypeSafeAccess("k"), lambda r: httpx.Response(500, text="oops"))
    for key in ("a", "b"):
        jev.rank((key,), CRITERIA, "s")
        loop.run()
    assert jev.available
    jev.rank(("c",), CRITERIA, "s")
    loop.run()
    assert not jev.available and len(requests) == 3


def test_one_candidate_is_not_worth_asking():
    jev, loop, _, requests = make(TypeSafeAccess("k"), GOOD)
    assert jev.rank(("k",), {"a": "A"}, "s").result is None and loop.jobs == []


# --- the session's quiet re-rank -----------------------------------------------------------


@pytest.fixture(scope="module")
def menu():
    return load_menu()


@pytest.fixture(scope="module")
def profile(menu):
    return load_profile(menu.contacts)


def home_prior(request):
    criteria = json.loads(request.content)["questions"]["next"]["criteria"]
    probs = {k: 0.02 for k in criteria}
    if "room" in criteria:  # the home level: Room is the likely one right now
        probs["room"] = 0.9
    return httpx.Response(200, json=reply(probs, "room" if "room" in criteria else next(iter(criteria))))


def make_session(menu, profile, *, learning=True):
    sent = []
    jev, loop, _, requests = make(TypeSafeAccess("k"), home_prior)
    s = Session(menu, sent.append, ManualScheduler(start=100.0), profile=profile, spawn=asyncio.run, lang="en", jev=jev, learning=learning)
    s.start()
    return s, sent, loop, requests


def ids(sent) -> list[str]:
    return [t.id for t in next(m for m in reversed(sent) if isinstance(m, Screen)).tiles]


def test_jev_answer_reranks_quietly_when_the_person_has_not_moved(menu, profile):
    s, sent, loop, requests = make_session(menu, profile)
    assert ids(sent) == ["suggested", "need", "people", "feel", "room", "other"]  # history only: yaml order
    s._scheduler.advance(1.0)  # the highlight moves on: that is not the person moving
    loop.run()
    assert ids(sent) == ["suggested", "room", "need", "people", "feel", "other"]  # Suggested stays first
    assert s.highlight == 1  # same position; nothing jumps back to the start
    assert json.loads(requests[0].content)["state"].startswith("Now: ")


def test_jev_answer_is_ignored_after_a_gesture(menu, profile):
    s, sent, loop, _ = make_session(menu, profile)
    s._scheduler.advance(0.35 + 1.0)
    s.handle(Clench(t=0.0, strength=1.0))  # picks "I need" before Jev answers
    n = len(sent)
    loop.run()
    assert all(not isinstance(m, Screen) or m.path == ["I need"] for m in sent[n:])
    go_back(s)  # back home: the answer Jev gave meanwhile is used at once
    assert ids(sent) == ["suggested", "room", "need", "people", "feel", "other"]


def test_day1_mode_never_asks_jev(menu, profile):
    s, sent, loop, requests = make_session(menu, profile, learning=False)
    loop.run()
    assert requests == [] and ids(sent)[:2] == ["suggested", "need"]
