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
    handler.stop = AsyncMock()
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


# --- process cleanup / init options on the wire ----------------------------


def _patched(handler):
    return patch(
        "multilspy.language_server.LanguageServerHandler", return_value=handler
    )


@pytest.mark.asyncio
async def test_initialization_options_on_the_wire(tmp_path):
    handler = _make_mock_server_handler({"capabilities": {}})
    handler.stop = AsyncMock()
    with _patched(handler):
        server = GenericLanguageServer(
            command=["x"],
            repository_root_path=str(tmp_path),
            initialization_options={"tsserver": {"path": "/p"}},
        )
        async with server.start_server():
            pass
    params = handler.send.initialize.await_args.args[0]
    assert params["initializationOptions"] == {"tsserver": {"path": "/p"}}


@pytest.mark.asyncio
async def test_process_stopped_on_normal_exit(tmp_path):
    handler = _make_mock_server_handler({"capabilities": {}})
    handler.stop = AsyncMock()
    with _patched(handler):
        server = GenericLanguageServer(
            command=["x"], repository_root_path=str(tmp_path)
        )
        async with server.start_server():
            handler.stop.assert_not_called()
    handler.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_stopped_when_initialize_fails_without_command_in_message(
    tmp_path,
):
    handler = _make_mock_server_handler({})
    handler.send.initialize = AsyncMock(side_effect=RuntimeError("no tsserver"))
    handler.stop = AsyncMock()
    with _patched(handler):
        server = GenericLanguageServer(
            command=["secret-bin", "--token=abc"], repository_root_path=str(tmp_path)
        )
        with pytest.raises(RuntimeError) as ei:
            async with server.start_server():
                pass
    handler.stop.assert_awaited_once()
    assert "no tsserver" in str(ei.value)
    assert "secret-bin" not in str(ei.value) and "abc" not in str(ei.value)


@pytest.mark.asyncio
async def test_process_stopped_when_start_is_cancelled(tmp_path):
    handler = _make_mock_server_handler({})

    async def _hang(_params):
        await asyncio.Event().wait()

    handler.send.initialize = _hang
    handler.stop = AsyncMock()
    with _patched(handler):
        server = GenericLanguageServer(
            command=["x"], repository_root_path=str(tmp_path)
        )

        async def _run():
            async with server.start_server():
                pass

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_run(), 0.05)
    handler.stop.assert_awaited_once()
