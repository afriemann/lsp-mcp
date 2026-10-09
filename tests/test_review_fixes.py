"""Tests for code-review hardening: deadlines, locking, context safety, hints."""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from multilspy.lsp_protocol_handler.server import Error

from lsp_mcp.dispatch.router import Dispatcher
from lsp_mcp.lsp.manager import ServerManager

from .test_resilience import SYM, _dispatcher, _ws_hit
from .test_router import _make_config, _make_entry, _make_location_dict, _make_server

CAPS = {
    "documentSymbolProvider": True,
    "definitionProvider": True,
    "implementationProvider": True,
    "referencesProvider": True,
    "workspaceSymbolProvider": True,
    "hoverProvider": True,
    "callHierarchyProvider": True,
    "renameProvider": True,
}


# --- 2. per-call deadline ----------------------------------------------------


async def test_deadline_covers_retries(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), CAPS)

    async def _slow_err(q):
        await asyncio.sleep(0.05)
        raise Error(-32801, "cm")

    server.request_workspace_symbol = _slow_err
    d, f = _dispatcher(tmp_path, server, call_deadline=0.12, retries=50)
    r = await asyncio.wait_for(d.find_symbol("foo", str(f)), 5)
    assert "deadline" in r.note and "find_symbol" in r.note
    assert "No symbols matching" not in r.note


async def test_deadline_covers_lock_wait_and_lock_survives(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), CAPS)
    gate = asyncio.Event()

    async def _ws(q):
        await gate.wait()
        return [_ws_hit("/a.py")]

    server.request_workspace_symbol = _ws
    d, f = _dispatcher(tmp_path, server, call_deadline=0.1, request_timeout=5)
    first = asyncio.create_task(d.find_symbol("foo", str(f)))
    await asyncio.sleep(0.01)
    second = await d.find_symbol("foo", str(f))  # waits for the lock, then expires
    assert "deadline" in second.note
    assert "deadline" in (await first).note
    gate.set()
    assert (await d.find_symbol("foo", str(f))).symbols  # lock was released


async def test_deadline_excludes_server_start_time(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])
    manager = MagicMock(spec=ServerManager)
    entry = _make_entry({}, server)

    async def _slow_acquire(spec, path):
        await asyncio.sleep(0.2)
        return entry

    manager.acquire = _slow_acquire
    f = tmp_path / "app.py"
    f.write_text("x")
    d = Dispatcher(_make_config(("*.py", ["s1"])), manager, call_deadline=0.1)
    r = await d.get_symbols_overview(str(f))
    assert r.symbols and not r.note


async def test_outer_cancellation_releases_lock(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), CAPS)
    started = asyncio.Event()

    async def _hang(q):
        started.set()
        await asyncio.Event().wait()

    server.request_workspace_symbol = _hang
    d, f = _dispatcher(tmp_path, server, request_timeout=30, call_deadline=30)
    t = asyncio.create_task(d.find_symbol("foo", str(f)))
    await started.wait()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    server.request_workspace_symbol = AsyncMock(return_value=[_ws_hit("/a.py")])
    r = await asyncio.wait_for(d.find_symbol("foo", str(f)), 2)
    assert r.symbols


# --- 3. one document sync per call -----------------------------------------


@pytest.mark.parametrize(
    "which", ["declaration", "implementations", "references", "overview"]
)
async def test_one_open_per_call_and_requests_happen_inside(
    tmp_path: Path, which: str
) -> None:
    events: list[str] = []
    server = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])

    @contextmanager
    def _open(rel):
        events.append("open")
        yield
        events.append("close")

    server.open_file = _open
    loc = _make_location_dict(str(tmp_path / "app.py"), 0)

    async def _syms(rel):
        events.append("req")
        return ([SYM], None)

    async def _defn(rel, l, c):
        events.append("req")
        return [loc]

    async def _raw(method, params=None):
        events.append("req")
        return [{"uri": "file:///x.py", "range": loc["range"]}]

    server.request_document_symbols = _syms
    server.request_definition = _defn
    server.request_references = _defn
    server.raw_request = _raw
    d, f = _dispatcher(tmp_path, server)
    call = {
        "declaration": lambda: d.find_declaration("foo", str(f)),
        "implementations": lambda: d.find_implementations("foo", str(f)),
        "references": lambda: d.find_referencing_symbols("foo", str(f)),
        "overview": lambda: d.get_symbols_overview(str(f)),
    }[which]
    await call()
    assert events.count("open") == 1 and events.count("close") == 1
    assert events[0] == "open" and events[-1] == "close"
    assert "req" in events


