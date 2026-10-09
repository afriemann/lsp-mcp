"""Retry, serialisation, timeout and failure-surfacing behaviour (mocked servers)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from multilspy.lsp_protocol_handler.server import Error

from lsp_mcp.dispatch.router import Dispatcher
from lsp_mcp.lsp.manager import ServerManager

from .test_router import _make_config, _make_entry, _make_server

SYM = {
    "name": "foo",
    "kind": 12,
    "range": {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 8}},
    "selectionRange": {
        "start": {"line": 0, "character": 4},
        "end": {"line": 0, "character": 7},
    },
    "children": [],
}


def _ws_hit(path: str, name: str = "foo", line: int = 0) -> dict:
    return {
        "name": name,
        "kind": 12,
        "location": {
            "uri": f"file://{path}",
            "range": {
                "start": {"line": line, "character": 0},
                "end": {"line": line, "character": 3},
            },
        },
    }


def _dispatcher(tmp_path: Path, server: MagicMock, **kw) -> tuple[Dispatcher, Path]:
    f = tmp_path / "app.py"
    f.write_text("def foo():\n    pass\n")
    manager = MagicMock(spec=ServerManager)
    entry = _make_entry({}, server)

    async def _acquire(spec, file_path):
        return entry

    manager.acquire = _acquire
    manager.failure_reason = lambda name, path: None
    config = _make_config(("*.py", ["s1"]))
    kw.setdefault("retry_backoff", 0.0)
    return Dispatcher(config=config, manager=manager, **kw), f


# --- 1. retries -------------------------------------------------------------


@pytest.mark.parametrize("code", [-32801, -32800])
async def test_retry_then_success(tmp_path: Path, code: int) -> None:
    server = _make_server(str(tmp_path), {"workspaceSymbolProvider": True})
    server.request_workspace_symbol = AsyncMock(
        side_effect=[
            Error(code, "content modified"),
            Error(code, "x"),
            [_ws_hit("/a.py")],
        ]
    )
    d, f = _dispatcher(tmp_path, server)
    r = await d.find_symbol("foo", str(f))
    assert [s["name"] for s in r.symbols] == ["foo"]
    assert server.request_workspace_symbol.await_count == 3


async def test_retries_exhausted_is_explicit_error_not_empty_match(
    tmp_path: Path,
) -> None:
    server = _make_server(str(tmp_path), {"workspaceSymbolProvider": True})
    server.request_workspace_symbol = AsyncMock(
        side_effect=Error(-32801, "content modified")
    )
    d, f = _dispatcher(tmp_path, server)
    r = await d.find_symbol("foo", str(f))
    assert r.symbols == []
    assert "failed" in r.note.lower() and "-32801" in r.note and "'s1'" in r.note
    assert "No symbols matching" not in r.note
    assert server.request_workspace_symbol.await_count == 4  # 1 + 3 retries


async def test_non_retryable_error_not_retried(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), {"workspaceSymbolProvider": True})
    server.request_workspace_symbol = AsyncMock(side_effect=Error(-32603, "boom"))
    d, f = _dispatcher(tmp_path, server)
    r = await d.find_symbol("foo", str(f))
    assert server.request_workspace_symbol.await_count == 1
    assert "boom" in r.note


@pytest.mark.parametrize(
    "tool",
    [
        "overview",
        "declaration",
        "implementations",
        "references",
        "diagnostics",
        "hover",
        "calls",
    ],
)
async def test_every_tool_reports_exhausted_retries(tmp_path: Path, tool: str) -> None:
    caps = {
        "documentSymbolProvider": True,
        "definitionProvider": True,
        "implementationProvider": True,
        "referencesProvider": True,
        "diagnosticProvider": True,
        "hoverProvider": True,
        "callHierarchyProvider": True,
    }
    server = _make_server(str(tmp_path), caps, doc_symbols=[SYM])
    err = Error(-32801, "content modified")
    server.request_document_symbols = AsyncMock(side_effect=err)
    server.request_definition = AsyncMock(side_effect=err)
    server.request_references = AsyncMock(side_effect=err)
    server.raw_request = AsyncMock(side_effect=err)
    d, f = _dispatcher(tmp_path, server)
    calls = {
        "overview": lambda: d.get_symbols_overview(str(f)),
        "declaration": lambda: d.find_declaration("foo", str(f)),
        "implementations": lambda: d.find_implementations("foo", str(f)),
        "references": lambda: d.find_referencing_symbols("foo", str(f)),
        "diagnostics": lambda: d.get_diagnostics_for_file(str(f)),
        "hover": lambda: d.get_hover(str(f), 0, 4),
        "calls": lambda: d.get_call_hierarchy(str(f), 0, 4, "incoming"),
    }
    r = await calls[tool]()
    assert "failed" in r.note.lower() and "-32801" in r.note, r.note


async def test_resolve_position_failure_is_not_reported_as_not_found(
    tmp_path: Path,
) -> None:
    server = _make_server(
        str(tmp_path), {"definitionProvider": True, "documentSymbolProvider": True}
    )
    server.request_document_symbols = AsyncMock(side_effect=Error(-32801, "cm"))
    d, f = _dispatcher(tmp_path, server)
    r = await d.find_declaration("foo", str(f))
    assert "failed" in r.note.lower()
    assert "No declaration found" not in r.note


# --- 2. serialisation -------------------------------------------------------


async def test_concurrent_calls_match_sequential(tmp_path: Path) -> None:
    server = _make_server(
        str(tmp_path),
        {"workspaceSymbolProvider": True, "documentSymbolProvider": True},
        doc_symbols=[SYM],
    )
    in_flight = 0

    def _guarded(result):
        async def _inner(*a, **k):
            nonlocal in_flight
            in_flight += 1
            try:
                await asyncio.sleep(0.01)
                if in_flight > 1:  # a real server invalidates overlapping requests
                    raise Error(-32801, "content modified")
                return result
            finally:
                in_flight -= 1

        return _inner

    server.request_workspace_symbol = _guarded([_ws_hit("/a.py")])
    server.request_document_symbols = _guarded(([SYM], None))
    # retries disabled: only serialisation can make this pass
    d, f = _dispatcher(tmp_path, server, retries=0)

    seq_a = await d.get_symbols_overview(str(f))
    seq_b = await d.find_symbol("foo", str(f))
    results = await asyncio.gather(
        *[d.get_symbols_overview(str(f)) for _ in range(5)],
        *[d.find_symbol("foo", str(f)) for _ in range(5)],
    )
    for r in results[:5]:
        assert r == seq_a and r.symbols
    for r in results[5:]:
        assert r == seq_b and r.symbols


# --- 3. timeouts ------------------------------------------------------------


async def test_request_timeout_names_server_and_operation(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), {"documentSymbolProvider": True})

    async def _never(*a, **k):
        await asyncio.Event().wait()

    server.request_document_symbols = _never
    d, f = _dispatcher(tmp_path, server, request_timeout=0.05)
    r = await asyncio.wait_for(d.get_symbols_overview(str(f)), timeout=5)
    assert "'s1'" in r.note and "timed out" in r.note
    assert "documentSymbol" in r.note


async def test_timeout_releases_lock_for_next_call(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), {"workspaceSymbolProvider": True})
    calls = 0

    async def _first_hangs(q):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.Event().wait()
        return [_ws_hit("/a.py")]

    server.request_workspace_symbol = _first_hangs
    d, f = _dispatcher(tmp_path, server, request_timeout=0.05)
    r1 = await d.find_symbol("foo", str(f))
    assert "timed out" in r1.note
    r2 = await d.find_symbol("foo", str(f))
    assert r2.symbols


# --- 4. start failures surface in note -------------------------------------


async def test_start_failure_in_note_with_reason(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    manager = MagicMock(spec=ServerManager)
    manager.acquire = AsyncMock(return_value=None)
    manager.failure_reason = lambda name, path: (
        "Could not find a valid TypeScript installation"
    )
    d = Dispatcher(config=_make_config(("*.py", ["tsls"])), manager=manager)
    for r in (
        await d.get_symbols_overview(str(f)),
        await d.find_symbol("x", str(f)),
        await d.find_declaration("x", str(f)),
        await d.get_diagnostics_for_file(str(f)),
        await d.get_hover(str(f), 0, 0),
    ):
        assert "failed to start" in r.note and "tsls" in r.note
        assert "TypeScript installation" in r.note


async def test_partial_start_failure_noted_with_results(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    f.write_text("def foo():\n    pass\n")
    good = _make_server(
        str(tmp_path), {"documentSymbolProvider": True}, doc_symbols=[SYM]
    )
    manager = MagicMock(spec=ServerManager)

    async def _acq(spec, path):
        return None if spec.name == "bad" else _make_entry({}, good)

    manager.acquire = _acq
    manager.failure_reason = lambda n, p: "no binary"
    d = Dispatcher(config=_make_config(("*.py", ["bad", "s1"])), manager=manager)
    r = await d.get_symbols_overview(str(f))
    assert r.symbols and "bad" in r.note and "no binary" in r.note
