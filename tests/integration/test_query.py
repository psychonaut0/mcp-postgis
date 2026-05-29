"""Integration tests for §6.2 query tools."""
from __future__ import annotations

import pytest

from mcp_postgis.config import Config, Mode
from mcp_postgis.db import Database
from mcp_postgis.errors import ToolError
from mcp_postgis.server import ServerContext


class _RC:
    def __init__(self, srv_ctx): self.lifespan_context = srv_ctx

class _Ctx:
    def __init__(self, srv_ctx): self.request_context = _RC(srv_ctx)


@pytest.fixture
def fake_ctx_factory():
    def make(srv_ctx): return _Ctx(srv_ctx)
    return make


@pytest.mark.integration
async def test_execute_sql_simple_select(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.query import execute_sql
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await execute_sql(
            fake_ctx_factory(srv),
            "SELECT name FROM app.cities ORDER BY name",
        )
        assert result["columns"] == ["name"]
        assert [r["name"] for r in result["rows"]] == ["Cagliari", "Milan", "Rome"]
        assert result["truncated"] is False


@pytest.mark.integration
async def test_execute_sql_truncates(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.query import execute_sql
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY, max_rows=2)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await execute_sql(
            fake_ctx_factory(srv),
            "SELECT name FROM app.cities ORDER BY name",
        )
        assert len(result["rows"]) == 2
        assert result["truncated"] is True
        assert "max_rows" in result["hint"]


@pytest.mark.integration
async def test_execute_sql_blocks_write_in_read_only(
    db_url: str, fake_ctx_factory
) -> None:
    from mcp_postgis.tools.query import execute_sql
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await execute_sql(fake_ctx_factory(srv), "DELETE FROM app.cities")
        assert exc.value.code == "permission_denied"


@pytest.mark.integration
async def test_execute_sql_accepts_params(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.query import execute_sql
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await execute_sql(
            fake_ctx_factory(srv),
            "SELECT id, name FROM app.cities WHERE name = %s",
            params=["Rome"],
        )
        assert [r["name"] for r in result["rows"]] == ["Rome"]


@pytest.mark.integration
async def test_explain_returns_json_plan(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.query import explain
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await explain(
            fake_ctx_factory(srv),
            "SELECT * FROM app.cities WHERE name = 'Rome'",
        )
        assert isinstance(result["plan"], list)
        assert "Plan" in result["plan"][0]


@pytest.mark.integration
async def test_explain_analyze_runs_query(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.query import explain
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await explain(
            fake_ctx_factory(srv),
            "SELECT count(*) FROM app.cities",
            analyze=True,
        )
        assert "Actual Total Time" in str(result["plan"])


@pytest.mark.integration
async def test_explain_rejects_non_select(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.query import explain
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        with pytest.raises(ToolError) as exc:
            await explain(fake_ctx_factory(srv), "DELETE FROM app.cities")
        assert exc.value.code == "permission_denied"


@pytest.mark.integration
async def test_sample_table_returns_rows_with_geojson(
    db_url: str, fake_ctx_factory
) -> None:
    from mcp_postgis.tools.query import sample_table
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await sample_table(fake_ctx_factory(srv), schema="app", table="cities", n=2)
        assert len(result["rows"]) == 2
        row = result["rows"][0]
        assert row["geom"]["type"] == "Point"


@pytest.mark.integration
async def test_sample_table_non_spatial(db_url: str, fake_ctx_factory) -> None:
    from mcp_postgis.tools.query import sample_table
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv = ServerContext(cfg=cfg, db=db)
        result = await sample_table(fake_ctx_factory(srv), schema="app", table="notes", n=5)
        assert result["rows"] == []
