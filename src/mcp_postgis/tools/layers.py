"""Layer publishing tools: create, refresh, list, describe, drop."""
from __future__ import annotations

import re
from typing import Any, cast

from mcp.server.fastmcp import Context, FastMCP
from psycopg.sql import SQL, Composable, Identifier, Literal

from mcp_postgis import errors
from mcp_postgis.config import Mode
from mcp_postgis.context import ServerContext
from mcp_postgis.errors import ToolError

# FastMCP Context is Generic[ServerSessionT, LifespanContextT, RequestT]; using
# Any for all params avoids noisy type-arg errors on every function signature.
_Ctx = Context[Any, Any, Any]

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ToolError(
            "invalid_argument",
            f"layer name {name!r} is invalid; must match ^[a-z][a-z0-9_]{{0,62}}$",
        )


def _require_writeable(cfg: Any) -> None:
    if cfg.mode is Mode.READ_ONLY:
        raise ToolError(
            "permission_denied",
            "layer-publishing tools require read_write or admin mode",
            hint="set MCP_POSTGIS_MODE=read_write",
        )


# ---------------------------------------------------------------------------
# Geometry column detection
# ---------------------------------------------------------------------------


async def _detect_geom_column(cur: Any, sql: str) -> str | None:
    """Return the first geometry/geography column in the SELECT, or None.

    PostGIS type OIDs vary per database, so we look them up from pg_type.
    """
    probe = SQL("SELECT * FROM ({inner}) src LIMIT 0").format(inner=SQL(sql))
    await cur.execute(probe)
    if not cur.description:
        return None
    oids = [d.type_code for d in cur.description]
    names = [d.name for d in cur.description]
    await cur.execute(
        "SELECT oid, typname FROM pg_type WHERE oid = ANY(%s::oid[])",
        (oids,),
    )
    typname_by_oid: dict[int, str] = {row[0]: row[1] for row in await cur.fetchall()}
    for col_name, oid in zip(names, oids, strict=True):
        if typname_by_oid.get(oid) in ("geometry", "geography"):
            return cast(str, col_name)
    return None


# ---------------------------------------------------------------------------
# 10.2  create_layer
# ---------------------------------------------------------------------------


