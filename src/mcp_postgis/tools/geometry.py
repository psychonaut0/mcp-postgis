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


def register(mcp: FastMCP) -> None:
    mcp.tool()(transform_srid)
    # Further ops are appended to this register() in later tasks.
