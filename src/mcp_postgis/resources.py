"""§8 MCP resources: read-only catalog data the model can attach as context.

Each resource returns a JSON string. They reuse the introspection tool functions
and the layers list to avoid duplicating SQL.
"""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from mcp_postgis.tools import introspection, layers


async def resource_schemas() -> str:
    """postgis://schemas — schemas (allow-list aware) with their tables."""
    from mcp_postgis.server import mcp

    ctx = mcp.get_context()
    schemas = await introspection.list_schemas(ctx)
    out = []
    for s in schemas["schemas"]:
        tables = await introspection.list_tables(ctx, s["name"])
        out.append({"name": s["name"], "tables": tables["tables"]})
    return json.dumps({"schemas": out}, default=str)


async def resource_table(schema: str, table: str) -> str:
    """postgis://schema/{schema}/{table} — full table description as JSON."""
    from mcp_postgis.server import mcp

    ctx = mcp.get_context()
    return json.dumps(await introspection.describe_table(ctx, schema, table), default=str)


async def resource_layers() -> str:
    """postgis://layers — published layers (mcp_layers._meta), [] if none."""
    from mcp_postgis.server import mcp

    ctx = mcp.get_context()
    return json.dumps(await layers.list_layers(ctx), default=str)


def register(mcp: FastMCP) -> None:
    mcp.resource("postgis://schemas")(resource_schemas)
    mcp.resource("postgis://schema/{schema}/{table}")(resource_table)
    mcp.resource("postgis://layers")(resource_layers)
