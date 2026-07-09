"""Capability discovery and routing predicates."""

from __future__ import annotations

from enum import Enum, auto
from typing import Any


class ToolKind(Enum):
    GET_SYMBOLS_OVERVIEW = auto()
    FIND_SYMBOL = auto()
    FIND_DECLARATION = auto()
    FIND_IMPLEMENTATIONS = auto()
    FIND_REFERENCING_SYMBOLS = auto()
    REPLACE_SYMBOL_BODY = auto()
    RENAME_SYMBOL = auto()
    GET_DIAGNOSTICS_FOR_FILE_PULL = auto()
    """Server supports pull diagnostics (textDocument/diagnostic, LSP 3.17+)."""
    GET_DIAGNOSTICS_FOR_FILE_PUSH = auto()
    """Server will push publishDiagnostics; no capability flag needed."""


class CapabilitySet:
    """
    Wraps the ``capabilities`` dict from an ``InitializeResult`` and answers
    routing predicates used by the dispatch layer.

    All servers are assumed to support push diagnostics (``publishDiagnostics``
    notifications) regardless of capabilities, as the push path has no
    advertisement flag.  Pull diagnostics require an explicit
    ``diagnosticProvider`` in the capability dict.
    """

    def __init__(self, capabilities: dict[str, Any]) -> None:
        self._caps = capabilities

    def supports(self, tool: ToolKind) -> bool:
        """Return True if this server can handle *tool*."""
        c = self._caps
        match tool:
            case ToolKind.GET_SYMBOLS_OVERVIEW | ToolKind.REPLACE_SYMBOL_BODY:
                return bool(c.get("documentSymbolProvider"))
            case ToolKind.FIND_SYMBOL:
                return bool(c.get("workspaceSymbolProvider"))
            case ToolKind.FIND_DECLARATION:
                return bool(c.get("definitionProvider"))
            case ToolKind.FIND_IMPLEMENTATIONS:
                return bool(c.get("implementationProvider"))
            case ToolKind.FIND_REFERENCING_SYMBOLS:
                return bool(c.get("referencesProvider"))
            case ToolKind.RENAME_SYMBOL:
                rp = c.get("renameProvider")
                if rp is None or rp is False:
                    return False
                # renameProvider may be True or {prepareProvider: bool}
                return True
            case ToolKind.GET_DIAGNOSTICS_FOR_FILE_PULL:
                return bool(c.get("diagnosticProvider"))
            case ToolKind.GET_DIAGNOSTICS_FOR_FILE_PUSH:
                # Push diagnostics are always assumed available
                return True
            case _:
                return False

    @property
    def raw(self) -> dict[str, Any]:
        return self._caps
