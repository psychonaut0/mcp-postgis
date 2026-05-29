"""Async psycopg connection pool with read-only / read-write transaction helpers."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType

from psycopg import AsyncCursor
from psycopg.sql import SQL, Identifier
from psycopg_pool import AsyncConnectionPool

from mcp_postgis.config import Config, Mode


class Database:
    """Owns the pool. Hands out read/write transaction-scoped cursors."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._pool: AsyncConnectionPool | None = None

    async def __aenter__(self) -> Database:
        self._pool = AsyncConnectionPool(
            conninfo=self._cfg.database_url,
            min_size=1,
            max_size=4,
            open=False,
        )
        await self._pool.open()
        await self._bootstrap_layer_schema()
        return self

    async def _bootstrap_layer_schema(self) -> None:
        """Create the layer schema and _meta table if in a writeable mode.

        No-op when mode is READ_ONLY. Idempotent (uses IF NOT EXISTS).
        """
        if self._cfg.mode is Mode.READ_ONLY:
            return

        pool = self._require_pool()
        schema_id = Identifier(self._cfg.layer_schema)
        meta_id = Identifier(self._cfg.layer_schema, "_meta")

        async with pool.connection() as conn:
            await conn.set_autocommit(True)
            await conn.execute(
                SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(schema=schema_id)
            )
            await conn.execute(
                SQL(
                    "CREATE TABLE IF NOT EXISTS {meta} ("
                    "  name TEXT PRIMARY KEY,"
                    "  kind TEXT NOT NULL CHECK (kind IN ('view','materialized_view')),"
                    "  source_sql TEXT NOT NULL,"
                    "  description TEXT,"
                    "  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                    "  refreshed_at TIMESTAMPTZ"
                    ")"
                ).format(meta=meta_id)
            )
            await conn.execute(
                SQL(
                    "COMMENT ON TABLE {meta} IS "
                    "'Bookkeeping for layers created via mcp-postgis.'"
                ).format(meta=meta_id)
            )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("Database is not open. Use `async with Database(cfg):`.")
        return self._pool

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncCursor]:
        """Run inside a READ ONLY transaction with statement_timeout enforced."""
        pool = self._require_pool()
        async with pool.connection() as conn:
            await conn.set_read_only(True)
            await conn.set_autocommit(False)
            async with conn.cursor() as cur:
                # SET LOCAL does not accept bound parameters; the value is an
                # int from our validated Config so interpolation is safe.
                await cur.execute(
                    f"SET LOCAL statement_timeout = {self._cfg.statement_timeout_ms:d}"
                )
                try:
                    yield cur
                    await conn.commit()
                except BaseException:
                    await conn.rollback()
                    raise

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncCursor]:
        """Run inside a READ WRITE transaction. Commits on clean exit."""
        pool = self._require_pool()
        async with pool.connection() as conn:
            await conn.set_read_only(False)
            await conn.set_autocommit(False)
            async with conn.cursor() as cur:
                # SET LOCAL does not accept bound parameters; the value is an
                # int from our validated Config so interpolation is safe.
                await cur.execute(
                    f"SET LOCAL statement_timeout = {self._cfg.statement_timeout_ms:d}"
                )
                try:
                    yield cur
                    await conn.commit()
                except BaseException:
                    await conn.rollback()
                    raise


async def fetch_dicts(cur: AsyncCursor) -> list[dict[str, object]]:
    """Helper: turn the last query's results into a list of dicts."""
    if cur.description is None:
        return []
    cols = [d.name for d in cur.description]
    rows = await cur.fetchall()
    return [dict(zip(cols, r, strict=True)) for r in rows]


__all__ = ["Database", "fetch_dicts"]