# --- 4. multi-server locking for edit tools -------------------------------


async def test_replace_holds_all_involved_server_locks(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    f.write_text("def foo():\n    pass\n")
    gate = asyncio.Event()
    s1 = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])
    s2 = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])

    async def _syms(rel):
        await gate.wait()
        return ([SYM], None)

    s1.request_document_symbols = _syms
    e1, e2 = _make_entry({}, s1), _make_entry({}, s2)
    manager = MagicMock(spec=ServerManager)

    async def _acq(spec, path):
        return e1 if spec.name == "s1" else e2

    manager.acquire = _acq
    manager.failure_reason = lambda n, p: None
    d = Dispatcher(_make_config(("*.py", ["s1", "s2"])), manager)
    task = asyncio.create_task(
        d.replace_symbol_body("foo", "def foo():\n    return 1", str(f))
    )
    await asyncio.sleep(0.02)
    assert e1.lock.locked() and e2.lock.locked()
    gate.set()
    r = await task
    assert r.success
    assert not e1.lock.locked() and not e2.lock.locked()


async def test_rename_holds_all_involved_server_locks(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    f.write_text("def foo():\n    pass\n")
    s1 = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])
    s2 = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])
    seen: list[bool] = []
    e1, e2 = _make_entry({}, s1), _make_entry({}, s2)

    async def _raw(method, params=None):
        seen.append(e1.lock.locked() and e2.lock.locked())
        return None

    s1.raw_request = _raw
    manager = MagicMock(spec=ServerManager)

    async def _acq(spec, path):
        return e1 if spec.name == "s1" else e2

    manager.acquire = _acq
    manager.failure_reason = lambda n, p: None
    d = Dispatcher(_make_config(("*.py", ["s1", "s2"])), manager)
    await d.rename_symbol("foo", "bar", str(f))
    assert seen == [True]


# --- 5. context_lines confinement ------------------------------------------


async def _decl_with_target(tmp_path: Path, target: Path, **kw):
    server = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])
    server.request_definition = AsyncMock(
        return_value=[_make_location_dict(str(target), 1)]
    )
    d, f = _dispatcher(tmp_path, server)
    return await d.find_declaration("foo", str(f), context_lines=1)


async def test_context_skips_paths_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").touch()
    outside = tmp_path / "secret.txt"
    outside.write_text("a\nTOPSECRET\nc\n")
    server = _make_server(str(root), CAPS, doc_symbols=[SYM])
    server.request_definition = AsyncMock(
        return_value=[_make_location_dict(str(outside), 1)]
    )
    d, f = _dispatcher(root, server)
    r = await d.find_declaration("foo", str(f), context_lines=1)
    assert r.locations[0].context is None


async def test_context_skips_symlink_escaping_root(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").touch()
    outside = tmp_path / "secret.txt"
    outside.write_text("a\nTOPSECRET\nc\n")
    link = root / "link.txt"
    os.symlink(outside, link)
    server = _make_server(str(root), CAPS, doc_symbols=[SYM])
    server.request_definition = AsyncMock(
        return_value=[_make_location_dict(str(link), 1)]
    )
    d, f = _dispatcher(root, server)
    r = await d.find_declaration("foo", str(f), context_lines=1)
    assert r.locations[0].context is None


async def test_context_inside_root_still_works_and_caps_bytes(tmp_path: Path) -> None:
    from lsp_mcp.dispatch import router

    t = tmp_path / "t.py"
    t.write_text("l0\nl1\nl2\n" + "x" * 100 + "\nlate\n")
    r = await _decl_with_target(tmp_path, t)
    assert r.locations[0].context == "l0\nl1\nl2"
    old = router.MAX_CONTEXT_BYTES
    router.MAX_CONTEXT_BYTES = 12
    try:
        late = tmp_path / "late.py"
        late.write_text("l0\nl1\nl2\n" + "x" * 100 + "\nlate\n")
        server = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])
        server.request_definition = AsyncMock(
            return_value=[_make_location_dict(str(late), 4)]
        )
        d, f = _dispatcher(tmp_path, server)
        r = await d.find_declaration("foo", str(f), context_lines=1)
        assert r.locations[0].context is None
    finally:
        router.MAX_CONTEXT_BYTES = old


# --- 7. residual empty-success paths -----------------------------------------


