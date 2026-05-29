"""Spatial analysis tools: bbox, polygon, knn, dwithin, buffer, intersect."""
from __future__ import annotations

from typing import Any

import psycopg.sql
from mcp.server.fastmcp import Context, FastMCP

from mcp_postgis import errors
from mcp_postgis.context import ServerContext
from mcp_postgis.errors import ToolError
from mcp_postgis.geom import parse_geom_input

# FastMCP Context is Generic[ServerSessionT, LifespanContextT, RequestT]; using
# Any for all params avoids noisy type-arg errors on every function signature.
_Ctx = Context[Any, Any, Any]

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _ensure_table_exists(cur: Any, schema: str, table: str) -> None:
    await cur.execute(
        "SELECT to_regclass(format('%%I.%%I', %s::text, %s::text)) IS NOT NULL",
        (schema, table),
    )
    row = await cur.fetchone()
    if not row or not row[0]:
        raise ToolError("not_found", f"{schema}.{table} does not exist")


def _resolve_limit(requested: int | None, cap: int) -> int:
    return cap if requested is None else max(1, min(requested, cap))


def _format_feature_collection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    features = []
    for r in rows:
        geom = r.pop("__geom_geojson", None)
        features.append({"type": "Feature", "geometry": geom, "properties": r})
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# 9.1  features_in_bbox
# ---------------------------------------------------------------------------


