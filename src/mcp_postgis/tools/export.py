"""§6 export tools: GeoJSON FeatureCollection and WKT rows. Read-only."""
from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from psycopg.sql import SQL, Composable, Identifier

from mcp_postgis.config import Mode
from mcp_postgis.errors import ToolError, translate
from mcp_postgis.safety import ensure_allowed
from mcp_postgis.server import ServerContext

_Ctx = Context[Any, Any, Any]

# A bare table reference: optional schema, then table; identifiers only.
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


async def _detect_geom_col(cur: Any) -> tuple[str | None, list[str]]:
    """After a query has executed (LIMIT 0 probe), return (geom_col_name,
    all_col_names). Geometry/geography columns are found via pg_type OID lookup."""
    if not cur.description:
        return None, []
    # Capture names and oids from description BEFORE the pg_type query replaces it.
    names = [d.name for d in cur.description]
    oids = [d.type_code for d in cur.description]
    await cur.execute(
        "SELECT oid, typname FROM pg_type WHERE oid = ANY(%s::oid[])", (oids,)
    )
    typname_by_oid = {r[0]: r[1] for r in await cur.fetchall()}
    geom = next(
        (n for n, o in zip(names, oids, strict=True)
         if typname_by_oid.get(o) in ("geometry", "geography")),
        None,
    )
    return geom, names


def _base_query(source: str, srv: ServerContext) -> Composable:
    """Return a composable SELECT for `source`: a bare table identifier becomes
    SELECT * FROM <ident>; otherwise it is treated as a read-only SELECT."""
    if _TABLE_RE.match(source.strip()):
        parts = source.strip().split(".")
        return SQL("SELECT * FROM {}").format(Identifier(*parts))
    info = ensure_allowed(source, mode=Mode.READ_ONLY, layer_schema=srv.cfg.layer_schema)
    if not info.is_read_only:
        raise ToolError("permission_denied", "export requires a read-only SELECT")
    return SQL(source)


async def export_geojson(
    ctx: _Ctx, source: str, max_features: int | None = None
) -> dict[str, Any]:
    """Export a table or read-only SELECT as a GeoJSON FeatureCollection.
    `source` is a bare `schema.table`/`table` identifier or a SELECT statement."""
    srv: ServerContext = ctx.request_context.lifespan_context
    cap = srv.cfg.max_rows if max_features is None else max(1, min(max_features, srv.cfg.max_rows))
    try:
        base = _base_query(source, srv)
        async with srv.db.read() as cur:
            await cur.execute(SQL("SELECT * FROM ({}) s LIMIT 0").format(base))
            geom_col, _ = await _detect_geom_col(cur)
            if geom_col is None:
                raise ToolError(
                    "invalid_geom", "source has no geometry/geography column to export"
                )
            gid = Identifier(geom_col)
            rows_sql = SQL(
                "SELECT ST_AsGeoJSON(s.{g})::json AS __geom, "
                "       to_jsonb(s) - %s::text AS __props "
                "FROM ({base}) s LIMIT %s"
            ).format(g=gid, base=base)
            await cur.execute(rows_sql, (geom_col, cap + 1))
            rows = await cur.fetchall()
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e

    truncated = len(rows) > cap
    rows = rows[:cap]
    features = [
        {"type": "Feature", "geometry": r[0], "properties": r[1] or {}} for r in rows
    ]
    return {"type": "FeatureCollection", "features": features, "truncated": truncated}


async def export_wkt(
    ctx: _Ctx, sql: str, with_attributes: bool = True, max_rows: int | None = None
) -> dict[str, Any]:
    """Export a read-only SELECT as one WKT string per row (+ attributes)."""
    srv: ServerContext = ctx.request_context.lifespan_context
    cap = srv.cfg.max_rows if max_rows is None else max(1, min(max_rows, srv.cfg.max_rows))
    try:
        info = ensure_allowed(sql, mode=Mode.READ_ONLY, layer_schema=srv.cfg.layer_schema)
        if not info.is_read_only:
            raise ToolError("permission_denied", "export requires a read-only SELECT")
        base = SQL(sql)
        async with srv.db.read() as cur:
            await cur.execute(SQL("SELECT * FROM ({}) s LIMIT 0").format(base))
            geom_col, names = await _detect_geom_col(cur)
            if geom_col is None:
                raise ToolError("invalid_geom", "SELECT has no geometry column")
            gid = Identifier(geom_col)
            other = [n for n in names if n != geom_col] if with_attributes else []
            select_other = SQL(", ").join(
                [SQL("s.{}").format(Identifier(n)) for n in other]
            )
            sep = SQL(", ") if other else SQL("")
            rows_sql = SQL(
                "SELECT ST_AsText(s.{g}) AS wkt{sep}{others} FROM ({base}) s LIMIT %s"
            ).format(g=gid, sep=sep, others=select_other, base=base)
            await cur.execute(rows_sql, (cap + 1,))
            desc = [d.name for d in cur.description] if cur.description else []
            fetched = await cur.fetchall()
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e

    truncated = len(fetched) > cap
    fetched = fetched[:cap]
    out_rows = [dict(zip(desc, r, strict=True)) for r in fetched]
    return {"columns": desc, "rows": out_rows, "truncated": truncated}


def register(mcp: FastMCP) -> None:
    mcp.tool()(export_geojson)
    mcp.tool()(export_wkt)
