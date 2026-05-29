"""Regression: tool/resource submodules must be importable before server.

Guards against the circular import where server.py registers tool modules at
import time while each tool module imports from server.py.
"""
from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "mcp_postgis.tools.geometry",
        "mcp_postgis.tools.export",
        "mcp_postgis.tools.introspection",
        "mcp_postgis.tools.query",
        "mcp_postgis.tools.spatial",
        "mcp_postgis.tools.layers",
        "mcp_postgis.resources",
    ],
)
def test_submodule_importable_before_server(module: str) -> None:
    code = f"import {module} as m; assert hasattr(m, 'register'), 'no register'"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
