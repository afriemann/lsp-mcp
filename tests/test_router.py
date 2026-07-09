"""Integration tests for the Dispatcher routing logic (mocked servers)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lsp_mcp.config.model import Config, FileHandler, ServerSpec
from lsp_mcp.dispatch.router import Dispatcher
from lsp_mcp.lsp.capabilities import CapabilitySet, ToolKind
from lsp_mcp.lsp.manager import ServerEntry, ServerManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(*patterns_and_servers: tuple[str, list[str]]) -> Config:
    servers: dict[str, ServerSpec] = {}
    handlers = []
    for pattern, snames in patterns_and_servers:
        for n in snames:
            if n not in servers:
                servers[n] = ServerSpec(name=n, command=(n,))
        handlers.append(FileHandler(pattern=pattern, server_names=tuple(snames)))
    return Config(servers=servers, handlers=tuple(handlers))


def _make_entry(caps: dict, server: MagicMock) -> ServerEntry:
    from contextlib import AsyncExitStack

    entry = ServerEntry(
        server=server,
        exit_stack=AsyncExitStack(),
        lock=asyncio.Lock(),
        last_used=0.0,
        state="ready",
    )
    return entry


def _make_server(
    root: str,
    caps: dict,
    *,
    doc_symbols: list | None = None,
    definition_result: list | None = None,
    references_result: list | None = None,
    push_diags: dict | None = None,
) -> MagicMock:
    server = MagicMock()
    server.repository_root_path = root
    server.capabilities = CapabilitySet(caps)
    server.open_file_buffers = {}
    server.push_diagnostics = push_diags or {}

    server.request_document_symbols = AsyncMock(return_value=(doc_symbols or [], None))
    server.request_definition = AsyncMock(return_value=definition_result or [])
    server.request_references = AsyncMock(return_value=references_result or [])
    server.request_workspace_symbol = AsyncMock(return_value=[])
    server.raw_request = AsyncMock(return_value=None)
    server.wait_for_diagnostics = AsyncMock()

    @contextmanager
    def _open_file(rel):
        yield

    server.open_file = _open_file
    return server


def _make_location_dict(path: str, line: int) -> dict:
    return {
        "absolutePath": path,
        "uri": f"file://{path}",
        "range": {
            "start": {"line": line, "character": 0},
            "end": {"line": line, "character": 5},
        },
    }


# ---------------------------------------------------------------------------
# Tests: get_symbols_overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_symbols_overview_first_wins(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    f.touch()

    raw_syms = [
        {
            "name": "MyFunc",
            "kind": 12,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 5, "character": 0},
            },
            "selectionRange": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 10},
            },
            "children": [],
        }
    ]
    server1 = _make_server(
        str(tmp_path), {"documentSymbolProvider": True}, doc_symbols=raw_syms
    )
    server2 = _make_server(
        str(tmp_path), {"documentSymbolProvider": True}, doc_symbols=[]
    )

    config = _make_config(("*.py", ["s1", "s2"]))
    manager = MagicMock(spec=ServerManager)

    async def _acquire(spec, file_path):
        if spec.name == "s1":
            return _make_entry({}, server1)
        return _make_entry({}, server2)

    manager.acquire = _acquire

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.get_symbols_overview(str(f))
    assert len(result.symbols) == 1
    assert result.symbols[0].name == "MyFunc"
    # server2 should NOT have been queried (first-wins)
    server2.request_document_symbols.assert_not_called()


@pytest.mark.asyncio
async def test_get_symbols_overview_no_capable_server(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    f.touch()

    config = _make_config(("*.py", ["s1"]))
    manager = MagicMock(spec=ServerManager)

    # server has no documentSymbolProvider
    server = _make_server(str(tmp_path), {})
    manager.acquire = AsyncMock(return_value=_make_entry({}, server))

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.get_symbols_overview(str(f))
    assert result.symbols == []
    assert "documentSymbolProvider" in result.note


@pytest.mark.asyncio
async def test_get_symbols_overview_partial_failure(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    f.touch()

    config = _make_config(("*.py", ["s1", "s2"]))
    manager = MagicMock(spec=ServerManager)

    raw_syms = [
        {
            "name": "Foo",
            "kind": 5,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 2, "character": 0},
            },
            "selectionRange": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 3},
            },
            "children": [],
        }
    ]
    server_ok = _make_server(
        str(tmp_path), {"documentSymbolProvider": True}, doc_symbols=raw_syms
    )

    call_count = 0

    async def _acquire(spec, file_path):
        nonlocal call_count
        call_count += 1
        if spec.name == "s1":
            return None  # failed to start
        return _make_entry({}, server_ok)

    manager.acquire = _acquire

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.get_symbols_overview(str(f))
    assert len(result.symbols) == 1
    assert any("s1" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Tests: find_declaration (first-wins fallthrough)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_declaration_first_wins_fallthrough(tmp_path: Path) -> None:
    """First server returns empty; second server returns result."""
    f = tmp_path / "app.py"
    f.write_text("def foo():\n    pass\n")

    raw_syms = [
        {
            "name": "foo",
            "kind": 12,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 1, "character": 8},
            },
            "selectionRange": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 7},
            },
            "children": [],
        }
    ]

    location = _make_location_dict(str(f), 5)

    server1 = _make_server(
        str(tmp_path),
        {"definitionProvider": True, "documentSymbolProvider": True},
        doc_symbols=raw_syms,
        definition_result=[],  # returns empty — not a win
    )
    server2 = _make_server(
        str(tmp_path),
        {"definitionProvider": True, "documentSymbolProvider": True},
        doc_symbols=raw_syms,
        definition_result=[location],
    )

    config = _make_config(("*.py", ["s1", "s2"]))
    manager = MagicMock(spec=ServerManager)

    async def _acquire(spec, file_path):
        if spec.name == "s1":
            return _make_entry({}, server1)
        return _make_entry({}, server2)

    manager.acquire = _acquire

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.find_declaration("foo", str(f))
    assert len(result.locations) == 1
    # Both servers were queried (fallthrough on empty)
    server1.request_definition.assert_called_once()
    server2.request_definition.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: get_diagnostics_for_file (merge)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_diagnostics_merged_from_all_servers(tmp_path: Path) -> None:
    """Diagnostics are merged from all capable servers, tagged by source."""
    f = tmp_path / "app.py"
    f.write_text("import os\n")

    uri = f.as_uri()
    server1 = _make_server(
        str(tmp_path),
        {},  # push path
        push_diags={
            uri: [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 9},
                    },
                    "message": "unused import 'os'",
                    "severity": 2,
                }
            ]
        },
    )
    server2 = _make_server(
        str(tmp_path),
        {},  # push path
        push_diags={
            uri: [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 9},
                    },
                    "message": "type error",
                    "severity": 1,
                }
            ]
        },
    )

    config = _make_config(("*.py", ["s1", "s2"]))
    manager = MagicMock(spec=ServerManager)

    async def _acquire(spec, file_path):
        if spec.name == "s1":
            return _make_entry({}, server1)
        return _make_entry({}, server2)

    manager.acquire = _acquire

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.get_diagnostics_for_file(str(f))
    messages = {d.message for d in result.diagnostics}
    sources = {d.source_server for d in result.diagnostics}
    assert "unused import 'os'" in messages
    assert "type error" in messages
    assert "s1" in sources
    assert "s2" in sources


@pytest.mark.asyncio
async def test_get_diagnostics_dedup_identical(tmp_path: Path) -> None:
    """Identical diagnostics from two servers are deduplicated."""
    f = tmp_path / "app.py"
    f.write_text("import os\n")
    uri = f.as_uri()

    same_diag = {
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 9},
        },
        "message": "unused import",
        "severity": 2,
    }

    server1 = _make_server(str(tmp_path), {}, push_diags={uri: [same_diag]})
    server2 = _make_server(str(tmp_path), {}, push_diags={uri: [same_diag]})

    config = _make_config(("*.py", ["s1", "s2"]))
    manager = MagicMock(spec=ServerManager)

    async def _acquire(spec, file_path):
        if spec.name == "s1":
            return _make_entry({}, server1)
        return _make_entry({}, server2)

    manager.acquire = _acquire

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.get_diagnostics_for_file(str(f))
    # Only one diagnostic despite two servers reporting the same thing
    assert len(result.diagnostics) == 1


@pytest.mark.asyncio
async def test_no_capable_server_note(tmp_path: Path) -> None:
    """Missing capable server returns explanatory note, not an exception."""
    f = tmp_path / "app.go"
    f.touch()

    # Config routes *.go to a server with no definitionProvider
    config = _make_config(("*.go", ["gopls"]))
    manager = MagicMock(spec=ServerManager)
    server = _make_server(str(tmp_path), {})  # no capabilities
    manager.acquire = AsyncMock(return_value=_make_entry({}, server))

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.find_declaration("main", str(f))
    assert result.locations == []
    assert result.note  # has an explanatory note


@pytest.mark.asyncio
async def test_partial_failure_warnings(tmp_path: Path) -> None:
    """Failed-to-start server appears in warnings; result still returned from working server."""
    f = tmp_path / "app.py"
    f.write_text("def foo(): pass\n")

    raw_syms = [
        {
            "name": "foo",
            "kind": 12,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 15},
            },
            "selectionRange": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 7},
            },
            "children": [],
        }
    ]
    location = _make_location_dict(str(f), 0)
    server_ok = _make_server(
        str(tmp_path),
        {"definitionProvider": True, "documentSymbolProvider": True},
        doc_symbols=raw_syms,
        definition_result=[location],
    )

    config = _make_config(("*.py", ["s1", "s2"]))
    manager = MagicMock(spec=ServerManager)

    async def _acquire(spec, file_path):
        if spec.name == "s1":
            return None  # failed
        return _make_entry({}, server_ok)

    manager.acquire = _acquire

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.find_declaration("foo", str(f))
    assert len(result.locations) == 1
    assert any("s1" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Tests: get_symbols_overview — fallthrough on empty first server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_symbols_overview_fallthrough_to_second(tmp_path: Path) -> None:
    """When first server returns empty symbols, second server's result is used."""
    f = tmp_path / "app.py"
    f.touch()

    raw_syms = [
        {
            "name": "Bar",
            "kind": 5,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 3, "character": 0},
            },
            "selectionRange": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 3},
            },
            "children": [],
        }
    ]
    server1 = _make_server(
        str(tmp_path), {"documentSymbolProvider": True}, doc_symbols=[]
    )
    server2 = _make_server(
        str(tmp_path), {"documentSymbolProvider": True}, doc_symbols=raw_syms
    )

    config = _make_config(("*.py", ["s1", "s2"]))
    manager = MagicMock(spec=ServerManager)

    async def _acquire(spec, file_path):
        if spec.name == "s1":
            return _make_entry({}, server1)
        return _make_entry({}, server2)

    manager.acquire = _acquire

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.get_symbols_overview(str(f))
    assert len(result.symbols) == 1
    assert result.symbols[0].name == "Bar"
    # Both servers were queried (empty is not a win)
    server1.request_document_symbols.assert_called_once()
    server2.request_document_symbols.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: replace_symbol_body — symbol not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_symbol_body_symbol_not_found(tmp_path: Path) -> None:
    """replace_symbol_body returns error when symbol is not in the file."""
    f = tmp_path / "app.py"
    f.write_text("def foo(): pass\n")

    server = _make_server(
        str(tmp_path),
        {"documentSymbolProvider": True},
        doc_symbols=[],  # empty — no symbols
    )

    config = _make_config(("*.py", ["s1"]))
    manager = MagicMock(spec=ServerManager)
    manager.acquire = AsyncMock(return_value=_make_entry({}, server))

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.replace_symbol_body(
        "nonexistent", "def nonexistent(): pass", str(f)
    )
    assert not result.success
    assert "nonexistent" in result.note


