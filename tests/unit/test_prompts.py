"""Unit tests for §9 prompt templates (pure string builders)."""
from __future__ import annotations

from mcp_postgis.prompts import (
    analyze_layer,
    compare_layers,
    nearest_things,
    within_radius,
)


def test_analyze_layer_text() -> None:
    msg = analyze_layer("app", "cities")
    assert "app.cities" in msg
    assert "SRID" in msg


def test_nearest_things_text() -> None:
    msg = nearest_things("app.cities", "Cagliari", k=5)
    assert "5" in msg
    assert "Cagliari" in msg
    assert "app.cities" in msg


def test_within_radius_text() -> None:
    msg = within_radius("app.cities", "Sardinia", "10 km", layer_name="near")
    assert "Sardinia" in msg
    assert "10 km" in msg
    assert "near" in msg


def test_within_radius_no_layer() -> None:
    msg = within_radius("app.cities", "Sardinia", "10 km")
    assert "Sardinia" in msg


def test_compare_layers_text() -> None:
    msg = compare_layers("layer_a", "layer_b")
    assert "layer_a" in msg
    assert "layer_b" in msg
