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
            "Use before reading an entire file to navigate its structure: lists all "
            "classes, functions, methods, and variables in one call — faster and more "
            "precise than grep for discovering what a file contains. "
            "file_path must be an absolute filesystem path. "
            "Returns a tree of SymbolNode objects with name, kind (LSP SymbolKind integer — "
            "5=Class, 6=Method, 12=Function, 13=Variable, etc.), "
            "range_start_line/char, range_end_line/char (full body), "
            "selection_start_line/char, selection_end_line/char (identifier), "
            "detail, and children (nested symbols); "
            "check the note field first — a non-empty note means no capable server was "
            "found for this file type and the symbols list will be empty."
        )
    )
    async def get_symbols_overview(file_path: str) -> dict[str, Any]:
        """file_path: absolute path to the source file."""
        result = await dispatcher.get_symbols_overview(file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Use to locate a symbol by name across the whole workspace without grepping: "
            "returns exact file, line, and kind for every match the language server indexes. "
            "file_path, when provided, is routing context only — it selects the language "
            "server by file extension; the search scope is always workspace-wide regardless; "
            "omitting file_path queries all configured servers. "
            "Returns a list of workspace symbol dicts (name, kind, location.uri, "
            "location.range); check the note field first — a non-empty note means no "
            "capable server was found."
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
            "Use when navigating to where a symbol is defined — more reliable than text "
            "search because the language server resolves overloaded names, aliases, and "
            "re-exports correctly. "
            "symbol is a name or slash-separated name-path for nested symbols "
            "(e.g. 'MyClass/my_method'); file_path must be an absolute path to the file "
            "containing the symbol and is used both to locate it and to select the language "
            "server. "
            "Returns a list of Location objects (path, line, character — all 0-based); "
            "check the note field first — a non-empty note means the symbol was not found "
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
            "Use before modifying an interface or abstract method to discover every "
            "concrete implementation that will be affected — more complete than grep "
            "because the language server resolves indirect inheritance chains. "
            "symbol is a name or slash-separated name-path for nested symbols; "
            "file_path must be an absolute path to the file containing the symbol. "
            "Returns Location objects (path, line, character — all 0-based) for each "
            "implementation; check the note field first — a non-empty note means the "
            "symbol was not found or no capable server is configured."
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
            "Use before renaming or deleting a symbol to see every call-site and usage "
            "across the workspace — more exhaustive than grep because the language server "
            "resolves imports and aliased names that text search would miss. "
            "symbol is a name or slash-separated name-path for nested symbols; "
            "file_path must be an absolute path to the file containing the symbol. "
            "Returns Location objects (path, line, character — all 0-based) for every "
            "reference; check the note field first — a non-empty note means the symbol "
            "was not found or no capable server is configured."
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
            "Use to replace a function, method, or class body without touching "
            "surrounding code — safer and more precise than rewriting the "
            "entire file. "
            "symbol is a name or slash-separated name-path (e.g. 'MyClass/my_method'); "
            "file_path must be an absolute path; new_body must include the full signature "
            "line (e.g. 'def greet(name):\\n    return f\"Hello, {name}\"'). "
            "DECORATOR RULE: if new_body does NOT start with '@', existing decorators are "
            "preserved automatically; if new_body starts with '@', the full symbol range "
            "(including all decorators) is replaced — call get_symbols_overview first to "
            "read current decorators before supplying '@'-prefixed new_body. "
            "Returns success=True; check the note field first — a non-empty note means "
            "the symbol was not found or the name was ambiguous."
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
            "Use instead of find-and-replace when renaming a symbol: the language server "
            "updates every reference across all workspace files atomically, including "
            "imports and aliased usages that text search would miss. "
            "symbol is a name or slash-separated name-path for nested symbols; "
            "file_path must be an absolute "
            "path to the file containing the symbol. "
            "Returns a list of changed file paths; check the note field first — a "
            "non-empty note means the rename is not supported, the symbol was not found, "
            "or the language server declined."
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
            "Use immediately after editing a file to surface type errors, linting issues, "
            "and language-server hints without running a separate build step. "
            "file_path must be an absolute filesystem path; results are merged and "
            "de-duplicated across all configured servers and include source_server, path, "
            "line, character, end_line, end_character (all 0-based), severity "
            "(1=Error 2=Warning 3=Info 4=Hint), message, and optional code. "
            "check the note field first — a non-empty note means no configured server "
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
