"""MCP server: registers eight tools as thin async adapters."""

from __future__ import annotations

import dataclasses
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .dispatch.router import Dispatcher
from .lsp.manager import ServerManager

logger = logging.getLogger(__name__)


def build_app(config_path: str | None = None) -> FastMCP:
    """
    Create the FastMCP application with all eight tools registered.

    *config_path* overrides the default ``~/.config/lsp-mcp/config.yml``.

    The ``ServerManager`` and ``Dispatcher`` are created at build time;
    LSP servers are started lazily on the first relevant tool call.
    """
    from pathlib import Path

    cfg = load_config(Path(config_path) if config_path else None)
    manager = ServerManager()
    dispatcher = Dispatcher(config=cfg, manager=manager)

    @asynccontextmanager
    async def lifespan(app: FastMCP):  # type: ignore[type-arg]
        manager.start_eviction_loop()
        try:
            yield {}
        finally:
            await manager.aclose()

    mcp: FastMCP = FastMCP("lsp-mcp", lifespan=lifespan)

    # ------------------------------------------------------------------
    # Tool adapters — thin wrappers that delegate to the Dispatcher.
    # ------------------------------------------------------------------

    @mcp.tool(
        description=(
            "List all symbols (classes, functions, variables) in a source file. "
            "file_path must be an absolute filesystem path. "
            "Returns a tree of SymbolNode objects, each with: name, kind (LSP SymbolKind "
            "integer — 5=Class, 6=Method, 12=Function, 13=Variable, etc.), "
            "range_start_line/char, range_end_line/char, selection_start/end, detail, "
            "and children (nested symbols). "
            "Check the note field first — a non-empty note means no capable server was "
            "found for this file type and the symbols list will be empty."
        )
    )
    async def get_symbols_overview(file_path: str) -> dict[str, Any]:
        """file_path: absolute path to the source file."""
        result = await dispatcher.get_symbols_overview(file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Search for a symbol by name across the workspace. "
            "Returns workspace-wide matches — not filtered to a single file. "
            "file_path, when provided, is routing context only: it selects which language "
            "server to query based on the file extension; the search itself is workspace-wide. "
            "Omitting file_path queries all configured servers. "
            "Returns a list of raw workspace symbol dicts (name, kind, location.uri, "
            "location.range). "
            "Check the note field first — a non-empty note means no capable server was found."
        )
    )
    async def find_symbol(query: str, file_path: str = "") -> dict[str, Any]:
        """
        query: symbol name to search for.
        file_path: optional absolute path used only to select the language server to query;
                   the search scope is workspace-wide regardless.
        """
        result = await dispatcher.find_symbol(query, file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Find the declaration (definition) of a symbol. "
            "symbol is a name or slash-separated name-path for nested symbols "
            "(e.g. 'MyClass/my_method'). "
            "file_path must be an absolute path to the file containing the symbol — "
            "it is used both to locate the symbol and to select the language server. "
            "Returns a list of Location objects (path, line, character — all 0-based). "
            "Check the note field first — a non-empty note means the symbol was not found "
            "or no capable server is configured."
        )
    )
    async def find_declaration(symbol: str, file_path: str) -> dict[str, Any]:
        """
        symbol: symbol name or name-path (e.g. 'MyClass/my_method').
        file_path: absolute path to the file containing the symbol.
        """
        result = await dispatcher.find_declaration(symbol, file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Find implementations of an interface or abstract method. "
            "symbol is a name or slash-separated name-path for nested symbols. "
            "file_path must be an absolute path to the file containing the symbol. "
            "Returns a list of Location objects (path, line, character — all 0-based) "
            "pointing to each concrete implementation. "
            "Check the note field first — a non-empty note means the symbol was not found "
            "or no capable server is configured."
        )
    )
    async def find_implementations(symbol: str, file_path: str) -> dict[str, Any]:
        """
        symbol: symbol name or name-path.
        file_path: absolute path to the file containing the symbol.
        """
        result = await dispatcher.find_implementations(symbol, file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Find all references to a symbol across the workspace. "
            "symbol is a name or slash-separated name-path for nested symbols. "
            "file_path must be an absolute path to the file containing the symbol. "
            "Returns a list of Location objects (path, line, character — all 0-based) "
            "for every call-site or usage of the symbol. "
            "Check the note field first — a non-empty note means the symbol was not found "
            "or no capable server is configured."
        )
    )
    async def find_referencing_symbols(symbol: str, file_path: str) -> dict[str, Any]:
        """
        symbol: symbol name or name-path.
        file_path: absolute path to the file containing the symbol.
        """
        result = await dispatcher.find_referencing_symbols(symbol, file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Replace the body of a symbol (function, class, method) with new source text. "
            "symbol is a name or slash-separated name-path for nested symbols "
            "(e.g. 'MyClass/my_method'). "
            "file_path must be an absolute path to the file containing the symbol. "
            "new_body must include the full signature line, for example: "
            "'def greet(name):\\n    return f\"Hello, {name}\"'. "
            "DECORATOR RULE — do not include decorators in new_body unless you intend to "
            "replace them: if new_body does NOT start with '@', existing decorators are "
            "automatically preserved. If new_body starts with '@', the full "
            "symbol range (including all existing decorators) is replaced and you are "
            "responsible for including every decorator you want to keep. "
            "To add or change decorators, first call get_symbols_overview to read the "
            "current decorators, then supply a new_body that starts with '@' and includes "
            "all desired decorators explicitly. "
            "Returns success=True on success. "
            "Check the note field first — a non-empty note means the symbol was not found "
            "or the name was ambiguous."
        )
    )
    async def replace_symbol_body(
        symbol: str, new_body: str, file_path: str
    ) -> dict[str, Any]:
        """
        symbol: symbol name or name-path.
        new_body: full replacement source text including the signature line.
                  Start with 'def'/'class'/'async def' to preserve decorators;
                  start with '@' to take ownership of all decorators.
        file_path: absolute path to the file containing the symbol.
        """
        result = await dispatcher.replace_symbol_body(symbol, new_body, file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Rename a symbol across the entire workspace. "
            "symbol is a name or slash-separated name-path for nested symbols. "
            "file_path must be an absolute path to the file containing the symbol. "
            "Uses the language server's rename capability to update every reference "
            "across all files in the workspace atomically. "
            "Returns a list of changed file paths. "
            "Check the note field first — a non-empty note means the rename is not "
            "supported, the symbol was not found, or the language server declined the rename."
        )
    )
    async def rename_symbol(
        symbol: str, new_name: str, file_path: str
    ) -> dict[str, Any]:
        """
        symbol: symbol name or name-path.
        new_name: new name for the symbol.
        file_path: absolute path to the file containing the symbol.
        """
        result = await dispatcher.rename_symbol(symbol, new_name, file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Get diagnostics (errors, warnings, hints) for a source file "
            "from all configured language servers. "
            "file_path must be an absolute filesystem path. "
            "Results are merged and de-duplicated across servers; each Diagnostic includes "
            "source_server (which server reported it), path, line, character, end_line, "
            "end_character (all 0-based), severity (1=Error 2=Warning 3=Info 4=Hint), "
            "message, and optional code. "
            "Check the note field first — a non-empty note means no configured server "
            "provided diagnostics for this file type."
        )
    )
    async def get_diagnostics_for_file(file_path: str) -> dict[str, Any]:
        """file_path: absolute path to the source file."""
        result = await dispatcher.get_diagnostics_for_file(file_path)
        return _to_dict(result)

    return mcp


def _to_dict(result: object) -> dict[str, Any]:
    """Convert a dataclass result to a JSON-serialisable dict."""
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return {
            k: _to_dict(v)
            if dataclasses.is_dataclass(v)
            else [_to_dict(i) for i in v]
            if isinstance(v, list)
            else v
            for k, v in dataclasses.asdict(result).items()
        }
    return result  # type: ignore[return-value]
