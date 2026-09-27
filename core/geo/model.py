"""Result shapes for the trip page and the car.

RideProfile, DropoffRequest and LatLng map one-to-one onto the messages of the same names in
proto/clench/rider/v1/rider.proto: same field names, enums as their proto value names (the proto3
JSON mapping). tests/geo/test_model.py checks the field lists against the .proto file.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from core.geo.geometry import LatLon


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------------------------
# proto/clench/rider/v1/rider.proto
# ---------------------------------------------------------------------------------------------


class LatLng(_Model):
    latitude: float
    longitude: float

    @classmethod
    def of(cls, p: LatLon) -> LatLng:
        return cls(latitude=round(p[0], 7), longitude=round(p[1], 7))

    def tuple(self) -> LatLon:
        return (self.latitude, self.longitude)


RoutePreference = Literal["ROUTE_PREFERENCE_UNSPECIFIED", "ROUTE_PREFERENCE_FASTEST", "ROUTE_PREFERENCE_SMOOTHEST"]


class RideProfile(_Model):
    route_preference: RoutePreference
    uses_wheelchair: bool
    needs_extra_boarding_time: bool


class DropoffRequest(_Model):
    request_id: str
    point: LatLng
    description: str
    reason: str
    fallback_point: LatLng | None = None


# ---------------------------------------------------------------------------------------------
# Layer 2: drop-off candidates
# ---------------------------------------------------------------------------------------------


class Factor(_Model):
    """One scored aspect of a candidate. value None = the data does not say (unknown)."""

    name: str
    label: str  # what the reason calls it: "curb", "slope", ...
    value: float | None  # 0 (bad) .. 1 (good)
    used: float  # value, or the unknown penalty when value is None
    weight: float
    evidence: str


class Slope(_Model):
    max_grade_pct: float
    avg_grade_pct: float
    climb_m: float
    length_m: float
    samples: int
    source: str
    resolution_m: float | None


class Candidate(_Model):
    id: str
    rank: int
    score: float  # 0..100
    stop: LatLng  # where the car stops
    entrance: LatLng  # where the rider is going
    heading_to_entrance_deg: float
    entrance_osm_id: int | None
    entrance_kind: str
    road_osm_id: int
    road_class: str
    road_name: str | None
    walk_m: float  # along mapped footways, or the straight line when none connect (walk_kind)
    walk_kind: str  # "step-free path" / "path with steps" / "straight line"
    walk_polyline: str  # encoded, precision 5
    slope: Slope | None
    factors: list[Factor]
    unknown: list[str]  # labels of the factors the data does not give
    description: str
    reason: str


class Dropoff(_Model):
    destination: str
    destination_point: LatLng
    radius_m: float
    candidates: list[Candidate]
    request: DropoffRequest | None
    osm_fetched_at: str
    notes: list[str]


# ---------------------------------------------------------------------------------------------
# Layer 1: ride comfort routes
# ---------------------------------------------------------------------------------------------


class Feature(_Model):
    kind: str  # sharp_turn / traffic_signal / stop_sign / traffic_calming / rough
    point: LatLng
    detail: str


class RouteStats(_Model):
    id: str
    label: str  # "Fastest", "Smoothest", "Fastest and smoothest", or "Alternative"
    duration_s: float
    distance_m: float
    summary: str
    polyline: str  # encoded, precision 5
    sharp_turns: int
    climb_m: float | None
    steepest_grade_pct: float | None
    elevation_source: str | None
    traffic_signals: int
    stop_signs: int
    traffic_calming: int
    traffic_calming_kinds: dict[str, int]
    rough_m: float
    surface_known_pct: float  # share of the route whose OSM road has surface or smoothness tagged
    bridge_m: float  # left out of the grade (bare-earth elevation reads a bridge as a dip)
    comfort_cost: float
    features: list[Feature]


class Tile(_Model):
    route_id: str
    kind: Literal["fastest", "smoothest", "fastest_and_smoothest", "alternative"]
    label: str  # "Fastest, 14 min" (an estimate without traffic: see Trip.duration_note)
    detail: str  # "9 fewer sharp turns, no speed bumps mapped"
    ride_profile: RideProfile | None  # None for an alternative: RideProfile can only ask for fastest or smoothest


class Routes(_Model):
    pickup: str
    destination: str
    pickup_point: LatLng
    destination_point: LatLng
    routes: list[RouteStats]
    fastest_id: str
    smoothest_id: str
    tiles: list[Tile]
    osm_fetched_at: str
    notes: list[str]


class Trip(_Model):
    generated_at: str
    attribution: list[str]
    duration_note: str
    dropoff: Dropoff | None
    ride: Routes | None
    notes: list[str]
