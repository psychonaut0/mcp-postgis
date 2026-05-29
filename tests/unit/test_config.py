"""Tests for env + TOML config loading."""
from __future__ import annotations

from pathlib import Path

import pytest

from mcp_postgis.config import Config, Mode, load_config


def test_load_config_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_POSTGIS_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="MCP_POSTGIS_DATABASE_URL"):
        load_config()


def test_load_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_POSTGIS_DATABASE_URL", "postgresql://localhost/db")
    cfg = load_config()
    assert cfg.database_url == "postgresql://localhost/db"
    assert cfg.mode is Mode.READ_ONLY
    assert cfg.statement_timeout_ms == 30_000
    assert cfg.max_rows == 1000
    assert cfg.layer_schema == "mcp_layers"
    assert cfg.allowed_schemas is None


def test_load_config_parses_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_POSTGIS_DATABASE_URL", "postgresql://localhost/db")
    monkeypatch.setenv("MCP_POSTGIS_MODE", "read_write")
    monkeypatch.setenv("MCP_POSTGIS_STATEMENT_TIMEOUT_MS", "5000")
    monkeypatch.setenv("MCP_POSTGIS_MAX_ROWS", "250")
    monkeypatch.setenv("MCP_POSTGIS_LAYER_SCHEMA", "claude")
    monkeypatch.setenv("MCP_POSTGIS_ALLOWED_SCHEMAS", "app, public ,mcp_layers")
    cfg = load_config()
    assert cfg.mode is Mode.READ_WRITE
    assert cfg.statement_timeout_ms == 5000
    assert cfg.max_rows == 250
    assert cfg.layer_schema == "claude"
    assert cfg.allowed_schemas == ("app", "public", "mcp_layers")


def test_load_config_rejects_bad_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_POSTGIS_DATABASE_URL", "postgresql://localhost/db")
    monkeypatch.setenv("MCP_POSTGIS_MODE", "yolo")
    with pytest.raises(ValueError, match="MCP_POSTGIS_MODE"):
        load_config()


def test_load_config_toml_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_POSTGIS_DATABASE_URL", "postgresql://localhost/db")
    toml = tmp_path / "config.toml"
    toml.write_text(
        'max_rows = 99\nmode = "admin"\nlayer_schema = "from_toml"\n'
    )
    monkeypatch.setenv("MCP_POSTGIS_CONFIG", str(toml))
    cfg = load_config()
    assert cfg.max_rows == 99
    assert cfg.mode is Mode.ADMIN
    assert cfg.layer_schema == "from_toml"


def test_config_is_immutable() -> None:
    import dataclasses

    cfg = Config(database_url="postgresql://x", mode=Mode.READ_ONLY)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.max_rows = 5  # type: ignore[misc]


def test_load_config_toml_rejects_unknown_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_POSTGIS_DATABASE_URL", "postgresql://localhost/db")
    toml = tmp_path / "config.toml"
    toml.write_text('not_a_field = 1\n')
    monkeypatch.setenv("MCP_POSTGIS_CONFIG", str(toml))
    with pytest.raises(ValueError, match="Unknown keys"):
        load_config()


def test_config_rejects_negative_timeout() -> None:
    with pytest.raises(ValueError, match="statement_timeout_ms"):
        Config(database_url="postgresql://x", statement_timeout_ms=-1)


def test_config_rejects_zero_max_rows() -> None:
    with pytest.raises(ValueError, match="max_rows"):
        Config(database_url="postgresql://x", max_rows=0)