async def create_layer(
    ctx: _Ctx,
    name: str,
    sql: str,
    materialized: bool = False,
    description: str | None = None,
    geometry_type: str | None = None,
) -> dict[str, Any]:
    """Publish a SELECT as a (materialized) view in the layer schema.

    *sql* must be a read-only SELECT statement — it is validated via the
    safety classifier before execution.  The view is registered in
    ``<layer_schema>._meta`` for bookkeeping.

    Returns metadata including the detected geometry column and row count.
    """
    srv: ServerContext = ctx.request_context.lifespan_context
    cfg = srv.cfg

    _require_writeable(cfg)
    _validate_name(name)

    _GEOM_TYPE_MAP = {
        "point": ("POINT", "MULTIPOINT"),
        "line": ("LINESTRING", "MULTILINESTRING"),
        "polygon": ("POLYGON", "MULTIPOLYGON"),
    }
    if geometry_type is not None and geometry_type not in _GEOM_TYPE_MAP:
        raise ToolError(
            "invalid_argument",
            f"geometry_type must be one of {sorted(_GEOM_TYPE_MAP)} or omitted",
        )

    # Validate the body SQL is read-only.
    from mcp_postgis import safety
    from mcp_postgis.safety import PermissionDeniedError

    try:
        safety.ensure_allowed(sql, mode=Mode.READ_ONLY, layer_schema=cfg.layer_schema)
    except PermissionDeniedError as exc:
        raise ToolError(
            "permission_denied",
            "create_layer body must be a SELECT (read-only) statement",
        ) from exc

    schema = cfg.layer_schema
    schema_id = Identifier(schema)
    name_id = Identifier(name)
    meta_id = Identifier(schema, "_meta")
    view_id = Identifier(schema, name)

    kind = "materialized_view" if materialized else "view"

    async with srv.db.write() as cur:
        try:
            # Detect geometry column from the source SQL.
            geom_col = await _detect_geom_column(cur, sql)
            if geom_col is None:
                raise ToolError(
                    "invalid_argument",
                    "layer SELECT must include exactly one geometry/geography column"
                    " — QGIS needs one to render the layer",
                    hint="add a column like `geom` or `ST_AsBinary(geom) AS geom`",
                )

            # Build effective SQL: optionally wrap with a geometry-type filter.
            effective_sql: Composable
            if geometry_type is not None:
                wanted = _GEOM_TYPE_MAP[geometry_type]
                types_sql = SQL(", ").join([Literal(t) for t in wanted])
                effective_sql = SQL(
                    "SELECT * FROM ({inner}) __src "
                    "WHERE GeometryType(__src.{gcol}) IN ({types})"
                ).format(inner=SQL(sql), gcol=Identifier(geom_col), types=types_sql)
            else:
                effective_sql = SQL(sql)

            # Drop any existing view/mv of the same name.
            if materialized:
                await cur.execute(
                    SQL("DROP MATERIALIZED VIEW IF EXISTS {view} CASCADE").format(
                        view=view_id
                    )
                )
            else:
                await cur.execute(
                    SQL("DROP VIEW IF EXISTS {view} CASCADE").format(view=view_id)
                )

            # Create the view.
            if materialized:
                await cur.execute(
                    SQL("CREATE MATERIALIZED VIEW {view} AS {sql}").format(
                        view=view_id,
                        sql=effective_sql,
                    )
                )
            else:
                await cur.execute(
                    SQL("CREATE VIEW {view} AS {sql}").format(
                        view=view_id,
                        sql=effective_sql,
                    )
                )

            # For materialized views, create a GIST index on the geom column.
            if materialized:
                index_name = Identifier(f"{name}_geom_gix")
                geom_col_id = Identifier(geom_col)
                await cur.execute(
                    SQL(
                        "CREATE INDEX {idx} ON {schema}.{table} USING GIST ({geom})"
                    ).format(
                        idx=index_name,
                        schema=schema_id,
                        table=name_id,
                        geom=geom_col_id,
                    )
                )

            # Count rows.
            await cur.execute(
                SQL("SELECT count(*) FROM {view}").format(view=view_id)
            )
            row_count_row = await cur.fetchone()
            row_count = int(row_count_row[0]) if row_count_row else 0

            # Detect mixed geometry types when no filter was applied.
            warning = None
            if geometry_type is None:
                await cur.execute(
                    SQL(
                        "SELECT array_agg(DISTINCT "
                        "  regexp_replace(GeometryType({gcol}), '^MULTI', '')) "
                        "FROM {schema}.{view}"
                    ).format(
                        gcol=Identifier(geom_col),
                        schema=schema_id,
                        view=name_id,
                    )
                )
                trow = await cur.fetchone()
                base_types = [t for t in (trow[0] if trow and trow[0] else []) if t]
                if len(base_types) > 1:
                    warning = (
                        f"layer mixes geometry types {sorted(base_types)}; QGIS prefers "
                        "one type per layer — re-run with geometry_type=point|line|polygon"
                    )

            # Upsert into _meta.
            if materialized:
                upsert_sql = SQL(
                    "INSERT INTO {meta} (name, kind, source_sql, description,"
                    " created_at, refreshed_at)"
                    " VALUES (%s, %s, %s, %s, now(), now())"
                    " ON CONFLICT (name) DO UPDATE SET"
                    "   kind = EXCLUDED.kind,"
                    "   source_sql = EXCLUDED.source_sql,"
                    "   description = EXCLUDED.description,"
                    "   refreshed_at = now(),"
                    "   created_at = now()"
                ).format(meta=meta_id)
            else:
                upsert_sql = SQL(
                    "INSERT INTO {meta} (name, kind, source_sql, description,"
                    " created_at, refreshed_at)"
                    " VALUES (%s, %s, %s, %s, now(), NULL)"
                    " ON CONFLICT (name) DO UPDATE SET"
                    "   kind = EXCLUDED.kind,"
                    "   source_sql = EXCLUDED.source_sql,"
                    "   description = EXCLUDED.description,"
                    "   refreshed_at = NULL,"
                    "   created_at = now()"
                ).format(meta=meta_id)
            await cur.execute(upsert_sql, (name, kind, sql, description))

        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    return {
        "name": name,
        "full_name": f"{schema}.{name}",
        "kind": kind,
        "geom_column": geom_col,
        "row_count": row_count,
        "description": description,
        "geometry_type": geometry_type,
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# 10.3  refresh_layer
# ---------------------------------------------------------------------------


async def refresh_layer(ctx: _Ctx, name: str) -> dict[str, Any]:
    """Refresh a materialized-view layer; rebuild its data from source.

    Only layers of kind ``materialized_view`` can be refreshed.
    Returns ``{"name", "row_count", "refreshed_at"}``.
    """
    srv: ServerContext = ctx.request_context.lifespan_context
    cfg = srv.cfg

    _require_writeable(cfg)
    _validate_name(name)

    schema = cfg.layer_schema
    meta_id = Identifier(schema, "_meta")
    view_id = Identifier(schema, name)

    async with srv.db.write() as cur:
        try:
            # Look up the layer in _meta.
            await cur.execute(
                SQL("SELECT kind FROM {meta} WHERE name = %s").format(meta=meta_id),
                (name,),
            )
            row = await cur.fetchone()
            if row is None:
                raise ToolError("not_found", f"layer {name!r} not found")
            kind = row[0]
            if kind != "materialized_view":
                raise ToolError(
                    "invalid_argument",
                    f"layer {name!r} is a view; only materialized_view layers can be refreshed",
                )

            # REFRESH MATERIALIZED VIEW cannot run inside a regular transaction
            # in some older PG versions, but it is fine in PG 14+.
            # We run it here inside the write() transaction.
            await cur.execute(
                SQL("REFRESH MATERIALIZED VIEW {view}").format(view=view_id)
            )

            # Update refreshed_at.
            await cur.execute(
                SQL(
                    "UPDATE {meta} SET refreshed_at = now() WHERE name = %s"
                    " RETURNING refreshed_at"
                ).format(meta=meta_id),
                (name,),
            )
            updated = await cur.fetchone()
            refreshed_at = updated[0].isoformat() if updated and updated[0] else None

            # Count rows.
            await cur.execute(
                SQL("SELECT count(*) FROM {view}").format(view=view_id)
            )
            count_row = await cur.fetchone()
            row_count = int(count_row[0]) if count_row else 0

        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    return {"name": name, "row_count": row_count, "refreshed_at": refreshed_at}


# ---------------------------------------------------------------------------
# 10.4  list_layers
# ---------------------------------------------------------------------------


async def list_layers(ctx: _Ctx) -> dict[str, Any]:
    """List all layers registered in the layer schema.

    Returns ``{"layers": [...]}`` where each entry has ``name``, ``kind``,
    ``description``, ``created_at``, ``refreshed_at``, and ``row_count``.
    In read-only mode (where _meta was never bootstrapped) returns an empty list.
    """
    srv: ServerContext = ctx.request_context.lifespan_context
    cfg = srv.cfg
    schema = cfg.layer_schema
    meta_id = Identifier(schema, "_meta")

    async with srv.db.read() as cur:
        try:
            # Check whether _meta exists.
            await cur.execute(
                "SELECT to_regclass(%s) IS NOT NULL",
                (f"{schema}._meta",),
            )
            exists_row = await cur.fetchone()
            if not exists_row or not exists_row[0]:
                return {"layers": []}

            await cur.execute(
                SQL(
                    "SELECT name, kind, description, created_at, refreshed_at"
                    " FROM {meta}"
                    " ORDER BY created_at"
                ).format(meta=meta_id)
            )
            rows = await cur.fetchall()
        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    layers = []
    for row in rows:
        layer_name, kind, desc, created_at, refreshed_at = row
        view_id = Identifier(schema, layer_name)
        async with srv.db.read() as cur2:
            try:
                await cur2.execute(
                    SQL("SELECT count(*) FROM {view}").format(view=view_id)
                )
                count_row = await cur2.fetchone()
                row_count = int(count_row[0]) if count_row else 0
            except Exception:
                row_count = -1

        layers.append(
            {
                "name": layer_name,
                "kind": kind,
                "description": desc,
                "created_at": created_at.isoformat() if created_at else None,
                "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
                "row_count": row_count,
            }
        )

    return {"layers": layers}


# ---------------------------------------------------------------------------
# 10.5  describe_layer
# ---------------------------------------------------------------------------


async def describe_layer(ctx: _Ctx, name: str) -> dict[str, Any]:
    """Return metadata and a row sample for a layer.

    Returns ``{name, kind, source_sql, description, created_at, refreshed_at,
    sample}``.
    """
    srv: ServerContext = ctx.request_context.lifespan_context
    cfg = srv.cfg

    _validate_name(name)

    schema = cfg.layer_schema
    meta_id = Identifier(schema, "_meta")
    view_id = Identifier(schema, name)
    sample_limit = min(10, cfg.max_rows)

    async with srv.db.read() as cur:
        try:
            await cur.execute(
                SQL(
                    "SELECT name, kind, source_sql, description, created_at, refreshed_at"
                    " FROM {meta} WHERE name = %s"
                ).format(meta=meta_id),
                (name,),
            )
            row = await cur.fetchone()
            if row is None:
                raise ToolError("not_found", f"layer {name!r} not found")

            layer_name, kind, source_sql, desc, created_at, refreshed_at = row

            await cur.execute(
                SQL("SELECT * FROM {view} LIMIT %s").format(view=view_id),
                (sample_limit,),
            )
            if cur.description:
                cols = [d.name for d in cur.description]
                raw = await cur.fetchall()
                sample = [dict(zip(cols, r, strict=True)) for r in raw]
                # Convert non-JSON-serialisable values to strings.
                sample = [
                    {k: (str(v) if not isinstance(v, (int, float, str, bool, type(None))) else v)
                     for k, v in s.items()}
                    for s in sample
                ]
            else:
                sample = []
        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    return {
        "name": layer_name,
        "kind": kind,
        "source_sql": source_sql,
        "description": desc,
        "created_at": created_at.isoformat() if created_at else None,
        "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
        "sample": sample,
    }


# ---------------------------------------------------------------------------
# 10.6  drop_layer
# ---------------------------------------------------------------------------


async def drop_layer(ctx: _Ctx, name: str) -> dict[str, Any]:
    """Drop a layer view and remove its _meta record.

    Returns ``{"dropped": name, "kind": kind}``.
    """
    srv: ServerContext = ctx.request_context.lifespan_context
    cfg = srv.cfg

    _require_writeable(cfg)
    _validate_name(name)

    schema = cfg.layer_schema
    meta_id = Identifier(schema, "_meta")
    view_id = Identifier(schema, name)

    async with srv.db.write() as cur:
        try:
            await cur.execute(
                SQL("SELECT kind FROM {meta} WHERE name = %s").format(meta=meta_id),
                (name,),
            )
            row = await cur.fetchone()
            if row is None:
                raise ToolError("not_found", f"layer {name!r} not found")
            kind = row[0]

            if kind == "materialized_view":
                await cur.execute(
                    SQL("DROP MATERIALIZED VIEW IF EXISTS {view} CASCADE").format(
                        view=view_id
                    )
                )
            else:
                await cur.execute(
                    SQL("DROP VIEW IF EXISTS {view} CASCADE").format(view=view_id)
                )

            await cur.execute(
                SQL("DELETE FROM {meta} WHERE name = %s").format(meta=meta_id),
                (name,),
            )
        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    return {"dropped": name, "kind": kind}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: FastMCP) -> None:
    """Register all layer-publishing tools on *mcp*."""
    mcp.tool()(create_layer)
    mcp.tool()(refresh_layer)
    mcp.tool()(list_layers)
    mcp.tool()(describe_layer)
    mcp.tool()(drop_layer)


__all__ = [
    "create_layer",
    "describe_layer",
    "drop_layer",
    "list_layers",
    "refresh_layer",
    "register",
]
