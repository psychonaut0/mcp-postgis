"""§4 inline geometry operations + §5 validity checker.

Every op takes an inline geometry (WKT or GeoJSON) and runs a single SELECT in
a read-only transaction. GeoJSON outputs echo the input SRID and are strictly
RFC 7946-conformant only at 4326 (the default); transform_srid omits geojson
for non-4326 targets.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from psycopg.sql import SQL

from mcp_postgis.errors import ToolError, translate
from mcp_postgis.geom import parse_geom_input
from mcp_postgis.server import ServerContext

_Ctx = Context[Any, Any, Any]


def _frag(geom: str | dict[str, Any], srid: int) -> tuple[str, tuple[Any, ...]]:
    """Parse geometry input into an SQL fragment + params, or raise ToolError."""
    try:
        g = parse_geom_input(geom)
    except ValueError as e:
        raise ToolError("invalid_geom", str(e)) from e
    return g.to_sql_fragment(srid=srid)


async def transform_srid(
    ctx: _Ctx,
    geom: str | dict[str, Any],
    target_srid: int,
    source_srid: int = 4326,
) -> dict[str, Any]:
    """Reproject a geometry to target_srid. Returns WKT + srid; includes a GeoJSON
    representation only when target_srid == 4326 (RFC 7946 is WGS84-only)."""
    srv: ServerContext = ctx.request_context.lifespan_context
    frag, params = _frag(geom, source_srid)
    sql = SQL(
        "WITH g AS (SELECT ST_Transform(" + frag + ", %s) AS t) "
        "SELECT ST_AsText(t), "
        "       CASE WHEN %s = 4326 THEN ST_AsGeoJSON(t)::json ELSE NULL END "
        "FROM g"
    )
    try:
        async with srv.db.read() as cur:
            await cur.execute(sql, (*params, target_srid, target_srid))
            row = await cur.fetchone()
        assert row is not None
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e
    out: dict[str, Any] = {"wkt": row[0], "srid": target_srid}
    if row[1] is not None:
        out["geojson"] = row[1]
    return out


async def centroid(
    ctx: _Ctx, geom: str | dict[str, Any], srid: int = 4326
) -> dict[str, Any]:
    """Geometric centroid of geom (may fall outside concave shapes)."""
    return await _single_geom_op(ctx, "ST_Centroid", geom, srid)


async def point_on_surface(
    ctx: _Ctx, geom: str | dict[str, Any], srid: int = 4326
) -> dict[str, Any]:
    """A point guaranteed to lie on/inside geom (use for label placement)."""
    return await _single_geom_op(ctx, "ST_PointOnSurface", geom, srid)


async def _single_geom_op(
    ctx: _Ctx, fn: str, geom: str | dict[str, Any], srid: int
) -> dict[str, Any]:
    srv: ServerContext = ctx.request_context.lifespan_context
    frag, params = _frag(geom, srid)
    sql = SQL("SELECT ST_AsGeoJSON(" + fn + "(" + frag + "))::json")
    try:
        async with srv.db.read() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
        assert row is not None
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e
    return {"geometry": row[0], "srid": srid}


async def bbox(
    ctx: _Ctx, geom: str | dict[str, Any], srid: int = 4326
) -> dict[str, Any]:
    """Envelope (bounding box) of geom as a GeoJSON polygon + [minx,miny,maxx,maxy]."""
    srv: ServerContext = ctx.request_context.lifespan_context
    frag, params = _frag(geom, srid)
    sql = SQL(
        "WITH g AS (SELECT (" + frag + ") AS geom) "
        "SELECT ST_AsGeoJSON(ST_Envelope(geom))::json, "
        "       ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom) "
        "FROM g"
    )
    try:
        async with srv.db.read() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
        assert row is not None
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e
    return {
        "geometry": row[0],
        "bounds": [float(row[1]), float(row[2]), float(row[3]), float(row[4])],
        "srid": srid,
    }


async def area(
    ctx: _Ctx, geom: str | dict[str, Any], srid: int = 4326, unit: str = "m2"
) -> dict[str, Any]:
    """Geodesic area of geom. unit in {"m2","km2"} (computed on ::geography)."""
    if unit not in ("m2", "km2"):
        raise ToolError("invalid_argument", "unit must be 'm2' or 'km2'")
    srv: ServerContext = ctx.request_context.lifespan_context
    frag, params = _frag(geom, srid)
    sql = SQL("SELECT ST_Area(ST_Transform(" + frag + ", 4326)::geography)")
    try:
        async with srv.db.read() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
        assert row is not None
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e
    m2 = float(row[0])
    return {"area": m2 / 1_000_000 if unit == "km2" else m2, "unit": unit}


async def length(
    ctx: _Ctx, geom: str | dict[str, Any], srid: int = 4326, unit: str = "m"
) -> dict[str, Any]:
    """Geodesic length of geom. unit in {"m","km"} (computed on ::geography)."""
    if unit not in ("m", "km"):
        raise ToolError("invalid_argument", "unit must be 'm' or 'km'")
    srv: ServerContext = ctx.request_context.lifespan_context
    frag, params = _frag(geom, srid)
    sql = SQL("SELECT ST_Length(ST_Transform(" + frag + ", 4326)::geography)")
    try:
        async with srv.db.read() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
        assert row is not None
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e
    m = float(row[0])
    return {"length": m / 1000 if unit == "km" else m, "unit": unit}


async def simplify(
    ctx: _Ctx,
    geom: str | dict[str, Any],
    tolerance: float,
    preserve_topology: bool = True,
    srid: int = 4326,
) -> dict[str, Any]:
    """Simplify geom (Douglas-Peucker). tolerance is in the SRID's units — for
    4326 that is DEGREES (~0.001 deg = 100 m near the equator)."""
    if tolerance < 0:
        raise ToolError("invalid_argument", "tolerance must be >= 0")
    srv: ServerContext = ctx.request_context.lifespan_context
    frag, params = _frag(geom, srid)
    fn = "ST_SimplifyPreserveTopology" if preserve_topology else "ST_Simplify"
    sql = SQL(
        "WITH g AS (SELECT (" + frag + ") AS o), "
        "s AS (SELECT o, " + fn + "(o, %s) AS simp FROM g) "
        "SELECT ST_AsGeoJSON(simp)::json, ST_NPoints(o), ST_NPoints(simp) FROM s"
    )
    try:
        async with srv.db.read() as cur:
            await cur.execute(sql, (*params, tolerance))
            row = await cur.fetchone()
        assert row is not None
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e
    return {
        "geometry": row[0],
        "vertices_before": int(row[1]),
        "vertices_after": int(row[2]),
        "srid": srid,
    }


async def is_valid(
    ctx: _Ctx, geom: str | dict[str, Any], srid: int = 4326
) -> dict[str, Any]:
    """Whether geom is OGC-valid, with the reason if not."""
    srv: ServerContext = ctx.request_context.lifespan_context
    frag, params = _frag(geom, srid)
    sql = SQL(
        "WITH g AS (SELECT (" + frag + ") AS geom) "
        "SELECT ST_IsValid(geom), ST_IsValidReason(geom) FROM g"
    )
    try:
        async with srv.db.read() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
        assert row is not None
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e
    valid = bool(row[0])
    return {"valid": valid, "reason": None if valid else row[1]}


async def make_valid(
    ctx: _Ctx, geom: str | dict[str, Any], srid: int = 4326
) -> dict[str, Any]:
    """Repair geom with ST_MakeValid. NOTE: the result type may differ from the
    input (e.g. an invalid polygon may become a multipolygon/collection)."""
    srv: ServerContext = ctx.request_context.lifespan_context
    frag, params = _frag(geom, srid)
    sql = SQL(
        "WITH g AS (SELECT ST_MakeValid(" + frag + ") AS geom) "
        "SELECT "
        "  CASE WHEN %s = 4326 THEN ST_AsGeoJSON(geom)::json ELSE NULL END, "
        "  ST_AsText(geom), GeometryType(geom) "
        "FROM g"
    )
    try:
        async with srv.db.read() as cur:
            await cur.execute(sql, (*params, srid))
            row = await cur.fetchone()
        assert row is not None
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e
    out: dict[str, Any] = {"wkt": row[1], "result_type": row[2], "srid": srid}
    if row[0] is not None:
        out["geometry"] = row[0]
    return out


def register(mcp: FastMCP) -> None:
    mcp.tool()(transform_srid)
    mcp.tool()(centroid)
    mcp.tool()(point_on_surface)
    mcp.tool()(bbox)
    mcp.tool()(area)
    mcp.tool()(length)
    mcp.tool()(simplify)
    mcp.tool()(is_valid)
    mcp.tool()(make_valid)
