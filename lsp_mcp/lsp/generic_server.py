"""Generic LSP server that can launch any command from config."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import shlex
from contextlib import asynccontextmanager, contextmanager
from pathlib import PurePath
from typing import Any, AsyncIterator, Iterator

from multilspy.language_server import LanguageServer, LSPFileBuffer
from multilspy.lsp_protocol_handler.lsp_constants import LSPConstants
from multilspy.multilspy_exceptions import MultilspyException
from multilspy.multilspy_utils import FileUtils
from multilspy.lsp_protocol_handler.server import ProcessLaunchInfo
from multilspy.multilspy_config import MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

from .capabilities import CapabilitySet

logger = logging.getLogger(__name__)

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
        "hover": {
            "dynamicRegistration": False,
            "contentFormat": ["markdown", "plaintext"],
        },
        "callHierarchy": {"dynamicRegistration": False},
        "rename": {"dynamicRegistration": False, "prepareSupport": False},
        "publishDiagnostics": {"relatedInformation": False},
        "diagnostic": {"dynamicRegistration": False, "relatedDocumentSupport": False},
    },
}


_LANGUAGE_IDS: dict[str, str] = {
    ".py": "python", ".pyi": "python", ".pyw": "python",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascriptreact",
    ".go": "go", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".html": "html", ".htm": "html", ".css": "css", ".json": "json",
    ".rs": "rust", ".java": "java", ".rb": "ruby", ".sh": "shellscript",
}  # fmt: skip


def language_id_for(path: str) -> str:
    """LSP ``languageId`` for *path* by extension; ``plaintext`` if unknown.

    Servers such as ty ignore documents opened with the wrong languageId and
    answer every request as if the file were empty/clean.
    """
    return _LANGUAGE_IDS.get(pathlib.PurePath(path).suffix.lower(), "plaintext")


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
        initialization_options: dict[str, Any] | None = None,
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
        self._initialization_options = initialization_options
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

    def document_uri(self, relative_file_path: str) -> str:
        """The URI under which *relative_file_path* is (or would be) opened."""
        return pathlib.Path(
            str(PurePath(self.repository_root_path, relative_file_path))
        ).as_uri()

    @contextmanager
    def open_file(self, relative_file_path: str) -> Iterator[None]:
        """
        Open a document for the duration of the block (ref-counted).

        Same behaviour as multilspy's ``open_file`` — ``didOpen`` on the first
        reference, ``didClose`` when the last one is released — except the
        release happens in a ``finally``: multilspy's version leaves the buffer
        open (and never sends ``didClose``) when the block raises or is
        cancelled, which poisons every later call on that file.
        """
        if not self.server_started:
            raise MultilspyException("Language Server not started")
        uri = self.document_uri(relative_file_path)
        buf = self.open_file_buffers.get(uri)
        if buf is not None:
            buf.ref_count += 1
        else:
            contents = FileUtils.read_file(
                self.logger,
                str(PurePath(self.repository_root_path, relative_file_path)),
            )
            lang = language_id_for(relative_file_path)
            buf = LSPFileBuffer(uri, contents, 0, lang, 1)
            self.open_file_buffers[uri] = buf
            self.server.notify.did_open_text_document(
                {
                    LSPConstants.TEXT_DOCUMENT: {
                        LSPConstants.URI: uri,
                        LSPConstants.LANGUAGE_ID: lang,
                        LSPConstants.VERSION: 0,
                        LSPConstants.TEXT: contents,
                    }
                }
            )
        try:
            yield
        finally:
            buf.ref_count -= 1
            if buf.ref_count <= 0:
                try:
                    self.server.notify.did_close_text_document(
                        {LSPConstants.TEXT_DOCUMENT: {LSPConstants.URI: uri}}
                    )
                finally:
                    self.open_file_buffers.pop(uri, None)

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
            # Only accept publishes for a document that is open right now: this
            # drops e.g. the (usually empty) publish a server sends in reaction
            # to our didClose of the previous call.
            buf = self.open_file_buffers.get(uri)
            if buf is None:
                return
            # A publish stamped with a document version other than the one we
            # last sent (didOpen / didChange) describes a different text.
            version = params.get("version")
            if version is not None and version != buf.version:
                return
            self._push_diagnostics[uri] = params.get("diagnostics", [])
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
            try:
                await self.server.start()
                await self._initialize()
                yield self
                try:
                    await self.server.shutdown()
                except Exception:
                    pass  # best-effort shutdown
            finally:
                # Always reap the subprocess — on normal exit, initialize
                # failure, and cancellation (e.g. a start timeout) alike.
                try:
                    await self.server.stop()
                except Exception as exc:
                    logger.debug("Error stopping language server: %s", exc)

    async def _initialize(self) -> None:
        """Run the LSP initialize handshake and capture capabilities."""
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
            "initializationOptions": self._initialization_options,
            "trace": "off",
        }

        try:
            init_response = await self.server.send.initialize(initialize_params)
        except Exception as exc:
            # The command line is deliberately NOT included: it can carry
            # credentials and this message reaches the agent.  Log it instead.
            logger.warning(
                "LSP initialize failed for command %r: %s", self._command, exc
            )
            raise RuntimeError(f"LSP initialize failed: {exc}") from exc

        raw_caps = {}
        if isinstance(init_response, dict):
            raw_caps = init_response.get("capabilities", {})
        self._capabilities = CapabilitySet(raw_caps)

        self.server.notify.initialized({})
        self.completions_available.set()

    def reset_diagnostics(self, uri: str) -> None:
        """
        Forget everything published for *uri* and arm a fresh event.

        Call this right after ``didOpen`` (before the first ``await``, so no
        publish can have been processed yet): only publishes arriving after the
        open will then be accepted by ``wait_for_diagnostics``.
        """
        self._push_diagnostics.pop(uri, None)
        self._diag_event[uri] = asyncio.Event()

    def forget_diagnostics(self, uri: str) -> None:
        """Drop all per-URI diagnostics state (call when the document is closed)."""
        self._push_diagnostics.pop(uri, None)
        self._diag_event.pop(uri, None)

    async def wait_for_diagnostics(
        self, uri: str, timeout: float = 2.0, quiet_period: float = 0.3
    ) -> bool:
        """
        Wait for pushed diagnostics for *uri* published after ``reset_diagnostics``.

        Waits up to *timeout* seconds for the first publish, then keeps waiting
        until no further publish arrives for *quiet_period* seconds (servers often
        send syntax diagnostics first and semantic ones later); the last publish
        is what ``push_diagnostics[uri]`` then holds.  The whole wait never
        exceeds *timeout*.  Returns True if a publish was received.
        """
        if uri in self._push_diagnostics:
            # Not preceded by reset_diagnostics: the document was already open
            # (nested open), so what is cached is current — no new publish is
            # coming because nothing was re-opened.
            return True
        event = self._diag_event.get(uri)
        if event is None:
            event = self._diag_event[uri] = asyncio.Event()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        while True:
            event.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                return True
            try:
                await asyncio.wait_for(
                    event.wait(), timeout=min(quiet_period, remaining)
                )
            except TimeoutError:
                return True
