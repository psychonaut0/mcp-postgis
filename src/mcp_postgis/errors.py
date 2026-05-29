"""Structured tool errors. All tools convert internal exceptions to these."""
from __future__ import annotations

from typing import Literal

import psycopg

from mcp_postgis.safety import PermissionDeniedError

ErrorCode = Literal[
    "permission_denied",
    "timeout",
    "truncated",
    "invalid_geom",
    "bad_srid",
    "not_found",
    "invalid_argument",
    "db_error",
]


class ToolError(Exception):
    """Structured tool error. Not a frozen dataclass: Python's exception
    machinery sets ``__traceback__`` after construction, which conflicts with
    ``@dataclass(frozen=True, slots=True)``."""

    def __init__(
        self, code: ErrorCode, message: str, hint: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, str | None]:
        return {"error": self.code, "message": self.message, "hint": self.hint}


def translate(exc: BaseException) -> ToolError:
    """Best-effort: turn an internal exception into a structured ToolError."""
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, PermissionDeniedError):
        return ToolError("permission_denied", str(exc),
                         hint="rewrite as a SELECT or run the server in a higher mode")
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return ToolError("timeout", str(exc),
                         hint="raise MCP_POSTGIS_STATEMENT_TIMEOUT_MS or narrow the query")
    if isinstance(exc, psycopg.errors.InvalidParameterValue):
        return ToolError("bad_srid", str(exc),
                         hint="check the SRID exists in spatial_ref_sys")
    if isinstance(exc, psycopg.Error):
        return ToolError("db_error", str(exc))
    return ToolError("db_error", repr(exc))


__all__ = ["ErrorCode", "ToolError", "translate"]
