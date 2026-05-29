"""Tests for the pglast-based statement classifier."""
from __future__ import annotations

import pytest

from mcp_postgis.config import Mode
from mcp_postgis.safety import (
    PermissionDeniedError,
    classify,
    ensure_allowed,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT name FROM app.cities WHERE id = 1",
        "SELECT count(*) FROM app.cities",
        "WITH t AS (SELECT 1) SELECT * FROM t",
        "EXPLAIN SELECT * FROM app.cities",
        "EXPLAIN ANALYZE SELECT * FROM app.cities",
    ],
)
def test_classify_select_only(sql: str) -> None:
    assert classify(sql).is_read_only is True


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO app.cities (name, geom) VALUES ('x', NULL)",
        "UPDATE app.cities SET name = 'x'",
        "DELETE FROM app.cities",
        "CREATE TABLE z (id int)",
        "DROP TABLE app.cities",
        "ALTER TABLE app.cities ADD COLUMN x int",
        "TRUNCATE app.cities",
        "CREATE VIEW v AS SELECT 1",
    ],
)
def test_classify_write(sql: str) -> None:
    assert classify(sql).is_read_only is False


def test_classify_layer_publishing() -> None:
    info = classify("CREATE OR REPLACE VIEW mcp_layers.foo AS SELECT 1")
    assert info.is_read_only is False
    assert info.creates_in_schema == "mcp_layers"
    assert info.is_layer_publishing is True


def test_classify_rejects_multiple_statements() -> None:
    with pytest.raises(PermissionDeniedError, match="single statement"):
        classify("SELECT 1; SELECT 2")


def test_classify_rejects_empty() -> None:
    with pytest.raises(PermissionDeniedError, match="empty"):
        classify("   ")


def test_ensure_allowed_read_only_passes_select() -> None:
    ensure_allowed("SELECT 1", mode=Mode.READ_ONLY, layer_schema="mcp_layers")


def test_ensure_allowed_read_only_blocks_insert() -> None:
    with pytest.raises(PermissionDeniedError, match="read_only"):
        ensure_allowed(
            "INSERT INTO app.cities VALUES (1, 'x', NULL)",
            mode=Mode.READ_ONLY,
            layer_schema="mcp_layers",
        )


def test_ensure_allowed_read_write_allows_layer_view() -> None:
    ensure_allowed(
        "CREATE OR REPLACE VIEW mcp_layers.foo AS SELECT 1",
        mode=Mode.READ_WRITE,
        layer_schema="mcp_layers",
    )


def test_ensure_allowed_read_write_blocks_ddl_outside_layer_schema() -> None:
    with pytest.raises(PermissionDeniedError, match="outside layer schema"):
        ensure_allowed(
            "CREATE TABLE app.tmp (id int)",
            mode=Mode.READ_WRITE,
            layer_schema="mcp_layers",
        )


def test_ensure_allowed_admin_allows_everything() -> None:
    ensure_allowed(
        "DROP TABLE app.cities",
        mode=Mode.ADMIN,
        layer_schema="mcp_layers",
    )


# --- New tests for the discovered safety bugs ---

@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE app.cities",
        "ALTER TABLE app.cities ADD COLUMN x int",
        "TRUNCATE app.cities",
        "CREATE TABLE mcp_layers.tmp (id int)",
        "CREATE TABLE app.tmp (id int)",
        "INSERT INTO app.cities (name, geom) VALUES ('x', NULL)",
    ],
)
def test_ensure_allowed_read_write_blocks_non_layer_writes(sql: str) -> None:
    with pytest.raises(PermissionDeniedError):
        ensure_allowed(sql, mode=Mode.READ_WRITE, layer_schema="mcp_layers")


def test_ensure_allowed_read_write_allows_create_view_in_layer_schema() -> None:
    ensure_allowed(
        "CREATE VIEW mcp_layers.v AS SELECT 1",
        mode=Mode.READ_WRITE, layer_schema="mcp_layers",
    )


def test_ensure_allowed_read_write_allows_create_materialized_view_in_layer_schema() -> None:
    ensure_allowed(
        "CREATE MATERIALIZED VIEW mcp_layers.mv AS SELECT 1",
        mode=Mode.READ_WRITE, layer_schema="mcp_layers",
    )


def test_ensure_allowed_read_write_allows_refresh_in_layer_schema() -> None:
    ensure_allowed(
        "REFRESH MATERIALIZED VIEW mcp_layers.mv",
        mode=Mode.READ_WRITE, layer_schema="mcp_layers",
    )


def test_ensure_allowed_read_write_blocks_view_outside_layer_schema() -> None:
    with pytest.raises(PermissionDeniedError, match="outside layer schema"):
        ensure_allowed(
            "CREATE VIEW app.v AS SELECT 1",
            mode=Mode.READ_WRITE, layer_schema="mcp_layers",
        )


def test_classify_explain_analyze_insert_is_not_read_only() -> None:
    info = classify("EXPLAIN ANALYZE INSERT INTO app.cities (name) VALUES ('x')")
    assert info.is_read_only is False


def test_classify_cte_with_delete_is_not_read_only() -> None:
    info = classify(
        "WITH d AS (DELETE FROM app.cities RETURNING id) SELECT * FROM d"
    )
    assert info.is_read_only is False


def test_classify_explain_analyze_select_is_read_only() -> None:
    info = classify("EXPLAIN ANALYZE SELECT count(*) FROM app.cities")
    assert info.is_read_only is True


def test_classify_cte_with_select_only_is_read_only() -> None:
    info = classify("WITH t AS (SELECT 1) SELECT * FROM t")
    assert info.is_read_only is True
