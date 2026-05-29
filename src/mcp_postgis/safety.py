"""Best-effort SQL statement classification using pglast.

This is the first of two safety layers; the second is the DB role's grants
(see docs/security.md). Treat this as a fast-fail filter, not a security
boundary on its own.

pglast AST notes (verified against pglast>=6.0):
- SELECT / CTEs  -> ast.SelectStmt
- EXPLAIN [ANALYZE] -> ast.ExplainStmt (always wraps a query node)
- INSERT          -> ast.InsertStmt
- UPDATE          -> ast.UpdateStmt
- DELETE          -> ast.DeleteStmt
- CREATE TABLE    -> ast.CreateStmt          (schema via .relation.schemaname)
- CREATE TABLE AS -> ast.CreateTableAsStmt   (schema via .into.rel.schemaname)
- CREATE MATERIALIZED VIEW -> ast.CreateTableAsStmt (objtype == OBJECT_MATVIEW)
- CREATE VIEW     -> ast.ViewStmt            (schema via .view.schemaname)
- REFRESH MAT VIEW-> ast.RefreshMatViewStmt  (schema via .relation.schemaname)
- DROP ...        -> ast.DropStmt
- ALTER TABLE     -> ast.AlterTableStmt
- TRUNCATE        -> ast.TruncateStmt

Safety design (whitelist approach in ``ensure_allowed``):
- ADMIN   → allow everything that parses.
- Any mode + read-only statement → allow.
- READ_WRITE + layer-publishing DDL targeting ``layer_schema`` → allow.
- Everything else → reject.

``classify`` detects two additional non-obvious write cases:

1. ``EXPLAIN ANALYZE <write-stmt>``: the top-level node is ``ExplainStmt``
   but the inner statement actually executes.  We recurse into ``stmt.query``
   and treat the result as non-read-only if the inner statement is not
   a bare SELECT.

2. CTE writes (``WITH d AS (DELETE …) SELECT …``): the top-level node is
   ``SelectStmt``, which would normally be read-only, but the ``withClause``
   may contain DML CTEs.  We inspect each CTE's ``ctequery`` and mark the
   whole statement non-read-only if any CTE contains
   ``InsertStmt`` / ``UpdateStmt`` / ``DeleteStmt``.
"""
from __future__ import annotations

from dataclasses import dataclass

import pglast
from pglast import ast

from mcp_postgis.config import Mode


class PermissionDeniedError(Exception):
    """Raised when a statement is not allowed in the current mode."""


@dataclass(frozen=True, slots=True)
class StmtInfo:
    is_read_only: bool
    creates_in_schema: str | None
    is_layer_publishing: bool


# DML node types that make a statement non-read-only.
_WRITE_DML_STMTS: tuple[type, ...] = (
    ast.InsertStmt,
    ast.UpdateStmt,
    ast.DeleteStmt,
)

# Statements that create a named object in a schema (view/matview/table-as).
# These are the only write operations allowed in READ_WRITE mode, and only
# when they target the configured layer_schema.
_LAYER_PUBLISHING_STMTS: tuple[type, ...] = (
    ast.ViewStmt,
    ast.CreateTableAsStmt,
    ast.RefreshMatViewStmt,
)


def _is_stmt_read_only(stmt: object) -> bool:
    """Return True if *stmt* (a pglast AST node) is purely read-only.

    Handles two tricky cases:

    * ``ExplainStmt`` — recurse into ``stmt.query``; EXPLAIN ANALYZE actually
      executes the inner statement, so we follow its type.
    * ``SelectStmt`` with a ``withClause`` — inspect each CTE's ``ctequery``
      for DML; a single DML CTE makes the whole statement non-read-only.
    """
    if isinstance(stmt, ast.ExplainStmt):
        # The inner query *executes* when ANALYZE is present.  Rather than
        # parsing the options list, we conservatively treat any EXPLAIN whose
        # inner statement is not a plain SELECT as non-read-only.
        inner = getattr(stmt, "query", None)
        if inner is None:
            return True
        return _is_stmt_read_only(inner)

    if isinstance(stmt, ast.SelectStmt):
        # Check for DML hidden inside a WITH clause.
        with_clause = getattr(stmt, "withClause", None)
        if with_clause is not None:
            ctes = getattr(with_clause, "ctes", None) or []
            for cte in ctes:
                cte_query = getattr(cte, "ctequery", None)
                if cte_query is not None and isinstance(cte_query, _WRITE_DML_STMTS):
                    return False
        return True

    return False


