"""Introspection tools: schema/table/column/geometry/index/extension discovery."""
from __future__ import annotations

from typing import Any, cast

import psycopg
from mcp.server.fastmcp import Context, FastMCP

from mcp_postgis.context import ServerContext
from mcp_postgis.db import fetch_dicts
from mcp_postgis.errors import ToolError

# FastMCP Context is Generic[ServerSessionT, LifespanContextT, RequestT]; using
# Any for all params avoids noisy type-arg errors on every function signature.
_Ctx = Context[Any, Any, Any]

# ---------------------------------------------------------------------------
# 7.1  list_schemas
# ---------------------------------------------------------------------------

async def list_schemas(ctx: _Ctx) -> dict[str, Any]:
    """List all non-system schemas in the database.

    Returns schemas filtered by allowed_schemas when that option is set.
    """
    srv: ServerContext = ctx.request_context.lifespan_context
    system = [
        "pg_catalog",
        "information_schema",
        "pg_toast",
    ]
    async with srv.db.read() as cur:
        await cur.execute(
            """
            SELECT n.nspname AS name,
                   pg_catalog.pg_get_userbyid(n.nspowner) AS owner
            FROM   pg_catalog.pg_namespace n
            WHERE  n.nspname != ALL(%s)
              AND  n.nspname NOT LIKE 'pg_temp_%%'
              AND  n.nspname NOT LIKE 'pg_toast_temp_%%'
            ORDER BY n.nspname
            """,
            (system,),
        )
        rows = await fetch_dicts(cur)

    if srv.cfg.allowed_schemas is not None:
        allowed = set(srv.cfg.allowed_schemas)
        rows = [r for r in rows if r["name"] in allowed]

    return {"schemas": rows}


# ---------------------------------------------------------------------------
# 7.2  list_tables
# ---------------------------------------------------------------------------

_KIND_MAP = {
    "r": "table",
    "v": "view",
    "m": "materialized_view",
    "f": "foreign_table",
    "p": "partitioned_table",
}


async def list_tables(ctx: _Ctx, schema: str) -> dict[str, Any]:
    """List tables, views, materialised views and foreign tables in a schema.

    Returns an empty list when the schema is not in allowed_schemas.
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    if srv.cfg.allowed_schemas is not None and schema not in srv.cfg.allowed_schemas:
        return {"tables": []}

    async with srv.db.read() as cur:
        await cur.execute(
            """
            SELECT c.relname                                    AS table,
                   c.relkind                                    AS kind,
                   GREATEST(c.reltuples::bigint, 0)            AS estimated_rows,
                   obj_description(c.oid, 'pg_class')          AS comment
            FROM   pg_catalog.pg_class     c
            JOIN   pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE  n.nspname = %s
              AND  c.relkind IN ('r','v','m','f','p')
            ORDER  BY c.relname
            """,
            (schema,),
        )
        rows = await fetch_dicts(cur)

    return {
        "tables": [
            {
                "table": r["table"],
                "kind": _KIND_MAP.get(str(r["kind"]), str(r["kind"])),
                "estimated_rows": int(cast(int, r["estimated_rows"])),
                "comment": r["comment"],
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# 7.3  describe_table
# ---------------------------------------------------------------------------

async def describe_table(ctx: _Ctx, schema: str, table: str) -> dict[str, Any]:
    """Return full metadata for a single table.

    Raises ToolError('not_found') when the table does not exist.
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    # Phase 1: existence check in its own transaction so we can raise cleanly
    # outside the context manager (frozen+slots dataclass raises cannot propagate
    # through async-with __aexit__ without tripping a TypeError on __traceback__).
    async with srv.db.read() as cur:
        await cur.execute(
            "SELECT to_regclass(%s)",
            (f"{schema}.{table}",),
        )
        row = await cur.fetchone()
        exists = row is not None and row[0] is not None

    if not exists:
        raise ToolError("not_found", f"Table {schema}.{table} does not exist")

    async with srv.db.read() as cur:

        # 2. Columns from information_schema
        await cur.execute(
            """
            SELECT column_name  AS name,
                   data_type    AS type,
                   udt_name,
                   is_nullable  AS nullable,
                   column_default AS "default"
            FROM   information_schema.columns
            WHERE  table_schema = %s
              AND  table_name   = %s
            ORDER  BY ordinal_position
            """,
            (schema, table),
        )
        columns = await fetch_dicts(cur)

        # 3. Geometry columns
        try:
            await cur.execute(
                """
                SELECT f_geometry_column AS column,
                       srid,
                       type,
                       coord_dimension
                FROM   public.geometry_columns
                WHERE  f_table_schema = %s
                  AND  f_table_name   = %s
                """,
                (schema, table),
            )
            geometry_columns = await fetch_dicts(cur)
        except psycopg.errors.UndefinedTable:
            geometry_columns = []

        # 4. Indexes
        await cur.execute(
            """
            SELECT i.relname                             AS name,
                   am.amname                            AS index_type,
                   pg_get_indexdef(ix.indexrelid)       AS definition,
                   ix.indisunique                       AS is_unique,
                   ix.indisprimary                      AS is_primary
            FROM   pg_catalog.pg_index     ix
            JOIN   pg_catalog.pg_class     t  ON t.oid  = ix.indrelid
            JOIN   pg_catalog.pg_class     i  ON i.oid  = ix.indexrelid
            JOIN   pg_catalog.pg_namespace n  ON n.oid  = t.relnamespace
            JOIN   pg_catalog.pg_am        am ON am.oid = i.relam
            WHERE  n.nspname = %s
              AND  t.relname = %s
            ORDER  BY i.relname
            """,
            (schema, table),
        )
        indexes = await fetch_dicts(cur)

        # 5. Foreign keys
        await cur.execute(
            """
            SELECT con.conname                         AS name,
                   pg_get_constraintdef(con.oid, true) AS definition
            FROM   pg_catalog.pg_constraint con
            JOIN   pg_catalog.pg_class      t   ON t.oid  = con.conrelid
            JOIN   pg_catalog.pg_namespace  n   ON n.oid  = t.relnamespace
            WHERE  n.nspname   = %s
              AND  t.relname   = %s
              AND  con.contype = 'f'
            ORDER  BY con.conname
            """,
            (schema, table),
        )
        foreign_keys = await fetch_dicts(cur)

    return {
        "schema": schema,
        "table": table,
        "columns": columns,
        "geometry_columns": geometry_columns,
        "indexes": [
            {
                "name": r["name"],
                "index_type": r["index_type"],
                "definition": r["definition"],
                "is_unique": bool(r["is_unique"]),
                "is_primary": bool(r["is_primary"]),
            }
            for r in indexes
        ],
        "foreign_keys": foreign_keys,
    }


