"""The caregiver insights page, served by the insights service on its own port (8300 by default).

    python -m insights.app

Reads continuous aggregates only (insights/queries.py). Shows a suggestion card when the clench
signal is weakening; it never changes a Clench setting, and it has no way to reach the core.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool

from insights import config, queries, rules

log = logging.getLogger("insights.app")
STATIC = Path(__file__).with_name("static")


def _align(axis: list[str], rows: list[dict[str, Any]], key: str) -> dict[str, list[Any]]:
    """Split one metric into a simulated and a live series on the shared day axis (None = no data)."""
    sim = dict.fromkeys(axis)
    live = dict.fromkeys(axis)
    for r in rows:
        if r["day"] in sim:
            (sim if r["simulated"] else live)[r["day"]] = r[key]
    return {"simulated": [sim[d] for d in axis], "live": [live[d] for d in axis]}


def build_payload(gestures: list[dict[str, Any]], messages: list[dict[str, Any]],
                  signal: list[dict[str, Any]], live_today: dict[str, Any],
                  suggestion: rules.Suggestion, today: date, days: int) -> dict[str, Any]:
    axis = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    refusal: dict[str, dict[str, list[Any]]] = {}
    for reason in queries.REASONS:
        rows = [{**g, "rate": g["refusal_by_reason"][reason]} for g in gestures]
        refusal[reason] = _align(axis, rows, "rate")
    recal = sorted({s["day"] for s in signal if s["recalibrated"] and s["day"] in axis})
    return {
        "today": today.isoformat(),
        "days": axis,
        "strength": _align(axis, gestures, "median_strength"),
        "margin": _align(axis, gestures, "median_margin"),
        "refusal_rate": _align(axis, gestures, "refusal_rate"),
        "refusal_by_reason": refusal,
        "clenches_per_message": _align(axis, messages, "clenches_per_message"),
        "day1_clenches_per_message": _align(axis, messages, "day1_clenches_per_message"),
        "compose_s_per_message": _align(axis, messages, "compose_s_per_message"),
        "recalibrated_days": recal,
        "live_today": live_today,
        "suggestion": asdict(suggestion),
    }


def create_app(cfg: config.Config) -> FastAPI:
    pool: ConnectionPool | None = None
    if cfg.database_url:
        # check_connection replaces a connection the database dropped (a restart, an idle timeout)
        # before a request gets it, instead of failing that request.
        pool = ConnectionPool(cfg.database_url, min_size=0, max_size=4, open=False,
                              check=ConnectionPool.check_connection,
                              kwargs={"autocommit": True, "connect_timeout": 5})

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if pool is not None:
            pool.open(wait=False)
        yield
        if pool is not None:
            pool.close()

    app = FastAPI(title="Clench Insights", lifespan=lifespan)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/insights")
    def insights(days: int = Query(29, ge=7, le=90)) -> Any:
        if pool is None:
            return JSONResponse({"error": "TIGER_DATABASE_URL is not set"}, status_code=503)
        today = queries.today_local()
        try:
            with pool.connection(timeout=8) as conn:
                return build_payload(
                    queries.gestures_daily(conn, days + 1), queries.messages_daily(conn, days + 1),
                    queries.signal_daily(conn, days + 1), queries.live_today(conn),
                    queries.suggestion(conn, cfg, today), today, days)
        except (psycopg.Error, TimeoutError) as e:
            # The type name only: an error message can quote the connection string.
            log.warning("database query failed: %s", type(e).__name__)
            return JSONResponse({"error": f"database unavailable ({type(e).__name__})"}, status_code=503)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "database_configured": pool is not None}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = config.load()
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=cfg.port)


if __name__ == "__main__":
    main()
