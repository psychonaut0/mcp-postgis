"""Integration tests for import_geojson (v0.3.0)."""
from __future__ import annotations

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


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _pt(lon, lat, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


@pytest.mark.integration
async def test_import_create_happy_path(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        gj = _fc(
            _pt(12.5, 41.9, name="Rome", kind="city"),
            _pt(9.19, 45.46, name="Milan"),
            _pt(9.12, 39.22, name="Cagliari"),
        )
        result = await import_geojson(
            fake_ctx_factory(srv),
            target_schema="mcp_layers", target_table="imported_cities", geojson=gj,
        )
        assert result["full_name"] == "mcp_layers.imported_cities"
        assert result["mode"] == "create"
        assert result["rows_imported"] == 3
        assert result["srid"] == 4326
        assert result["geom_column"] == "geom"

        async with db.read() as cur:
            await cur.execute(
                "SELECT count(*), "
                "       count(*) FILTER (WHERE ST_SRID(geom) = 4326), "
                "       count(*) FILTER (WHERE properties->>'name' IS NOT NULL) "
                "FROM mcp_layers.imported_cities"
            )
            cnt, srid_ok, named = await cur.fetchone()
            assert cnt == 3 and srid_ok == 3 and named == 3
            await cur.execute(
                "SELECT properties->>'kind' FROM mcp_layers.imported_cities "
                "WHERE properties->>'name' = 'Rome'"
            )
            assert (await cur.fetchone())[0] == "city"
            await cur.execute(
                "SELECT count(*) FROM pg_indexes "
                "WHERE schemaname='mcp_layers' AND tablename='imported_cities' "
                "AND indexdef ILIKE '%USING gist%'"
            )
            assert (await cur.fetchone())[0] == 1


def _poly(**props):
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[8.0, 38.8], [9.8, 38.8], [9.8, 41.3], [8.0, 41.3], [8.0, 38.8]]],
        },
        "properties": props,
    }


# --- Task 2: append, mixed geometry, single feature ---