def classify(sql: str) -> StmtInfo:
    """Parse *sql* and return a :class:`StmtInfo` describing its nature.

    Raises :class:`PermissionDeniedError` for empty input, multiple
    statements, or unparseable SQL.
    """
    sql = sql.strip()
    if not sql:
        raise PermissionDeniedError("empty statement")

    try:
        parsed = pglast.parse_sql(sql)
    except pglast.parser.ParseError as e:
        raise PermissionDeniedError(f"could not parse SQL: {e}") from e

    if len(parsed) != 1:
        raise PermissionDeniedError(
            "tools accept a single statement per call; got "
            f"{len(parsed)} statements"
        )

    stmt = parsed[0].stmt
    is_read_only = _is_stmt_read_only(stmt)
    is_layer_publishing = isinstance(stmt, _LAYER_PUBLISHING_STMTS)
    creates_in_schema = _statement_target_schema(stmt)

    return StmtInfo(
        is_read_only=is_read_only,
        creates_in_schema=creates_in_schema,
        is_layer_publishing=is_layer_publishing,
    )


def _statement_target_schema(stmt: object) -> str | None:
    """Best-effort: extract the target schema from common DDL nodes.

    Returns the schema name string, or ``None`` if the statement is
    unschemaed or not a DDL node we recognise.
    """
    schema: str | None = None

    if isinstance(stmt, ast.ViewStmt):
        # CREATE [OR REPLACE] VIEW [schema.]name AS …
        schema = getattr(stmt.view, "schemaname", None)
    elif isinstance(stmt, ast.CreateStmt):
        # CREATE TABLE [schema.]name (…)
        schema = getattr(stmt.relation, "schemaname", None)
    elif isinstance(stmt, ast.CreateTableAsStmt):
        # CREATE [MATERIALIZED] VIEW / TABLE [schema.]name AS SELECT …
        # The target is buried in stmt.into.rel
        into = getattr(stmt, "into", None)
        if into is not None:
            rel = getattr(into, "rel", None)
            if rel is not None:
                schema = getattr(rel, "schemaname", None)
    elif isinstance(stmt, ast.RefreshMatViewStmt):
        # REFRESH MATERIALIZED VIEW [schema.]name
        schema = getattr(stmt.relation, "schemaname", None)

    # Normalise: pglast may return None for an unqualified name
    return str(schema) if schema else None


def ensure_allowed(sql: str, *, mode: Mode, layer_schema: str) -> StmtInfo:
    """Classify *sql* and raise if it is not permitted under *mode*.

    Uses a **whitelist** strategy: only explicitly permitted statement classes
    are allowed; everything else is rejected.

    :param sql: The raw SQL string (single statement).
    :param mode: The current operating mode from :class:`~mcp_postgis.config.Mode`.
    :param layer_schema: The schema that layer-publishing DDL is allowed to
        target in ``READ_WRITE`` mode.
    :raises PermissionDeniedError: When the statement violates the mode policy.
    :returns: The :class:`StmtInfo` for the statement.
    """
    info = classify(sql)

    # ADMIN: no restrictions beyond parse validity.
    if mode is Mode.ADMIN:
        return info

    # READ_ONLY and READ_WRITE both allow genuinely read-only statements.
    if info.is_read_only:
        return info

    # From here the statement is a write of some kind.

    # READ_ONLY mode: no writes at all.
    if mode is Mode.READ_ONLY:
        raise PermissionDeniedError(
            f"statement is not allowed in read_only mode: "
            f"{sql[:120]!r}"
        )

    # READ_WRITE mode (whitelist):
    # Only layer-publishing DDL (CREATE [OR REPLACE] VIEW /
    # CREATE MATERIALIZED VIEW / REFRESH MATERIALIZED VIEW) that explicitly
    # targets layer_schema is allowed.  Everything else — DML, DROP, ALTER,
    # TRUNCATE, CREATE TABLE, and any DDL outside layer_schema — is rejected.
    if info.is_layer_publishing:
        if info.creates_in_schema == layer_schema:
            return info
        raise PermissionDeniedError(
            f"layer-publishing DDL outside layer schema is not allowed in "
            f"read_write mode: target={info.creates_in_schema!r}, "
            f"allowed={layer_schema!r}"
        )

    raise PermissionDeniedError(
        f"statement is not allowed in read_write mode (only layer-publishing DDL "
        f"outside layer schema is permitted): {sql[:120]!r}"
    )


__all__ = ["PermissionDeniedError", "StmtInfo", "classify", "ensure_allowed"]
