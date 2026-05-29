"""FastMCP server entry point. Tools are imported and registered here."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from mcp_postgis import __version__
from mcp_postgis.config import load_config
from mcp_postgis.context import ServerContext
from mcp_postgis.db import Database


@asynccontextmanager
async def _lifespan(_app: FastMCP) -> AsyncIterator[ServerContext]:
    cfg = load_config()
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO))
    async with Database(cfg) as db:
        yield ServerContext(cfg=cfg, db=db)


mcp: FastMCP = FastMCP("mcp-postgis", lifespan=_lifespan)

# FastMCP Context is Generic[ServerSessionT, LifespanContextT, RequestT]; using
# Any for all params avoids noisy type-arg errors on every function signature.
_Ctx = Context[Any, Any, Any]


@mcp.tool()
async def ping(ctx: _Ctx) -> dict[str, str]:
    """Health check: returns PostGIS and PostgreSQL versions plus the server version."""
    srv: ServerContext = ctx.request_context.lifespan_context
    async with srv.db.read() as cur:
        await cur.execute("SELECT version(), postgis_full_version()")
        row = await cur.fetchone()
    assert row is not None
    return {
        "server_version": __version__,
        "postgresql_version": row[0],
        "postgis_version": row[1],
        "mode": srv.cfg.mode.value,
    }


def main() -> None:
    """Console entry point. Runs the stdio server."""
    mcp.run()


from mcp_postgis import prompts as _prompts  # noqa: E402
from mcp_postgis import resources as _resources  # noqa: E402
from mcp_postgis.tools import export as _export  # noqa: E402
from mcp_postgis.tools import geometry as _geometry  # noqa: E402
from mcp_postgis.tools import ingest as _ingest  # noqa: E402
from mcp_postgis.tools import introspection as _introspection  # noqa: E402
from mcp_postgis.tools import layers as _layers  # noqa: E402
from mcp_postgis.tools import query as _query  # noqa: E402
from mcp_postgis.tools import spatial as _spatial  # noqa: E402

_introspection.register(mcp)
_layers.register(mcp)
_query.register(mcp)
_spatial.register(mcp)
_geometry.register(mcp)
_export.register(mcp)
_ingest.register(mcp)
_resources.register(mcp)
_prompts.register(mcp)


if __name__ == "__main__":
    main()
