"""Tests for GenericLanguageServer (mocked multilspy internals)."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lsp_mcp.lsp.capabilities import ToolKind
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

            (tmp_path / "test.py").write_text("import os\n")
            uri = (tmp_path / "test.py").as_uri()
            diags = [{"message": "unused import", "severity": 2}]
            with server.open_file("test.py"):  # publishes for closed files are ignored
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
            (tmp_path / "a.py").write_text("x\n")
            uri = (tmp_path / "a.py").as_uri()
            with server.open_file("a.py"):
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


# spec: openspec/specs/lsp-lifecycle/spec.md
async def test_records_successful_startup_stages(tmp_path, caplog):
    handler = _make_mock_server_handler({"capabilities": {}})
    with caplog.at_level(logging.INFO), _patched(handler):
        server = GenericLanguageServer(
            command=["x", "private-argument"],
            repository_root_path=str(tmp_path),
            startup_attempt=2,
        )
        async with server.start_server():
            pass
    stages = [record.getMessage() for record in caplog.records]
    for stage in [
        "process_launch_begin",
        "process_launch_complete",
        "initialize_begin",
        "initialize_complete",
        "ready",
    ]:
        assert any(f"stage={stage} " in message for message in stages), stage
    assert all("attempt=2" in message for message in stages)
    assert str(tmp_path) in caplog.text
    assert "private-argument" not in caplog.text


# spec: openspec/specs/lsp-lifecycle/spec.md
def test_captures_stderr_without_protocol_payloads(tmp_path, caplog):
    server = GenericLanguageServer(command=["x"], repository_root_path=str(tmp_path))
    with caplog.at_level(logging.DEBUG):
        # multilspy's _log emits this exact source/target pair for stderr.
        server.server._log(
            "LSP stderr: missing TypeScript installation 100% done %s %d"
        )
        server.server.logger("server", "client", {"text": "PROTOCOL_DOCUMENT_SENTINEL"})
        server.server.logger(
            "client", "server", {"initializationOptions": "private options"}
        )
        server.server.logger("server", "client", "LSP stderr: private protocol")
    assert "missing TypeScript installation" in caplog.text
    assert str(tmp_path) in caplog.text
    assert "100% done %s %d" in caplog.text
    assert "PROTOCOL_DOCUMENT_SENTINEL" not in caplog.text
    assert "private options" not in caplog.text
    assert "private protocol" not in caplog.text


@pytest.mark.parametrize(
    "message_type,level",
    [(1, logging.ERROR), (2, logging.WARNING), (3, logging.INFO), (4, logging.INFO)],
)
# spec: openspec/specs/lsp-lifecycle/spec.md
async def test_captures_language_server_log_messages(
    tmp_path, caplog, message_type, level
):
    handler = _make_mock_server_handler({"capabilities": {}})
    callbacks = {}
    handler.on_notification.side_effect = lambda method, callback: callbacks.update(
        {method: callback}
    )
    with caplog.at_level(logging.INFO), _patched(handler):
        server = GenericLanguageServer(
            command=["x"], repository_root_path=str(tmp_path)
        )
        async with server.start_server():
            await callbacks["window/logMessage"](
                {"type": message_type, "message": "server diagnostic"}
            )
    records = [
        record
        for record in caplog.records
        if "server diagnostic" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].levelno == level
    assert str(tmp_path) in records[0].getMessage()


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
# spec: openspec/specs/lsp-lifecycle/spec.md
async def test_async_variant_used(tmp_path):
    handler = _make_mock_server_handler({"capabilities": {}})
    handler.stop = AsyncMock()
    with _patched(handler):
        server = GenericLanguageServer(
            command=["x"], repository_root_path=str(tmp_path)
        )
        async with server.start_server():
            handler.start.assert_awaited_once()
            handler.send.initialize.assert_awaited_once()
            handler.stop.assert_not_called()
    handler.stop.assert_awaited_once()


@pytest.mark.parametrize(
    "command",
    [
        ["tsc", "--lsp", "--stdio"],
        ["/bin/server name", "two words", "a'b", 'a"b', "$HOME;echo *"],
    ],
)
def test_server_launched_from_configured_command(tmp_path, command):
    # spec: openspec/specs/lsp-lifecycle/spec.md
    handler = _make_mock_server_handler({"capabilities": {}})
    with _patched(handler) as constructor:
        GenericLanguageServer(command=command, repository_root_path=str(tmp_path))
    launch = constructor.call_args.args[0]
    assert launch.cmd == command
    command.append("caller mutation")
    assert launch.cmd != command


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
