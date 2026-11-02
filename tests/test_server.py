"""MCP tool registration, schemas and dispatcher outcome translation."""

# spec: openspec/specs/mcp-tools/spec.md

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from multilspy.lsp_protocol_handler.server import Error

from lsp_mcp.dispatch.router import Dispatcher
from lsp_mcp.lsp.manager import ServerManager
from lsp_mcp.server import build_app

from .test_resilience import SYM, _dispatcher
from .test_router import _make_config, _make_entry, _make_server

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


def _app(monkeypatch, config_file, dispatcher):
    monkeypatch.setattr("lsp_mcp.server.Dispatcher", lambda **kw: dispatcher)
    return build_app(config_path=str(config_file))


def _arguments(tool, path):
    args = {"file_path": str(path)}
    if tool == "find_symbol":
        args["query"] = "foo"
    elif tool.startswith("find_") or tool in {"rename_symbol", "replace_symbol_body"}:
        args["symbol"] = "foo"
    if tool in {"get_hover", "get_call_hierarchy"}:
        args.update(line=0, character=4)
    if tool == "rename_symbol":
        args["new_name"] = "bar"
    if tool == "replace_symbol_body":
        args["new_body"] = "def foo():\n    return 1\n"
    return args


TOOLS = [
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
]
CAPS = {
    key: True
    for key in [
        "documentSymbolProvider",
        "workspaceSymbolProvider",
        "definitionProvider",
        "implementationProvider",
        "referencesProvider",
        "renameProvider",
        "diagnosticProvider",
        "hoverProvider",
        "callHierarchyProvider",
    ]
}


def _payload(result):
    return result.structured_content or json.loads(result.content[0].text)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("failure", ["request", "timeout", "deadline"])
async def test_total_execution_failure_across_tools(
    tmp_path, monkeypatch, config_file, tool, failure
):
    server = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])

    async def fail(*args, **kwargs):
        if failure == "request":
            raise Error(-32603, "backend unavailable")
        await asyncio.Event().wait()

    server.request_document_symbols = fail
    server.request_workspace_symbol = fail
    server.request_definition = fail
    server.request_references = fail
    server.raw_request = fail
    d, path = _dispatcher(
        tmp_path,
        server,
        request_timeout=0.01 if failure == "timeout" else 5,
        call_deadline=0.01 if failure == "deadline" else 5,
    )
    result = await _app(monkeypatch, config_file, d).call_tool(
        tool, _arguments(tool, path)
    )
    assert result.is_error, (tool, failure, _payload(result))
    payload = _payload(result)
    assert payload["note"] and payload["warnings"]
    assert json.loads(result.content[0].text) == payload


async def test_server_failed_to_start(tmp_path, monkeypatch, config_file):
    path = tmp_path / "app.py"
    path.touch()
    manager = MagicMock(spec=ServerManager)
    manager.acquire = AsyncMock(return_value=None)
    manager.failure_reason = lambda *a: "missing executable"
    config = _make_config(("*.py", ["tsls"]))
    sentinel = "launch-argument-not-for-tool-output"
    config.servers["tsls"] = replace(
        config.servers["tsls"], command=("missing-server", sentinel)
    )
    d = Dispatcher(config=config, manager=manager)
    result = await _app(monkeypatch, config_file, d).call_tool(
        "get_symbols_overview", {"file_path": str(path)}
    )
    assert result.is_error
    assert "tsls" in _payload(result)["note"]
    assert "missing executable" in result.content[0].text
    assert sentinel not in result.content[0].text
    assert "_failed" not in _payload(result)


@pytest.mark.parametrize(
    "tool",
    [
        "find_declaration",
        "find_implementations",
        "find_referencing_symbols",
        "rename_symbol",
        "replace_symbol_body",
        "get_call_hierarchy",
    ],
)
async def test_total_execution_failure_across_tools_after_preparation(
    tmp_path, monkeypatch, config_file, tool
):
    server = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])
    err = Error(-32603, "terminal operation failed")
    server.request_definition = AsyncMock(side_effect=err)
    server.request_references = AsyncMock(side_effect=err)
    item = {
        "name": "foo",
        "kind": 12,
        "uri": (tmp_path / "app.py").as_uri(),
        "range": SYM["range"],
        "selectionRange": SYM["selectionRange"],
    }

    async def raw(method, params):
        if method == "textDocument/prepareCallHierarchy":
            return [item]
        raise err

    server.raw_request = raw
    if tool == "replace_symbol_body":
        monkeypatch.setattr(
            "lsp_mcp.dispatch.router.apply_edit",
            MagicMock(side_effect=OSError("write failed")),
        )
    d, path = _dispatcher(tmp_path, server)
    result = await _app(monkeypatch, config_file, d).call_tool(
        tool, _arguments(tool, path)
    )
    assert result.is_error, _payload(result)
    assert _payload(result)["warnings"]


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("partial", [False, True])
async def test_successful_empty_answer_alongside_failure(
    tmp_path, monkeypatch, config_file, tool, partial
):
    server = _make_server(str(tmp_path), CAPS)
    server.raw_request = AsyncMock(
        return_value={"kind": "full", "items": []}
        if tool == "get_diagnostics_for_file"
        else []
    )
    d, path = _dispatcher(tmp_path, server)
    if partial:
        d._config = _make_config(("*.py", ["bad", "s1"]))
        entry = _make_entry({}, server)
        d._manager.acquire = AsyncMock(
            side_effect=lambda spec, path: None if spec.name == "bad" else entry
        )
        d._manager.failure_reason = lambda *a: "missing executable"
    result = await _app(monkeypatch, config_file, d).call_tool(
        tool, _arguments(tool, path)
    )
    assert not result.is_error
    payload = _payload(result)
    if partial:
        assert payload["note"].startswith("Partial result"), payload
        assert "bad" in payload["note"] and payload["warnings"]
    if tool == "get_symbols_overview":
        assert set(payload) == {"symbols", "note", "warnings"}


