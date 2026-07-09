"""Central routing and dispatch for all eight MCP tools."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
from typing import Any
from urllib.parse import unquote, urlparse

from ..config.model import Config, ServerSpec
from ..config.resolver import resolve
from ..lsp.capabilities import ToolKind
from ..lsp.generic_server import GenericLanguageServer
from ..lsp.manager import ServerEntry, ServerManager, infer_project_root
from ..types import (
    DeclarationResult,
    Diagnostic,
    DiagnosticsResult,
    FindSymbolResult,
    ImplementationsResult,
    Location,
    RenameSymbolResult,
    ReplaceSymbolBodyResult,
    ReferencingSymbolsResult,
    SymbolNode,
    SymbolsOverviewResult,
)
from .edits import apply_edit, apply_workspace_edit
from .symbols import ResolvedSymbol, SymbolRange, resolve_symbol

logger = logging.getLogger(__name__)

# How long to wait for pushed diagnostics to settle
_DIAG_SETTLE_SECONDS: float = 2.0


class Dispatcher:
    """
    Routes tool calls to the correct language server(s) and normalises results.

    Semantics:
    - Navigation tools: first-wins (first server returning a non-empty result wins).
    - Diagnostics: merge from all servers.
    - No capable server: returns empty result with explanatory ``note``.
    - Partial failure: skips failed servers, records them in ``warnings``.
    """

    def __init__(self, config: Config, manager: ServerManager) -> None:
        self._config = config
        self._manager = manager

    # ------------------------------------------------------------------
    # Helper: acquire servers for a file
    # ------------------------------------------------------------------

    async def _acquire_servers(
        self, file_path: str, tool: ToolKind
    ) -> tuple[list[tuple[ServerSpec, ServerEntry | None]], list[str]]:
        """
        Resolve, acquire, and capability-filter servers for *file_path*.

        Returns (capable_servers, warnings) where each element of
        capable_servers is (spec, entry_or_None).
        """
        specs = resolve(file_path, self._config)
        results: list[tuple[ServerSpec, ServerEntry | None]] = []
        warnings: list[str] = []

        for spec in specs:
            entry = await self._manager.acquire(spec, file_path)
            if entry is None or entry.state != "ready":
                warnings.append(f"Server '{spec.name}' failed to start")
                results.append((spec, None))
                continue
            if not entry.server.capabilities.supports(tool):
                continue  # silently skip non-capable servers
            results.append((spec, entry))

        return results, warnings

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

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def get_symbols_overview(self, file_path: str) -> SymbolsOverviewResult:
        abs_path = self._abs(file_path)
        servers, warnings = await self._acquire_servers(
            abs_path, ToolKind.GET_SYMBOLS_OVERVIEW
        )
        if not any(e for _, e in servers):
            return SymbolsOverviewResult(
                note=_no_cap_note(abs_path, "documentSymbolProvider"), warnings=warnings
            )

        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            root = infer_project_root(abs_path)
            rel = self._rel(abs_path, root)
            try:
                raw, _ = await server.request_document_symbols(rel)
                nodes = _symbols_to_nodes(raw)
                if nodes:
                    return SymbolsOverviewResult(symbols=nodes, warnings=warnings)
            except Exception as exc:
                warnings.append(f"Server '{spec.name}': {exc}")

        return SymbolsOverviewResult(
            note=f"No symbols found in {abs_path}", warnings=warnings
        )

    async def find_symbol(self, query: str, file_path: str = "") -> FindSymbolResult:
        if not file_path:
            # No file context: try every configured server for workspace search.
            return await self._find_symbol_all_servers(query)

        # workspace/symbol doesn't need a file; use it for routing context only
        route_path = self._abs(file_path)
        servers, warnings = await self._acquire_servers(
            route_path, ToolKind.FIND_SYMBOL
        )
        if not any(e for _, e in servers):
            return FindSymbolResult(
                note=_no_cap_note(route_path, "workspaceSymbolProvider"),
                warnings=warnings,
            )

        for spec, entry in servers:
            if entry is None:
                continue
            try:
                raw = await entry.server.request_workspace_symbol(query)
                if raw:
                    symbols = [
                        {
                            "name": s.get("name", ""),
                            "kind": s.get("kind", 0),
                            "path": s.get("absolutePath")
                            or s.get("location", {}).get("uri", ""),
                            "line": s.get("location", {})
                            .get("range", {})
                            .get("start", {})
                            .get("line", 0),
                        }
                        for s in (raw if isinstance(raw, list) else [])
                    ]
                    return FindSymbolResult(symbols=symbols, warnings=warnings)
            except Exception as exc:
                warnings.append(f"Server '{spec.name}': {exc}")

        return FindSymbolResult(
            note=f"No symbols matching '{query}'", warnings=warnings
        )

    async def _find_symbol_all_servers(self, query: str) -> FindSymbolResult:
        """Try workspace/symbol on every server in the config (no file context)."""
        cwd = os.getcwd()
        warnings: list[str] = []
        for name, spec in self._config.servers.items():
            entry = await self._manager.acquire(spec, cwd)
            if entry is None or entry.state != "ready":
                warnings.append(f"Server '{name}' failed to start")
                continue
            if not entry.server.capabilities.supports(ToolKind.FIND_SYMBOL):
                continue
            try:
                raw = await entry.server.request_workspace_symbol(query)
                if raw:
                    symbols = [
                        {
                            "name": s.get("name", ""),
                            "kind": s.get("kind", 0),
                            "path": s.get("absolutePath")
                            or s.get("location", {}).get("uri", ""),
                            "line": s.get("location", {})
                            .get("range", {})
                            .get("start", {})
                            .get("line", 0),
                        }
                        for s in (raw if isinstance(raw, list) else [])
                    ]
                    return FindSymbolResult(symbols=symbols, warnings=warnings)
            except Exception as exc:
                warnings.append(f"Server '{name}': {exc}")

        return FindSymbolResult(
            note=f"No symbols matching '{query}' across all configured servers",
            warnings=warnings,
        )

    async def find_declaration(self, symbol: str, file_path: str) -> DeclarationResult:
        abs_path = self._abs(file_path)
        servers, warnings = await self._acquire_servers(
            abs_path, ToolKind.FIND_DECLARATION
        )
        if not any(e for _, e in servers):
            return DeclarationResult(
                note=_no_cap_note(abs_path, "definitionProvider"), warnings=warnings
            )

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)

        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                pos = await _resolve_symbol_position(server, rel, symbol)
                if pos is None:
                    continue
                if isinstance(pos, list):
                    return DeclarationResult(
                        note=f"Ambiguous symbol '{symbol}': {', '.join(pos)}",
                        warnings=warnings,
                    )
                raw = await server.request_definition(rel, pos[0], pos[1])
                if raw:
                    return DeclarationResult(
                        locations=[self._loc_from_multilspy(dict(l)) for l in raw],
                        warnings=warnings,
                    )
            except Exception as exc:
                warnings.append(f"Server '{spec.name}': {exc}")

        return DeclarationResult(
            note=f"No declaration found for '{symbol}'", warnings=warnings
        )

    async def find_implementations(
        self, symbol: str, file_path: str
    ) -> ImplementationsResult:
        abs_path = self._abs(file_path)
        servers, warnings = await self._acquire_servers(
            abs_path, ToolKind.FIND_IMPLEMENTATIONS
        )
        if not any(e for _, e in servers):
            return ImplementationsResult(
                note=_no_cap_note(abs_path, "implementationProvider"), warnings=warnings
            )

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)

        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                pos = await _resolve_symbol_position(server, rel, symbol)
                if pos is None:
                    continue
                if isinstance(pos, list):
                    return ImplementationsResult(
                        note=f"Ambiguous symbol '{symbol}': {', '.join(pos)}",
                        warnings=warnings,
                    )
                raw = await server.raw_request(
                    "textDocument/implementation",
                    {
                        "textDocument": {"uri": pathlib.Path(abs_path).as_uri()},
                        "position": {"line": pos[0], "character": pos[1]},
                    },
                )
                locations = _parse_locations(raw, server.repository_root_path)
                if locations:
                    return ImplementationsResult(locations=locations, warnings=warnings)
            except Exception as exc:
                warnings.append(f"Server '{spec.name}': {exc}")

        return ImplementationsResult(
            note=f"No implementations found for '{symbol}'", warnings=warnings
        )

    async def find_referencing_symbols(
        self, symbol: str, file_path: str
    ) -> ReferencingSymbolsResult:
        abs_path = self._abs(file_path)
        servers, warnings = await self._acquire_servers(
            abs_path, ToolKind.FIND_REFERENCING_SYMBOLS
        )
        if not any(e for _, e in servers):
            return ReferencingSymbolsResult(
                note=_no_cap_note(abs_path, "referencesProvider"), warnings=warnings
            )

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)

        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                pos = await _resolve_symbol_position(server, rel, symbol)
                if pos is None:
                    continue
                if isinstance(pos, list):
                    return ReferencingSymbolsResult(
                        note=f"Ambiguous symbol '{symbol}': {', '.join(pos)}",
                        warnings=warnings,
                    )
                raw = await server.request_references(rel, pos[0], pos[1])
                if raw:
                    return ReferencingSymbolsResult(
                        locations=[self._loc_from_multilspy(dict(l)) for l in raw],
                        warnings=warnings,
                    )
            except Exception as exc:
                warnings.append(f"Server '{spec.name}': {exc}")

        return ReferencingSymbolsResult(
            note=f"No references found for '{symbol}'", warnings=warnings
        )

    async def replace_symbol_body(
        self, symbol: str, new_body: str, file_path: str
    ) -> ReplaceSymbolBodyResult:
        abs_path = self._abs(file_path)
        servers, warnings = await self._acquire_servers(
            abs_path, ToolKind.REPLACE_SYMBOL_BODY
        )
        if not any(e for _, e in servers):
            return ReplaceSymbolBodyResult(
                note=_no_cap_note(abs_path, "documentSymbolProvider"), warnings=warnings
            )

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)

        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                raw, _ = await server.request_document_symbols(rel)
                matches = resolve_symbol(symbol, [_unifiedsym_to_dict(s) for s in raw])
                if not matches:
                    continue
                if len(matches) > 1:
                    names = [
                        f"{m.name} (line {m.full_range.start_line})" for m in matches
                    ]
                    return ReplaceSymbolBodyResult(
                        note=f"Ambiguous symbol '{symbol}': {', '.join(names)}",
                        warnings=warnings,
                    )
                sym = matches[0]
                all_servers = [e2.server for _, e2 in servers if e2 is not None]
                apply_edit(abs_path, sym.full_range, new_body, all_servers)
                return ReplaceSymbolBodyResult(success=True, warnings=warnings)
            except Exception as exc:
                warnings.append(f"Server '{spec.name}': {exc}")

        return ReplaceSymbolBodyResult(
            note=f"Symbol '{symbol}' not found in {abs_path}", warnings=warnings
        )

    async def rename_symbol(
        self, symbol: str, new_name: str, file_path: str
    ) -> RenameSymbolResult:
        abs_path = self._abs(file_path)
        servers, warnings = await self._acquire_servers(
            abs_path, ToolKind.RENAME_SYMBOL
        )
        if not any(e for _, e in servers):
            return RenameSymbolResult(
                note=_no_cap_note(abs_path, "renameProvider"), warnings=warnings
            )

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)

        for spec, entry in servers:
            if entry is None:
                continue
            server = entry.server
            try:
                pos = await _resolve_symbol_position(server, rel, symbol)
                if pos is None:
                    continue
                if isinstance(pos, list):
                    return RenameSymbolResult(
                        note=f"Ambiguous symbol '{symbol}': {', '.join(pos)}",
                        warnings=warnings,
                    )
                # ty (and other servers) require the document to be open via
                # didOpen before accepting textDocument/rename requests.
                with server.open_file(rel):
                    workspace_edit = await server.raw_request(
                        "textDocument/rename",
                        {
                            "textDocument": {"uri": pathlib.Path(abs_path).as_uri()},
                            "position": {"line": pos[0], "character": pos[1]},
                            "newName": new_name,
                        },
                    )
                if workspace_edit:
                    all_servers = [e2.server for _, e2 in servers if e2 is not None]
                    changed = apply_workspace_edit(workspace_edit, all_servers)
                    return RenameSymbolResult(changed_files=changed, warnings=warnings)
            except Exception as exc:
                warnings.append(f"Server '{spec.name}': {exc}")

        return RenameSymbolResult(
            note=f"Could not rename '{symbol}' in {abs_path}", warnings=warnings
        )

    async def get_diagnostics_for_file(self, file_path: str) -> DiagnosticsResult:
        abs_path = self._abs(file_path)
        # For diagnostics we need all servers — both pull and push capable.
        specs = resolve(abs_path, self._config)
        if not specs:
            return DiagnosticsResult(note=f"No servers configured for {abs_path}")

        all_diags: list[Diagnostic] = []
        warnings: list[str] = []
        seen: set[tuple] = set()

        root = infer_project_root(abs_path)
        rel = self._rel(abs_path, root)
        uri = pathlib.Path(abs_path).as_uri()

        for spec in specs:
            entry = await self._manager.acquire(spec, abs_path)
            if entry is None or entry.state != "ready":
                warnings.append(f"Server '{spec.name}' failed to start")
                continue

            server = entry.server
            diags: list[dict[str, Any]] = []

            try:
                # All diagnostic paths require the document to be open.
                # open_file sends didOpen; the with-block keeps it open while
                # we request or wait for diagnostics, then sends didClose.
                with server.open_file(rel):
                    if server.capabilities.supports(
                        ToolKind.GET_DIAGNOSTICS_FOR_FILE_PULL
                    ):
                        # Pull path: textDocument/diagnostic (LSP 3.17+)
                        raw = await server.raw_request(
                            "textDocument/diagnostic",
                            {"textDocument": {"uri": uri}},
                        )
                        if isinstance(raw, dict):
                            diags = raw.get("items", [])
                        elif isinstance(raw, list):
                            diags = raw
                    else:
                        # Push path: wait for textDocument/publishDiagnostics
                        await server.wait_for_diagnostics(
                            uri, timeout=_DIAG_SETTLE_SECONDS
                        )
                        diags = server.push_diagnostics.get(uri, [])
            except Exception as exc:
                warnings.append(f"Server '{spec.name}': {exc}")
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

        return DiagnosticsResult(diagnostics=all_diags, warnings=warnings)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _no_cap_note(file_path: str, capability: str) -> str:
    ext = pathlib.Path(file_path).suffix or "unknown extension"
    return (
        f"No server configured for {ext!r} files advertises '{capability}'. "
        f"Add a server with this capability to file_handlers in your config."
    )


async def _resolve_symbol_position(
    server: GenericLanguageServer, rel_path: str, symbol: str
) -> tuple[int, int] | list[str] | None:
    """
    Resolve *symbol* to an (line, char) position using document symbols.

    Returns:
    - ``(line, char)`` on unambiguous match.
    - ``list[str]`` of candidate descriptions when the bare name is ambiguous.
    - ``None`` if not found or on error.
    """
    try:
        raw, _ = await server.request_document_symbols(rel_path)
        matches = resolve_symbol(symbol, [_unifiedsym_to_dict(s) for s in raw])
        if not matches:
            return None
        if len(matches) > 1:
            # Spec: return candidates rather than guessing
            return [f"{m.name} (line {m.selection_range.start_line})" for m in matches]
        m = matches[0]
        return m.selection_range.start_line, m.selection_range.start_char
    except Exception:
        return None


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
