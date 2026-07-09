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
from .types import (
    DeclarationResult,
    DiagnosticsResult,
    FindSymbolResult,
    ImplementationsResult,
    RenameSymbolResult,
    ReplaceSymbolBodyResult,
    ReferencingSymbolsResult,
    SymbolsOverviewResult,
)

logger = logging.getLogger(__name__)

_manager: ServerManager | None = None
_dispatcher: Dispatcher | None = None


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
            "Returns the symbol tree for the file."
        )
    )
    async def get_symbols_overview(file_path: str) -> dict[str, Any]:
        """file_path: absolute path to the source file."""
        result = await dispatcher.get_symbols_overview(file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Search for a symbol by name across the workspace. "
            "Returns a list of matching symbols with their locations."
        )
    )
    async def find_symbol(query: str, file_path: str = "") -> dict[str, Any]:
        """
        query: symbol name to search for.
        file_path: optional context file to select which servers to query.
        """
        result = await dispatcher.find_symbol(query, file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Find the declaration (definition) of a symbol. "
            "Use a name-path like 'MyClass/my_method' for nested symbols."
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
            "Returns a list of locations where the symbol is implemented."
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
            "Returns a list of locations where the symbol is used."
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
            "The symbol is located by name; use 'MyClass/my_method' for nested symbols. "
            "The replacement text must be syntactically valid for the language."
        )
    )
    async def replace_symbol_body(
        symbol: str, new_body: str, file_path: str
    ) -> dict[str, Any]:
        """
        symbol: symbol name or name-path.
        new_body: full replacement source text (including signature line).
        file_path: absolute path to the file containing the symbol.
        """
        result = await dispatcher.replace_symbol_body(symbol, new_body, file_path)
        return _to_dict(result)

    @mcp.tool(
        description=(
            "Rename a symbol across the entire workspace. "
            "Uses the language server's rename capability to produce a workspace-wide edit."
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
            "from all configured language servers. Results are merged and tagged by source server."
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
