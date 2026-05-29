"""Integration tests for Phase 7 introspection tools."""
from __future__ import annotations

import pytest

from mcp_postgis.config import Config, Mode
from mcp_postgis.db import Database
from mcp_postgis.errors import ToolError
from mcp_postgis.server import ServerContext

# ---------------------------------------------------------------------------
# Minimal fake Context objects (injected into every tool call)
# ---------------------------------------------------------------------------


class _RC:
    def __init__(self, srv_ctx: ServerContext) -> None:
        self.lifespan_context = srv_ctx


class _Ctx:
    def __init__(self, srv_ctx: ServerContext) -> None:
        self.request_context = _RC(srv_ctx)


@pytest.fixture
def fake_ctx_factory():  # type: ignore[return]
    def make(srv_ctx: ServerContext) -> _Ctx:
        return _Ctx(srv_ctx)

    return make


# ---------------------------------------------------------------------------
# Import tools after registration so they are bound to the server's mcp object
# ---------------------------------------------------------------------------

from mcp_postgis.tools.introspection import (  # noqa: E402
    describe_table,
    list_extensions,
    list_geometry_columns,
    list_schemas,
    list_spatial_indexes,
    list_tables,
)

# ---------------------------------------------------------------------------
# 7.1  list_schemas
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_schemas_returns_app_and_public(
    db_url: str, fake_ctx_factory
) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await list_schemas(fake_ctx_factory(srv))  # type: ignore[arg-type]

    names = {s["name"] for s in result["schemas"]}
    assert "app" in names
    assert "public" in names
    # system schemas must be excluded
    assert "pg_catalog" not in names
    assert "information_schema" not in names


@pytest.mark.integration
async def test_list_schemas_respects_allow_list(
    db_url: str, fake_ctx_factory
) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY, allowed_schemas=("app",))
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await list_schemas(fake_ctx_factory(srv))  # type: ignore[arg-type]

    names = {s["name"] for s in result["schemas"]}
    assert names == {"app"}


# ---------------------------------------------------------------------------
# 7.2  list_tables
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_tables_app_schema(db_url: str, fake_ctx_factory) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await list_tables(fake_ctx_factory(srv), schema="app")  # type: ignore[arg-type]

    table_names = {t["table"] for t in result["tables"]}
    assert {"cities", "regions", "notes"}.issubset(table_names)
    # All reported kinds must be valid readable strings
    for t in result["tables"]:
        assert t["kind"] in {
            "table", "view", "materialized_view", "foreign_table", "partitioned_table"
        }
        assert isinstance(t["estimated_rows"], int)
        assert t["estimated_rows"] >= 0


@pytest.mark.integration
async def test_list_tables_unknown_schema_returns_empty(
    db_url: str, fake_ctx_factory
) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY, allowed_schemas=("app",))
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await list_tables(fake_ctx_factory(srv), schema="ghost_schema")  # type: ignore[arg-type]

    assert result == {"tables": []}


# ---------------------------------------------------------------------------
# 7.3  describe_table
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_describe_table_cities(db_url: str, fake_ctx_factory) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await describe_table(fake_ctx_factory(srv), schema="app", table="cities")  # type: ignore[arg-type]

    assert result["schema"] == "app"
    assert result["table"] == "cities"

    col_names = {c["name"] for c in result["columns"]}
    assert {"id", "name", "geom"}.issubset(col_names)

    # Geometry column metadata
    assert len(result["geometry_columns"]) == 1
    gc = result["geometry_columns"][0]
    assert gc["column"] == "geom"
    assert gc["srid"] == 4326
    assert gc["type"].upper() == "POINT"

    # GIST index must be present
    index_types = {idx["index_type"] for idx in result["indexes"]}
    assert "gist" in index_types

    # cities has no foreign keys in our seed
    assert result["foreign_keys"] == []


@pytest.mark.integration
async def test_describe_table_not_found_raises_tool_error(
    db_url: str, fake_ctx_factory
) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc_info:
            await describe_table(fake_ctx_factory(srv), schema="app", table="ghost")  # type: ignore[arg-type]

    assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# 7.4  list_geometry_columns
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_geometry_columns_finds_app_geoms(
    db_url: str, fake_ctx_factory
) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await list_geometry_columns(fake_ctx_factory(srv))  # type: ignore[arg-type]

    cols = result["columns"]
    # Find app.cities.geom and app.regions.geom
    app_geoms = {(c["schema"], c["table"], c["column"]) for c in cols}
    assert ("app", "cities", "geom") in app_geoms
    assert ("app", "regions", "geom") in app_geoms


@pytest.mark.integration
async def test_list_geometry_columns_allow_list_filters_schema(
    db_url: str, fake_ctx_factory
) -> None:
    # allowed_schemas=("public",) — public has no geometry columns in our seed,
    # so we assert that no app.* rows are present.
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY, allowed_schemas=("public",))
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await list_geometry_columns(fake_ctx_factory(srv))  # type: ignore[arg-type]

    cols = result["columns"]
    # All returned rows (if any) must belong to "public"
    assert all(c["schema"] == "public" for c in cols)
    # No app.* rows
    assert not any(c["schema"] == "app" for c in cols)


# ---------------------------------------------------------------------------
# 7.5  list_spatial_indexes
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_spatial_indexes_schema_filter(
    db_url: str, fake_ctx_factory
) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await list_spatial_indexes(fake_ctx_factory(srv), schema="app")  # type: ignore[arg-type]

    indexes = result["indexes"]
    index_names = {idx["index"] for idx in indexes}
    assert "cities_geom_gix" in index_names
    assert "regions_geom_gix" in index_names
    # All must be gist (our seed only uses GIST)
    assert all(idx["index_type"] == "gist" for idx in indexes)
    # All must belong to app schema
    assert all(idx["schema"] == "app" for idx in indexes)


@pytest.mark.integration
async def test_list_spatial_indexes_no_filter(db_url: str, fake_ctx_factory) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await list_spatial_indexes(fake_ctx_factory(srv))  # type: ignore[arg-type]

    # At minimum the two app indexes must appear
    index_names = {idx["index"] for idx in result["indexes"]}
    assert "cities_geom_gix" in index_names
    assert "regions_geom_gix" in index_names


# ---------------------------------------------------------------------------
# 7.6  list_extensions
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_extensions_contains_postgis(db_url: str, fake_ctx_factory) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await list_extensions(fake_ctx_factory(srv))  # type: ignore[arg-type]

    ext_names = {e["name"] for e in result["extensions"]}
    assert "postgis" in ext_names
    # Each row must have name, version, schema
    for ext in result["extensions"]:
        assert "name" in ext
        assert "version" in ext
        assert "schema" in ext
