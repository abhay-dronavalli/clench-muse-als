"""Open-data HTTP (cache, fallback, rate limits), live Google (never cached, key never shown),
the Gemini ramp classifier, and the proto mapping of the result models."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from core.geo.config import load_geo_config
from core.geo.google import GoogleError, GoogleLive
from core.geo.http import DiskCache, FetchError, OpenDataClient
from core.geo.model import DropoffRequest, LatLng, RideProfile
from core.geo.vision import RampClassifier, RampLabel, VisionError

KEY = "AIzaTESTKEY-never-print-me"
PROTO = Path(__file__).resolve().parents[2] / "proto" / "clench" / "rider" / "v1" / "rider.proto"


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def client(tmp_path, handler, *, live=True, refresh=False, clock=None):
    clock = clock or Clock()
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenDataClient(load_geo_config().http, DiskCache(tmp_path), live=live, refresh=refresh, client=http, sleep=clock.sleep, clock=clock.now), clock


def ok(data):
    return lambda request: httpx.Response(200, json=data)


# --- open-data client ---------------------------------------------------------------------------


def test_live_response_is_cached_and_reused(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"n": len(calls)})

    c, _ = client(tmp_path, handler)
    first = c.get("osrm", "https://example.org/route", {"a": 1})
    second = c.get("osrm", "https://example.org/route", {"a": 1})
    assert (first.source, second.source) == ("live", "cache")
    assert second.data == {"n": 1} and len(calls) == 1


def test_offline_uses_the_cache_and_never_the_network(tmp_path):
    c, _ = client(tmp_path, ok({"x": 1}))
    c.get("osrm", "https://example.org/r", {"q": "a"})
    offline, _ = client(tmp_path, lambda r: pytest.fail("offline client made a request"), live=False)
    assert offline.get("osrm", "https://example.org/r", {"q": "a"}).data == {"x": 1}
    with pytest.raises(FetchError, match="offline"):
        offline.get("osrm", "https://example.org/r", {"q": "other"})


def test_failure_falls_back_to_the_cache_with_a_visible_note(tmp_path):
    c, _ = client(tmp_path, ok({"x": 1}))
    c.get("overpass", "https://example.org/r", {"q": "a"})

    def down(request):
        raise httpx.ReadTimeout("slow", request=request)

    again, _ = client(tmp_path, down, refresh=True)
    got = again.get("overpass", "https://example.org/r", {"q": "a"})
    assert got.source == "cache" and got.data == {"x": 1}
    assert "overpass failed (example.org: ReadTimeout); used the cached response" in again.notes[0]


def test_failure_with_nothing_cached_raises(tmp_path):
    c, _ = client(tmp_path, lambda r: httpx.Response(500))
    with pytest.raises(FetchError, match="HTTP 500"):
        c.get("osrm", "https://example.org/r")


def test_429_pauses_30_s_then_retries_once(tmp_path):
    replies = iter([httpx.Response(429), httpx.Response(200, json={"ok": True})])
    c, clock = client(tmp_path, lambda r: next(replies))
    assert c.get("nominatim", "https://example.org/s").data == {"ok": True}
    assert 30.0 in clock.slept


def test_overpass_tries_the_next_mirror(tmp_path):
    def handler(request):
        return httpx.Response(504) if request.url.host == "a.example" else httpx.Response(200, json={"elements": []})

    c, _ = client(tmp_path, handler)
    got = c.post_form("overpass", ["https://a.example/api", "https://b.example/api"], {"data": "q"})
    assert got.data == {"elements": []}


def test_requests_to_one_service_are_spaced_by_its_policy(tmp_path):
    c, clock = client(tmp_path, ok({}))
    for i in range(3):
        c.get("nominatim", "https://example.org/s", {"i": i})
    gap = load_geo_config().http.min_interval_s["nominatim"]
    assert clock.slept == pytest.approx([gap, gap])


def test_requests_carry_the_user_agent(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={})

    c = OpenDataClient(load_geo_config().http, DiskCache(tmp_path), live=True, sleep=lambda s: None)
    c.client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": c.config.user_agent})
    c.get("osrm", "https://example.org/r")
    assert seen[0].startswith("Clench/")


def test_google_services_can_never_be_cached(tmp_path):
    cache = DiskCache(tmp_path)
    for service in ("google", "streetview", "places"):
        with pytest.raises(ValueError):
            cache.put(service, "k", {}, {}, "now")
    c, _ = client(tmp_path, ok({}))
    with pytest.raises(ValueError):
        c.get("streetview", "https://maps.googleapis.com/maps/api/streetview")


# --- live Google --------------------------------------------------------------------------------


def google(handler):
    return GoogleLive(KEY, client=httpx.Client(transport=httpx.MockTransport(handler)))


def places_handler(options):
    def handler(request):
        if request.url.path.endswith(":searchText"):
            assert request.headers["X-Goog-FieldMask"] == "places.id"  # the free IDs-only SKU
            return httpx.Response(200, json={"places": [{"id": "PID"}]})
        return httpx.Response(200, json={"id": "PID", "displayName": {"text": "Clinic"}, **({"accessibilityOptions": options} if options is not None else {})})

    return handler


def test_places_explicit_false_is_kept_as_false():
    p = google(places_handler({"wheelchairAccessibleEntrance": False, "wheelchairAccessibleParking": True})).place_accessibility("x")
    assert p.options == {"wheelchairAccessibleEntrance": False, "wheelchairAccessibleParking": True}


def test_places_absent_fields_stay_absent_so_they_read_as_unknown():
    p = google(places_handler({"wheelchairAccessibleParking": True})).place_accessibility("x")
    assert "wheelchairAccessibleEntrance" not in p.options
    assert google(places_handler(None)).place_accessibility("x").options == {}


def test_google_errors_never_contain_the_key():
    def fail(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(GoogleError) as e:
        google(fail).streetview_metadata((25.0, -80.0))
    assert KEY not in str(e.value)
    with pytest.raises(GoogleError) as e:
        google(lambda r: httpx.Response(403, text=f"bad key {KEY}")).streetview_image("pano", 90)
    assert KEY not in str(e.value)


def test_streetview_metadata_is_not_billed_and_the_image_is():
    def handler(request):
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200, json={"status": "OK", "pano_id": "P", "location": {"lat": 25.0, "lng": -80.0}, "date": "2025-02"})
        return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})

    g = google(handler)
    meta = g.streetview_metadata((25.0, -80.0))
    assert meta.pano_id == "P" and meta.date == "2025-02"
    assert g.streetview_image("P", 10) == b"\xff\xd8jpeg"
    assert g.billed == {"streetview_image": 1}


def test_missing_google_key_is_an_error():
    with pytest.raises(GoogleError):
        GoogleLive("")


# --- Gemini ramp classifier ---------------------------------------------------------------------


def fake_gemini(text):
    return SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=text)))


def test_classifier_returns_the_structured_label():
    answer = {"label": "steps_with_accessible_route", "confidence": 0.85, "cues": ["steps", "wheelchair_symbol"], "note": "Sign by the steps."}
    label = RampClassifier("", client=fake_gemini(json.dumps(answer))).classify(b"img", 20)
    assert label == RampLabel(**answer)


@pytest.mark.parametrize("text", ["", "not json", json.dumps({"label": "maybe", "confidence": 0.5, "note": ""}), json.dumps({"label": "ramp", "confidence": 2, "note": ""})])
def test_classifier_rejects_bad_answers(text):
    with pytest.raises(VisionError):
        RampClassifier("", client=fake_gemini(text)).classify(b"img", 20)


def test_classifier_needs_a_key():
    with pytest.raises(VisionError):
        RampClassifier("")


# --- proto mapping ------------------------------------------------------------------------------


def proto_fields(message: str) -> list[str]:
    text = PROTO.read_text(encoding="utf-8")
    body = re.search(rf"message {message} \{{(.*?)\n\}}", text, re.S).group(1)
    body = re.sub(r"enum \w+ \{.*?\}", "", body, flags=re.S)
    return re.findall(r"^\s*(?:repeated\s+)?[\w.]+\s+(\w+)\s*=\s*\d+;", body, re.M)


@pytest.mark.parametrize(("model", "message"), [(RideProfile, "RideProfile"), (DropoffRequest, "DropoffRequest"), (LatLng, "LatLng")])
def test_models_match_the_proto_field_for_field(model, message):
    assert list(model.model_fields) == proto_fields(message)


def test_route_preference_names_match_the_proto_enum():
    text = PROTO.read_text(encoding="utf-8")
    names = set(re.findall(r"(ROUTE_PREFERENCE_\w+) = \d+;", text))
    literal = RideProfile.model_fields["route_preference"].annotation
    assert set(literal.__args__) == names


def fake_gemini_seq(*texts):
    replies = iter(texts)
    return SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=next(replies), candidates=[SimpleNamespace(finish_reason="STOP")])))


def test_classifier_asks_once_more_after_a_malformed_answer():
    good = json.dumps({"label": "ramp", "confidence": 0.85, "cues": ["ramp"], "note": "A ramp."})
    label = RampClassifier("", client=fake_gemini_seq('{"label": "ramp", "confid', good)).classify(b"img", 20)
    assert label.label == "ramp"


def test_classifier_error_says_what_was_wrong():
    bad = json.dumps({"label": "maybe", "confidence": 0.5, "note": ""})
    with pytest.raises(VisionError, match=r"wrong shape \(finish STOP; label: "):
        RampClassifier("", client=fake_gemini_seq(bad, bad)).classify(b"img", 20)


def test_long_note_is_trimmed_not_rejected():
    long = json.dumps({"label": "level", "confidence": 0.7, "cues": [], "note": "word " * 100})
    label = RampClassifier("", client=fake_gemini(long)).classify(b"img", 20)
    assert len(label.note) <= 200 and label.note.endswith("…")


# --- malformed replies become controlled errors (review findings) --------------------------------


@pytest.mark.parametrize("body", [{"code": "Ok"}, {"code": "Ok", "routes": [{"duration": 1}]}, ["not", "a", "dict"]])
def test_malformed_osrm_reply_is_a_fetch_error(tmp_path, body):
    from core.geo.sources import osrm_routes

    c, _ = client(tmp_path, ok(body))
    with pytest.raises(FetchError):
        osrm_routes(c, (25.0, -80.0), (25.1, -80.1))


@pytest.mark.parametrize("element", [{"type": "node", "id": 1}, {"id": 2}, {"type": "way", "id": 3, "geometry": [{"lat": 1}]}])
def test_malformed_overpass_element_is_a_fetch_error(tmp_path, element):
    from core.geo.sources import overpass

    c, _ = client(tmp_path, ok({"elements": [element]}))
    with pytest.raises(FetchError):
        overpass(c, "q")


@pytest.mark.parametrize("reply", [httpx.Response(200, text="<html>"), httpx.Response(200, json=[1, 2]), httpx.Response(200, json={"places": [{"no_id": 1}]})])
def test_malformed_places_reply_is_a_google_error(reply):
    with pytest.raises(GoogleError):
        google(lambda r: reply).place_accessibility("x")


def test_streetview_metadata_with_bad_location_has_no_pano():
    meta = google(lambda r: httpx.Response(200, json={"status": "OK", "pano_id": "P", "location": {"lat": "x"}})).streetview_metadata((25.0, -80.0))
    assert meta.location is None and meta.pano_id is None
