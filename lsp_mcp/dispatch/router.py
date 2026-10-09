"""Central routing and dispatch for all ten MCP tools."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import logging
import os
import pathlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import unquote, urlparse

from ..config.model import Config, ServerSpec
from ..config.resolver import resolve
from ..lsp.capabilities import ToolKind
from ..lsp.generic_server import GenericLanguageServer
from ..lsp.manager import ServerEntry, ServerManager, infer_project_root
from ..types import (
    CallEdge,
    CallHierarchyResult,
    CallItem,
    DeclarationResult,
    Diagnostic,
    DiagnosticsResult,
    FindSymbolResult,
    HoverResult,
    ImplementationsResult,
    Location,
    ReferencingSymbolsResult,
    RenameSymbolResult,
    ReplaceSymbolBodyResult,
    SymbolNode,
    SymbolsOverviewResult,
)
from .edits import apply_edit, apply_workspace_edit
from .symbols import SymbolRange, resolve_symbol

logger = logging.getLogger(__name__)

T = TypeVar("T")

# How long to wait for pushed diagnostics to settle
_DIAG_SETTLE_SECONDS: float = 2.0
DEFAULT_DIAG_QUIET_PERIOD: float = 0.3

DEFAULT_REQUEST_TIMEOUT: float = 15.0
DEFAULT_CALL_DEADLINE: float = 30.0
DEFAULT_RETRIES: int = 3
DEFAULT_RETRY_BACKOFF: float = 0.1

# LSP error codes that mean "the server dropped this request because state
# changed under it" — safe to re-issue for read-only requests.
_CONTENT_MODIFIED = -32801
_REQUEST_CANCELLED = -32800
_RETRYABLE_CODES = frozenset({_CONTENT_MODIFIED, _REQUEST_CANCELLED})

DEFAULT_FIND_LIMIT = 50
MAX_FIND_LIMIT = 500
MAX_CONTEXT_LINES = 20
MAX_CONTEXT_BYTES = 2 * 1024 * 1024  # never read more than this per context file

SYMBOL_KINDS: dict[int, str] = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
    15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
    20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
    25: "Operator", 26: "TypeParameter",
}  # fmt: skip
_KIND_BY_NAME = {v.lower(): k for k, v in SYMBOL_KINDS.items()}


class LspRequestError(Exception):
    """A language-server request failed; the message is safe to show the agent."""


@dataclass
class _Ctx:
    """Per-call accumulator for warnings and failures."""

    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    """Caveats that apply only to an *empty* result (e.g. server still indexing)."""

    def hint(self, message: str) -> None:
        if message not in self.hints:
            self.hints.append(message)

    def fail(self, message: str) -> None:
        self.failures.append(message)
        self.warnings.append(message)

    def note(self, found: bool, empty_note: str) -> str:
        """Compose the result note.

        Failures are never hidden behind an "empty" note: if nothing was found
        and anything failed, the note says the request failed.  If something was
        found despite failures, the note flags the result as partial.
        """
        if not self.failures:
            if found:
                return ""
            return " ".join(p for p in (empty_note, *self.hints) if p)
        detail = "; ".join(self.failures)
        if found:
            return f"Partial result — some servers failed: {detail}"
        return f"Request failed (this is NOT a 'no matches' result): {detail}"


@dataclass
class _Outcome:
    items: list[Any] = field(default_factory=list)
    note: str = ""
    warnings: list[str] = field(default_factory=list)


def _error_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


_deadline_cm: contextvars.ContextVar[asyncio.Timeout | None] = contextvars.ContextVar(
    "lsp_mcp_deadline", default=None
)

_INDEXING_HINT = (
    "The server returned an empty result; it may still be indexing the project "
    "— retry shortly before concluding the symbol does not exist."
)


def with_deadline(result_cls: type):
    """
    Bound a public tool call by ``Dispatcher._call_deadline``.

    The deadline covers lock waits, requests and retries (time spent starting
    servers is excluded — see ``Dispatcher._acquire``).  On expiry the tool
    returns an explicit note instead of blocking.
    """

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            cm = asyncio.timeout(self._call_deadline)
            token = _deadline_cm.set(cm)
            try:
                async with cm:
                    return await fn(self, *args, **kwargs)
            except TimeoutError:
                if not cm.expired():
                    raise
                msg = (
                    f"{fn.__name__} exceeded the {self._call_deadline:g}s per-call "
                    "deadline (waiting for the server lock, requests and retries); "
                    "the result is unknown — this is NOT a 'no matches' result."
                )
                return result_cls(note=msg, warnings=[msg])
            finally:
                _deadline_cm.reset(token)

        return wrapper

    return deco


class Dispatcher:
    """
    Routes tool calls to the correct language server(s) and normalises results.

    Semantics:
    - Navigation tools: first-wins (first server returning a non-empty result wins).
    - Diagnostics: merge from all servers.
    - No capable server: returns empty result with explanatory ``note``.
    - Failures (start failure, timeout, exhausted retries) are never reported
      as empty-but-successful: they are put in ``note`` (and ``warnings``).
    - Requests to one language server are serialised (``ServerEntry.lock``) so
      concurrent tool calls cannot invalidate each other's document state.
    """

    def __init__(
        self,
        config: Config,
        manager: ServerManager,
        *,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
        call_deadline: float = DEFAULT_CALL_DEADLINE,
        diag_quiet_period: float = DEFAULT_DIAG_QUIET_PERIOD,
        diag_settle_timeout: float = _DIAG_SETTLE_SECONDS,
    ) -> None:
        self._diag_quiet_period = diag_quiet_period
        self._diag_settle_timeout = diag_settle_timeout
        self._call_deadline = call_deadline
        self._config = config
        self._manager = manager
        self._request_timeout = request_timeout
        self._retries = retries
        self._retry_backoff = retry_backoff

    # ------------------------------------------------------------------
    # Helpers: acquisition, guarded requests
    # ------------------------------------------------------------------

    def _start_failure(self, spec_name: str, file_path: str) -> str:
        reason = self._manager.failure_reason(spec_name, file_path)
        msg = f"Server '{spec_name}' failed to start"
        return f"{msg}: {reason}" if isinstance(reason, str) and reason else msg

    async def _acquire(self, spec: ServerSpec, file_path: str) -> ServerEntry | None:
        """``manager.acquire`` that does not count start time against the deadline."""
        cm = _deadline_cm.get()
        loop = asyncio.get_running_loop()
        remaining: float | None = None
        if cm is not None and not cm.expired() and cm.when() is not None:
            remaining = cm.when() - loop.time()
            cm.reschedule(None)  # pause the deadline while servers start
        try:
            return await self._manager.acquire(spec, file_path)
        finally:
            if cm is not None and remaining is not None:
                cm.reschedule(loop.time() + max(remaining, 0.0))

    async def _acquire_servers(
        self, file_path: str, tool: ToolKind, ctx: _Ctx
    ) -> list[tuple[ServerSpec, ServerEntry | None]]:
        """
        Resolve, acquire, and capability-filter servers for *file_path*.

        Start failures are recorded on *ctx*.  Returns the capable servers as
        (spec, entry) pairs.
        """
        specs = resolve(file_path, self._config)
        results: list[tuple[ServerSpec, ServerEntry | None]] = []

        for spec in specs:
            entry = await self._acquire(spec, file_path)
            if entry is None or entry.state != "ready":
                ctx.fail(self._start_failure(spec.name, file_path))
                results.append((spec, None))
                continue
            if not entry.server.capabilities.supports(tool):
                continue  # silently skip non-capable servers
            results.append((spec, entry))

        return results

    async def _req(
        self, server_name: str, op: str, make: Callable[[], Awaitable[T]]
    ) -> T:
        """
        Issue one LSP request with a timeout and bounded retry on
        ContentModified (-32801) / RequestCancelled (-32800).

        Raises ``LspRequestError`` with an agent-readable message on failure.
        """
        attempts = self._retries + 1
        for attempt in range(1, attempts + 1):
            cm = asyncio.timeout(self._request_timeout)
            try:
                async with cm:
                    return await make()
            except TimeoutError as exc:
                if cm.expired():
                    raise LspRequestError(
                        f"Server '{server_name}': {op} timed out after "
                        f"{self._request_timeout:g}s"
                    ) from None
                # A TimeoutError raised from inside the request is not ours.
                raise LspRequestError(
                    f"Server '{server_name}': {op} failed: {exc or 'TimeoutError'}"
                ) from exc
            except Exception as exc:
                code = _error_code(exc)
                if code in _RETRYABLE_CODES:
                    if attempt < attempts:
                        await asyncio.sleep(self._retry_backoff * 2 ** (attempt - 1))
                        continue
                    raise LspRequestError(
                        f"Server '{server_name}': {op} failed after {attempts} "
                        f"attempts: {exc}"
                    ) from exc
                raise LspRequestError(
                    f"Server '{server_name}': {op} failed: {exc}"
                ) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    @contextlib.asynccontextmanager
    async def _lock_all(self, entries: list[ServerEntry | None]):
        """
        Hold the locks of every involved server (stable order, no duplicates).

        Edit tools notify *all* capable servers of the change, so they must not
        race with another call using any of those servers.  Other tools hold a
        single lock at a time, so the sorted acquisition cannot deadlock.
        """
        unique = {id(e): e for e in entries if e is not None}
        async with contextlib.AsyncExitStack() as stack:
            for key in sorted(unique):
                await stack.enter_async_context(unique[key].lock)
            yield

    def _abs(self, file_path: str) -> str:
        return str(pathlib.Path(file_path).resolve())

    def _rel(self, abs_file: str, root: str) -> str:
        try:
            return os.path.relpath(abs_file, root)
        except ValueError:
            return abs_file

    def _loc_from_multilspy(self, loc: dict[str, Any]) -> Location:
        r = loc.get("range", {})
        s = r.get("start", {})
        e = r.get("end", {})
        return Location(
            path=loc.get("absolutePath") or loc.get("uri", ""),
            line=s.get("line", 0),
            character=s.get("character", 0),
            end_line=e.get("line"),
            end_character=e.get("character"),
        )

    async def _resolve_position(
        self,
        spec: ServerSpec,
        server: GenericLanguageServer,
        rel: str,
        symbol: str,
        ctx: _Ctx,
    ) -> tuple[int, int] | list[str] | None:
        """Like the module-level resolver, but request failures raise."""
        raw, _ = await self._req(
            spec.name,
            "textDocument/documentSymbol",
            lambda: server.request_document_symbols(rel),
        )
        if not raw:
            ctx.hint(_INDEXING_HINT)
        return _position_from_symbols(raw, symbol)

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    @with_deadline(SymbolsOverviewResult)
    async def get_symbols_overview(self, file_path: str) -> SymbolsOverviewResult:
        abs_path = self._abs(file_path)
        ctx = _Ctx()
        servers = await self._acquire_servers(
            abs_path, ToolKind.GET_SYMBOLS_OVERVIEW, ctx
        )
        if not any(e for _, e in servers):
            return SymbolsOverviewResult(
                note=ctx.note(False, _no_cap_note(abs_path, "documentSymbolProvider")),
                warnings=ctx.warnings,
            )

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)
        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                async with entry.lock:
                    with _maybe_open(server, rel):
                        raw, _ = await self._req(
                            spec.name,
                            "textDocument/documentSymbol",
                            lambda: server.request_document_symbols(rel),
                        )
                if not raw:
                    ctx.hint(_INDEXING_HINT)
                nodes = _symbols_to_nodes(raw)
                if nodes:
                    return SymbolsOverviewResult(
                        symbols=nodes,
                        note=ctx.note(True, ""),
                        warnings=ctx.warnings,
                    )
            except LspRequestError as exc:
                ctx.fail(str(exc))
            except Exception as exc:
                ctx.fail(f"Server '{spec.name}': {exc}")

        return SymbolsOverviewResult(
            note=ctx.note(False, f"No symbols found in {abs_path}"),
            warnings=ctx.warnings,
        )

    @with_deadline(FindSymbolResult)
    async def find_symbol(
        self,
        query: str,
        file_path: str = "",
        kind: str | int | None = None,
        limit: int = DEFAULT_FIND_LIMIT,
    ) -> FindSymbolResult:
        kind_num: int | None = None
        if kind is not None and str(kind).strip() != "":
            kind_num = _parse_kind(kind)
            if kind_num is None:
                return FindSymbolResult(
                    note=(
                        f"Unknown kind {kind!r}: use an LSP SymbolKind number (1-26) "
                        f"or name such as {', '.join(list(SYMBOL_KINDS.values())[:6])}…"
                    )
                )
        limit = max(1, min(int(limit), MAX_FIND_LIMIT))

        ctx = _Ctx()
        if not file_path:
            # No file context: try every configured server for workspace search.
            pairs = await self._all_servers(ctx)
            empty = f"No symbols matching '{query}' across all configured servers"
            route_rel = ""
        else:
            route_path = self._abs(file_path)
            pairs = await self._acquire_servers(route_path, ToolKind.FIND_SYMBOL, ctx)
            if not any(e for _, e in pairs):
                return FindSymbolResult(
                    note=ctx.note(
                        False, _no_cap_note(route_path, "workspaceSymbolProvider")
                    ),
                    warnings=ctx.warnings,
                )
            empty = f"No symbols matching '{query}'"
            route_rel = self._rel(route_path, infer_project_root(route_path))

        for spec, entry in pairs:
            if entry is None:
                continue
            server = entry.server
            try:
                async with entry.lock:
                    # Some servers (tsserver) have no project until a file is
                    # open, so open the routing file for the duration of the query.
                    with _maybe_open(server, route_rel):
                        raw = await self._req(
                            spec.name,
                            "workspace/symbol",
                            lambda: server.request_workspace_symbol(query),
                        )
                if not raw:
                    ctx.hint(_INDEXING_HINT)
                symbols = _normalise_workspace_symbols(raw)
                if kind_num is not None:
                    symbols = [s for s in symbols if s["kind"] == kind_num]
                if symbols:
                    symbols = _rank_symbols(symbols, query)
                    total = len(symbols)
                    return FindSymbolResult(
                        symbols=symbols[:limit],
                        truncated=total > limit,
                        total_matches=total,
                        note=ctx.note(True, ""),
                        warnings=ctx.warnings,
                    )
            except LspRequestError as exc:
                ctx.fail(str(exc))
            except Exception as exc:
                ctx.fail(f"Server '{spec.name}': {exc}")

        return FindSymbolResult(note=ctx.note(False, empty), warnings=ctx.warnings)

    async def _all_servers(
        self, ctx: _Ctx
    ) -> list[tuple[ServerSpec, ServerEntry | None]]:
        """Acquire every configured server that supports workspace symbols."""
        cwd = os.getcwd()
        out: list[tuple[ServerSpec, ServerEntry | None]] = []
        for spec in self._config.servers.values():
            entry = await self._acquire(spec, cwd)
            if entry is None or entry.state != "ready":
                ctx.fail(self._start_failure(spec.name, cwd))
                continue
            if not entry.server.capabilities.supports(ToolKind.FIND_SYMBOL):
                continue
            out.append((spec, entry))
        return out

    async def _locations_tool(
        self,
        symbol: str,
        file_path: str,
        tool: ToolKind,
        capability: str,
        what: str,
        request: Callable[
            [ServerSpec, GenericLanguageServer, str, str, tuple[int, int]],
            Awaitable[list[Location]],
        ],
        context_lines: int,
    ) -> _Outcome:
        """Shared flow: resolve *symbol* to a position, then ask each server."""
        abs_path = self._abs(file_path)
        ctx = _Ctx()
        servers = await self._acquire_servers(abs_path, tool, ctx)
        if not any(e for _, e in servers):
            return _Outcome(
                note=ctx.note(False, _no_cap_note(abs_path, capability)),
                warnings=ctx.warnings,
            )

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)

        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                async with entry.lock:
                    # One didOpen for the whole call (multilspy's own per-request
                    # open_file calls nest on this one via its ref count).
                    with _maybe_open(server, rel):
                        pos = await self._resolve_position(
                            spec, server, rel, symbol, ctx
                        )
                        if pos is None:
                            continue
                        if isinstance(pos, list):
                            return _Outcome(
                                note=f"Ambiguous symbol '{symbol}': {', '.join(pos)}",
                                warnings=ctx.warnings,
                            )
                        locations = await request(spec, server, rel, abs_path, pos)
                if locations:
                    _add_context(
                        locations, context_lines, [root, server.repository_root_path]
                    )
                    return _Outcome(
                        items=locations, note=ctx.note(True, ""), warnings=ctx.warnings
                    )
            except LspRequestError as exc:
                ctx.fail(str(exc))
            except Exception as exc:
                ctx.fail(f"Server '{spec.name}': {exc}")

        return _Outcome(
            note=ctx.note(False, f"No {what} found for '{symbol}'"),
            warnings=ctx.warnings,
        )

    @with_deadline(DeclarationResult)
    async def find_declaration(
        self, symbol: str, file_path: str, context_lines: int = 0
    ) -> DeclarationResult:
        async def request(spec, server, rel, abs_path, pos):
            raw = await self._req(
                spec.name,
                "textDocument/definition",
                lambda: server.request_definition(rel, pos[0], pos[1]),
            )
            return [self._loc_from_multilspy(dict(loc)) for loc in raw or []]

        o = await self._locations_tool(
            symbol, file_path, ToolKind.FIND_DECLARATION, "definitionProvider",
            "declaration", request, context_lines,
        )  # fmt: skip
        return DeclarationResult(locations=o.items, note=o.note, warnings=o.warnings)

    @with_deadline(ImplementationsResult)
    async def find_implementations(
        self, symbol: str, file_path: str, context_lines: int = 0
    ) -> ImplementationsResult:
        async def request(spec, server, rel, abs_path, pos):
            raw = await self._req(
                spec.name,
                "textDocument/implementation",
                lambda: server.raw_request(
                    "textDocument/implementation",
                    {
                        "textDocument": {"uri": pathlib.Path(abs_path).as_uri()},
                        "position": {"line": pos[0], "character": pos[1]},
                    },
                ),
            )
            return _parse_locations(raw, server.repository_root_path)

        o = await self._locations_tool(
            symbol, file_path, ToolKind.FIND_IMPLEMENTATIONS, "implementationProvider",
            "implementations", request, context_lines,
        )  # fmt: skip
        return ImplementationsResult(
            locations=o.items, note=o.note, warnings=o.warnings
        )

    @with_deadline(ReferencingSymbolsResult)
    async def find_referencing_symbols(
        self, symbol: str, file_path: str, context_lines: int = 0
    ) -> ReferencingSymbolsResult:
        async def request(spec, server, rel, abs_path, pos):
            raw = await self._req(
                spec.name,
                "textDocument/references",
                lambda: server.request_references(rel, pos[0], pos[1]),
            )
            return [self._loc_from_multilspy(dict(loc)) for loc in raw or []]

        o = await self._locations_tool(
            symbol, file_path, ToolKind.FIND_REFERENCING_SYMBOLS, "referencesProvider",
            "references", request, context_lines,
        )  # fmt: skip
        return ReferencingSymbolsResult(
            locations=o.items, note=o.note, warnings=o.warnings
        )

    @with_deadline(ReplaceSymbolBodyResult)
    async def replace_symbol_body(
        self, symbol: str, new_body: str, file_path: str
    ) -> ReplaceSymbolBodyResult:
        abs_path = self._abs(file_path)
        ctx = _Ctx()
        servers = await self._acquire_servers(
            abs_path, ToolKind.REPLACE_SYMBOL_BODY, ctx
        )
        if not any(e for _, e in servers):
            return ReplaceSymbolBodyResult(
                note=ctx.note(False, _no_cap_note(abs_path, "documentSymbolProvider")),
                warnings=ctx.warnings,
            )

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)

        async with self._lock_all([e for _, e in servers]):
            return await self._replace_locked(
                symbol, new_body, abs_path, rel, servers, ctx
            )

    async def _replace_locked(
        self,
        symbol: str,
        new_body: str,
        abs_path: str,
        rel: str,
        servers: list[tuple[ServerSpec, ServerEntry | None]],
        ctx: _Ctx,
    ) -> ReplaceSymbolBodyResult:
        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                raw, _ = await self._req(
                    spec.name,
                    "textDocument/documentSymbol",
                    lambda: server.request_document_symbols(rel),
                )
                if not raw:
                    ctx.hint(_INDEXING_HINT)
                matches = resolve_symbol(symbol, [_unifiedsym_to_dict(s) for s in raw])
                if not matches:
                    continue
                if len(matches) > 1:
                    names = [
                        f"{m.name} (line {m.full_range.start_line})" for m in matches
                    ]
                    return ReplaceSymbolBodyResult(
                        note=f"Ambiguous symbol '{symbol}': {', '.join(names)}",
                        warnings=ctx.warnings,
                    )
                sym = matches[0]
                all_servers = [e2.server for _, e2 in servers if e2 is not None]
                # Preserve decorators: if the symbol has leading decorator lines
                # (full_range starts before selection_range) and the caller has
                # NOT supplied their own decorators (new_body does not begin with
                # "@"), start the edit at selection_range.start_line so the
                # existing decorator lines are left untouched.
                #
                # lstrip() guards against callers supplying leading whitespace
                # before "@"; _normalize_replacement handles indentation later.
                #
                # We use full_range.start_char (not selection_range.start_char)
                # because start_char is the *indentation column* for the whole
                # symbol block (both decorator and def share the same indent).
                # selection_range.start_char points to the identifier name *within*
                # the def line (e.g. column 4 for "def greet"), which is wrong as
                # an edit-start column.
                if (
                    sym.full_range.start_line < sym.selection_range.start_line
                    and not new_body.lstrip().startswith("@")
                ):
                    edit_range = SymbolRange(
                        start_line=sym.selection_range.start_line,
                        start_char=sym.full_range.start_char,
                        end_line=sym.full_range.end_line,
                        end_char=sym.full_range.end_char,
                    )
                else:
                    edit_range = sym.full_range
                apply_edit(abs_path, edit_range, new_body, all_servers)
                return ReplaceSymbolBodyResult(
                    success=True, note=ctx.note(True, ""), warnings=ctx.warnings
                )
            except LspRequestError as exc:
                ctx.fail(str(exc))
            except Exception as exc:
                ctx.fail(f"Server '{spec.name}': {exc}")

        return ReplaceSymbolBodyResult(
            note=ctx.note(False, f"Symbol '{symbol}' not found in {abs_path}"),
            warnings=ctx.warnings,
        )

    @with_deadline(RenameSymbolResult)
    async def rename_symbol(
        self, symbol: str, new_name: str, file_path: str
    ) -> RenameSymbolResult:
        abs_path = self._abs(file_path)
        ctx = _Ctx()
        servers = await self._acquire_servers(abs_path, ToolKind.RENAME_SYMBOL, ctx)
        if not any(e for _, e in servers):
            return RenameSymbolResult(
                note=ctx.note(False, _no_cap_note(abs_path, "renameProvider")),
                warnings=ctx.warnings,
            )

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)

        async with self._lock_all([e for _, e in servers]):
            for spec, entry in servers:
                if entry is None:
                    continue
                server = entry.server
                try:
                    pos = await self._resolve_position(spec, server, rel, symbol, ctx)
                    if pos is None:
                        continue
                    if isinstance(pos, list):
                        return RenameSymbolResult(
                            note=f"Ambiguous symbol '{symbol}': {', '.join(pos)}",
                            warnings=ctx.warnings,
                        )
                    # ty (and other servers) require the document to be open via
                    # didOpen before accepting textDocument/rename requests.
                    with server.open_file(rel):
                        workspace_edit = await self._req(
                            spec.name,
                            "textDocument/rename",
                            lambda: server.raw_request(
                                "textDocument/rename",
                                {
                                    "textDocument": {
                                        "uri": pathlib.Path(abs_path).as_uri()
                                    },
                                    "position": {
                                        "line": pos[0],
                                        "character": pos[1],
                                    },
                                    "newName": new_name,
                                },
                            ),
                        )
                    if workspace_edit:
                        all_servers = [e2.server for _, e2 in servers if e2 is not None]
                        changed = apply_workspace_edit(workspace_edit, all_servers)
                        return RenameSymbolResult(
                            changed_files=changed,
                            note=ctx.note(True, ""),
                            warnings=ctx.warnings,
                        )
                except LspRequestError as exc:
                    ctx.fail(str(exc))
                except Exception as exc:
                    ctx.fail(f"Server '{spec.name}': {exc}")

        return RenameSymbolResult(
            note=ctx.note(False, f"Could not rename '{symbol}' in {abs_path}"),
            warnings=ctx.warnings,
        )

    @with_deadline(HoverResult)
    async def get_hover(self, file_path: str, line: int, character: int) -> HoverResult:
        abs_path = self._abs(file_path)
        ctx = _Ctx()
        bad = _check_position(line, character)
        if bad:
            return HoverResult(note=bad)
        servers = await self._acquire_servers(abs_path, ToolKind.GET_HOVER, ctx)
        if not any(e for _, e in servers):
            return HoverResult(
                note=ctx.note(False, _no_cap_note(abs_path, "hoverProvider")),
                warnings=ctx.warnings,
            )

        rel = self._rel(abs_path, infer_project_root(abs_path))
        params = {
            "textDocument": {"uri": pathlib.Path(abs_path).as_uri()},
            "position": {"line": line, "character": character},
        }
        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                async with entry.lock:
                    with server.open_file(rel):
                        raw = await self._req(
                            spec.name,
                            "textDocument/hover",
                            lambda: server.raw_request("textDocument/hover", params),
                        )
                text = _hover_text(raw)
                if text:
                    rng = None
                    r = raw.get("range") if isinstance(raw, dict) else None
                    if isinstance(r, dict):
                        rng = _location_from_range(abs_path, r)
                    return HoverResult(
                        contents=text,
                        range=rng,
                        note=ctx.note(True, ""),
                        warnings=ctx.warnings,
                    )
            except LspRequestError as exc:
                ctx.fail(str(exc))
            except Exception as exc:
                ctx.fail(f"Server '{spec.name}': {exc}")

        return HoverResult(
            note=ctx.note(
                False,
                f"No hover information at {abs_path}:{line}:{character} (0-based)",
            ),
            warnings=ctx.warnings,
        )

    @with_deadline(CallHierarchyResult)
    async def get_call_hierarchy(
        self,
        file_path: str,
        line: int,
        character: int,
        direction: str = "incoming",
    ) -> CallHierarchyResult:
        direction = (direction or "").strip().lower()
        if direction not in ("incoming", "outgoing"):
            return CallHierarchyResult(
                note=f"Invalid direction {direction!r}: use 'incoming' or 'outgoing'."
            )
        bad = _check_position(line, character)
        if bad:
            return CallHierarchyResult(direction=direction, note=bad)

        abs_path = self._abs(file_path)
        ctx = _Ctx()
        servers = await self._acquire_servers(
            abs_path, ToolKind.GET_CALL_HIERARCHY, ctx
        )
        if not any(e for _, e in servers):
            return CallHierarchyResult(
                direction=direction,
                note=ctx.note(False, _no_cap_note(abs_path, "callHierarchyProvider")),
                warnings=ctx.warnings,
            )

        rel = self._rel(abs_path, infer_project_root(abs_path))
        params = {
            "textDocument": {"uri": pathlib.Path(abs_path).as_uri()},
            "position": {"line": line, "character": character},
        }
        method = f"callHierarchy/{direction}Calls"
        key = "from" if direction == "incoming" else "to"

        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                async with entry.lock:
                    with server.open_file(rel):
                        prepared = await self._req(
                            spec.name,
                            "textDocument/prepareCallHierarchy",
                            lambda: server.raw_request(
                                "textDocument/prepareCallHierarchy", params
                            ),
                        )
                        if not prepared:
                            continue
                        roots = [_call_item(i) for i in prepared if isinstance(i, dict)]
                        edges: list[CallEdge] = []
                        answered = 0
                        for item in prepared:
                            if not isinstance(item, dict):
                                continue
                            try:
                                raw = await self._req(
                                    spec.name,
                                    method,
                                    lambda item=item: server.raw_request(
                                        method, {"item": item}
                                    ),
                                )
                            except LspRequestError as exc:
                                # Keep what the other items yielded; flag partial.
                                ctx.fail(f"{exc} (item '{item.get('name', '')}')")
                                continue
                            answered += 1
                            for call in raw or []:
                                if not isinstance(call, dict) or key not in call:
                                    continue
                                edges.append(
                                    CallEdge(
                                        item=_call_item(call[key]),
                                        call_sites=[
                                            _location_from_range(
                                                _uri_path(call[key].get("uri", "")), r
                                            )
                                            if direction == "incoming"
                                            else _location_from_range(abs_path, r)
                                            for r in call.get("fromRanges", [])
                                        ],
                                    )
                                )
                        if answered == 0:
                            continue  # every item failed: recorded in ctx
                return CallHierarchyResult(
                    roots=roots,
                    direction=direction,
                    calls=edges,
                    note=ctx.note(True, ""),
                    warnings=ctx.warnings,
                )
            except LspRequestError as exc:
                ctx.fail(str(exc))
            except Exception as exc:
                ctx.fail(f"Server '{spec.name}': {exc}")

        return CallHierarchyResult(
            direction=direction,
            note=ctx.note(
                False,
                f"No callable symbol at {abs_path}:{line}:{character} (0-based)",
            ),
            warnings=ctx.warnings,
        )

    @with_deadline(DiagnosticsResult)
    async def get_diagnostics_for_file(self, file_path: str) -> DiagnosticsResult:
        abs_path = self._abs(file_path)
        # For diagnostics we need all servers — both pull and push capable.
        specs = resolve(abs_path, self._config)
        if not specs:
            return DiagnosticsResult(note=f"No servers configured for {abs_path}")

        all_diags: list[Diagnostic] = []
        ctx = _Ctx()
        seen: set[tuple] = set()

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)
        uri = pathlib.Path(abs_path).as_uri()

        for spec in specs:
            entry = await self._acquire(spec, abs_path)
            if entry is None or entry.state != "ready":
                ctx.fail(self._start_failure(spec.name, abs_path))
                continue

            server = entry.server
            diags: list[dict[str, Any]] = []

            try:
                async with entry.lock:
                    # All diagnostic paths require the document to be open.
                    # open_file sends didOpen; the with-block keeps it open while
                    # we request or wait for diagnostics, then sends didClose.
                    with _diag_session(server, rel, uri):
                        if server.capabilities.supports(
                            ToolKind.GET_DIAGNOSTICS_FOR_FILE_PULL
                        ):
                            # Pull path: textDocument/diagnostic (LSP 3.17+)
                            raw = await self._req(
                                spec.name,
                                "textDocument/diagnostic",
                                lambda: server.raw_request(
                                    "textDocument/diagnostic",
                                    {"textDocument": {"uri": uri}},
                                ),
                            )
                            if isinstance(raw, dict):
                                diags = raw.get("items", [])
                            elif isinstance(raw, list):
                                diags = raw
                        else:
                            # Push path: wait for textDocument/publishDiagnostics
                            await server.wait_for_diagnostics(
                                uri,
                                timeout=self._diag_settle_timeout,
                                quiet_period=self._diag_quiet_period,
                            )
                            if uri in server.push_diagnostics:
                                diags = server.push_diagnostics[uri]
                            else:
                                ctx.hint(
                                    f"Server '{spec.name}' published no diagnostics "
                                    f"within {self._diag_settle_timeout:g}s — the result is "
                                    "unknown, not clean."
                                )
            except LspRequestError as exc:
                ctx.fail(str(exc))
                continue
            except Exception as exc:
                ctx.fail(f"Server '{spec.name}': {exc}")
                continue

            for d in diags:
                r = d.get("range", {})
                s = r.get("start", {})
                e = r.get("end", {})
                key = (
                    abs_path,
                    s.get("line", 0),
                    s.get("character", 0),
                    d.get("message", ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                all_diags.append(
                    Diagnostic(
                        source_server=spec.name,
                        path=abs_path,
                        line=s.get("line", 0),
                        character=s.get("character", 0),
                        end_line=e.get("line", 0),
                        end_character=e.get("character", 0),
                        severity=d.get("severity", 1),
                        message=d.get("message", ""),
                        code=d.get("code"),
                    )
                )

        note = ctx.note(bool(all_diags), "")
        if all_diags and ctx.hints and not note:
            # Some server may have more to say: don't let partial look complete.
            note = "Partial result — " + " ".join(ctx.hints)
        return DiagnosticsResult(
            diagnostics=all_diags,
            note=note,
            warnings=ctx.warnings,
        )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _no_cap_note(file_path: str, capability: str) -> str:
    ext = pathlib.Path(file_path).suffix or "unknown extension"
    return (
        f"No server configured for {ext!r} files advertises '{capability}'. "
        f"Add a server with this capability to file_handlers in your config."
    )


@contextlib.contextmanager
def _diag_session(server: GenericLanguageServer, rel: str, uri: str):
    """
    Open *rel* for a diagnostics request and arm a clean diagnostics slot.

    If this call performs the ``didOpen``, the reset runs right after it and
    before any ``await``, so only publishes that arrive after this call's open
    are accepted, and per-URI state is dropped on exit whatever happens (error,
    cancellation, call deadline).  If the document was already open (an outer
    open — no ``didOpen`` is sent, so no fresh publish is coming) the cache is
    left alone: it is current, and it belongs to the outer holder.
    """
    already_open = uri in server.open_file_buffers
    try:
        with server.open_file(rel):
            if not already_open:
                server.reset_diagnostics(uri)
            yield
    finally:
        if not already_open:
            server.forget_diagnostics(uri)


@contextlib.contextmanager
def _maybe_open(server: GenericLanguageServer, rel: str):
    """Open *rel* in *server* for the duration of the block; no-op when empty."""
    if not rel:
        yield
        return
    with server.open_file(rel):
        yield


def _position_from_symbols(
    raw: list[Any], symbol: str
) -> tuple[int, int] | list[str] | None:
    """
    Resolve *symbol* to a (line, char) position using document symbols.

    Returns the position on an unambiguous match, a list of candidate
    descriptions when the bare name is ambiguous, or ``None`` if not found.
    """
    matches = resolve_symbol(symbol, [_unifiedsym_to_dict(s) for s in raw])
    if not matches:
        return None
    if len(matches) > 1:
        # Spec: return candidates rather than guessing
        return [f"{m.name} (line {m.selection_range.start_line})" for m in matches]
    m = matches[0]
    return m.selection_range.start_line, m.selection_range.start_char


def _parse_kind(kind: str | int) -> int | None:
    """Map an LSP SymbolKind name or number to its integer, or None if invalid."""
    text = str(kind).strip()
    if text.isdigit():
        n = int(text)
        return n if n in SYMBOL_KINDS else None
    return _KIND_BY_NAME.get(text.lower())


def _normalise_workspace_symbols(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in raw if isinstance(raw, list) else []:
        loc = s.get("location", {})
        line = loc.get("range", {}).get("start", {}).get("line", 0)
        kind = s.get("kind", 0)
        out.append(
            {
                "name": s.get("name", ""),
                "kind": kind,
                "kind_name": SYMBOL_KINDS.get(kind, ""),
                "path": s.get("absolutePath") or loc.get("uri", ""),
                "line": line,
                "line_1based": line + 1,
            }
        )
    return out


def _rank_symbols(symbols: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Stable sort: exact name, case-insensitive exact, prefix, substring, rest."""
    q = query.lower()

    def tier(s: dict[str, Any]) -> int:
        name = s["name"]
        low = name.lower()
        if name == query:
            return 0
        if low == q:
            return 1
        if low.startswith(q):
            return 2
        if q in low:
            return 3
        return 4

    return sorted(symbols, key=tier)


