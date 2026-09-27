"""data/geo.yaml: the demo trip, scoring weights, thresholds, and service settings."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from core.menu import DATA_DIR

GEO_CONFIG_PATH = DATA_DIR / "geo.yaml"
GEO_DIR = DATA_DIR / "geo"  # committed demo result
CACHE_DIR = DATA_DIR / "geo_cache"  # git-ignored open-data response cache


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoTrip(_Strict):
    pickup: str
    destination: str
    pickup_query: str
    destination_query: str
    destination_osm_way: int | None = None


class SavedPlace(_Strict):
    """A destination tile under Car mode > Trip > Plan a trip."""

    key: str
    label_en: str
    label_es: str
    query: str  # Nominatim search


class Rider(_Strict):
    uses_wheelchair: bool = False
    needs_extra_boarding_time: bool = False


class DropoffWeights(_Strict):
    entrance_access: float = Field(ge=0)
    entrance_type: float = Field(ge=0)
    slope: float = Field(ge=0)
    steps: float = Field(ge=0)
    kerb: float = Field(ge=0)
    sidewalk: float = Field(ge=0)
    road: float = Field(ge=0)
    walk: float = Field(ge=0)


class DropoffConfig(_Strict):
    radius_m: float = Field(gt=0)
    max_walk_m: float = Field(gt=0)
    max_candidates: int = Field(ge=1)
    dedupe_m: float = Field(ge=0)
    elevation_step_m: float = Field(gt=0)
    max_elevation_samples: int = Field(ge=2)
    slope_min_run_m: float = Field(ge=0)
    kerb_search_m: float = Field(gt=0)
    steps_search_m: float = Field(gt=0)
    sidewalk_search_m: float = Field(gt=0)
    weights: DropoffWeights


class ComfortWeights(_Strict):
    sharp_turn: float = Field(ge=0)
    traffic_signal: float = Field(ge=0)
    stop_sign: float = Field(ge=0)
    traffic_calming: float = Field(ge=0)
    rough_100m: float = Field(ge=0)
    climb_10m: float = Field(ge=0)
    steep_pct: float = Field(ge=0)


class ComfortConfig(_Strict):
    sharp_turn_deg: float = Field(gt=0, le=180)
    turn_rate_deg_per_m: float = Field(gt=0)
    route_elevation_step_m: float = Field(gt=0)
    grade_min_run_m: float = Field(ge=0)
    match_m: float = Field(gt=0)
    parallel_deg: float = Field(gt=0, le=90)
    signal_cluster_m: float = Field(ge=0)
    rough_smoothness: list[str]
    rough_surface: list[str]
    weights: ComfortWeights


class HttpConfig(_Strict):
    user_agent: str = Field(min_length=10)
    connect_timeout_s: float = Field(gt=0)
    read_timeout_s: float = Field(gt=0)
    overpass_read_timeout_s: float = Field(gt=0)
    min_interval_s: dict[str, float]
    endpoints: dict[str, str | list[str]]

    def endpoint(self, service: str) -> str:
        value = self.endpoints[service]
        return value[0] if isinstance(value, list) else value

    def endpoint_list(self, service: str) -> list[str]:
        value = self.endpoints[service]
        return list(value) if isinstance(value, list) else [value]


class GeoConfig(_Strict):
    demo_trip: DemoTrip
    rider: Rider
    places: list[SavedPlace] = Field(default_factory=list)
    unknown_penalty: float = Field(ge=0, le=1)
    dropoff: DropoffConfig
    comfort: ComfortConfig
    http: HttpConfig


def load_geo_config(path: Path = GEO_CONFIG_PATH) -> GeoConfig:
    return GeoConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
