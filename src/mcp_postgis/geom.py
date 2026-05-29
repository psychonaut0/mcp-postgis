"""Tiny helpers for accepting geometry input in WKT or GeoJSON form."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

_WKT_PREFIXES = (
    "POINT", "LINESTRING", "POLYGON",
    "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON",
    "GEOMETRYCOLLECTION",
)
_WKT_RE = re.compile(
    r"^\s*(SRID=\d+;\s*)?(" + "|".join(_WKT_PREFIXES) + r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class GeomInput:
    kind: Literal["wkt", "geojson"]
    value: str

    def to_sql_fragment(self, *, srid: int) -> tuple[str, tuple[Any, ...]]:
        if self.kind == "wkt":
            return "ST_GeomFromText(%s, %s)", (self.value, srid)
        return "ST_SetSRID(ST_GeomFromGeoJSON(%s), %s)", (self.value, srid)


def parse_geom_input(raw: str | dict[str, Any]) -> GeomInput:
    if isinstance(raw, dict):
        return GeomInput(kind="geojson", value=json.dumps(raw))
    text = raw.strip()
    if not text:
        raise ValueError("geometry input is empty")
    if _WKT_RE.match(text):
        return GeomInput(kind="wkt", value=text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(
            f"geometry input not recognized as WKT or GeoJSON: {text[:60]!r}"
        ) from err
    if not isinstance(obj, dict) or "type" not in obj:
        raise ValueError("geometry input not recognized as GeoJSON object")
    return GeomInput(kind="geojson", value=text)


__all__ = ["GeomInput", "parse_geom_input"]
