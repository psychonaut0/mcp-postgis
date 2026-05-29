"""§9 prompt templates. Each builder returns the prompt text; register() exposes
them as MCP prompts. They teach common workflows; they do not constrain."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def analyze_layer(schema: str, table: str) -> str:
    return (
        f"Summarise the spatial table {schema}.{table}: its geometry column, "
        f"SRID, spatial extent, row count, and the notable attribute columns. "
        f"Use the introspection and query tools."
    )


def nearest_things(table: str, point_or_place: str, k: int = 10) -> str:
    return (
        f"Find the {k} nearest {table} features to {point_or_place}. "
        f"Return them ordered by distance with the distance in metres."
    )


def within_radius(
    table: str, place: str, distance: str, layer_name: str | None = None
) -> str:
    base = f"Find all {table} features within {distance} of {place}."
    if layer_name:
        base += f" Publish the result as the layer '{layer_name}'."
    return base


def compare_layers(left: str, right: str) -> str:
    return (
        f"Compare the two layers {left} and {right}: their spatial overlap, "
        f"differences in attribute distributions, and candidate join keys."
    )


def register(mcp: FastMCP) -> None:
    mcp.prompt()(analyze_layer)
    mcp.prompt()(nearest_things)
    mcp.prompt()(within_radius)
    mcp.prompt()(compare_layers)
