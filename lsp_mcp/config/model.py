"""Config data model for lsp-mcp."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServerSpec:
    """Specification for a single language server."""

    name: str
    command: tuple[str, ...]
    """Command to launch the server, e.g. ('uvx', 'ty', 'server')."""
    initialization_options: dict | None = None
    """Reserved for v1; not consumed."""

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError(f"Server '{self.name}': command must not be empty")


@dataclass(frozen=True)
class FileHandler:
    """Maps a glob pattern to an ordered list of server names."""

    pattern: str
    server_names: tuple[str, ...]
    """Server names in dispatch priority order."""


@dataclass(frozen=True)
class Config:
    """Parsed and validated lsp-mcp configuration."""

    servers: dict[str, ServerSpec]
    """All declared servers keyed by name."""
    handlers: tuple[FileHandler, ...]
    """File handlers in file order (preserves priority)."""