# ---------------------------------------------------------------------------
# 7.4  list_geometry_columns
# ---------------------------------------------------------------------------

async def list_geometry_columns(ctx: _Ctx) -> dict[str, Any]:
    """List all geometry and geography columns in the database.

    Filtered by allowed_schemas when that option is set.
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    async with srv.db.read() as cur:
        try:
            await cur.execute(
                """
                SELECT f_table_schema     AS schema,
                       f_table_name      AS table,
                       f_geometry_column AS column,
                       srid,
                       type,
                       coord_dimension,
                       'geometry'::text  AS gtype
                FROM   public.geometry_columns
                UNION ALL
                SELECT f_table_schema,
                       f_table_name,
                       f_geography_column,
                       srid,
                       type,
                       coord_dimension,
                       'geography'::text
                FROM   public.geography_columns
                ORDER  BY schema, "table", "column"
                """
            )
            rows = await fetch_dicts(cur)
        except psycopg.errors.UndefinedTable:
            rows = []

    if srv.cfg.allowed_schemas is not None:
        allowed = set(srv.cfg.allowed_schemas)
        rows = [r for r in rows if r["schema"] in allowed]

    return {"columns": rows}


# ---------------------------------------------------------------------------
# 7.5  list_spatial_indexes
# ---------------------------------------------------------------------------

async def list_spatial_indexes(
    ctx: _Ctx,
    schema: str | None = None,
    table: str | None = None,
) -> dict[str, Any]:
    """List GiST / SP-GiST indexes, optionally filtered by schema and/or table.

    Also respects allowed_schemas when that option is set.
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    conditions = ["am.amname IN ('gist', 'spgist')"]
    params: list[Any] = []

    if schema is not None:
        conditions.append("n.nspname = %s")
        params.append(schema)

    if table is not None:
        conditions.append("t.relname = %s")
        params.append(table)

    if srv.cfg.allowed_schemas is not None:
        placeholders = ", ".join(["%s"] * len(srv.cfg.allowed_schemas))
        conditions.append(f"n.nspname IN ({placeholders})")
        params.extend(srv.cfg.allowed_schemas)

    where_clause = " AND ".join(conditions)

    sql = f"""
        SELECT n.nspname                         AS schema,
               t.relname                         AS table,
               i.relname                         AS index,
               am.amname                         AS index_type,
               pg_get_indexdef(ix.indexrelid)    AS definition
        FROM   pg_catalog.pg_index     ix
        JOIN   pg_catalog.pg_class     t  ON t.oid  = ix.indrelid
        JOIN   pg_catalog.pg_class     i  ON i.oid  = ix.indexrelid
        JOIN   pg_catalog.pg_namespace n  ON n.oid  = t.relnamespace
        JOIN   pg_catalog.pg_am        am ON am.oid = i.relam
        WHERE  {where_clause}
        ORDER  BY schema, "table", index
    """  # noqa: S608

    async with srv.db.read() as cur:
        await cur.execute(sql, params if params else None)
        rows = await fetch_dicts(cur)

    return {"indexes": rows}


# ---------------------------------------------------------------------------
# 7.6  list_extensions
# ---------------------------------------------------------------------------

async def list_extensions(ctx: _Ctx) -> dict[str, Any]:
    """List all installed PostgreSQL extensions."""
    srv: ServerContext = ctx.request_context.lifespan_context

    async with srv.db.read() as cur:
        await cur.execute(
            """
            SELECT e.extname    AS name,
                   e.extversion AS version,
                   n.nspname    AS schema
            FROM   pg_catalog.pg_extension e
            JOIN   pg_catalog.pg_namespace  n ON n.oid = e.extnamespace
            ORDER  BY e.extname
            """
        )
        rows = await fetch_dicts(cur)

    return {"extensions": rows}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(mcp: FastMCP) -> None:
    """Register all introspection tools on *mcp*."""
    mcp.tool()(list_schemas)
    mcp.tool()(list_tables)
    mcp.tool()(describe_table)
    mcp.tool()(list_geometry_columns)
    mcp.tool()(list_spatial_indexes)
    mcp.tool()(list_extensions)


__all__ = [
    "describe_table",
    "list_extensions",
    "list_geometry_columns",
    "list_schemas",
    "list_spatial_indexes",
    "list_tables",
    "register",
]
