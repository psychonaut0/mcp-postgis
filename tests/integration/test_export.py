"""Integration tests for §6 export tools."""
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


@pytest.mark.integration
async def test_export_geojson_from_table(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.export import export_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await export_geojson(fake_ctx_factory(srv), source="app.cities")
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 3
        f = result["features"][0]
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] == "Point"
        assert "name" in f["properties"]
        assert result["truncated"] is False


@pytest.mark.integration
async def test_export_geojson_from_select(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.export import export_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await export_geojson(
            fake_ctx_factory(srv),
            source="SELECT name, geom FROM app.cities WHERE name = 'Rome'",
        )
        assert len(result["features"]) == 1
        assert result["features"][0]["properties"]["name"] == "Rome"


@pytest.mark.integration
async def test_export_geojson_truncates(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.export import export_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY, max_rows=2)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await export_geojson(fake_ctx_factory(srv), source="app.cities")
        assert len(result["features"]) == 2
        assert result["truncated"] is True


@pytest.mark.integration
async def test_export_geojson_rejects_write(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.export import export_geojson

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await export_geojson(fake_ctx_factory(srv), source="DELETE FROM app.cities")
        assert exc.value.code == "permission_denied"


@pytest.mark.integration
async def test_export_wkt_with_attrs(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.export import export_wkt

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await export_wkt(
            fake_ctx_factory(srv),
            sql="SELECT name, geom FROM app.cities ORDER BY name",
        )
        assert "wkt" in result["columns"]
        assert result["rows"][0]["wkt"].upper().startswith("POINT")
        assert result["rows"][0]["name"] == "Cagliari"
