"""Tests for symbol name-path resolution."""

from __future__ import annotations

from lsp_mcp.dispatch.symbols import ResolvedSymbol, SymbolRange, resolve_symbol


def _sym(
    name: str,
    start_line: int = 0,
    end_line: int = 10,
    children: list | None = None,
) -> dict:
    """Build a minimal raw document-symbol dict."""
    return {
        "name": name,
        "kind": 12,  # Function
        "range": {
            "start": {"line": start_line, "character": 0},
            "end": {"line": end_line, "character": 0},
        },
        "selectionRange": {
            "start": {"line": start_line, "character": 4},
            "end": {"line": start_line, "character": 4 + len(name)},
        },
        "children": children or [],
    }


# ---------------------------------------------------------------------------
# Basic lookup
# ---------------------------------------------------------------------------


def test_find_top_level_symbol() -> None:
    symbols = [_sym("my_func"), _sym("other_func")]
    result = resolve_symbol("my_func", symbols)
    assert len(result) == 1
    assert result[0].name == "my_func"


def test_find_nested_symbol_path() -> None:
    symbols = [
        _sym("MyClass", children=[_sym("my_method")]),
    ]
    result = resolve_symbol("MyClass/my_method", symbols)
    assert len(result) == 1
    assert result[0].name == "my_method"


def test_find_deeply_nested() -> None:
    symbols = [
        _sym(
            "Outer",
            children=[
                _sym("Inner", children=[_sym("deep_fn")]),
            ],
        )
    ]
    result = resolve_symbol("Outer/Inner/deep_fn", symbols)
    assert len(result) == 1
    assert result[0].name == "deep_fn"


def test_not_found_returns_empty() -> None:
    symbols = [_sym("my_func")]
    result = resolve_symbol("nonexistent", symbols)
    assert result == []


def test_path_not_found_returns_empty() -> None:
    symbols = [_sym("MyClass", children=[_sym("method_a")])]
    result = resolve_symbol("MyClass/method_b", symbols)
    assert result == []


# ---------------------------------------------------------------------------
# Ambiguous bare names
# ---------------------------------------------------------------------------


def test_ambiguous_bare_name_returns_multiple() -> None:
    """A bare name matching multiple nodes returns all candidates."""
    symbols = [
        _sym("MyClass", children=[_sym("helper")]),
        _sym("OtherClass", children=[_sym("helper")]),
    ]
    result = resolve_symbol("helper", symbols)
    assert len(result) == 2


def test_unambiguous_top_level() -> None:
    symbols = [_sym("func_a"), _sym("func_b")]
    result = resolve_symbol("func_a", symbols)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Range information
# ---------------------------------------------------------------------------


def test_selection_range_captured() -> None:
    symbols = [_sym("my_func", start_line=5, end_line=10)]
    result = resolve_symbol("my_func", symbols)
    sym = result[0]
    assert sym.selection_range.start_line == 5
    assert sym.selection_range.start_char == 4  # as set in _sym helper
    assert sym.full_range.end_line == 10