def _check_position(line: int, character: int) -> str:
    if not isinstance(line, int) or not isinstance(character, int):
        return "line and character must be integers (0-based)."
    if line < 0 or character < 0:
        return f"line and character are 0-based and must be >= 0 (got {line}, {character})."
    return ""


def _uri_path(uri: str) -> str:
    if uri.startswith("file://"):
        return str(pathlib.Path(unquote(urlparse(uri).path)))
    return uri


def _location_from_range(path: str, r: dict[str, Any]) -> Location:
    s = r.get("start", {})
    e = r.get("end", {})
    return Location(
        path=path,
        line=s.get("line", 0),
        character=s.get("character", 0),
        end_line=e.get("line"),
        end_character=e.get("character"),
    )


def _call_item(item: dict[str, Any]) -> CallItem:
    r = item.get("selectionRange") or item.get("range") or {}
    return CallItem(
        name=item.get("name", ""),
        kind=item.get("kind", 0),
        detail=item.get("detail", "") or "",
        location=_location_from_range(_uri_path(item.get("uri", "")), r),
    )


def _hover_text(raw: Any) -> str:
    """Flatten a Hover result's ``contents`` (MarkupContent | MarkedString | list)."""
    if not isinstance(raw, dict):
        return ""

    def one(c: Any) -> str:
        if isinstance(c, str):
            return c
        if isinstance(c, dict):
            value = c.get("value", "")
            if "language" in c and value:
                return f"```{c['language']}\n{value}\n```"
            return value
        return ""

    contents = raw.get("contents")
    parts = contents if isinstance(contents, list) else [contents]
    return "\n\n".join(p for p in (one(c) for c in parts) if p).strip()


