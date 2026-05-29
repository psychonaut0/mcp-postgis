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
