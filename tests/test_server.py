"""Smoke tests for the MCP server entry-point (server.py).

These tests guard against the server module failing to import or construct its
application — a class of breakage the unit tests for dispatcher/manager/config
do not catch because they never import lsp_mcp.server directly.
"""

# spec: openspec/specs/mcp-tools/spec.md

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer

from lsp_mcp.server import build_app

_MINIMAL_CONFIG = """\
servers:
  test_server:
    command: [echo, hello]
file_handlers:
  "*.py":
    - server: test_server
"""


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(_MINIMAL_CONFIG)
    return p


def test_build_app_returns_mcp_server(config_file: Path) -> None:
    """build_app() must return an MCPServer with all eight tools registered."""
    app = build_app(config_path=str(config_file))
    assert isinstance(app, MCPServer)


async def test_build_app_registers_eight_tools(config_file: Path) -> None:
    """build_app() must register exactly the eight expected LSP tool names."""
    app = build_app(config_path=str(config_file))
    registered = {t.name for t in await app.list_tools()}
    expected = {
        "get_symbols_overview",
        "find_symbol",
        "find_declaration",
        "find_implementations",
        "find_referencing_symbols",
        "replace_symbol_body",
        "rename_symbol",
        "get_diagnostics_for_file",
    }
    assert registered == expected
