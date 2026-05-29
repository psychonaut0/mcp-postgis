"""Tests for the tiny geometry input helper."""
from __future__ import annotations

import pytest

from mcp_postgis.geom import GeomInput, parse_geom_input


def test_parse_wkt() -> None:
    g = parse_geom_input("POINT(12.5 41.9)")
    assert g.kind == "wkt"
    assert g.value == "POINT(12.5 41.9)"


def test_parse_geojson_str() -> None:
    g = parse_geom_input('{"type":"Point","coordinates":[12.5,41.9]}')
    assert g.kind == "geojson"
    assert '"Point"' in g.value


def test_parse_geojson_dict() -> None:
    g = parse_geom_input({"type": "Point", "coordinates": [12.5, 41.9]})
    assert g.kind == "geojson"


def test_parse_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="not recognized"):
        parse_geom_input("not a geometry")


def test_to_sql_fragment_wkt() -> None:
    g = GeomInput(kind="wkt", value="POINT(0 0)")
    frag, params = g.to_sql_fragment(srid=4326)
    assert frag == "ST_GeomFromText(%s, %s)"
    assert params == ("POINT(0 0)", 4326)


def test_to_sql_fragment_geojson() -> None:
    g = GeomInput(kind="geojson", value='{"type":"Point","coordinates":[0,0]}')
    frag, params = g.to_sql_fragment(srid=4326)
    assert frag == "ST_SetSRID(ST_GeomFromGeoJSON(%s), %s)"
    assert params[0].startswith("{")
    assert params[1] == 4326
