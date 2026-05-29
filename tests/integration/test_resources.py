"""Integration tests for §8 resources."""
from __future__ import annotations

import json

import pytest

from mcp_postgis.config import Config, Mode
from mcp_postgis.db import Database
from mcp_postgis.server import ServerContext


@pytest.fixture
def patch_ctx(monkeypatch: pytest.MonkeyPatch):
    def _patch(srv_ctx: ServerContext) -> None:
        from mcp_postgis import server as server_mod

        class _Req:
            lifespan_context = srv_ctx

        class _Ctx:
            request_context = _Req()

        monkeypatch.setattr(server_mod.mcp, "get_context", lambda: _Ctx())

    return _patch


@pytest.mark.integration
async def test_resource_schemas(db_url: str, patch_ctx) -> None:
    from mcp_postgis.resources import resource_schemas

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        patch_ctx(ServerContext(cfg=cfg, db=db))
        payload = json.loads(await resource_schemas())
        names = {s["name"] for s in payload["schemas"]}
        assert "app" in names


@pytest.mark.integration
async def test_resource_table(db_url: str, patch_ctx) -> None:
    from mcp_postgis.resources import resource_table

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        patch_ctx(ServerContext(cfg=cfg, db=db))
        payload = json.loads(await resource_table("app", "cities"))
        assert payload["table"] == "cities"
        assert payload["geometry_columns"][0]["srid"] == 4326


@pytest.mark.integration
async def test_resource_layers_empty_in_read_only(db_url: str, patch_ctx) -> None:
    from mcp_postgis.resources import resource_layers

    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        patch_ctx(ServerContext(cfg=cfg, db=db))
        payload = json.loads(await resource_layers())
        assert payload["layers"] == []