def _read_context_file(path: str, roots: list[str]) -> list[str] | None:
    """
    Read *path* for context only if, after resolving symlinks, it is a regular
    file under one of *roots*.  At most ``MAX_CONTEXT_BYTES`` are read.
    """
    try:
        resolved = pathlib.Path(path).resolve()
        if not any(
            resolved.is_relative_to(pathlib.Path(r).resolve())
            for r in roots
            if isinstance(r, str) and r
        ):
            return None
        if not resolved.is_file():
            return None
        with resolved.open("rb") as fh:
            data = fh.read(MAX_CONTEXT_BYTES)
        return data.decode("utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None


def _add_context(
    locations: list[Location], context_lines: int, roots: list[str]
) -> None:
    """Attach ``context`` (source lines around each location) when requested.

    Paths are server-supplied, so only files inside *roots* are read.
    """
    n = max(0, min(int(context_lines or 0), MAX_CONTEXT_LINES))
    if n == 0:
        return
    cache: dict[str, list[str] | None] = {}
    for loc in locations:
        path = _uri_path(loc.path)
        if path not in cache:
            cache[path] = _read_context_file(path, roots)
        lines = cache[path]
        if lines is None or loc.line >= len(lines):
            continue
        loc.context = "\n".join(lines[max(0, loc.line - n) : loc.line + n + 1])


def _unifiedsym_to_dict(sym: Any) -> dict[str, Any]:
    """Convert a multilspy UnifiedSymbolInformation to a plain dict."""
    if isinstance(sym, dict):
        return sym
    # multilspy returns TypedDict-like objects; access as dict
    return dict(sym)


def _symbols_to_nodes(raw: list[Any]) -> list[SymbolNode]:
    """Convert multilspy symbols to SymbolNode objects."""
    nodes: list[SymbolNode] = []
    for s in raw:
        d = _unifiedsym_to_dict(s)
        r = d.get("range") or d.get("location", {}).get("range", {})
        sr = d.get("selectionRange") or r
        if not r:
            continue
        nodes.append(
            SymbolNode(
                name=d.get("name", ""),
                kind=d.get("kind", 0),
                range_start_line=r.get("start", {}).get("line", 0),
                range_start_char=r.get("start", {}).get("character", 0),
                range_end_line=r.get("end", {}).get("line", 0),
                range_end_char=r.get("end", {}).get("character", 0),
                selection_start_line=sr.get("start", {}).get("line", 0),
                selection_start_char=sr.get("start", {}).get("character", 0),
                selection_end_line=sr.get("end", {}).get("line", 0),
                selection_end_char=sr.get("end", {}).get("character", 0),
                detail=d.get("detail", ""),
            )
        )
    return nodes


def _parse_locations(raw: Any, repo_root: str) -> list[Location]:
    """Parse a raw LSP location response into Location objects."""
    if not raw:
        return []
    items = raw if isinstance(raw, list) else [raw]
    locs: list[Location] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("targetUri") or item.get("uri", "")
        r = (
            item.get("targetSelectionRange")
            or item.get("targetRange")
            or item.get("range", {})
        )
        s = r.get("start", {})
        e = r.get("end", {})
        try:
            abs_path = (
                str(pathlib.Path(unquote(urlparse(uri).path)))
                if uri.startswith("file://")
                else uri
            )
        except Exception:
            abs_path = uri
        locs.append(
            Location(
                path=abs_path,
                line=s.get("line", 0),
                character=s.get("character", 0),
                end_line=e.get("line"),
                end_character=e.get("character"),
            )
        )
    return locs
