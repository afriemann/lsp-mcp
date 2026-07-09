"""Tests for config layer: model, loader, validation, resolver."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lsp_mcp.config.loader import load_config
from lsp_mcp.config.model import Config, FileHandler, ServerSpec
from lsp_mcp.config.resolver import resolve


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Loader: success
# ---------------------------------------------------------------------------


def test_load_simple_config(tmp_path: Path) -> None:
    cfg_file = _write_config(
        tmp_path,
        """
        servers:
          ty:
            command: [uvx, ty, server]
          ruff:
            command: [ruff, server]
        file_handlers:
          "*.py":
            - server: ty
            - server: ruff
        """,
    )
    cfg = load_config(cfg_file)

    assert "ty" in cfg.servers
    assert cfg.servers["ty"].command == ("uvx", "ty", "server")
    assert "ruff" in cfg.servers

    assert len(cfg.handlers) == 1
    handler = cfg.handlers[0]
    assert handler.pattern == "*.py"
    assert handler.server_names == ("ty", "ruff")


def test_load_multiple_patterns(tmp_path: Path) -> None:
    cfg_file = _write_config(
        tmp_path,
        """
        servers:
          gopls:
            command: [gopls]
          ts:
            command: [npx, typescript-language-server, --stdio]
        file_handlers:
          "*.go":
            - server: gopls
          "*.ts":
            - server: ts
        """,
    )
    cfg = load_config(cfg_file)
    assert len(cfg.handlers) == 2
    assert cfg.handlers[0].pattern == "*.go"
    assert cfg.handlers[1].pattern == "*.ts"


# ---------------------------------------------------------------------------
# Loader: fatal errors (all exit via SystemExit)
# ---------------------------------------------------------------------------


def test_missing_config_file_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        load_config(tmp_path / "nonexistent.yml")


def test_invalid_yaml_exits(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yml"
    cfg_file.write_text("{\nbroken yaml: [unclosed", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_config(cfg_file)


def test_undefined_server_reference_exits(tmp_path: Path) -> None:
    cfg_file = _write_config(
        tmp_path,
        """
        servers:
          ty:
            command: [uvx, ty, server]
        file_handlers:
          "*.py":
            - server: nonexistent
        """,
    )
    with pytest.raises(SystemExit):
        load_config(cfg_file)


def test_empty_command_exits(tmp_path: Path) -> None:
    cfg_file = _write_config(
        tmp_path,
        """
        servers:
          bad:
            command: []
        file_handlers:
          "*.py":
            - server: bad
        """,
    )
    with pytest.raises(SystemExit):
        load_config(cfg_file)


def test_missing_command_exits(tmp_path: Path) -> None:
    cfg_file = _write_config(
        tmp_path,
        """
        servers:
          bad:
            note: no command here
        file_handlers:
          "*.py":
            - server: bad
        """,
    )
    with pytest.raises(SystemExit):
        load_config(cfg_file)


# ---------------------------------------------------------------------------
# ServerSpec model
# ---------------------------------------------------------------------------


def test_serverspec_empty_command_raises() -> None:
    with pytest.raises(ValueError, match="command must not be empty"):
        ServerSpec(name="test", command=())


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _make_config(*patterns_and_servers: tuple[str, list[str]]) -> Config:
    """Build a Config from (pattern, [server_names]) pairs."""
    servers: dict[str, ServerSpec] = {}
    handlers: list[FileHandler] = []
    for pattern, snames in patterns_and_servers:
        for name in snames:
            if name not in servers:
                servers[name] = ServerSpec(name=name, command=(name,))
        handlers.append(FileHandler(pattern=pattern, server_names=tuple(snames)))
    return Config(servers=servers, handlers=tuple(handlers))


def test_resolve_single_match() -> None:
    cfg = _make_config(("*.py", ["ty"]))
    result = resolve("src/app.py", cfg)
    assert [s.name for s in result] == ["ty"]


def test_resolve_no_match() -> None:
    cfg = _make_config(("*.py", ["ty"]))
    result = resolve("src/main.go", cfg)
    assert result == []


def test_resolve_basename_matching() -> None:
    """*.py should match /long/path/to/file.py"""
    cfg = _make_config(("*.py", ["ty"]))
    result = resolve("/long/path/to/file.py", cfg)
    assert [s.name for s in result] == ["ty"]


def test_resolve_multi_match_merge() -> None:
    """Two handlers matching the same file produce a merged ordered list."""
    cfg = _make_config(("*.py", ["ty"]), ("*.py", ["ruff"]))
    result = resolve("app.py", cfg)
    assert [s.name for s in result] == ["ty", "ruff"]


def test_resolve_dedup_first_occurrence() -> None:
    """A server appearing in multiple handlers is included once (first occurrence)."""
    cfg = _make_config(("*.py", ["ty", "ruff"]), ("*.py", ["ty"]))
    result = resolve("app.py", cfg)
    names = [s.name for s in result]
    assert names == ["ty", "ruff"]  # ty appears once, first occurrence preserved


def test_resolve_no_match_returns_empty() -> None:
    cfg = _make_config(("*.ts", ["ts"]))
    assert resolve("app.py", cfg) == []


def test_resolve_path_style_pattern() -> None:
    """A pattern with '/' is matched against the full path."""
    cfg = _make_config(("src/*.py", ["ty"]))
    assert resolve("src/app.py", cfg) != []
    assert resolve("lib/app.py", cfg) == []
