"""find_symbol ranking, kind filter, limit, line_1based, context_lines."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from .test_resilience import SYM, _dispatcher, _ws_hit
from .test_router import _make_location_dict, _make_server


def _server(tmp_path: Path, hits: list[dict]):
    s = _make_server(str(tmp_path), {"workspaceSymbolProvider": True})
    s.request_workspace_symbol = AsyncMock(return_value=hits)
    return s


async def test_ranking_exact_prefix_substring(tmp_path: Path) -> None:
    hits = [
        _ws_hit("/a.py", "my_foo_bar"),  # substring
        _ws_hit("/a.py", "foo_bar"),  # prefix
        _ws_hit("/a.py", "Foo"),  # case-insensitive exact
        _ws_hit("/a.py", "foo"),  # exact
        _ws_hit("/a.py", "other"),  # no match: last
    ]
    d, f = _dispatcher(tmp_path, _server(tmp_path, hits))
    r = await d.find_symbol("foo", str(f))
    assert [s["name"] for s in r.symbols] == [
        "foo",
        "Foo",
        "foo_bar",
        "my_foo_bar",
        "other",
    ]


async def test_kind_filter_by_name_and_number(tmp_path: Path) -> None:
    hits = [_ws_hit("/a.py", "foo"), {**_ws_hit("/a.py", "foo"), "kind": 5}]
    d, f = _dispatcher(tmp_path, _server(tmp_path, hits))
    assert [
        s["kind"] for s in (await d.find_symbol("foo", str(f), kind="class")).symbols
    ] == [5]
    assert [
        s["kind"] for s in (await d.find_symbol("foo", str(f), kind=12)).symbols
    ] == [12]
    assert [
        s["kind"] for s in (await d.find_symbol("foo", str(f), kind="12")).symbols
    ] == [12]
    bad = await d.find_symbol("foo", str(f), kind="nonsense")
    assert bad.symbols == [] and "kind" in bad.note.lower()


async def test_limit_truncates_and_reports(tmp_path: Path) -> None:
    hits = [_ws_hit("/a.py", f"foo{i}", i) for i in range(10)]
    d, f = _dispatcher(tmp_path, _server(tmp_path, hits))
    r = await d.find_symbol("foo", str(f), limit=3)
    assert len(r.symbols) == 3 and r.truncated and r.total_matches == 10
    full = await d.find_symbol("foo", str(f))
    assert len(full.symbols) == 10 and not full.truncated
    clamped = await d.find_symbol("foo", str(f), limit=10_000)
    assert len(clamped.symbols) == 10


async def test_symbol_has_line_1based(tmp_path: Path) -> None:
    d, f = _dispatcher(tmp_path, _server(tmp_path, [_ws_hit("/a.py", "foo", 4)]))
    s = (await d.find_symbol("foo", str(f))).symbols[0]
    assert s["line"] == 4 and s["line_1based"] == 5


async def test_location_line_1based_and_context(tmp_path: Path) -> None:
    server = _make_server(
        str(tmp_path),
        {"definitionProvider": True, "documentSymbolProvider": True},
        doc_symbols=[SYM],
    )
    target = tmp_path / "t.py"
    target.write_text("l0\nl1\nl2\nl3\nl4\n")
    server.request_definition = AsyncMock(
        return_value=[_make_location_dict(str(target), 2)]
    )
    d, f = _dispatcher(tmp_path, server)
    r = await d.find_declaration("foo", str(f))
    assert r.locations[0].line == 2 and r.locations[0].line_1based == 3
    assert r.locations[0].context is None
    r = await d.find_declaration("foo", str(f), context_lines=1)
    assert r.locations[0].context == "l1\nl2\nl3"
    r = await d.find_declaration("foo", str(f), context_lines=0)
    assert r.locations[0].context is None


async def test_find_symbol_opens_routing_file_during_query(tmp_path: Path) -> None:
    from contextlib import contextmanager

    events: list[str] = []
    s = _server(tmp_path, [_ws_hit("/a.py", "foo")])

    @contextmanager
    def _open(rel):
        events.append("open")
        yield
        events.append("close")

    s.open_file = _open

    async def _ws(q):
        events.append("query")
        return [_ws_hit("/a.py", "foo")]

    s.request_workspace_symbol = _ws
    d, f = _dispatcher(tmp_path, s)
    await d.find_symbol("foo", str(f))
    assert events == ["open", "query", "close"]
