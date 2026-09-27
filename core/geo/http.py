"""HTTP for the OPEN-DATA services only (Nominatim, Overpass, OSRM, OpenTopoData, USGS EPQS).

Every response is cached on disk, keyed by the request, in data/geo_cache/ (git-ignored). Each
service gets its own minimum gap between requests (their usage policies), a descriptive
User-Agent, and a timeout. A failed request falls back to the cached response and records a note
that the trip page shows; with nothing cached it raises FetchError.

Google responses must never go through this module: the Google Maps Platform terms forbid caching
them (docs/decisions.md #22). core/geo/google.py has its own uncached client.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from core.geo.config import HttpConfig

log = logging.getLogger("clench.geo")

OPEN_DATA_SERVICES = frozenset({"nominatim", "overpass", "osrm", "opentopodata", "epqs"})
RATE_LIMIT_PAUSE_S = 30.0  # Overpass wiki: pause 30 s after a 429


class FetchError(RuntimeError):
    """A request failed and there was no cached response to fall back on."""


@dataclass(frozen=True)
class Fetched:
    data: Any
    source: str  # "live" or "cache"
    fetched_at: str  # ISO time the data was fetched from the service


class DiskCache:
    """One JSON file per request under `root`. Only open-data services may write here."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def key(service: str, method: str, url: str, params: Mapping[str, Any] | None) -> str:
        blob = json.dumps([service, method, url, sorted((params or {}).items())], separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def _path(self, service: str, key: str) -> Path:
        return self.root / service / f"{key}.json"

    def get(self, service: str, key: str) -> Fetched | None:
        path = self._path(service, key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            log.warning("unreadable cache file %s; ignoring it", path)
            return None
        return Fetched(entry["data"], "cache", entry["fetched_at"])

    def put(self, service: str, key: str, request: Mapping[str, Any], data: Any, fetched_at: str) -> None:
        if service not in OPEN_DATA_SERVICES:
            raise ValueError(f"{service} responses must not be cached")
        path = self._path(service, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"request": request, "fetched_at": fetched_at, "data": data}), encoding="utf-8")
        tmp.replace(path)


@dataclass
class OpenDataClient:
    """live=False: cache only, no network (tests, and the offline rebuild).
    live=True: a cached response is reused (OSM changes slowly; the usage policies ask for caching)
    unless refresh=True, which asks the service again and falls back to the cache if that fails."""

    config: HttpConfig
    cache: DiskCache
    live: bool
    refresh: bool = False
    client: httpx.Client | None = None
    sleep: Callable[[float], None] = lambda s: time.sleep(s)  # looked up per call, so tests can patch it
    clock: Callable[[], float] = time.monotonic
    notes: list[str] = field(default_factory=list)
    requests_sent: dict[str, int] = field(default_factory=dict)
    _last: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.client is None and self.live:
            timeout = httpx.Timeout(self.config.read_timeout_s, connect=self.config.connect_timeout_s)
            self.client = httpx.Client(timeout=timeout, headers={"User-Agent": self.config.user_agent})

    def get(self, service: str, url: str, params: Mapping[str, Any] | None = None) -> Fetched:
        return self._fetch(service, "GET", [url], params)

    def post_form(self, service: str, urls: list[str], data: Mapping[str, Any]) -> Fetched:
        """POST form data, trying each url in turn (Overpass mirrors). Cached under the first url."""
        return self._fetch(service, "POST", urls, data)

    def _fetch(self, service: str, method: str, urls: list[str], params: Mapping[str, Any] | None) -> Fetched:
        if service not in OPEN_DATA_SERVICES:
            raise ValueError(f"{service} is not an open-data service")
        key = self.cache.key(service, method, urls[0], params)
        cached = self.cache.get(service, key)
        if not self.live or (cached is not None and not self.refresh):
            if cached is None:
                raise FetchError(f"{service}: offline and no cached response")
            return cached
        errors: list[str] = []
        for url in urls:
            for attempt in range(2):
                self._throttle(service)
                try:
                    assert self.client is not None
                    self.requests_sent[service] = self.requests_sent.get(service, 0) + 1
                    timeout = self.config.overpass_read_timeout_s if service == "overpass" else self.config.read_timeout_s
                    t = httpx.Timeout(timeout, connect=self.config.connect_timeout_s)
                    if method == "GET":
                        resp = self.client.get(url, params=params, timeout=t)
                    else:
                        resp = self.client.post(url, data=params, timeout=t)
                except httpx.HTTPError as e:
                    errors.append(f"{_host(url)}: {type(e).__name__}")
                    break
                if resp.status_code == 429 and attempt == 0:
                    log.info("%s rate-limited us; pausing %.0f s", service, RATE_LIMIT_PAUSE_S)
                    self.sleep(RATE_LIMIT_PAUSE_S)
                    continue
                if resp.status_code != 200:
                    errors.append(f"{_host(url)}: HTTP {resp.status_code}")
                    break
                try:
                    data = resp.json()
                except ValueError:
                    errors.append(f"{_host(url)}: not JSON")
                    break
                now = datetime.now(UTC).isoformat(timespec="seconds")
                self.cache.put(service, key, {"method": method, "url": urls[0], "params": dict(params or {})}, data, now)
                return Fetched(data, "live", now)
        reason = "; ".join(errors)
        if cached is not None:
            self.notes.append(f"{service} failed ({reason}); used the cached response from {cached.fetched_at}")
            log.warning(self.notes[-1])
            return cached
        raise FetchError(f"{service} failed ({reason}) and nothing is cached")

    def _throttle(self, service: str) -> None:
        gap = self.config.min_interval_s.get(service, 1.0)
        last = self._last.get(service)
        if last is not None:
            wait = gap - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[service] = self.clock()

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


def _host(url: str) -> str:
    return httpx.URL(url).host
