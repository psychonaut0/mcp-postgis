"""Query tools: execute_sql, explain, sample_table."""
from __future__ import annotations

from typing import Any

import psycopg
import psycopg.sql
from mcp.server.fastmcp import Context, FastMCP

from mcp_postgis import errors
from mcp_postgis.errors import ToolError
from mcp_postgis.safety import ensure_allowed
from mcp_postgis.server import ServerContext

# FastMCP Context is Generic[ServerSessionT, LifespanContextT, RequestT]; using
# Any for all params avoids noisy type-arg errors on every function signature.
_Ctx = Context[Any, Any, Any]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _psycopg_to_dollar_params(sql: str) -> str:
    """Replace psycopg-style ``%s`` placeholders with Postgres ``$1``, ``$2``, …

    pglast uses the real PostgreSQL parser, which does not recognise ``%s``.
    This is used *only* for safety classification; the original SQL (with
    ``%s``) is still sent to psycopg for execution.

    Handles the common case of plain ``%s`` positional parameters.  Named
    (``%(name)s``) or format (``%f``, ``%%``) placeholders are left unchanged
    so the classifier still fails fast on obviously bad input.
    """
    import re

    counter = 0

    def _replace(_m: re.Match) -> str:  # type: ignore[type-arg]
        nonlocal counter
        counter += 1
        return f"${counter}"

    return re.sub(r"%s", _replace, sql)


# ---------------------------------------------------------------------------
# 8.1  execute_sql
# ---------------------------------------------------------------------------


async def execute_sql(
    ctx: _Ctx,
    sql: str,
    params: list[Any] | None = None,
) -> dict[str, Any]:
    """Execute a SQL statement and return results as a list of dicts.

    In READ_ONLY mode only SELECT statements (and other read-only SQL) are
    accepted; writes are blocked by the safety classifier before they reach
    the database.

    Returns a dict with keys:
    - ``columns``: list of column names
    - ``rows``: list of dicts (column → value)
    - ``row_count``: affected/returned row count
    - ``truncated``: True when results were trimmed to max_rows
    - ``hint``: (only when truncated) advice on how to get more rows
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    # pglast uses the Postgres parser which does not recognise psycopg's %s
    # placeholders.  Replace them with $1, $2, … before classifying so that
    # parameterised queries pass the safety check.
    _sql_for_classify = _psycopg_to_dollar_params(sql)

    try:
        info = ensure_allowed(
            _sql_for_classify, mode=srv.cfg.mode, layer_schema=srv.cfg.layer_schema
        )
    except Exception as exc:
        raise errors.translate(exc) from exc

    cap = srv.cfg.max_rows

    ctx_manager = srv.db.read() if info.is_read_only else srv.db.write()
    async with ctx_manager as cur:
        try:
            await cur.execute(sql, params)
        except Exception as exc:
            raise errors.translate(exc) from exc

        if cur.description is None:
            # DDL or DML without RETURNING
            return {
                "columns": [],
                "rows": [],
                "row_count": cur.rowcount,
                "truncated": False,
            }

        cols = [d.name for d in cur.description]
        raw = await cur.fetchmany(cap + 1)

    truncated = len(raw) > cap
    if truncated:
        raw = raw[:cap]

    rows = [dict(zip(cols, r, strict=True)) for r in raw]
    result: dict[str, Any] = {
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
    if truncated:
        result["hint"] = (
            f"result truncated at max_rows={cap}; "
            "narrow the query or raise MCP_POSTGIS_MAX_ROWS"
        )
    return result


# ---------------------------------------------------------------------------
# 8.2  explain
# ---------------------------------------------------------------------------


async def explain(
    ctx: _Ctx,
    sql: str,
    analyze: bool = False,
) -> dict[str, Any]:
    """Return the query plan for a read-only SQL statement.

    When *analyze* is True, the statement is actually executed and runtime
    statistics are included in the plan.

    Returns a dict with keys:
    - ``plan``: the parsed query plan (a list with one dict, as Postgres emits
      for ``EXPLAIN (FORMAT JSON)``)
    - ``analyzed``: whether ANALYZE was used
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    try:
        info = ensure_allowed(sql, mode=srv.cfg.mode, layer_schema=srv.cfg.layer_schema)
    except Exception as exc:
        raise errors.translate(exc) from exc

    if not info.is_read_only:
        raise ToolError(
            "permission_denied",
            "explain only accepts read-only statements",
            hint="rewrite as SELECT",
        )

    # Build EXPLAIN query using psycopg.sql for safe composition
    if analyze:
        explain_sql = psycopg.sql.SQL("EXPLAIN (FORMAT JSON, ANALYZE TRUE) ") + psycopg.sql.SQL(sql)
    else:
        explain_sql = psycopg.sql.SQL("EXPLAIN (FORMAT JSON) ") + psycopg.sql.SQL(sql)

    async with srv.db.read() as cur:
        try:
            await cur.execute(explain_sql)
        except Exception as exc:
            raise errors.translate(exc) from exc
        row = await cur.fetchone()

    assert row is not None
    # psycopg with the default json adapter returns the JSON column as a
    # Python object (list/dict) already — no manual json.loads needed.
    plan = row[0]

    return {"plan": plan, "analyzed": analyze}


