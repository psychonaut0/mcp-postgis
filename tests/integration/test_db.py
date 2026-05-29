"""Integration tests for the DB layer."""
from __future__ import annotations

import psycopg
import pytest

from mcp_postgis.config import Config, Mode
from mcp_postgis.db import Database


@pytest.mark.integration
async def test_bootstrap_creates_layer_schema_in_write_mode(db_url: str) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="claude")
    async with Database(cfg):
        pass
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT to_regclass('claude._meta') IS NOT NULL")
            assert (await cur.fetchone()) == (True,)


@pytest.mark.integration
async def test_bootstrap_skipped_in_read_only(db_url: str) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY, layer_schema="claude")
    async with Database(cfg):
        pass
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT to_regclass('claude._meta') IS NULL")
            assert (await cur.fetchone()) == (True,)


@pytest.mark.integration
async def test_database_open_and_query(db_url: str) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        async with db.read() as cur:
            await cur.execute("SELECT 1")
            assert await cur.fetchone() == (1,)


@pytest.mark.integration
async def test_read_transaction_is_readonly(db_url: str) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        async with db.read() as cur:
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                await cur.execute("CREATE TABLE app.tmp (id int)")


@pytest.mark.integration
async def test_write_transaction_commits(db_url: str) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        async with db.write() as cur:
            await cur.execute("CREATE TABLE app.tmp (id int)")
            await cur.execute("INSERT INTO app.tmp VALUES (42)")

        async with db.read() as cur:
            await cur.execute("SELECT id FROM app.tmp")
            assert await cur.fetchall() == [(42,)]


@pytest.mark.integration
async def test_write_transaction_rolls_back_on_error(db_url: str) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        with pytest.raises(psycopg.errors.SyntaxError):
            async with db.write() as cur:
                await cur.execute("CREATE TABLE app.tmp (id int)")
                await cur.execute("THIS IS NOT SQL")

        async with db.read() as cur:
            await cur.execute(
                "SELECT to_regclass('app.tmp') IS NULL"
            )
            assert (await cur.fetchone()) == (True,)


@pytest.mark.integration
async def test_statement_timeout_applied(db_url: str) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY, statement_timeout_ms=100)
    async with Database(cfg) as db:
        with pytest.raises(psycopg.errors.QueryCanceled):
            async with db.read() as cur:
                await cur.execute("SELECT pg_sleep(2)")
