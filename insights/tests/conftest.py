"""Shared fixtures. Database tests run in a throwaway schema on INSIGHTS_TEST_DATABASE_URL, or on
TIGER_DATABASE_URL when that is unset, and are skipped when neither is set. The schema is dropped
afterwards, so the real tables are never touched."""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Iterator

import psycopg
import pytest

from insights import config, db


def _test_url() -> str | None:
    config.load()  # reads .env into the environment
    return os.environ.get("INSIGHTS_TEST_DATABASE_URL") or os.environ.get("TIGER_DATABASE_URL")


@pytest.fixture
def pg() -> Iterator[psycopg.Connection]:
    url = _test_url()
    if not url:
        pytest.skip("no database: set INSIGHTS_TEST_DATABASE_URL or TIGER_DATABASE_URL")
    schema = f"insights_test_{secrets.token_hex(4)}"
    with db.connect(url) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    try:
        with db.connect(url, options=f"-c search_path={schema},public") as conn:
            db.apply_schema(conn)
            yield conn
    finally:
        _drop(url, schema)


def _drop(url: str, schema: str) -> None:
    """The schema's refresh policies may be mid-run; stop them first and retry until the drop lands."""
    with db.connect(url) as admin:
        for (job,) in admin.execute("SELECT job_id FROM timescaledb_information.jobs WHERE hypertable_schema = %s",
                                    (schema,)).fetchall():
            admin.execute("SELECT delete_job(%s)", (job,))
        for attempt in range(20):
            try:
                admin.execute(f"DROP SCHEMA {schema} CASCADE")
                return
            except psycopg.errors.DatabaseError:
                if attempt == 19:
                    raise
                time.sleep(0.5)
