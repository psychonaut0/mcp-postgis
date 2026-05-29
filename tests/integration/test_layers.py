"""Integration tests for §10 layer-publishing tools."""
from __future__ import annotations

import psycopg
import pytest

from mcp_postgis.config import Config, Mode
from mcp_postgis.db import Database
from mcp_postgis.errors import ToolError
from mcp_postgis.server import ServerContext


class _RC:
    def __init__(self, srv_ctx): self.lifespan_context = srv_ctx

class _Ctx:
    def __init__(self, srv_ctx): self.request_context = _RC(srv_ctx)


@pytest.fixture
def fake_ctx_factory():
    def make(srv_ctx): return _Ctx(srv_ctx)
    return make


# Helper: open a read-write database and return (db, srv, ctx).
# Used as an async context manager (caller enters and exits db).


# ---------------------------------------------------------------------------
# Test 1: create_layer — view mode
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_create_layer_view(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await create_layer(
            fake_ctx_factory(srv),
            name="cities_in_italy",
            sql="SELECT id, name, geom FROM app.cities",
            materialized=False,
            description="Italian cities",
        )

    assert result["full_name"] == "mcp_layers.cities_in_italy"
    assert result["kind"] == "view"
    assert result["row_count"] == 3
    assert result["geom_column"] == "geom"
    assert result["description"] == "Italian cities"


# ---------------------------------------------------------------------------
# Test 2: create_layer — rejects non-SELECT
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_create_layer_rejects_non_select(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await create_layer(
                fake_ctx_factory(srv),
                name="bad_layer",
                sql="DELETE FROM app.cities",
            )
    assert exc.value.code == "permission_denied"


# ---------------------------------------------------------------------------
# Test 3: create_layer — rejects missing geometry column
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_create_layer_rejects_no_geom_column(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await create_layer(
                fake_ctx_factory(srv),
                name="cities_no_geom",
                sql="SELECT id, name FROM app.cities",
            )
    assert exc.value.code == "invalid_argument"
    assert "geometry" in exc.value.message


# ---------------------------------------------------------------------------
# Test 4: create_layer — materialized view with GIST index
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_create_layer_materialized(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await create_layer(
            fake_ctx_factory(srv),
            name="cities_mv",
            sql="SELECT id, name, geom FROM app.cities",
            materialized=True,
        )

    assert result["kind"] == "materialized_view"
    assert result["row_count"] == 3

    # Verify index exists in pg_indexes.
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT indexname FROM pg_indexes"
                " WHERE schemaname = 'mcp_layers' AND tablename = 'cities_mv'"
                " AND indexname = 'cities_mv_geom_gix'"
            )
            row = await cur.fetchone()
    assert row is not None, "Expected GIST index cities_mv_geom_gix to exist"


# ---------------------------------------------------------------------------
# Test 5: create_layer — blocked in read-only mode
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_create_layer_blocked_in_read_only_mode(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await create_layer(
                fake_ctx_factory(srv),
                name="anyname",
                sql="SELECT id, name, geom FROM app.cities",
            )
    assert exc.value.code == "permission_denied"


# ---------------------------------------------------------------------------
# Test 6: create_layer — rejects bad name
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_create_layer_rejects_bad_name(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await create_layer(
                fake_ctx_factory(srv),
                name="bad name with spaces",
                sql="SELECT id, name, geom FROM app.cities",
            )
    assert exc.value.code == "invalid_argument"


# ---------------------------------------------------------------------------
# Test 7: refresh_layer
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_refresh_layer(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer, refresh_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)

        # Create materialized view.
        await create_layer(
            fake_ctx_factory(srv),
            name="cities_refresh_mv",
            sql="SELECT id, name, geom FROM app.cities",
            materialized=True,
        )

    # Insert a new city directly into the DB.
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.set_autocommit(True)
        await conn.execute(
            "INSERT INTO app.cities (name, geom) VALUES ('Naples',"
            " ST_SetSRID(ST_MakePoint(14.2681, 40.8518), 4326))"
        )

    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await refresh_layer(fake_ctx_factory(srv), name="cities_refresh_mv")

    assert result["row_count"] == 4
    assert result["refreshed_at"] is not None


# ---------------------------------------------------------------------------
# Test 8: refresh_layer — rejects plain view
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_refresh_layer_rejects_view(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer, refresh_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)

        await create_layer(
            fake_ctx_factory(srv),
            name="cities_plain_view",
            sql="SELECT id, name, geom FROM app.cities",
            materialized=False,
        )

        with pytest.raises(ToolError) as exc:
            await refresh_layer(fake_ctx_factory(srv), name="cities_plain_view")

    assert exc.value.code == "invalid_argument"


# ---------------------------------------------------------------------------
# Test 9: list_layers
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_list_layers(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer, list_layers

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)

        await create_layer(
            fake_ctx_factory(srv),
            name="list_test_view",
            sql="SELECT id, name, geom FROM app.cities",
            materialized=False,
            description="A plain view",
        )
        await create_layer(
            fake_ctx_factory(srv),
            name="list_test_mv",
            sql="SELECT id, name, geom FROM app.cities",
            materialized=True,
            description="A materialized view",
        )

        result = await list_layers(fake_ctx_factory(srv))

    layers = result["layers"]
    names = [layer["name"] for layer in layers]
    assert "list_test_view" in names
    assert "list_test_mv" in names

    kinds = {layer["name"]: layer["kind"] for layer in layers}
    assert kinds["list_test_view"] == "view"
    assert kinds["list_test_mv"] == "materialized_view"


# ---------------------------------------------------------------------------
# Test 10: describe_layer
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_describe_layer(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer, describe_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    src_sql = "SELECT id, name, geom FROM app.cities"
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)

        await create_layer(
            fake_ctx_factory(srv),
            name="desc_test_layer",
            sql=src_sql,
            description="Describe me",
        )

        result = await describe_layer(fake_ctx_factory(srv), name="desc_test_layer")

    assert result["description"] == "Describe me"
    assert src_sql in result["source_sql"] or result["source_sql"] == src_sql
    assert result["sample"]  # non-empty
    assert len(result["sample"]) <= 10


# ---------------------------------------------------------------------------
# Test 11: drop_layer
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_drop_layer(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import create_layer, drop_layer, list_layers

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)

        await create_layer(
            fake_ctx_factory(srv),
            name="drop_test_layer",
            sql="SELECT id, name, geom FROM app.cities",
        )

        drop_result = await drop_layer(fake_ctx_factory(srv), name="drop_test_layer")
        assert drop_result["dropped"] == "drop_test_layer"

        remaining = await list_layers(fake_ctx_factory(srv))

    names = [layer["name"] for layer in remaining["layers"]]
    assert "drop_test_layer" not in names


# ---------------------------------------------------------------------------
# Test 12: drop_layer — unknown layer
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_drop_layer_unknown(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.layers import drop_layer

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, layer_schema="mcp_layers")
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)

        with pytest.raises(ToolError) as exc:
            await drop_layer(fake_ctx_factory(srv), name="missing_layer")

    assert exc.value.code == "not_found"