async def test_empty_document_symbols_hint_while_resolving(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), CAPS, doc_symbols=[])
    d, f = _dispatcher(tmp_path, server)
    r = await d.find_declaration("foo", str(f))
    assert "No declaration found" in r.note and "indexing" in r.note


async def test_empty_overview_and_workspace_symbol_hint(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), CAPS, doc_symbols=[])
    d, f = _dispatcher(tmp_path, server)
    assert "indexing" in (await d.get_symbols_overview(str(f))).note
    assert "indexing" in (await d.find_symbol("foo", str(f))).note


async def test_unpublished_push_diagnostics_is_unknown_not_clean(
    tmp_path: Path,
) -> None:
    server = _make_server(str(tmp_path), {})  # no pull capability -> push path
    server.push_diagnostics = {}
    d, f = _dispatcher(tmp_path, server)
    r = await d.get_diagnostics_for_file(str(f))
    assert r.diagnostics == []
    assert "published" in r.note and "not clean" in r.note


async def test_published_empty_diagnostics_is_clean(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    server = _make_server(str(tmp_path), {})
    server.push_diagnostics = {f.resolve().as_uri(): []}
    d, f = _dispatcher(tmp_path, server)
    r = await d.get_diagnostics_for_file(str(f))
    assert r.note == ""


async def test_inner_timeout_error_not_relabelled_as_ours(tmp_path: Path) -> None:
    server = _make_server(str(tmp_path), CAPS)
    server.request_workspace_symbol = AsyncMock(
        side_effect=asyncio.TimeoutError("inner")
    )
    d, f = _dispatcher(tmp_path, server)
    r = await d.find_symbol("foo", str(f))
    assert "failed" in r.note and "timed out after" not in r.note


# --- 9. call hierarchy partial failure / multiple items --------------------


def _rng(l: int) -> dict:
    return {"start": {"line": l, "character": 0}, "end": {"line": l, "character": 3}}


def _item(name: str, line: int) -> dict:
    return {
        "name": name,
        "kind": 12,
        "uri": "file:///x/m.py",
        "range": _rng(line),
        "selectionRange": _rng(line),
    }


async def test_call_hierarchy_multiple_items_aggregated(tmp_path: Path) -> None:
    s = _make_server(str(tmp_path), CAPS)

    async def raw(method, params=None):
        if method == "textDocument/prepareCallHierarchy":
            return [_item("a", 0), _item("b", 5)]
        n = params["item"]["name"]
        return [{"from": _item(f"caller_of_{n}", 9), "fromRanges": [_rng(10)]}]

    s.raw_request = raw
    d, f = _dispatcher(tmp_path, s)
    r = await d.get_call_hierarchy(str(f), 0, 0)
    assert [x.name for x in r.roots] == ["a", "b"]
    assert [c.item.name for c in r.calls] == ["caller_of_a", "caller_of_b"]
    assert not r.note


async def test_call_hierarchy_partial_failure(tmp_path: Path) -> None:
    s = _make_server(str(tmp_path), CAPS)

    async def raw(method, params=None):
        if method == "textDocument/prepareCallHierarchy":
            return [_item("a", 0), _item("b", 5)]
        if params["item"]["name"] == "b":
            raise Error(-32801, "cm")
        return [{"from": _item("caller_of_a", 9), "fromRanges": [_rng(10)]}]

    s.raw_request = raw
    d, f = _dispatcher(tmp_path, s)
    r = await d.get_call_hierarchy(str(f), 0, 0)
    assert [c.item.name for c in r.calls] == ["caller_of_a"]
    assert r.note.startswith("Partial result") and "-32801" in r.note


async def test_call_hierarchy_all_items_fail_is_failure(tmp_path: Path) -> None:
    s = _make_server(str(tmp_path), CAPS)

    async def raw(method, params=None):
        if method == "textDocument/prepareCallHierarchy":
            return [_item("a", 0)]
        raise Error(-32801, "cm")

    s.raw_request = raw
    d, f = _dispatcher(tmp_path, s)
    r = await d.get_call_hierarchy(str(f), 0, 0)
    assert r.calls == [] and r.note.startswith("Request failed")


# --- round 2 ---------------------------------------------------------------


def _two_server_dispatcher(tmp_path: Path, s1, s2, **kw):
    f = tmp_path / "app.py"
    f.write_text("def foo():\n    pass\n")
    e1, e2 = _make_entry({}, s1), _make_entry({}, s2)
    manager = MagicMock(spec=ServerManager)

    async def _acq(spec, path):
        return e1 if spec.name == "s1" else e2

    manager.acquire = _acq
    manager.failure_reason = lambda n, p: None
    order = kw.pop("order", ["s1", "s2"])
    return Dispatcher(_make_config(("*.py", order)), manager, **kw), f, e1, e2


async def test_partial_diagnostics_flagged_when_other_push_server_silent(
    tmp_path: Path,
) -> None:
    f = tmp_path / "app.py"
    uri = f.resolve().as_uri()
    diag = {
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 1},
        },
        "message": "boom",
        "severity": 1,
    }
    s1 = _make_server(str(tmp_path), {})
    s1.push_diagnostics = {uri: [diag]}
    s2 = _make_server(str(tmp_path), {})
    s2.push_diagnostics = {}  # never publishes
    d, f, *_ = _two_server_dispatcher(tmp_path, s1, s2)
    r = await d.get_diagnostics_for_file(str(f))
    assert len(r.diagnostics) == 1
    assert r.note.startswith("Partial result") and "'s2'" in r.note
    assert "not clean" in r.note


