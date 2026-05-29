"""Configuration loading: env first, optional TOML override file second."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path


class Mode(StrEnum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class Config:
    database_url: str
    mode: Mode = Mode.READ_ONLY
    statement_timeout_ms: int = 30_000
    max_rows: int = 1000
    layer_schema: str = "mcp_layers"
    allowed_schemas: tuple[str, ...] | None = None
    log_level: str = "info"

    def __post_init__(self) -> None:
        if self.statement_timeout_ms < 0:
            raise ValueError(
                f"statement_timeout_ms must be >= 0, got {self.statement_timeout_ms}"
            )
        if self.max_rows < 1:
            raise ValueError(f"max_rows must be >= 1, got {self.max_rows}")


_TOML_KEYS = {
    "mode",
    "statement_timeout_ms",
    "max_rows",
    "layer_schema",
    "allowed_schemas",
    "log_level",
    "database_url",
}


def _parse_mode(raw: str) -> Mode:
    try:
        return Mode(raw)
    except ValueError as e:
        valid = ", ".join(m.value for m in Mode)
        raise ValueError(
            f"MCP_POSTGIS_MODE={raw!r} is invalid. Valid: {valid}"
        ) from e


def _parse_allowed_schemas(raw: str | list[str] | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return tuple(s.strip() for s in raw if s.strip())
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def load_config() -> Config:
    """Load configuration from env, then merge in MCP_POSTGIS_CONFIG TOML if set."""
    db_url = os.environ.get("MCP_POSTGIS_DATABASE_URL")
    if not db_url:
        raise ValueError(
            "MCP_POSTGIS_DATABASE_URL is required (e.g. "
            "postgresql://user:pass@host:5432/dbname)"
        )

    cfg = Config(
        database_url=db_url,
        mode=_parse_mode(os.environ.get("MCP_POSTGIS_MODE", Mode.READ_ONLY.value)),
        statement_timeout_ms=int(os.environ.get("MCP_POSTGIS_STATEMENT_TIMEOUT_MS", "30000")),
        max_rows=int(os.environ.get("MCP_POSTGIS_MAX_ROWS", "1000")),
        layer_schema=os.environ.get("MCP_POSTGIS_LAYER_SCHEMA", "mcp_layers"),
        allowed_schemas=_parse_allowed_schemas(os.environ.get("MCP_POSTGIS_ALLOWED_SCHEMAS")),
        log_level=os.environ.get("MCP_POSTGIS_LOG_LEVEL", "info"),
    )

    toml_path = os.environ.get("MCP_POSTGIS_CONFIG")
    if toml_path:
        cfg = _merge_toml(cfg, Path(toml_path))
    return cfg


def _merge_toml(cfg: Config, path: Path) -> Config:
    if not path.exists():
        raise ValueError(f"MCP_POSTGIS_CONFIG points to missing file: {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)
    unknown = set(data) - _TOML_KEYS
    if unknown:
        raise ValueError(f"Unknown keys in {path}: {sorted(unknown)}")

    updates: dict[str, object] = {}
    for k, v in data.items():
        if k == "mode":
            updates["mode"] = _parse_mode(str(v))
        elif k == "allowed_schemas":
            updates["allowed_schemas"] = _parse_allowed_schemas(v)
        else:
            updates[k] = v
    return replace(cfg, **updates)  # type: ignore[arg-type]