@pytest.mark.parametrize("outcome", ["renamed", "ambiguous"])
@pytest.mark.parametrize("tool", ["rename_symbol", "replace_symbol_body"])
async def test_successful_empty_answer_alongside_failure_after_resolution(
    tmp_path, monkeypatch, config_file, tool, outcome
):
    symbols = (
        [
            SYM,
            {
                **SYM,
                "range": {
                    "start": {"line": 2, "character": 0},
                    "end": {"line": 3, "character": 0},
                },
            },
        ]
        if outcome == "ambiguous"
        else [SYM]
    )
    server = _make_server(str(tmp_path), CAPS, doc_symbols=symbols)
    server.raw_request = AsyncMock(return_value={"changes": {"file:///f": []}})
    applied = MagicMock(return_value=["f"])
    monkeypatch.setattr("lsp_mcp.dispatch.router.apply_workspace_edit", applied)
    monkeypatch.setattr("lsp_mcp.dispatch.router.apply_edit", MagicMock())
    d, path = _dispatcher(tmp_path, server)
    d._config = _make_config(("*.py", ["bad", "s1"]))
    entry = _make_entry({}, server)
    d._manager.acquire = AsyncMock(
        side_effect=lambda spec, path: None if spec.name == "bad" else entry
    )
    d._manager.failure_reason = lambda *a: "missing executable"
    result = await _app(monkeypatch, config_file, d).call_tool(
        tool, _arguments(tool, path)
    )
    payload = _payload(result)
    assert not result.is_error, payload
    assert payload["note"].startswith("Partial result") and payload["warnings"]
    if outcome == "renamed":
        if tool == "rename_symbol":
            assert payload["changed_files"] == ["f"]
            applied.assert_called_once()
        else:
            assert payload["success"]
    else:
        assert payload.get("changed_files", []) == []
        assert not payload.get("success", False)
        server.raw_request.assert_not_awaited()
        applied.assert_not_called()


@pytest.mark.parametrize("case", ["unknown_with_failure", "deadline_after_answer"])
async def test_total_execution_failure_across_tools_after_diagnostic_reply(
    tmp_path, monkeypatch, config_file, case
):
    server = _make_server(str(tmp_path), CAPS)
    server.raw_request = AsyncMock(
        return_value={
            "kind": "full",
            "items": [{"range": SYM["range"], "message": "first server diagnostic"}],
        }
        if case == "deadline_after_answer"
        else None
    )
    d, path = _dispatcher(tmp_path, server, call_deadline=0.2)
    d._config = _make_config(("*.py", ["s1", "bad"]))
    entry = _make_entry({}, server)
    stalled = _make_server(str(tmp_path), CAPS)

    async def hang(*args):
        await asyncio.Event().wait()

    stalled.raw_request = hang
    stalled_entry = _make_entry({}, stalled)

    async def acquire(spec, path):
        if spec.name == "bad":
            if case == "deadline_after_answer":
                return stalled_entry
            return None
        return entry

    d._manager.acquire = acquire
    d._manager.failure_reason = lambda *a: "missing executable"
    result = await _app(monkeypatch, config_file, d).call_tool(
        "get_diagnostics_for_file", {"file_path": str(path)}
    )
    payload = _payload(result)
    assert result.is_error, payload
    assert payload["diagnostics"] == []
    assert payload["warnings"]
    if case == "deadline_after_answer":
        server.raw_request.assert_awaited_once()
        assert "deadline" in payload["note"].lower()
    else:
        assert "unknown" in payload["note"].lower()


@pytest.mark.parametrize(
    "case",
    ["missing_capability", "invalid_kind", "unknown_diagnostics", "ambiguous_symbol"],
)
async def test_successful_empty_and_informational_responses(
    tmp_path, monkeypatch, config_file, case
):
    server = _make_server(
        str(tmp_path),
        {} if case == "missing_capability" else CAPS,
        doc_symbols=[
            SYM,
            {
                **SYM,
                "range": {
                    "start": {"line": 2, "character": 0},
                    "end": {"line": 3, "character": 0},
                },
            },
        ],
    )
    d, path = _dispatcher(tmp_path, server)
    tool = (
        "find_symbol"
        if case == "invalid_kind"
        else "get_diagnostics_for_file"
        if case == "unknown_diagnostics"
        else "find_declaration"
    )
    args = _arguments(tool, path)
    if case == "invalid_kind":
        args["kind"] = "bogus"
    result = await _app(monkeypatch, config_file, d).call_tool(tool, args)
    assert not result.is_error
    assert _payload(result)["note"]


async def test_no_push_diagnostics_published(tmp_path, monkeypatch, config_file):
    server = _make_server(str(tmp_path), {})
    d, path = _dispatcher(tmp_path, server)
    result = await _app(monkeypatch, config_file, d).call_tool(
        "get_diagnostics_for_file", {"file_path": str(path)}
    )
    assert not result.is_error
    assert "unknown, not clean" in _payload(result)["note"]


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
