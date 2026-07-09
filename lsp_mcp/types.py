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