@pytest.mark.integration
async def test_import_mixed_geometry_types(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        gj = _fc(_pt(12.5, 41.9, name="pt"), _poly(name="poly"))
        result = await import_geojson(
            fake_ctx_factory(srv),
            target_schema="mcp_layers", target_table="mixed_import", geojson=gj,
        )
        assert result["rows_imported"] == 2
        async with db.read() as cur:
            await cur.execute(
                "SELECT count(DISTINCT GeometryType(geom)) FROM mcp_layers.mixed_import"
            )
            assert (await cur.fetchone())[0] == 2


@pytest.mark.integration
async def test_import_append(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        await import_geojson(
            fake_ctx_factory(srv),
            target_schema="mcp_layers", target_table="appendable",
            geojson=_fc(_pt(1, 1, n="a")),
        )
        result = await import_geojson(
            fake_ctx_factory(srv),
            target_schema="mcp_layers", target_table="appendable",
            geojson=_fc(_pt(2, 2, n="b"), _pt(3, 3, n="c")), mode="append",
        )
        assert result["mode"] == "append"
        assert result["rows_imported"] == 2
        async with db.read() as cur:
            await cur.execute("SELECT count(*) FROM mcp_layers.appendable")
            assert (await cur.fetchone())[0] == 3


@pytest.mark.integration
async def test_import_single_feature(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await import_geojson(
            fake_ctx_factory(srv),
            target_schema="mcp_layers", target_table="one_feature",
            geojson=_pt(5, 5, name="solo"),
        )
        assert result["rows_imported"] == 1


# --- Task 3: mode/schema gating ---

@pytest.mark.integration
async def test_import_read_only_denied(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="nope", geojson=_fc(_pt(1, 1)),
            )
        assert exc.value.code == "permission_denied"


@pytest.mark.integration
async def test_import_read_write_nonlayer_schema_denied(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="app", target_table="sneaky", geojson=_fc(_pt(1, 1)),
            )
        assert exc.value.code == "permission_denied"


@pytest.mark.integration
async def test_import_admin_other_schema_ok(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.ADMIN)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await import_geojson(
            fake_ctx_factory(srv),
            target_schema="app", target_table="admin_import",
            geojson=_fc(_pt(1, 1, name="x")),
        )
        assert result["full_name"] == "app.admin_import"
        assert result["rows_imported"] == 1


# --- Task 4: edge cases ---

@pytest.mark.integration
async def test_import_create_on_existing_errors(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        await import_geojson(
            fake_ctx_factory(srv),
            target_schema="mcp_layers", target_table="dup", geojson=_fc(_pt(1, 1)),
        )
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="dup", geojson=_fc(_pt(2, 2)),
            )
        assert exc.value.code == "invalid_argument"
        assert "already exists" in exc.value.message


@pytest.mark.integration
async def test_import_append_missing_table(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="ghost",
                geojson=_fc(_pt(1, 1)), mode="append",
            )
        assert exc.value.code == "not_found"


@pytest.mark.integration
async def test_import_bad_mode(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="x",
                geojson=_fc(_pt(1, 1)), mode="replace",
            )
        assert exc.value.code == "invalid_argument"


@pytest.mark.integration
async def test_import_not_a_featurecollection(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="x",
                geojson={"type": "Point", "coordinates": [0, 0]},
            )
        assert exc.value.code == "invalid_argument"


@pytest.mark.integration
async def test_import_null_geometry_feature(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        gj = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": None, "properties": {"x": 1}},
        ]}
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="x", geojson=gj,
            )
        assert exc.value.code == "invalid_geom"


@pytest.mark.integration
async def test_import_over_cap_errors_and_no_table(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE, max_rows=2)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        gj = _fc(_pt(1, 1), _pt(2, 2), _pt(3, 3))
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="too_big", geojson=gj,
            )
        assert exc.value.code == "invalid_argument"
        async with db.read() as cur:
            await cur.execute("SELECT to_regclass('mcp_layers.too_big') IS NULL")
            assert (await cur.fetchone())[0] is True


@pytest.mark.integration
async def test_import_malformed_geometry_rolls_back(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        gj = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "NotARealType", "coordinates": [0, 0]},
             "properties": {"x": 1}},
        ]}
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="rollback_me", geojson=gj,
            )
        assert exc.value.code == "invalid_geom"
        async with db.read() as cur:
            await cur.execute("SELECT to_regclass('mcp_layers.rollback_me') IS NULL")
            assert (await cur.fetchone())[0] is True


@pytest.mark.integration
async def test_import_append_malformed_rolls_back_no_partial_rows(
    db_url: str, fake_ctx_factory
) -> None:
    # spec §9: a malformed feature in APPEND mode leaves no partial rows.
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        await import_geojson(
            fake_ctx_factory(srv),
            target_schema="mcp_layers", target_table="append_rb",
            geojson=_fc(_pt(1, 1, n="seed")),
        )
        gj = {"type": "FeatureCollection", "features": [
            _pt(2, 2, n="ok"),
            {"type": "Feature",
             "geometry": {"type": "NotARealType", "coordinates": [0, 0]},
             "properties": {"n": "bad"}},
        ]}
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="append_rb",
                geojson=gj, mode="append",
            )
        assert exc.value.code == "invalid_geom"
        async with db.read() as cur:
            await cur.execute("SELECT count(*) FROM mcp_layers.append_rb")
            assert (await cur.fetchone())[0] == 1  # only the seed row; append rolled back


@pytest.mark.integration
async def test_import_bad_name_rejected(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.ingest import import_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_WRITE)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await import_geojson(
                fake_ctx_factory(srv),
                target_schema="mcp_layers", target_table="Bad Name", geojson=_fc(_pt(1, 1)),
            )
        assert exc.value.code == "invalid_argument"
