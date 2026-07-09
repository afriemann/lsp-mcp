"""Generic LSP server that can launch any command from config."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import shlex
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from multilspy.language_server import LanguageServer
from multilspy.lsp_protocol_handler.server import ProcessLaunchInfo
from multilspy.multilspy_config import MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

from .capabilities import CapabilitySet

# Client capabilities we advertise to the server.
# Broad enough to enable all features we need.
_CLIENT_CAPABILITIES: dict[str, Any] = {
    "workspace": {
        "applyEdit": False,
        "symbol": {"dynamicRegistration": False},
        "workspaceFolders": True,
        "diagnostics": {},
    },
    "textDocument": {
        "synchronization": {
            "dynamicRegistration": False,
            "willSave": False,
            "willSaveWaitUntil": False,
            "didSave": True,
        },
        "documentSymbol": {
            "dynamicRegistration": False,
            "hierarchicalDocumentSymbolSupport": True,
        },
        "definition": {"dynamicRegistration": False},
        "references": {"dynamicRegistration": False},
        "implementation": {"dynamicRegistration": False},
        "rename": {"dynamicRegistration": False, "prepareSupport": False},
        "publishDiagnostics": {"relatedInformation": False},
        "diagnostic": {"dynamicRegistration": False, "relatedDocumentSupport": False},
    },
}


class GenericLanguageServer(LanguageServer):
    """
    A ``LanguageServer`` subclass that launches an arbitrary command from
    lsp-mcp config instead of a language-enum-specific binary.

    Responsibilities:
    - Launch the configured command via ``ProcessLaunchInfo``.
    - Perform the LSP initialize handshake and capture capabilities.
    - Register a ``textDocument/publishDiagnostics`` notification handler.
    - Expose ``raw_request(method, params)`` for LSP methods not wrapped by
      multilspy (``workspace/symbol``, ``textDocument/implementation``,
      ``textDocument/rename``, ``textDocument/diagnostic``).
    """

    def __init__(
        self,
        command: list[str],
        repository_root_path: str,
        *,
        logger: MultilspyLogger | None = None,
        language_id: str = "plaintext",
    ) -> None:
        if not command:
            raise ValueError("command must not be empty")

        _logger = logger or MultilspyLogger()
        # multilspy's ProcessLaunchInfo.cmd is a string passed to
        # create_subprocess_shell.  We mitigate injection risk with shlex.join,
        # but shell variable expansion and aliases remain active.  See STYLE.md
        # "Known multilspy limitation: shell-based process launch".
        cmd_str = shlex.join(command)
        config = MultilspyConfig(code_language="python")  # value unused in generic path
        super().__init__(
            config=config,
            logger=_logger,
            repository_root_path=repository_root_path,
            process_launch_info=ProcessLaunchInfo(
                cmd=cmd_str, cwd=repository_root_path
            ),
            language_id=language_id,
        )
        self._command = command
        self._capabilities: CapabilitySet = CapabilitySet({})
        # per-URI push diagnostics cache: uri -> list[dict]
        self._push_diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._diag_event: dict[str, asyncio.Event] = {}

    @property
    def capabilities(self) -> CapabilitySet:
        return self._capabilities

    @property
    def push_diagnostics(self) -> dict[str, list[dict[str, Any]]]:
        return self._push_diagnostics

    async def raw_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Send a raw LSP request and return the response."""
        return await self.server.send_request(method, params)

    @asynccontextmanager
    async def start_server(self) -> AsyncIterator["GenericLanguageServer"]:
        """Start the language server process and complete the initialize handshake.

        Implementation note: ``LanguageServer.start_server()`` (the multilspy base)
        only sets the ``server_started`` flag — it does NOT call
        ``self.server.start()`` or send ``initialize``.  Those are the
        responsibility of each concrete subclass, exactly as done here.
        """

        async def _on_publish_diagnostics(params: dict[str, Any]) -> None:
            uri = params.get("uri", "")
            diags = params.get("diagnostics", [])
            self._push_diagnostics[uri] = diags
            event = self._diag_event.get(uri)
            if event is not None:
                event.set()

        async def _noop(params: Any) -> None:
            pass

        self.server.on_notification(
            "textDocument/publishDiagnostics", _on_publish_diagnostics
        )
        self.server.on_notification("window/logMessage", _noop)
        self.server.on_notification("$/progress", _noop)
        self.server.on_notification("window/showMessage", _noop)
        self.server.on_request("client/registerCapability", lambda p: None)

        async with super().start_server():
            await self.server.start()

            root_uri = pathlib.Path(self.repository_root_path).as_uri()
            initialize_params: dict[str, Any] = {
                "processId": os.getpid(),
                "clientInfo": {"name": "lsp-mcp", "version": "0.1.0"},
                "rootUri": root_uri,
                "rootPath": self.repository_root_path,
                "workspaceFolders": [
                    {
                        "uri": root_uri,
                        "name": os.path.basename(self.repository_root_path),
                    }
                ],
                "capabilities": _CLIENT_CAPABILITIES,
                "initializationOptions": None,
                "trace": "off",
            }

            try:
                init_response = await self.server.send.initialize(initialize_params)
            except Exception as exc:
                raise RuntimeError(
                    f"LSP initialize failed for command {self._command!r}: {exc}"
                ) from exc

            raw_caps = {}
            if isinstance(init_response, dict):
                raw_caps = init_response.get("capabilities", {})
            self._capabilities = CapabilitySet(raw_caps)

            self.server.notify.initialized({})
            self.completions_available.set()

            yield self

            try:
                await self.server.shutdown()
            except Exception:
                pass  # best-effort shutdown

    async def wait_for_diagnostics(self, uri: str, timeout: float = 2.0) -> None:
        """
        Wait up to *timeout* seconds for pushed diagnostics for *uri*.
        Returns immediately if diagnostics have already arrived.
        """
        if uri not in self._diag_event:
            self._diag_event[uri] = asyncio.Event()
        if uri in self._push_diagnostics:
            return
        try:
            await asyncio.wait_for(self._diag_event[uri].wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass  # return whatever we have
