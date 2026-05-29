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


_SARDINIA = "POLYGON((8.0 38.8, 9.8 38.8, 9.8 41.3, 8.0 41.3, 8.0 38.8))"


@pytest.mark.integration
async def test_centroid_inside_bbox(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import centroid

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await centroid(fake_ctx_factory(srv), geom=_SARDINIA)
        assert result["geometry"]["type"] == "Point"
        x, y = result["geometry"]["coordinates"]
        assert 8.0 < x < 9.8 and 38.8 < y < 41.3


@pytest.mark.integration
async def test_point_on_surface_inside(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import point_on_surface

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await point_on_surface(fake_ctx_factory(srv), geom=_SARDINIA)
        assert result["geometry"]["type"] == "Point"


@pytest.mark.integration
async def test_bbox_bounds(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import bbox

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await bbox(fake_ctx_factory(srv), geom=_SARDINIA)
        assert result["bounds"] == [8.0, 38.8, 9.8, 41.3]
        assert result["geometry"]["type"] == "Polygon"


_BOWTIE = "POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))"


@pytest.mark.integration
async def test_area_km2_positive(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import area

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await area(fake_ctx_factory(srv), geom=_SARDINIA, unit="km2")
        assert result["unit"] == "km2"
        assert 10_000 < result["area"] < 100_000


@pytest.mark.integration
async def test_length_m_positive(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import length

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await length(
            fake_ctx_factory(srv),
            geom="LINESTRING(9.12 39.22, 9.19 45.46)", unit="km",
        )
        assert result["unit"] == "km"
        assert result["length"] > 600


@pytest.mark.integration
async def test_simplify_reduces_vertices(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import simplify

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        dense = (
            "LINESTRING(0 0, 0.1 0.01, 0.2 -0.01, 0.3 0.0, 0.4 0.01, "
            "0.5 0.0, 0.6 -0.01, 0.7 0.0, 0.8 0.0, 1 0)"
        )
        result = await simplify(fake_ctx_factory(srv), geom=dense, tolerance=0.05)
        assert result["vertices_after"] < result["vertices_before"]
        assert result["geometry"]["type"] == "LineString"


@pytest.mark.integration
async def test_is_valid_false_on_bowtie(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import is_valid

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await is_valid(fake_ctx_factory(srv), geom=_BOWTIE)
        assert result["valid"] is False
        assert result["reason"]


@pytest.mark.integration
async def test_make_valid_fixes_bowtie(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.geometry import is_valid, make_valid

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await make_valid(fake_ctx_factory(srv), geom=_BOWTIE)
        assert result["result_type"]
        again = await is_valid(fake_ctx_factory(srv), geom=result["wkt"])
        assert again["valid"] is True