async def features_in_bbox(
    ctx: _Ctx,
    schema: str,
    table: str,
    geom_col: str,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    srid: int = 4326,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return all features whose geometry overlaps the given bounding box.

    Returns a GeoJSON FeatureCollection with a ``truncated`` flag and
    ``limit`` field.
    """
    srv: ServerContext = ctx.request_context.lifespan_context
    eff_limit = _resolve_limit(limit, srv.cfg.max_rows)

    async with srv.db.read() as cur:
        try:
            await _ensure_table_exists(cur, schema, table)

            query = psycopg.sql.SQL(
                "SELECT *, ST_AsGeoJSON({geom})::json AS __geom_geojson "
                "FROM {schema}.{table} "
                "WHERE {geom} && ST_MakeEnvelope(%s, %s, %s, %s, %s) "
                "LIMIT %s"
            ).format(
                geom=psycopg.sql.Identifier(geom_col),
                schema=psycopg.sql.Identifier(schema),
                table=psycopg.sql.Identifier(table),
            )

            await cur.execute(
                query,
                (min_x, min_y, max_x, max_y, srid, eff_limit + 1),
            )
            assert cur.description is not None
            cols = [d.name for d in cur.description]
            raw = await cur.fetchall()
        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    truncated = len(raw) > eff_limit
    if truncated:
        raw = raw[:eff_limit]

    rows = [dict(zip(cols, r, strict=True)) for r in raw]
    fc = _format_feature_collection(rows)
    fc["truncated"] = truncated
    fc["limit"] = eff_limit
    return fc


# ---------------------------------------------------------------------------
# 9.2  features_in_polygon
# ---------------------------------------------------------------------------

_PREDICATES = {
    "intersects": "ST_Intersects",
    "within": "ST_Within",
    "contains": "ST_Contains",
}


async def features_in_polygon(
    ctx: _Ctx,
    schema: str,
    table: str,
    geom_col: str,
    polygon: str | dict[str, Any],
    predicate: str = "intersects",
    srid: int = 4326,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return features that satisfy a spatial predicate against a polygon.

    *polygon* accepts WKT or GeoJSON (string or dict).
    *predicate* must be one of ``intersects``, ``within``, or ``contains``.

    Returns a GeoJSON FeatureCollection with ``truncated`` and ``limit`` fields.
    """
    srv: ServerContext = ctx.request_context.lifespan_context
    eff_limit = _resolve_limit(limit, srv.cfg.max_rows)

    if predicate not in _PREDICATES:
        raise ToolError(
            "invalid_argument",
            f"Unknown predicate {predicate!r}. Must be one of: "
            + ", ".join(_PREDICATES),
        )

    pg_fn = _PREDICATES[predicate]
    geom_input = parse_geom_input(polygon)
    geom_frag, geom_params = geom_input.to_sql_fragment(srid=srid)

    async with srv.db.read() as cur:
        try:
            await _ensure_table_exists(cur, schema, table)

            query = psycopg.sql.SQL(
                "SELECT *, ST_AsGeoJSON({geom_col})::json AS __geom_geojson "
                "FROM {schema}.{table} "
                "WHERE {fn}({geom_col}, " + geom_frag + ") "
                "LIMIT %s"
            ).format(
                geom_col=psycopg.sql.Identifier(geom_col),
                schema=psycopg.sql.Identifier(schema),
                table=psycopg.sql.Identifier(table),
                fn=psycopg.sql.SQL(pg_fn),
            )

            await cur.execute(query, (*geom_params, eff_limit + 1))
            assert cur.description is not None
            cols = [d.name for d in cur.description]
            raw = await cur.fetchall()
        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    truncated = len(raw) > eff_limit
    if truncated:
        raw = raw[:eff_limit]

    rows = [dict(zip(cols, r, strict=True)) for r in raw]
    fc = _format_feature_collection(rows)
    fc["truncated"] = truncated
    fc["limit"] = eff_limit
    return fc


# ---------------------------------------------------------------------------
# 9.3  nearest_features
# ---------------------------------------------------------------------------


async def nearest_features(
    ctx: _Ctx,
    schema: str,
    table: str,
    geom_col: str,
    point: str | dict[str, Any],
    k: int = 10,
    max_distance_m: float | None = None,
    srid: int = 4326,
) -> dict[str, Any]:
    """Return the *k* nearest features to a given point, ordered by distance.

    Each feature's ``properties`` includes ``__distance_m`` (in metres).
    If *max_distance_m* is given, only features within that radius are returned.

    Returns a GeoJSON FeatureCollection.
    """
    srv: ServerContext = ctx.request_context.lifespan_context
    k = max(1, min(k, srv.cfg.max_rows))

    geom_input = parse_geom_input(point)
    geom_frag, geom_params = geom_input.to_sql_fragment(srid=srid)

    async with srv.db.read() as cur:
        try:
            await _ensure_table_exists(cur, schema, table)

            geom_id = psycopg.sql.Identifier(geom_col)
            schema_id = psycopg.sql.Identifier(schema)
            table_id = psycopg.sql.Identifier(table)

            if max_distance_m is not None:
                # SELECT clause uses geom_params (1st occurrence)
                # WHERE clause uses geom_params + max_distance_m (2nd occurrence)
                # ORDER BY uses geom_params (3rd occurrence)
                query = psycopg.sql.SQL(
                    "SELECT *, ST_AsGeoJSON({geom})::json AS __geom_geojson, "
                    "ST_Distance({geom}::geography, (" + geom_frag + ")::geography) AS __distance_m "  # noqa: E501
                    "FROM {schema}.{table} "
                    "WHERE ST_DWithin({geom}::geography, (" + geom_frag + ")::geography, %s) "
                    "ORDER BY {geom} <-> (" + geom_frag + ") "
                    "LIMIT %s"
                ).format(
                    geom=geom_id,
                    schema=schema_id,
                    table=table_id,
                )
                params = (
                    *geom_params,           # SELECT ST_Distance — 1st
                    *geom_params,           # WHERE ST_DWithin — 2nd
                    max_distance_m,         # DWithin radius
                    *geom_params,           # ORDER BY <-> — 3rd
                    k,
                )
            else:
                # SELECT clause uses geom_params (1st occurrence)
                # ORDER BY uses geom_params (2nd occurrence)
                query = psycopg.sql.SQL(
                    "SELECT *, ST_AsGeoJSON({geom})::json AS __geom_geojson, "
                    "ST_Distance({geom}::geography, (" + geom_frag + ")::geography) AS __distance_m "  # noqa: E501
                    "FROM {schema}.{table} "
                    "ORDER BY {geom} <-> (" + geom_frag + ") "
                    "LIMIT %s"
                ).format(
                    geom=geom_id,
                    schema=schema_id,
                    table=table_id,
                )
                params = (
                    *geom_params,   # SELECT ST_Distance — 1st
                    *geom_params,   # ORDER BY <-> — 2nd
                    k,
                )

            await cur.execute(query, params)
            assert cur.description is not None
            cols = [d.name for d in cur.description]
            raw = await cur.fetchall()
        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    rows = [dict(zip(cols, r, strict=True)) for r in raw]
    return _format_feature_collection(rows)


# ---------------------------------------------------------------------------
# 9.4  within_distance
# ---------------------------------------------------------------------------


async def within_distance(
    ctx: _Ctx,
    schema: str,
    table: str,
    geom_col: str,
    geom: str | dict[str, Any],
    distance_m: float,
    srid: int = 4326,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return all features within *distance_m* metres of *geom*.

    Returns a GeoJSON FeatureCollection with ``truncated`` and ``limit`` fields.
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    if distance_m < 0:
        raise ToolError("invalid_argument", "distance_m must be >= 0")

    eff_limit = _resolve_limit(limit, srv.cfg.max_rows)
    geom_input = parse_geom_input(geom)
    geom_frag, geom_params = geom_input.to_sql_fragment(srid=srid)

    async with srv.db.read() as cur:
        try:
            await _ensure_table_exists(cur, schema, table)

            query = psycopg.sql.SQL(
                "SELECT *, ST_AsGeoJSON({geom_col})::json AS __geom_geojson "
                "FROM {schema}.{table} "
                "WHERE ST_DWithin({geom_col}::geography, (" + geom_frag + ")::geography, %s) "
                "LIMIT %s"
            ).format(
                geom_col=psycopg.sql.Identifier(geom_col),
                schema=psycopg.sql.Identifier(schema),
                table=psycopg.sql.Identifier(table),
            )

            await cur.execute(query, (*geom_params, distance_m, eff_limit + 1))
            assert cur.description is not None
            cols = [d.name for d in cur.description]
            raw = await cur.fetchall()
        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    truncated = len(raw) > eff_limit
    if truncated:
        raw = raw[:eff_limit]

    rows = [dict(zip(cols, r, strict=True)) for r in raw]
    fc = _format_feature_collection(rows)
    fc["truncated"] = truncated
    fc["limit"] = eff_limit
    return fc


# ---------------------------------------------------------------------------
# 9.5  buffer
# ---------------------------------------------------------------------------


async def buffer(
    ctx: _Ctx,
    geom: str | dict[str, Any],
    distance_m: float,
    srid: int = 4326,
    return_format: str = "geojson",
) -> dict[str, Any]:
    """Return a buffered geometry at *distance_m* metres around *geom*.

    *return_format* must be ``geojson`` (default) or ``wkt``.

    Returns a dict with ``geometry`` (GeoJSON) or ``wkt``, plus ``area_m2``.
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    if return_format not in {"geojson", "wkt"}:
        raise ToolError(
            "invalid_argument",
            f"return_format must be 'geojson' or 'wkt', got {return_format!r}",
        )

    geom_input = parse_geom_input(geom)
    geom_frag, geom_params = geom_input.to_sql_fragment(srid=srid)

    sql = (
        "WITH b AS ("  # noqa: S608
        "  SELECT ST_Buffer((" + geom_frag + ")::geography, %s)::geometry AS g"
        ") "
        "SELECT ST_AsGeoJSON(g)::json, ST_AsText(g), ST_Area(g::geography) FROM b"
    )

    async with srv.db.read() as cur:
        try:
            await cur.execute(sql, (*geom_params, distance_m))
            row = await cur.fetchone()
        except Exception as exc:
            raise errors.translate(exc) from exc

    assert row is not None
    geojson, wkt_text, area_m2 = row[0], row[1], row[2]

    if return_format == "geojson":
        return {"geometry": geojson, "area_m2": float(area_m2)}
    return {"wkt": wkt_text, "area_m2": float(area_m2)}


# ---------------------------------------------------------------------------
# 9.6  intersect_layers
# ---------------------------------------------------------------------------


async def intersect_layers(
    ctx: _Ctx,
    left_schema: str,
    left_table: str,
    left_geom: str,
    right_schema: str,
    right_table: str,
    right_geom: str,
    return_format: str = "left_with_right_attrs",
    limit: int | None = None,
) -> dict[str, Any]:
    """Spatially intersect two layers and return the result.

    Two modes:

    - ``left_with_right_attrs`` — one row per intersecting left feature with
      right attributes prefixed ``r__``.
    - ``intersection_geom`` — one row per intersecting pair with the actual
      intersection geometry; attributes prefixed ``l__`` and ``r__``.

    Returns a GeoJSON FeatureCollection with ``truncated`` and ``limit`` fields.
    """
    srv: ServerContext = ctx.request_context.lifespan_context

    if return_format not in {"left_with_right_attrs", "intersection_geom"}:
        raise ToolError(
            "invalid_argument",
            "return_format must be 'left_with_right_attrs' or 'intersection_geom', "
            f"got {return_format!r}",
        )

    eff_limit = _resolve_limit(limit, srv.cfg.max_rows)

    ls = psycopg.sql.Identifier(left_schema)
    lt = psycopg.sql.Identifier(left_table)
    lg = psycopg.sql.Identifier(left_geom)
    rs = psycopg.sql.Identifier(right_schema)
    rt = psycopg.sql.Identifier(right_table)
    rg = psycopg.sql.Identifier(right_geom)

    async with srv.db.read() as cur:
        try:
            await _ensure_table_exists(cur, left_schema, left_table)
            await _ensure_table_exists(cur, right_schema, right_table)

            if return_format == "left_with_right_attrs":
                query = psycopg.sql.SQL(
                    "SELECT l.*, "
                    "ST_AsGeoJSON(l.{lg})::json AS __geom_geojson, "
                    "to_jsonb(r) - %s::text AS __right "
                    "FROM {ls}.{lt} l "
                    "JOIN {rs}.{rt} r ON ST_Intersects(l.{lg}, r.{rg}) "
                    "LIMIT %s"
                ).format(ls=ls, lt=lt, lg=lg, rs=rs, rt=rt, rg=rg)

                await cur.execute(query, (right_geom, eff_limit + 1))

            else:  # intersection_geom
                query = psycopg.sql.SQL(
                    "SELECT ST_AsGeoJSON(ST_Intersection(l.{lg}, r.{rg}))::json AS __geom_geojson, "
                    "to_jsonb(l) - %s::text AS __left, "
                    "to_jsonb(r) - %s::text AS __right "
                    "FROM {ls}.{lt} l "
                    "JOIN {rs}.{rt} r ON ST_Intersects(l.{lg}, r.{rg}) "
                    "WHERE NOT ST_IsEmpty(ST_Intersection(l.{lg}, r.{rg})) "
                    "LIMIT %s"
                ).format(ls=ls, lt=lt, lg=lg, rs=rs, rt=rt, rg=rg)

                await cur.execute(query, (left_geom, right_geom, eff_limit + 1))

            assert cur.description is not None
            cols = [d.name for d in cur.description]
            raw = await cur.fetchall()
        except ToolError:
            raise
        except Exception as exc:
            raise errors.translate(exc) from exc

    truncated = len(raw) > eff_limit
    if truncated:
        raw = raw[:eff_limit]

    rows = [dict(zip(cols, r, strict=True)) for r in raw]
    features = []
    if return_format == "left_with_right_attrs":
        for r in rows:
            geom_geojson = r.pop("__geom_geojson", None)
            right_attrs = r.pop("__right", None) or {}
            # Drop left geometry column from properties to avoid duplication
            r.pop(left_geom, None)
            props: dict[str, Any] = dict(r)
            # Merge right attrs with r__ prefix
            for k, v in right_attrs.items():
                props[f"r__{k}"] = v
            features.append({"type": "Feature", "geometry": geom_geojson, "properties": props})
    else:  # intersection_geom
        for r in rows:
            geom_geojson = r.pop("__geom_geojson", None)
            left_attrs = r.pop("__left", None) or {}
            right_attrs = r.pop("__right", None) or {}
            iprops: dict[str, Any] = {}
            for k, v in left_attrs.items():
                iprops[f"l__{k}"] = v
            for k, v in right_attrs.items():
                iprops[f"r__{k}"] = v
            features.append({"type": "Feature", "geometry": geom_geojson, "properties": iprops})

    fc: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    fc["truncated"] = truncated
    fc["limit"] = eff_limit
    return fc


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: FastMCP) -> None:
    """Register all spatial analysis tools on *mcp*."""
    mcp.tool()(features_in_bbox)
    mcp.tool()(features_in_polygon)
    mcp.tool()(nearest_features)
    mcp.tool()(within_distance)
    mcp.tool()(buffer)
    mcp.tool()(intersect_layers)


__all__ = [
    "buffer",
    "features_in_bbox",
    "features_in_polygon",
    "intersect_layers",
    "nearest_features",
    "register",
    "within_distance",
]
