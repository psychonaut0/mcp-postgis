"""Per-request server context shared by tools, resources, and the server.

Kept in its own module (importing only config + db) so tool modules can import
ServerContext without importing server.py, which would create a circular import
(server.py registers the tool modules at import time).
"""
from __future__ import annotations

from dataclasses import dataclass

from mcp_postgis.config import Config
from mcp_postgis.db import Database


@dataclass(slots=True)
class ServerContext:
    cfg: Config
    db: Database