# ---------------------------------------------------------------------------
# Tests: rename_symbol — no capable server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_symbol_no_capable_server(tmp_path: Path) -> None:
    """rename_symbol returns an explanatory note when no server has renameProvider."""
    f = tmp_path / "app.py"
    f.touch()

    config = _make_config(("*.py", ["s1"]))
    manager = MagicMock(spec=ServerManager)
    server = _make_server(str(tmp_path), {})  # no renameProvider
    manager.acquire = AsyncMock(return_value=_make_entry({}, server))

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.rename_symbol("foo", "bar", str(f))
    assert result.changed_files == []
    assert result.note
    assert "renameProvider" in result.note


# ---------------------------------------------------------------------------
# Tests: find_symbol without file_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_symbol_no_file_path_tries_all_servers(tmp_path: Path) -> None:
    """find_symbol with no file_path iterates all configured servers."""
    config = _make_config(("*.py", ["s1"]))
    manager = MagicMock(spec=ServerManager)

    symbol_result = [
        {
            "name": "MyClass",
            "kind": 5,
            "location": {
                "uri": "file:///app.py",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 10, "character": 0},
                },
            },
        }
    ]
    server = _make_server(str(tmp_path), {"workspaceSymbolProvider": True})
    server.request_workspace_symbol = AsyncMock(return_value=symbol_result)
    manager.acquire = AsyncMock(return_value=_make_entry({}, server))

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.find_symbol("MyClass")  # no file_path
    assert len(result.symbols) == 1
    assert result.symbols[0]["name"] == "MyClass"


