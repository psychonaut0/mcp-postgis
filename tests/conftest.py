"""Shared test fixtures: a PostGIS container per session, fresh DB per test."""
from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

SEED_SQL = (Path(__file__).parent / "fixtures" / "seed.sql").read_text()


@pytest.fixture(scope="session")
def postgis_container() -> Iterator[PostgresContainer]:
    """Boot a postgis/postgis container once for the test session."""
    with PostgresContainer("postgis/postgis:16-3.4", driver=None) as pg:
        yield pg


@pytest_asyncio.fixture
async def db_url(postgis_container: PostgresContainer) -> AsyncIterator[str]:
    """Create a fresh database per test, seed it, and tear it down after."""
    admin_dsn = postgis_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    db_name = f"t_{secrets.token_hex(6)}"

    async with await psycopg.AsyncConnection.connect(
        admin_dsn, autocommit=True
    ) as conn:
        await conn.execute(f'CREATE DATABASE "{db_name}"')

    test_dsn = admin_dsn.rsplit("/", 1)[0] + f"/{db_name}"
    try:
        async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as conn:
            await conn.execute(SEED_SQL)
        yield test_dsn
    finally:
        async with await psycopg.AsyncConnection.connect(
            admin_dsn, autocommit=True
        ) as conn:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s",
                [db_name],
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any inherited MCP_POSTGIS_* env to make tests deterministic."""
    for key in list(os.environ):
        if key.startswith("MCP_POSTGIS_"):
            monkeypatch.delenv(key, raising=False)
