"""Load and validate the lsp-mcp config file."""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

import yaml

from .model import Config, FileHandler, ServerSpec


def _config_path() -> Path:
    """Return the config file path, honouring $XDG_CONFIG_HOME."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    return base / "lsp-mcp" / "config.yml"


def load_config(path: Path | None = None) -> Config:
    """
    Load and validate the configuration file.

    Raises SystemExit with an actionable message on fatal errors:
    - file missing or unreadable
    - YAML parse error
    - validation failure (undefined server ref, empty command, bad glob)
    """
    config_path = path or _config_path()

    # --- read ---
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _fatal(
            f"Config file not found: {config_path}\n"
            "Create it with at least one entry under 'servers' and 'file_handlers'.\n"
            "Example:\n"
            "  servers:\n"
            "    ty:\n"
            "      command: [uvx, ty, server]\n"
            "  file_handlers:\n"
            "    '*.py':\n"
            "      - server: ty\n"
        )
    except OSError as exc:
        _fatal(f"Cannot read config file {config_path}: {exc}")

    # --- parse ---
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        _fatal(f"Config file {config_path} is not valid YAML: {exc}")

    if not isinstance(data, dict):
        _fatal(f"Config file {config_path} must be a YAML mapping at the top level")

    # --- build server specs ---
    raw_servers = data.get("servers") or {}
    if not isinstance(raw_servers, dict):
        _fatal(f"Config 'servers' must be a mapping; got {type(raw_servers).__name__}")

    servers: dict[str, ServerSpec] = {}
    for name, spec in raw_servers.items():
        if not isinstance(spec, dict):
            _fatal(f"Server '{name}': value must be a mapping")
        cmd = spec.get("command")
        if not cmd:
            _fatal(f"Server '{name}': 'command' is required and must not be empty")
        if not isinstance(cmd, list) or not all(isinstance(c, str) for c in cmd):
            _fatal(f"Server '{name}': 'command' must be a list of strings")
        init_opts = spec.get("initialization_options")
        try:
            servers[name] = ServerSpec(
                name=name,
                command=tuple(cmd),
                initialization_options=init_opts,
            )
        except ValueError as exc:
            _fatal(str(exc))

    # --- build file handlers ---
    raw_handlers = data.get("file_handlers") or {}
    if not isinstance(raw_handlers, dict):
        _fatal(
            f"Config 'file_handlers' must be a mapping; got {type(raw_handlers).__name__}"
        )

    handlers: list[FileHandler] = []
    for pattern, handler_list in raw_handlers.items():
        # Validate glob pattern compiles
        try:
            fnmatch.translate(pattern)
        except (ValueError, TypeError) as exc:
            _fatal(f"file_handlers pattern '{pattern}' is not a valid glob: {exc}")

        if not isinstance(handler_list, list) or not handler_list:
            _fatal(f"file_handlers['{pattern}']: value must be a non-empty list")

        server_names: list[str] = []
        for entry in handler_list:
            if not isinstance(entry, dict) or "server" not in entry:
                _fatal(
                    f"file_handlers['{pattern}']: each entry must be a mapping with a 'server' key"
                )
            sname = entry["server"]
            if sname not in servers:
                _fatal(
                    f"file_handlers['{pattern}']: server '{sname}' is not declared under 'servers'"
                )
            server_names.append(sname)

        handlers.append(FileHandler(pattern=pattern, server_names=tuple(server_names)))

    return Config(servers=servers, handlers=tuple(handlers))


def _fatal(message: str) -> None:
    """Print an actionable error and exit non-zero."""
    print(f"lsp-mcp: configuration error: {message}", file=sys.stderr)
    sys.exit(1)
