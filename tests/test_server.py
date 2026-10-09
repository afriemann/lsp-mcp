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
    """build_app() must return an MCPServer with all ten tools registered."""
    app = build_app(config_path=str(config_file))
    assert isinstance(app, MCPServer)


async def test_build_app_registers_ten_tools(config_file: Path) -> None:
    """build_app() must register exactly the ten expected LSP tool names."""
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
        "get_hover",
        "get_call_hierarchy",
    }
    assert registered == expected


async def test_tool_descriptions_follow_conventions(config_file: Path) -> None:
    app = build_app(config_path=str(config_file))
    for t in await app.list_tools():
        d = t.description or ""
        assert d.startswith("Use "), t.name
        assert "note" in d, t.name
        if t.name not in {"replace_symbol_body", "rename_symbol"}:
            assert "0-based" in d, t.name


async def test_find_symbol_schema_is_backward_compatible(config_file: Path) -> None:
    app = build_app(config_path=str(config_file))
    tool = next(t for t in await app.list_tools() if t.name == "find_symbol")
    schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
    assert schema["required"] == ["query"]
    assert {"query", "file_path", "kind", "limit"} <= set(schema["properties"])


def test_timeout_env_vars(monkeypatch: pytest.MonkeyPatch, config_file: Path) -> None:
    monkeypatch.setenv("LSP_MCP_REQUEST_TIMEOUT", "3")
    monkeypatch.setenv("LSP_MCP_START_TIMEOUT", "7")
    from lsp_mcp import server

    captured: dict = {}
    real_disp, real_mgr = server.Dispatcher, server.ServerManager

    class D(real_disp):
        def __init__(self, *a, **k):
            captured["req"] = k["request_timeout"]
            super().__init__(*a, **k)

    class M(real_mgr):
        def __init__(self, *a, **k):
            captured["start"] = k["start_timeout"]
            super().__init__(*a, **k)

    monkeypatch.setattr(server, "Dispatcher", D)
    monkeypatch.setattr(server, "ServerManager", M)
    build_app(config_path=str(config_file))
    assert captured == {"req": 3.0, "start": 7.0}


def test_timeout_defaults(monkeypatch: pytest.MonkeyPatch, config_file: Path) -> None:
    monkeypatch.delenv("LSP_MCP_REQUEST_TIMEOUT", raising=False)
    monkeypatch.delenv("LSP_MCP_START_TIMEOUT", raising=False)
    from lsp_mcp import server

    captured: dict = {}
    real_disp = server.Dispatcher

    class D(real_disp):
        def __init__(self, *a, **k):
            captured["req"] = k["request_timeout"]
            super().__init__(*a, **k)

    monkeypatch.setattr(server, "Dispatcher", D)
    build_app(config_path=str(config_file))
    assert captured["req"] == 15.0
    assert server.DEFAULT_START_TIMEOUT == 30.0


def test_call_deadline_env_and_invalid_values_ignored(
    monkeypatch: pytest.MonkeyPatch, config_file: Path
) -> None:
    from lsp_mcp import server

    captured: dict = {}
    real_disp = server.Dispatcher

    class D(real_disp):
        def __init__(self, *a, **k):
            captured["deadline"] = k["call_deadline"]
            captured["req"] = k["request_timeout"]
            super().__init__(*a, **k)

    monkeypatch.setattr(server, "Dispatcher", D)
    monkeypatch.setenv("LSP_MCP_CALL_DEADLINE", "11")
    monkeypatch.setenv("LSP_MCP_REQUEST_TIMEOUT", "-5")
    build_app(config_path=str(config_file))
    assert captured == {"deadline": 11.0, "req": 15.0}
    monkeypatch.delenv("LSP_MCP_CALL_DEADLINE")
    build_app(config_path=str(config_file))
    assert captured["deadline"] == 30.0