@pytest.mark.asyncio
async def test_find_symbol_no_file_path_no_capable_server(tmp_path: Path) -> None:
    """find_symbol with no file_path returns note when no server has workspaceSymbolProvider."""
    config = _make_config(("*.py", ["s1"]))
    manager = MagicMock(spec=ServerManager)
    server = _make_server(str(tmp_path), {})  # no workspaceSymbolProvider
    manager.acquire = AsyncMock(return_value=_make_entry({}, server))

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.find_symbol("anything")
    assert result.symbols == []
    assert result.note


# ---------------------------------------------------------------------------
# Tests: ambiguous symbol returns candidates note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_declaration_ambiguous_symbol_returns_note(tmp_path: Path) -> None:
    """When a bare name matches multiple symbols, a note with candidates is returned."""
    f = tmp_path / "app.py"
    f.write_text("def run(): pass\nclass Foo:\n    def run(self): pass\n")

    # Two symbols named "run"
    raw_syms = [
        {
            "name": "run",
            "kind": 12,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 15},
            },
            "selectionRange": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 7},
            },
            "children": [],
        },
        {
            "name": "Foo",
            "kind": 5,
            "range": {
                "start": {"line": 1, "character": 0},
                "end": {"line": 2, "character": 20},
            },
            "selectionRange": {
                "start": {"line": 1, "character": 6},
                "end": {"line": 1, "character": 9},
            },
            "children": [
                {
                    "name": "run",
                    "kind": 6,
                    "range": {
                        "start": {"line": 2, "character": 4},
                        "end": {"line": 2, "character": 20},
                    },
                    "selectionRange": {
                        "start": {"line": 2, "character": 8},
                        "end": {"line": 2, "character": 11},
                    },
                    "children": [],
                }
            ],
        },
    ]
    server = _make_server(
        str(tmp_path),
        {"definitionProvider": True, "documentSymbolProvider": True},
        doc_symbols=raw_syms,
    )

    config = _make_config(("*.py", ["s1"]))
    manager = MagicMock(spec=ServerManager)
    manager.acquire = AsyncMock(return_value=_make_entry({}, server))

    dispatcher = Dispatcher(config=config, manager=manager)
    result = await dispatcher.find_declaration("run", str(f))
    assert result.locations == []
    assert "Ambiguous" in result.note
    assert "run" in result.note
