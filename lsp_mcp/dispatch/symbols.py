"""Symbol name-path resolution against a document-symbol tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SymbolRange:
    """Extracted range information for a symbol."""

    start_line: int
    start_char: int
    end_line: int
    end_char: int


@dataclass
class ResolvedSymbol:
    """A symbol node located within a document-symbol tree."""

    name: str
    kind: int
    full_range: SymbolRange
    """Full range covering the entire symbol body."""
    selection_range: SymbolRange
    """Range covering just the identifier (name)."""
    detail: str = ""
    children: list["ResolvedSymbol"] = field(default_factory=list)


def _parse_range(r: dict[str, Any]) -> SymbolRange:
    start = r["start"]
    end = r["end"]
    return SymbolRange(
        start_line=start["line"],
        start_char=start["character"],
        end_line=end["line"],
        end_char=end["character"],
    )


def _build_tree(raw_symbols: list[Any]) -> list[ResolvedSymbol]:
    """
    Build a list of ``ResolvedSymbol`` trees from multilspy's flat symbol list.

    multilspy's ``request_document_symbols`` returns a flat list where child
    symbols follow their parent (the hierarchy information is embedded in the
    ``range`` nesting).  When the raw items include a ``children`` key, the
    response was already hierarchical (DocumentSymbol); otherwise we treat the
    flat list as top-level (SymbolInformation).
    """
    result: list[ResolvedSymbol] = []
    for item in raw_symbols:
        if not isinstance(item, dict):
            continue
        full_range_raw = item.get("range") or item.get("location", {}).get("range")
        sel_range_raw = item.get("selectionRange") or full_range_raw
        if not full_range_raw:
            continue
        children_raw = item.get("children", [])
        sym = ResolvedSymbol(
            name=item.get("name", ""),
            kind=item.get("kind", 0),
            full_range=_parse_range(full_range_raw),
            selection_range=_parse_range(sel_range_raw),
            detail=item.get("detail", ""),
            children=_build_tree(children_raw),
        )
        result.append(sym)
    return result


def resolve_symbol(
    name_path: str,
    raw_symbols: list[Any],
) -> list[ResolvedSymbol]:
    """
    Locate a symbol by *name_path* in a document-symbol tree.

    *name_path* is a ``/``-separated path of symbol names, e.g.
    ``MyClass/my_method``.  A bare name (no ``/``) matches any symbol with
    that name at any depth.

    Returns a list of matches:
    - Single element list → unambiguous match.
    - Multiple elements → ambiguous (caller should surface candidates).
    - Empty list → not found.
    """
    segments = [s.strip() for s in name_path.split("/") if s.strip()]
    if not segments:
        return []

    tree = _build_tree(raw_symbols)
    return _find_in_tree(segments, tree)


def _find_in_tree(
    segments: list[str], nodes: list[ResolvedSymbol]
) -> list[ResolvedSymbol]:
    """Recursively search *nodes* for the symbol path described by *segments*."""
    if not segments:
        return []

    head, *tail = segments
    candidates: list[ResolvedSymbol] = []

    for node in nodes:
        if node.name != head:
            # For a bare single-segment search, collect any depth match
            if len(segments) == 1:
                candidates.extend(_find_in_tree(segments, node.children))
            continue

        if not tail:
            candidates.append(node)
        else:
            candidates.extend(_find_in_tree(tail, node.children))

    return candidates