async def test_deadline_fires_at_original_budget_after_slow_acquires(
    tmp_path: Path,
) -> None:
    server = _make_server(str(tmp_path), CAPS)

    async def _hang(q):
        await asyncio.Event().wait()

    server.request_workspace_symbol = _hang
    manager = MagicMock(spec=ServerManager)
    entry = _make_entry({}, server)

    async def _slow_acquire(spec, path):
        await asyncio.sleep(0.15)
        return entry

    manager.acquire = _slow_acquire
    manager.failure_reason = lambda n, p: None
    f = tmp_path / "app.py"
    f.write_text("x")
    # two servers => two slow acquires (0.3 s) that must not count
    d = Dispatcher(
        _make_config(("*.py", ["s1", "s2"])),
        manager,
        call_deadline=0.2,
        request_timeout=5,
    )
    t0 = asyncio.get_running_loop().time()
    r = await d.find_symbol("foo", str(f))
    elapsed = asyncio.get_running_loop().time() - t0
    assert "deadline" in r.note
    # 0.3 s acquiring + ~0.2 s budget; not shortened (>=0.45) nor extended (<1.0)
    assert 0.45 <= elapsed < 1.0, elapsed


async def test_opposite_server_order_edits_do_not_deadlock(tmp_path: Path) -> None:
    s1 = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])
    s2 = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])

    async def _slow(*a, **k):
        await asyncio.sleep(0.02)
        return ([SYM], None)

    s1.request_document_symbols = _slow
    s2.request_document_symbols = _slow
    d1, f, e1, e2 = _two_server_dispatcher(tmp_path, s1, s2, order=["s1", "s2"])
    # second dispatcher shares the very same entries but lists servers in the
    # opposite order, so unsorted acquisition would deadlock (AB / BA).
    manager = d1._manager
    d2 = Dispatcher(_make_config(("*.py", ["s2", "s1"])), manager)
    for _ in range(10):
        await asyncio.wait_for(
            asyncio.gather(
                d1.replace_symbol_body("foo", "def foo():\n    return 1", str(f)),
                d2.rename_symbol("foo", "bar", str(f)),
                d2.replace_symbol_body("foo", "def foo():\n    return 1", str(f)),
                d1.rename_symbol("foo", "bar", str(f)),
            ),
            timeout=3,
        )


async def test_deadline_on_edit_tool_releases_locks_and_applies_no_edit(
    tmp_path: Path,
) -> None:
    s1 = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])
    s2 = _make_server(str(tmp_path), CAPS, doc_symbols=[SYM])

    async def _hang(rel):
        await asyncio.Event().wait()

    s1.request_document_symbols = _hang
    d, f, e1, e2 = _two_server_dispatcher(
        tmp_path, s1, s2, call_deadline=0.1, request_timeout=5
    )
    before = f.read_text()
    r = await d.replace_symbol_body("foo", "def foo():\n    return 1", str(f))
    assert not r.success and "deadline" in r.note and "replace_symbol_body" in r.note
    assert f.read_text() == before
    assert not e1.lock.locked() and not e2.lock.locked()
    r = await d.rename_symbol("foo", "bar", str(f))
    assert "deadline" in r.note and "rename_symbol" in r.note
    assert not e1.lock.locked() and not e2.lock.locked()
