"""Google Places (New) and Street View Static, LIVE ONLY (docs/decisions.md #21).

The Google Maps Platform terms (ToS 3.2.3, Service Specific Terms, as of 2026-09) forbid caching
Google content (except place IDs and pano IDs), storing results derived from it, showing it with a
non-Google map, and speaking it with text-to-speech. So this module:

  - has no disk cache and keeps nothing between requests;
  - is never used for scoring, the committed demo result, or anything the board says;
  - is shown only on /trip/live, a page with no map, with "Google Maps" attribution.

The key is sent in a header where the API allows it (Places). Street View only takes it as a URL
parameter, so errors here never include the URL or the exception text, only its type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from core.geo.geometry import LatLon

log = logging.getLogger("clench.geo")

PLACES_SEARCH = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS = "https://places.googleapis.com/v1/places/{place_id}"
STREETVIEW_META = "https://maps.googleapis.com/maps/api/streetview/metadata"
STREETVIEW_IMAGE = "https://maps.googleapis.com/maps/api/streetview"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Text Search with only places.id is the free "IDs Only" SKU; the accessibility fields are billed
# once, on Place Details Pro.
SEARCH_MASK = "places.id"
DETAILS_MASK = "id,displayName,accessibilityOptions"


class GoogleError(RuntimeError):
    """A Google request failed. The message never contains the key or the request URL."""


@dataclass(frozen=True)
class PlaceAccessibility:
    place_id: str
    name: str | None
    options: dict[str, bool]  # wheelchairAccessibleEntrance, ...Parking, ...Restroom, ...Seating; absent = unknown


@dataclass(frozen=True)
class StreetViewMeta:
    status: str  # OK / ZERO_RESULTS / NOT_FOUND / ...
    pano_id: str | None
    location: LatLon | None
    date: str | None
    copyright: str | None


class GoogleLive:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise GoogleError("GOOGLE_MAPS_API_KEY is not set")
        self._key = api_key
        self._client = client or httpx.Client(timeout=TIMEOUT)
        self.billed: dict[str, int] = {}  # SKU -> requests, for the cost estimate

    def _send(self, sku: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            raise GoogleError(f"{sku}: {type(e).__name__}") from None
        if sku != "streetview_metadata" and sku != "places_text_search_ids":
            self.billed[sku] = self.billed.get(sku, 0) + 1
        if resp.status_code != 200:
            raise GoogleError(f"{sku}: HTTP {resp.status_code}")
        return resp

    def _json(self, sku: str, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            data = self._send(sku, method, url, **kwargs).json()
        except ValueError:
            raise GoogleError(f"{sku}: not JSON") from None
        if not isinstance(data, dict):
            raise GoogleError(f"{sku}: unexpected reply")
        return data

    def place_accessibility(self, query: str) -> PlaceAccessibility:
        headers = {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": SEARCH_MASK}
        found = self._json("places_text_search_ids", "POST", PLACES_SEARCH, json={"textQuery": query, "pageSize": 1}, headers=headers)
        places = found.get("places") or []
        place_id = places[0].get("id") if isinstance(places, list) and places and isinstance(places[0], dict) else None
        if not isinstance(place_id, str):
            raise GoogleError("Places found no match")
        headers = {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": DETAILS_MASK}
        details = self._json("places_details_pro", "GET", PLACES_DETAILS.format(place_id=place_id), headers=headers)
        display = details.get("displayName")
        name = display.get("text") if isinstance(display, dict) else None
        raw = details.get("accessibilityOptions")
        options = {k: v for k, v in raw.items() if isinstance(v, bool)} if isinstance(raw, dict) else {}
        return PlaceAccessibility(place_id, name, options)

    def streetview_metadata(self, point: LatLon, radius_m: int = 30) -> StreetViewMeta:
        """Free: no charge and no quota (Street View Static metadata)."""
        params = {"location": f"{point[0]:.6f},{point[1]:.6f}", "radius": str(radius_m), "source": "outdoor", "key": self._key}
        data = self._json("streetview_metadata", "GET", STREETVIEW_META, params=params)
        loc = data.get("location")
        try:
            location = (float(loc["lat"]), float(loc["lng"])) if loc else None
        except (KeyError, TypeError, ValueError):
            location = None
        return StreetViewMeta(
            status=data.get("status", "UNKNOWN_ERROR"),
            pano_id=data.get("pano_id") if location else None,
            location=location,
            date=data.get("date"),
            copyright=data.get("copyright"),
        )

    def streetview_image(self, pano_id: str, heading_deg: float, fov: int = 80, size: str = "640x400") -> bytes:
        params = {"pano": pano_id, "heading": f"{heading_deg:.0f}", "fov": str(fov), "pitch": "0", "size": size, "return_error_code": "true", "key": self._key}
        resp = self._send("streetview_image", "GET", STREETVIEW_IMAGE, params=params)
        if not resp.headers.get("content-type", "").startswith("image/"):
            raise GoogleError("streetview_image: not an image")
        return resp.content

    def close(self) -> None:
        self._client.close()
