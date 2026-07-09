"""Tests for CapabilitySet."""

from __future__ import annotations

import pytest

from lsp_mcp.lsp.capabilities import CapabilitySet, ToolKind


def test_empty_capabilities() -> None:
    caps = CapabilitySet({})
    for tool in ToolKind:
        if tool == ToolKind.GET_DIAGNOSTICS_FOR_FILE_PUSH:
            assert caps.supports(tool)  # push always True
        else:
            assert not caps.supports(tool)


def test_document_symbol_provider() -> None:
    caps = CapabilitySet({"documentSymbolProvider": True})
    assert caps.supports(ToolKind.GET_SYMBOLS_OVERVIEW)
    assert caps.supports(ToolKind.REPLACE_SYMBOL_BODY)
    assert not caps.supports(ToolKind.FIND_SYMBOL)


def test_workspace_symbol_provider() -> None:
    caps = CapabilitySet({"workspaceSymbolProvider": True})
    assert caps.supports(ToolKind.FIND_SYMBOL)
    assert not caps.supports(ToolKind.GET_SYMBOLS_OVERVIEW)


def test_definition_provider() -> None:
    caps = CapabilitySet({"definitionProvider": True})
    assert caps.supports(ToolKind.FIND_DECLARATION)
    assert not caps.supports(ToolKind.FIND_IMPLEMENTATIONS)


def test_implementation_provider() -> None:
    caps = CapabilitySet({"implementationProvider": True})
    assert caps.supports(ToolKind.FIND_IMPLEMENTATIONS)


def test_references_provider() -> None:
    caps = CapabilitySet({"referencesProvider": True})
    assert caps.supports(ToolKind.FIND_REFERENCING_SYMBOLS)


def test_rename_provider_bool_true() -> None:
    caps = CapabilitySet({"renameProvider": True})
    assert caps.supports(ToolKind.RENAME_SYMBOL)


def test_rename_provider_bool_false() -> None:
    caps = CapabilitySet({"renameProvider": False})
    assert not caps.supports(ToolKind.RENAME_SYMBOL)


def test_rename_provider_dict() -> None:
    caps = CapabilitySet({"renameProvider": {"prepareProvider": True}})
    assert caps.supports(ToolKind.RENAME_SYMBOL)


def test_rename_provider_absent() -> None:
    caps = CapabilitySet({})
    assert not caps.supports(ToolKind.RENAME_SYMBOL)


def test_diagnostic_provider_pull() -> None:
    caps = CapabilitySet({"diagnosticProvider": {"identifier": "ruff"}})
    assert caps.supports(ToolKind.GET_DIAGNOSTICS_FOR_FILE_PULL)


def test_diagnostic_provider_absent_no_pull() -> None:
    caps = CapabilitySet({})
    assert not caps.supports(ToolKind.GET_DIAGNOSTICS_FOR_FILE_PULL)


def test_push_diagnostics_always_supported() -> None:
    """Push diagnostics have no capability flag; always True."""
    assert CapabilitySet({}).supports(ToolKind.GET_DIAGNOSTICS_FOR_FILE_PUSH)
    assert CapabilitySet({"diagnosticProvider": False}).supports(
        ToolKind.GET_DIAGNOSTICS_FOR_FILE_PUSH
    )


def test_raw_property() -> None:
    d = {"definitionProvider": True}
    caps = CapabilitySet(d)
    assert caps.raw is d
