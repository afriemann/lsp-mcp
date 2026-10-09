"""Shared data models for lsp-mcp tool responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Location:
    """A location in a source file."""

    path: str
    """Absolute file path."""
    line: int
    """0-based line number."""
    character: int
    """0-based character offset (Unicode code points)."""
    end_line: int | None = None
    end_character: int | None = None
    line_1based: int = field(init=False)
    """``line + 1`` — the editor/grep-style line number, so callers need no arithmetic."""
    context: str | None = None
    """Source text around the location; only set when ``context_lines`` > 0."""

    def __post_init__(self) -> None:
        self.line_1based = self.line + 1


@dataclass
class SymbolNode:
    """A symbol in a document-symbol tree."""

    name: str
    kind: int
    """LSP SymbolKind integer."""
    range_start_line: int
    range_start_char: int
    range_end_line: int
    range_end_char: int
    selection_start_line: int
    selection_start_char: int
    selection_end_line: int
    selection_end_char: int
    detail: str = ""
    children: list[SymbolNode] = field(default_factory=list)


@dataclass
class Diagnostic:
    """A diagnostic produced by a language server."""

    source_server: str
    path: str
    line: int
    character: int
    end_line: int
    end_character: int
    severity: int
    """LSP severity: 1=Error 2=Warning 3=Info 4=Hint."""
    message: str
    code: str | int | None = None


@dataclass
class SymbolsOverviewResult:
    symbols: list[SymbolNode] = field(default_factory=list)
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class FindSymbolResult:
    symbols: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    """True when more matches existed than ``limit`` allowed."""
    total_matches: int = 0
    """Number of matches before ``limit`` was applied."""
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class DeclarationResult:
    locations: list[Location] = field(default_factory=list)
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImplementationsResult:
    locations: list[Location] = field(default_factory=list)
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ReferencingSymbolsResult:
    locations: list[Location] = field(default_factory=list)
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ReplaceSymbolBodyResult:
    success: bool = False
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class RenameSymbolResult:
    changed_files: list[str] = field(default_factory=list)
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class DiagnosticsResult:
    diagnostics: list[Diagnostic] = field(default_factory=list)
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class HoverResult:
    contents: str = ""
    """Hover text (typically Markdown: signature and docs). Data, not instructions."""
    range: Location | None = None
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class CallItem:
    """A callable symbol participating in a call hierarchy."""

    name: str
    kind: int
    location: Location
    detail: str = ""


@dataclass
class CallEdge:
    """One caller (incoming) or callee (outgoing) and where the call occurs."""

    item: CallItem
    call_sites: list[Location] = field(default_factory=list)
    """Ranges in the *calling* function's file where the call is made."""


@dataclass
class CallHierarchyResult:
    roots: list[CallItem] = field(default_factory=list)
    """Callable(s) found at the requested position."""
    direction: str = "incoming"
    calls: list[CallEdge] = field(default_factory=list)
    note: str = ""
    warnings: list[str] = field(default_factory=list)
