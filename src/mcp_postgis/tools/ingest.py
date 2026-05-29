"""import_geojson — load a GeoJSON FeatureCollection into a PostGIS table.

The first data-ingestion write path. Builds its own parameterised DDL/DML
(Identifier-quoted) inside a write transaction; it does NOT run user SQL, so it
is gated by mode + schema checks (not the ensure_allowed classifier).
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from psycopg.sql import SQL, Identifier, Literal
from psycopg.types.json import Jsonb

from mcp_postgis.context import ServerContext
from mcp_postgis.errors import ToolError, translate
from mcp_postgis.tools.layers import _require_writeable, _validate_name

_Ctx = Context[Any, Any, Any]


def _parse_features(geojson: str | dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise input to a list of Feature dicts, or raise ToolError."""
    if isinstance(geojson, str):
        try:
            obj = json.loads(geojson)
        except json.JSONDecodeError as e:
            raise ToolError("invalid_argument", f"geojson is not valid JSON: {e}") from e
    elif isinstance(geojson, dict):
        obj = geojson
    else:
        raise ToolError("invalid_argument", "geojson must be a JSON string or object")

    gtype = obj.get("type")
    if gtype == "FeatureCollection":
        features = obj.get("features")
        if not isinstance(features, list):
            raise ToolError("invalid_argument", "FeatureCollection has no 'features' array")
        return features
    if gtype == "Feature":
        return [obj]
    raise ToolError("invalid_argument", "expected a GeoJSON FeatureCollection or Feature")


def _check_target(cfg: Any, target_schema: str) -> None:
    """Mode + schema gate. read_write -> layer schema only; admin -> any."""
    from mcp_postgis.config import Mode

    _require_writeable(cfg)  # raises permission_denied in read_only
    if cfg.mode is Mode.READ_WRITE and target_schema != cfg.layer_schema:
        raise ToolError(
            "permission_denied",
            f"read_write mode can only import into the layer schema "
            f"'{cfg.layer_schema}'; use admin mode for other schemas",
        )


async def import_geojson(
    ctx: _Ctx,
    target_schema: str,
    target_table: str,
    geojson: str | dict[str, Any],
    srid: int = 4326,
    mode: str = "create",
) -> dict[str, Any]:
    """Load a GeoJSON FeatureCollection (or Feature) into <target_schema>.<target_table>.

    Table shape: (id BIGSERIAL PK, geom GEOMETRY(Geometry, srid), properties JSONB),
    with a GIST index on geom. Each feature's properties are stored as jsonb.

    mode="create" makes a new table (errors if it exists); mode="append" inserts
    into an existing table (must have geom + properties columns). Requires
    read_write (layer schema only) or admin (any schema)."""
    srv: ServerContext = ctx.request_context.lifespan_context
    cfg = srv.cfg

    if mode not in ("create", "append"):
        raise ToolError("invalid_argument", "mode must be 'create' or 'append'")
    _check_target(cfg, target_schema)
    _validate_name(target_table)

    features = _parse_features(geojson)
    if len(features) > cfg.max_rows:
        raise ToolError(
            "invalid_argument",
            f"{len(features)} features exceeds MAX_ROWS={cfg.max_rows}; "
            "split the import into smaller batches",
        )

    rows: list[tuple[str, Jsonb]] = []
    for i, feat in enumerate(features):
        if not isinstance(feat, dict) or feat.get("geometry") is None:
            raise ToolError("invalid_geom", f"feature at index {i} has no geometry")
        rows.append((json.dumps(feat["geometry"]), Jsonb(feat.get("properties") or {})))

    sch = Identifier(target_schema)
    tbl = Identifier(target_table)
    idx = Identifier(f"{target_table}_geom_gix")

    try:
        async with srv.db.write() as cur:
            if mode == "create":
                await cur.execute(
                    "SELECT to_regclass(format('%%I.%%I', %s::text, %s::text)) IS NOT NULL",
                    (target_schema, target_table),
                )
                exists = await cur.fetchone()
                if exists and exists[0]:
                    raise ToolError(
                        "invalid_argument",
                        f"{target_schema}.{target_table} already exists; "
                        "use mode='append' or drop it first",
                    )
                # SRID in a geometry typmod must be a LITERAL (bind param rejected).
                await cur.execute(
                    SQL(
                        "CREATE TABLE {sch}.{tbl} ("
                        " id BIGSERIAL PRIMARY KEY, "
                        " geom geometry(Geometry, {srid}), "
                        " properties JSONB)"
                    ).format(sch=sch, tbl=tbl, srid=Literal(srid))
                )
                await cur.execute(
                    SQL("CREATE INDEX {idx} ON {sch}.{tbl} USING GIST (geom)").format(
                        idx=idx, sch=sch, tbl=tbl
                    )
                )
            else:  # append
                await cur.execute(
                    "SELECT to_regclass(format('%%I.%%I', %s::text, %s::text)) IS NOT NULL",
                    (target_schema, target_table),
                )
                exists = await cur.fetchone()
                if not exists or not exists[0]:
                    raise ToolError(
                        "not_found",
                        f"{target_schema}.{target_table} does not exist (use mode='create')",
                    )
                await cur.execute(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s "
                    "AND column_name IN ('geom', 'properties')",
                    (target_schema, target_table),
                )
                col_row = await cur.fetchone()
                if not col_row or col_row[0] < 2:
                    raise ToolError(
                        "invalid_argument",
                        f"{target_schema}.{target_table} is missing geom/properties "
                        "columns; not an import-compatible table",
                    )

            insert = SQL(
                "INSERT INTO {sch}.{tbl} (geom, properties) "
                "VALUES (ST_SetSRID(ST_GeomFromGeoJSON(%s), %s), %s)"
            ).format(sch=sch, tbl=tbl)
            await cur.executemany(insert, [(g, srid, p) for (g, p) in rows])
            rows_imported = len(rows)
    except ToolError:
        raise
    except Exception as e:
        raise translate(e) from e

    return {
        "schema": target_schema,
        "table": target_table,
        "full_name": f"{target_schema}.{target_table}",
        "srid": srid,
        "mode": mode,
        "rows_imported": rows_imported,
        "geom_column": "geom",
    }


def register(mcp: FastMCP) -> None:
    mcp.tool()(import_geojson)
