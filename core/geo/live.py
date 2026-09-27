"""Live Google evidence for one drop-off candidate, for /trip/live. Shown, never scored or stored.

Per call: Street View metadata (free), then, if there is a panorama, one Street View image
(Essentials) facing the building from where the panorama was taken, which Gemini classifies. The
image goes back to the page inline and is not kept. Places accessibility for the destination
(Place Details Pro) is a separate call.
"""

from __future__ import annotations

import base64
from typing import Any

from core.geo.geometry import bearing_deg, haversine_m
from core.geo.google import GoogleError, GoogleLive
from core.geo.model import Candidate
from core.geo.vision import RampClassifier, VisionError

ATTRIBUTION = "Google Maps"


def candidate_evidence(google: GoogleLive, vision: RampClassifier | None, c: Candidate) -> dict[str, Any]:
    out: dict[str, Any] = {"candidate_id": c.id, "attribution": ATTRIBUTION, "streetview": None, "vision": None, "errors": []}
    try:
        meta = google.streetview_metadata(c.stop.tuple())
    except GoogleError as e:
        out["errors"].append(str(e))
        return out
    sv: dict[str, Any] = {"status": meta.status, "date": meta.date, "copyright": meta.copyright}
    out["streetview"] = sv
    if meta.status != "OK" or meta.pano_id is None or meta.location is None:
        return out
    heading = bearing_deg(meta.location, c.entrance.tuple())
    sv["heading_deg"] = round(heading)
    sv["pano_to_stop_m"] = round(haversine_m(meta.location, c.stop.tuple()), 1)
    sv["pano_to_target_m"] = round(haversine_m(meta.location, c.entrance.tuple()), 1)
    try:
        image = google.streetview_image(meta.pano_id, heading)
    except GoogleError as e:
        out["errors"].append(str(e))
        return out
    sv["image"] = "data:image/jpeg;base64," + base64.b64encode(image).decode()
    if vision is None:
        out["errors"].append("Gemini not configured: no ramp/steps label")
        return out
    try:
        label = vision.classify(image, sv["pano_to_target_m"])
        out["vision"] = label.model_dump()
    except VisionError as e:
        out["errors"].append(str(e))
    return out


def place_evidence(google: GoogleLive, query: str) -> dict[str, Any]:
    try:
        p = google.place_accessibility(query)
    except GoogleError as e:
        return {"attribution": ATTRIBUTION, "error": str(e)}
    return {"attribution": ATTRIBUTION, "place_id": p.place_id, "name": p.name, "accessibility": p.options}
