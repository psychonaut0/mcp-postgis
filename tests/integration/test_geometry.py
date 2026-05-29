"""Integration tests for §4/§5 geometry tools."""
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
async def test_transform_srid_4326_to_3857(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import transform_srid

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await transform_srid(
            fake_ctx_factory(srv), geom="POINT(12.4964 41.9028)", target_srid=3857,
        )
        assert result["srid"] == 3857
        assert result["wkt"].upper().startswith("POINT")
        assert "geojson" not in result or result["geojson"] is None
        import re
        x = float(re.findall(r"[-\d.]+", result["wkt"])[0])
        assert 1_300_000 < x < 1_500_000


@pytest.mark.integration
async def test_transform_srid_to_4326_includes_geojson(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import transform_srid

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await transform_srid(
            fake_ctx_factory(srv),
            geom="POINT(1389467 5146427)", source_srid=3857, target_srid=4326,
        )
        assert result["srid"] == 4326
        assert result["geojson"]["type"] == "Point"


@pytest.mark.integration
async def test_transform_srid_bad_geom(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import transform_srid

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await transform_srid(fake_ctx_factory(srv), geom="not a geom", target_srid=3857)
        assert exc.value.code == "invalid_geom"
