"""End-to-end-ish test: drive ping via the in-process FastMCP context."""
from __future__ import annotations

import pytest

from mcp_postgis.config import Config, Mode
from mcp_postgis.db import Database
from mcp_postgis.server import ServerContext, ping


@pytest.mark.integration
async def test_ping_returns_versions(monkeypatch: pytest.MonkeyPatch, db_url: str) -> None:
    cfg = Config(database_url=db_url, mode=Mode.READ_ONLY)
    async with Database(cfg) as db:
        srv_ctx = ServerContext(cfg=cfg, db=db)

        class _RequestContext:
            lifespan_context = srv_ctx

        class _FakeContext:
            request_context = _RequestContext()

        result = await ping(_FakeContext())  # type: ignore[arg-type]
        assert "POSTGIS" in result["postgis_version"].upper()
        assert result["mode"] == "read_only"
        assert result["server_version"] == "0.1.0"
