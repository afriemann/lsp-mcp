"""get_hover and get_call_hierarchy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from .test_resilience import _dispatcher
from .test_router import _make_server


def _rng(l: int, c0: int = 0, c1: int = 3) -> dict:
    return {"start": {"line": l, "character": c0}, "end": {"line": l, "character": c1}}


async def test_hover_markup_content(tmp_path: Path) -> None:
    s = _make_server(str(tmp_path), {"hoverProvider": True})
    s.raw_request = AsyncMock(
        return_value={
            "contents": {"kind": "markdown", "value": "def foo() -> int"},
            "range": _rng(0),
        }
    )
    d, f = _dispatcher(tmp_path, s)
    r = await d.get_hover(str(f), 0, 4)
    assert r.contents == "def foo() -> int" and r.range.line_1based == 1 and not r.note
    method, params = s.raw_request.await_args.args
    assert method == "textDocument/hover" and params["position"] == {
        "line": 0,
        "character": 4,
    }


async def test_hover_legacy_contents_forms(tmp_path: Path) -> None:
    s = _make_server(str(tmp_path), {"hoverProvider": True})
    s.raw_request = AsyncMock(
        return_value={"contents": ["plain", {"language": "python", "value": "x: int"}]}
    )
    d, f = _dispatcher(tmp_path, s)
    r = await d.get_hover(str(f), 0, 0)
    assert "plain" in r.contents and "```python\nx: int\n```" in r.contents


async def test_hover_nothing_and_no_capability(tmp_path: Path) -> None:
    s = _make_server(str(tmp_path), {"hoverProvider": True})
    d, f = _dispatcher(tmp_path, s)  # raw_request -> None
    assert "No hover" in (await d.get_hover(str(f), 0, 0)).note
    s2 = _make_server(str(tmp_path), {})
    d2, f2 = _dispatcher(tmp_path, s2)
    assert "hoverProvider" in (await d2.get_hover(str(f2), 0, 0)).note
    assert "line" in (await d.get_hover(str(f), -1, 0)).note.lower()


def _item(name: str, line: int) -> dict:
    return {
        "name": name,
        "kind": 12,
        "uri": "file:///x/m.py",
        "range": _rng(line),
        "selectionRange": _rng(line, 4, 7),
    }


async def test_call_hierarchy_incoming_and_outgoing(tmp_path: Path) -> None:
    s = _make_server(str(tmp_path), {"callHierarchyProvider": True})

    async def raw(method, params=None):
        if method == "textDocument/prepareCallHierarchy":
            return [_item("foo", 0)]
        if method == "callHierarchy/incomingCalls":
            return [{"from": _item("caller", 9), "fromRanges": [_rng(10)]}]
        if method == "callHierarchy/outgoingCalls":
            return [{"to": _item("callee", 20), "fromRanges": [_rng(1), _rng(2)]}]
        raise AssertionError(method)

    s.raw_request = raw
    d, f = _dispatcher(tmp_path, s)
    r = await d.get_call_hierarchy(str(f), 0, 4, "incoming")
    assert r.roots[0].name == "foo"
    assert (
        r.calls[0].item.name == "caller" and r.calls[0].item.location.line_1based == 10
    )
    assert r.calls[0].call_sites[0].line == 10
    r = await d.get_call_hierarchy(str(f), 0, 4, "outgoing")
    assert r.calls[0].item.name == "callee" and len(r.calls[0].call_sites) == 2
    bad = await d.get_call_hierarchy(str(f), 0, 4, "sideways")
    assert "direction" in bad.note.lower()


async def test_call_hierarchy_no_item_and_no_capability(tmp_path: Path) -> None:
    s = _make_server(str(tmp_path), {"callHierarchyProvider": True})
    d, f = _dispatcher(tmp_path, s)
    assert "No callable" in (await d.get_call_hierarchy(str(f), 0, 0)).note
    s2 = _make_server(str(tmp_path), {})
    d2, f2 = _dispatcher(tmp_path, s2)
    assert "callHierarchyProvider" in (await d2.get_call_hierarchy(str(f2), 0, 0)).note
