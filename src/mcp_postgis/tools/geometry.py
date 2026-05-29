"""§4 inline geometry operations + §5 validity checker.

Every op takes an inline geometry (WKT or GeoJSON) and runs a single SELECT in
a read-only transaction. GeoJSON outputs echo the input SRID and are strictly
RFC 7946-conformant only at 4326 (the default); transform_srid omits geojson
for non-4326 targets.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from psycopg.sql import SQL, Identifier

from mcp_postgis.context import ServerContext
from mcp_postgis.errors import ToolError, translate
from mcp_postgis.geom import parse_geom_input

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
        "WITH g AS (SELECT (" + frag + ") AS o), "  # noqa: S608
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
        "WITH g AS (SELECT (" + frag + ") AS geom) "  # noqa: S608
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


async def _primary_key_expr(cur: Any, schema: str, table: str) -> Any:
    """Return an SQL expression for the row id: the single-column PK if there is
    one, else ctid::text. Returns a psycopg SQL/Identifier composable."""
    await cur.execute(
        "SELECT a.attname "
        "FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = to_regclass(%s) AND i.indisprimary",
        (f"{schema}.{table}",),
    )
    pk_cols = await cur.fetchall()
    if len(pk_cols) == 1:
        return Identifier(pk_cols[0][0])
    return SQL("ctid::text")


async def check_geometry_validity(
    ctx: _Ctx,
    schema: str,
    table: str,
    geom_col: str,
    limit: int | None = None,
) -> dict[str, Any]:
    """Scan a table for invalid or out-of-range (lon/lat) geometries. Read-only.
    Returns counts plus a capped sample of offenders; publish them as a QA layer
    via create_layer if you want to inspect them in QGIS."""
    srv: ServerContext = ctx.request_context.lifespan_context
    cap = srv.cfg.max_rows if limit is None else max(1, min(limit, srv.cfg.max_rows))
    g = Identifier(geom_col)
    sch = Identifier(schema)
    tbl = Identifier(table)

    try:
        async with srv.db.read() as cur:
            await cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{table}",))
            exists = await cur.fetchone()
            if not exists or not exists[0]:
                raise ToolError("not_found", f"{schema}.{table} does not exist")

            id_expr = await _primary_key_expr(cur, schema, table)

            oor = SQL(
                "(ST_XMin({g}) < -180 OR ST_XMax({g}) > 180 "
                " OR ST_YMin({g}) < -90 OR ST_YMax({g}) > 90)"
            ).format(g=g)

            counts_sql = SQL(
                "SELECT count(*) FILTER (WHERE NOT ST_IsValid({g})), "
                "       count(*) FILTER (WHERE {oor}) "
                "FROM {sch}.{tbl}"
            ).format(g=g, oor=oor, sch=sch, tbl=tbl)
            await cur.execute(counts_sql)
            crow = await cur.fetchone()
            invalid_count = int(crow[0]) if crow else 0
            out_of_range_count = int(crow[1]) if crow else 0

            sample_sql = SQL(
                "SELECT ({id})::text AS id, "
                "       ST_IsValidReason({g}) AS reason, "
                "       NOT ST_IsValid({g}) AS invalid, "
                "       {oor} AS out_of_range "
                "FROM {sch}.{tbl} "
                "WHERE NOT ST_IsValid({g}) OR {oor} "
                "LIMIT %s"
            ).format(id=id_expr, g=g, oor=oor, sch=sch, tbl=tbl)
            await cur.execute(sample_sql, (cap + 1,))
            rows = await cur.fetchall()
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e

    truncated = len(rows) > cap
    rows = rows[:cap]
    offenders = [
        {"id": r[0], "reason": r[1], "invalid": bool(r[2]), "out_of_range": bool(r[3])}
        for r in rows
    ]
    return {
        "schema": schema,
        "table": table,
        "geom_col": geom_col,
        "invalid_count": invalid_count,
        "out_of_range_count": out_of_range_count,
        "offenders": offenders,
        "truncated": truncated,
        "hint": (
            "publish offenders as a QA layer with create_layer using the same "
            "predicates; preview a fix with make_valid"
        ),
    }


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
    mcp.tool()(check_geometry_validity)
