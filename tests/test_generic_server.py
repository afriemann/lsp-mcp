"""Tests for GenericLanguageServer (mocked multilspy internals)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lsp_mcp.lsp.capabilities import CapabilitySet, ToolKind
from lsp_mcp.lsp.generic_server import GenericLanguageServer


def _make_mock_server_handler(init_response: dict) -> MagicMock:
    """Build a mock LanguageServerHandler that returns *init_response* on initialize."""
    handler = MagicMock()
    handler.send = MagicMock()
    handler.send.initialize = AsyncMock(return_value=init_response)
    handler.notify = MagicMock()
    handler.notify.initialized = MagicMock()
    handler.on_notification = MagicMock()
    handler.on_request = MagicMock()
    handler.start = AsyncMock()
    handler.shutdown = AsyncMock()
    handler.send_request = AsyncMock(return_value=None)
    return handler


def _make_process_launch_info_side_effect(handler: MagicMock):
    """Patch LanguageServerHandler constructor to return our mock."""
    return handler


# ---------------------------------------------------------------------------
# Capability capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_captured_from_initialize(tmp_path):
    """GenericLanguageServer captures capabilities from InitializeResult."""
    caps = {
        "definitionProvider": True,
        "documentSymbolProvider": True,
        "referencesProvider": False,
    }
    init_response = {"capabilities": caps}

    mock_handler = _make_mock_server_handler(init_response)

    with patch(
        "multilspy.language_server.LanguageServerHandler", return_value=mock_handler
    ):
        server = GenericLanguageServer(
            command=["ty", "server"],
            repository_root_path=str(tmp_path),
        )
        async with server.start_server():
            assert server.capabilities.supports(ToolKind.FIND_DECLARATION)
            assert server.capabilities.supports(ToolKind.GET_SYMBOLS_OVERVIEW)
            assert not server.capabilities.supports(ToolKind.FIND_REFERENCING_SYMBOLS)


@pytest.mark.asyncio
async def test_empty_capabilities_on_failed_init(tmp_path):
    """If initialize returns no capabilities, CapabilitySet is empty."""
    init_response = {}  # no 'capabilities' key

    mock_handler = _make_mock_server_handler(init_response)

    with patch(
        "multilspy.language_server.LanguageServerHandler", return_value=mock_handler
    ):
        server = GenericLanguageServer(
            command=["ty", "server"],
            repository_root_path=str(tmp_path),
        )
        async with server.start_server():
            assert not server.capabilities.supports(ToolKind.FIND_DECLARATION)


# ---------------------------------------------------------------------------
# Push diagnostics notification handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_diagnostics_stored_per_uri(tmp_path):
    """publishDiagnostics notifications are stored in push_diagnostics dict."""
    mock_handler = _make_mock_server_handler({"capabilities": {}})

    # Capture the registered notification callback
    registered_callbacks: dict[str, object] = {}

    def _on_notification(method, cb):
        registered_callbacks[method] = cb

    mock_handler.on_notification.side_effect = _on_notification

    with patch(
        "multilspy.language_server.LanguageServerHandler", return_value=mock_handler
    ):
        server = GenericLanguageServer(
            command=["ruff", "server"],
            repository_root_path=str(tmp_path),
        )
        async with server.start_server():
            # Simulate server pushing diagnostics
            push_cb = registered_callbacks.get("textDocument/publishDiagnostics")
            assert push_cb is not None

            uri = "file:///tmp/test.py"
            diags = [{"message": "unused import", "severity": 2}]
            await push_cb({"uri": uri, "diagnostics": diags})

            assert uri in server.push_diagnostics
            assert server.push_diagnostics[uri][0]["message"] == "unused import"


@pytest.mark.asyncio
async def test_push_diagnostics_overwrite_per_uri(tmp_path):
    """Later push replaces earlier diagnostics for the same URI."""
    mock_handler = _make_mock_server_handler({"capabilities": {}})
    registered: dict = {}
    mock_handler.on_notification.side_effect = lambda m, c: registered.update({m: c})

    with patch(
        "multilspy.language_server.LanguageServerHandler", return_value=mock_handler
    ):
        server = GenericLanguageServer(
            command=["ruff", "server"],
            repository_root_path=str(tmp_path),
        )
        async with server.start_server():
            cb = registered["textDocument/publishDiagnostics"]
            uri = "file:///tmp/a.py"
            await cb({"uri": uri, "diagnostics": [{"message": "first"}]})
            await cb({"uri": uri, "diagnostics": [{"message": "second"}]})
            assert len(server.push_diagnostics[uri]) == 1
            assert server.push_diagnostics[uri][0]["message"] == "second"


@pytest.mark.asyncio
async def test_raw_request_delegates_to_handler(tmp_path):
    """raw_request forwards to server.send_request."""
    mock_handler = _make_mock_server_handler({"capabilities": {}})
    mock_handler.send_request = AsyncMock(return_value={"items": []})

    with patch(
        "multilspy.language_server.LanguageServerHandler", return_value=mock_handler
    ):
        server = GenericLanguageServer(
            command=["gopls"],
            repository_root_path=str(tmp_path),
        )
        async with server.start_server():
            result = await server.raw_request("workspace/symbol", {"query": "Foo"})
            mock_handler.send_request.assert_called_once_with(
                "workspace/symbol", {"query": "Foo"}
            )
            assert result == {"items": []}