# ---------------------------------------------------------------------------
# 8.3  sample_table
# ---------------------------------------------------------------------------

_GEOM_UDTS = frozenset({"geometry", "geography"})


async def sample_table(
    ctx: _Ctx,
    schema: str,
    table: str,
    n: int = 10,
) -> dict[str, Any]:
    """Return up to *n* rows from *schema*.*table*.

    Geometry / geography columns are automatically converted to GeoJSON dicts
    via ``ST_AsGeoJSON``.  Other columns are returned as-is.

    Raises ``ToolError('not_found')`` when the table does not exist.
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    # Clamp n
    n = max(1, min(n, srv.cfg.max_rows))

    # 1. Existence check
    async with srv.db.read() as cur:
        await cur.execute(
            "SELECT to_regclass(%s)",
            (f"{schema}.{table}",),
        )
        row = await cur.fetchone()
        exists = row is not None and row[0] is not None

    if not exists:
        raise ToolError("not_found", f"Table {schema}.{table} does not exist")

    # 2. Fetch column metadata
    async with srv.db.read() as cur:
        await cur.execute(
            """
            SELECT column_name, udt_name
            FROM   information_schema.columns
            WHERE  table_schema = %s
              AND  table_name   = %s
            ORDER  BY ordinal_position
            """,
            (schema, table),
        )
        col_meta = await cur.fetchall()

    # 3. Build SELECT list
    select_parts: list[psycopg.sql.Composable] = []
    col_names: list[str] = []
    for col_name, udt_name in col_meta:
        ident = psycopg.sql.Identifier(col_name)
        expr: psycopg.sql.Composable
        if udt_name in _GEOM_UDTS:
            # Cast result to json so psycopg deserialises it as a Python dict
            expr = psycopg.sql.SQL("ST_AsGeoJSON({col})::json AS {alias}").format(
                col=ident,
                alias=ident,
            )
        else:
            expr = ident
        select_parts.append(expr)
        col_names.append(col_name)

    select_list = psycopg.sql.SQL(", ").join(select_parts)
    query = psycopg.sql.SQL(
        "SELECT {cols} FROM {schema}.{table} LIMIT {n}"
    ).format(
        cols=select_list,
        schema=psycopg.sql.Identifier(schema),
        table=psycopg.sql.Identifier(table),
        n=psycopg.sql.Literal(n),
    )

    # 4. Execute and build result
    async with srv.db.read() as cur:
        try:
            await cur.execute(query)
        except Exception as exc:
            raise errors.translate(exc) from exc
        raw = await cur.fetchall()

    rows = [dict(zip(col_names, r, strict=True)) for r in raw]
    return {"columns": col_names, "rows": rows}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: FastMCP) -> None:
    """Register all query tools on *mcp*."""
    mcp.tool()(execute_sql)
    mcp.tool()(explain)
    mcp.tool()(sample_table)


__all__ = [
    "execute_sql",
    "explain",
    "register",
    "sample_table",
]
