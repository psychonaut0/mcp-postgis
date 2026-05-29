"""Integration tests for §6.3 spatial-analysis tools."""
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


# 9.1 features_in_bbox

@pytest.mark.integration
async def test_features_in_bbox_finds_rome(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import features_in_bbox
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await features_in_bbox(
            fake_ctx_factory(srv),
            schema="app", table="cities", geom_col="geom",
            min_x=12.0, min_y=41.0, max_x=13.0, max_y=42.0,
        )
        names = [f["properties"]["name"] for f in result["features"]]
        assert names == ["Rome"]
        feat = result["features"][0]
        assert feat["geometry"]["type"] == "Point"
        assert feat["geometry"]["coordinates"][0] == pytest.approx(12.4964, abs=1e-3)


@pytest.mark.integration
async def test_features_in_bbox_truncation(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import features_in_bbox
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY, max_rows=1)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await features_in_bbox(
            fake_ctx_factory(srv),
            schema="app", table="cities", geom_col="geom",
            min_x=-180, min_y=-90, max_x=180, max_y=90, limit=10,
        )
        assert len(result["features"]) == 1
        assert result["truncated"] is True


# 9.2 features_in_polygon

@pytest.mark.integration
async def test_features_in_polygon_within(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import features_in_polygon
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        polygon_wkt = "POLYGON((6.6 36.6, 18.5 36.6, 18.5 47.1, 6.6 47.1, 6.6 36.6))"
        result = await features_in_polygon(
            fake_ctx_factory(srv),
            schema="app", table="cities", geom_col="geom",
            polygon=polygon_wkt, predicate="within",
        )
        names = sorted(f["properties"]["name"] for f in result["features"])
        assert names == ["Cagliari", "Milan", "Rome"]


@pytest.mark.integration
async def test_features_in_polygon_geojson_input(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import features_in_polygon
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        polygon_gj = {
            "type": "Polygon",
            "coordinates": [[
                [12.0, 41.0], [13.0, 41.0],
                [13.0, 42.0], [12.0, 42.0], [12.0, 41.0],
            ]],
        }
        result = await features_in_polygon(
            fake_ctx_factory(srv),
            schema="app", table="cities", geom_col="geom",
            polygon=polygon_gj, predicate="intersects",
        )
        assert [f["properties"]["name"] for f in result["features"]] == ["Rome"]


@pytest.mark.integration
async def test_features_in_polygon_bad_predicate(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import features_in_polygon
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await features_in_polygon(
                fake_ctx_factory(srv),
                schema="app", table="cities", geom_col="geom",
                polygon="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
                predicate="hugs",
            )
        assert exc.value.code == "invalid_argument"


# 9.3 nearest_features

@pytest.mark.integration
async def test_nearest_features_orders_by_distance(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import nearest_features
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await nearest_features(
            fake_ctx_factory(srv),
            schema="app", table="cities", geom_col="geom",
            point="POINT(12.5 41.9)", k=3,
        )
        names = [f["properties"]["name"] for f in result["features"]]
        assert names[0] == "Rome"
        assert len(names) == 3
        distances = [f["properties"]["__distance_m"] for f in result["features"]]
        assert distances == sorted(distances)


@pytest.mark.integration
async def test_nearest_features_with_max_distance(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import nearest_features
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await nearest_features(
            fake_ctx_factory(srv),
            schema="app", table="cities", geom_col="geom",
            point="POINT(12.5 41.9)", k=10, max_distance_m=1_000,
        )
        names = [f["properties"]["name"] for f in result["features"]]
        assert names == ["Rome"]


# 9.4 within_distance

@pytest.mark.integration
async def test_within_distance_finds_rome(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import within_distance
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await within_distance(
            fake_ctx_factory(srv),
            schema="app", table="cities", geom_col="geom",
            geom="POINT(12.5 41.9)", distance_m=50_000,
        )
        assert [f["properties"]["name"] for f in result["features"]] == ["Rome"]


# 9.5 buffer

@pytest.mark.integration
async def test_buffer_returns_geojson_polygon(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import buffer
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await buffer(
            fake_ctx_factory(srv),
            geom="POINT(12.5 41.9)", distance_m=1000, return_format="geojson",
        )
        assert result["geometry"]["type"] == "Polygon"
        assert result["area_m2"] > 0


@pytest.mark.integration
async def test_buffer_returns_wkt(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import buffer
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await buffer(
            fake_ctx_factory(srv),
            geom="POINT(12.5 41.9)", distance_m=1000, return_format="wkt",
        )
        assert result["wkt"].startswith("POLYGON")


# 9.6 intersect_layers

@pytest.mark.integration
async def test_intersect_layers_left_with_right_attrs(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import intersect_layers
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await intersect_layers(
            fake_ctx_factory(srv),
            left_schema="app", left_table="cities", left_geom="geom",
            right_schema="app", right_table="regions", right_geom="geom",
            return_format="left_with_right_attrs",
        )
        assert len(result["features"]) == 3
        assert all("r__name" in f["properties"] for f in result["features"])
        assert {f["properties"]["r__name"] for f in result["features"]} == {"Italy-bbox"}


@pytest.mark.integration
async def test_intersect_layers_intersection_geom(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.spatial import intersect_layers
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await intersect_layers(
            fake_ctx_factory(srv),
            left_schema="app", left_table="regions", left_geom="geom",
            right_schema="app", right_table="regions", right_geom="geom",
            return_format="intersection_geom",
        )
        assert len(result["features"]) == 1
        assert result["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")
