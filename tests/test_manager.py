"""Tests for ServerManager and infer_project_root."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lsp_mcp.config.model import ServerSpec
from lsp_mcp.lsp.manager import ServerManager, infer_project_root


# ---------------------------------------------------------------------------
# infer_project_root
# ---------------------------------------------------------------------------


def test_infer_project_root_finds_git(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    src = tmp_path / "src" / "app.py"
    src.parent.mkdir()
    src.touch()
    assert infer_project_root(str(src)) == str(tmp_path)


def test_infer_project_root_finds_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").touch()
    f = tmp_path / "lib" / "mod.py"
    f.parent.mkdir()
    f.touch()
    assert infer_project_root(str(f)) == str(tmp_path)


def test_infer_project_root_fallback(tmp_path: Path) -> None:
    f = tmp_path / "standalone.py"
    f.touch()
    # No marker in tmp_path or parents (tmp_path itself has no marker)
    result = infer_project_root(str(f))
    # Fallback to file's directory
    assert result == str(tmp_path)


def test_infer_project_root_go_mod(tmp_path: Path) -> None:
    (tmp_path / "go.mod").touch()
    f = tmp_path / "pkg" / "main.go"
    f.parent.mkdir()
    f.touch()
    assert infer_project_root(str(f)) == str(tmp_path)


# ---------------------------------------------------------------------------
# ServerManager pool
# ---------------------------------------------------------------------------


def _make_spec(name: str = "ty") -> ServerSpec:
    return ServerSpec(name=name, command=(name, "server"))


def _mock_server(root: str = "/tmp/root") -> MagicMock:
    """Create a mock GenericLanguageServer."""
    server = MagicMock()
    server.repository_root_path = root
    server.capabilities = MagicMock()
    server.open_file_buffers = {}

    # start_server is an async context manager
    @asynccontextmanager
    async def _start_server():
        yield server

    server.start_server = _start_server
    return server


@pytest.mark.asyncio
async def test_acquire_starts_server(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").touch()
    f = tmp_path / "app.py"
    f.touch()

    manager = ServerManager()

    mock_server = _mock_server(str(tmp_path))
    with patch("lsp_mcp.lsp.manager.GenericLanguageServer", return_value=mock_server):
        entry = await manager.acquire(_make_spec(), str(f))

    assert entry is not None
    assert entry.state == "ready"
    await manager.aclose()


@pytest.mark.asyncio
async def test_acquire_reuses_running_server(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    f.touch()

    manager = ServerManager()
    mock_server = _mock_server(str(tmp_path))
    call_count = 0

    original_start = mock_server.start_server

    @asynccontextmanager
    async def _counted_start():
        nonlocal call_count
        call_count += 1
        async with original_start():
            yield mock_server

    mock_server.start_server = _counted_start

    with patch("lsp_mcp.lsp.manager.GenericLanguageServer", return_value=mock_server):
        entry1 = await manager.acquire(_make_spec(), str(f))
        entry2 = await manager.acquire(_make_spec(), str(f))

    # Server was started exactly once
    assert call_count == 1
    assert entry1 is entry2
    await manager.aclose()


@pytest.mark.asyncio
async def test_acquire_separate_roots(tmp_path: Path) -> None:
    root1 = tmp_path / "proj1"
    root2 = tmp_path / "proj2"
    root1.mkdir()
    root2.mkdir()
    (root1 / "pyproject.toml").touch()
    (root2 / "pyproject.toml").touch()
    f1 = root1 / "a.py"
    f2 = root2 / "b.py"
    f1.touch()
    f2.touch()

    manager = ServerManager()
    servers_created: list[MagicMock] = []

    def _make_mock(*args, **kwargs) -> MagicMock:
        root = (
            kwargs.get("repository_root_path") or args[1] if len(args) > 1 else "/tmp"
        )
        mock = _mock_server(root)
        servers_created.append(mock)
        return mock

    with patch("lsp_mcp.lsp.manager.GenericLanguageServer", side_effect=_make_mock):
        entry1 = await manager.acquire(_make_spec(), str(f1))
        entry2 = await manager.acquire(_make_spec(), str(f2))

    # Two distinct entries for different roots
    assert entry1 is not entry2
    assert len(servers_created) == 2
    await manager.aclose()


@pytest.mark.asyncio
async def test_aclose_closes_all_entries(tmp_path: Path) -> None:
    f = tmp_path / "app.py"
    f.touch()

    manager = ServerManager()
    mock_server = _mock_server(str(tmp_path))
    closed = []

    @asynccontextmanager
    async def _start():
        try:
            yield mock_server
        finally:
            closed.append(True)

    mock_server.start_server = _start

    with patch("lsp_mcp.lsp.manager.GenericLanguageServer", return_value=mock_server):
        await manager.acquire(_make_spec(), str(f))

    await manager.aclose()
    assert closed  # server was closed


@pytest.mark.asyncio
async def test_lru_eviction_at_cap(tmp_path: Path) -> None:
    """Pool should evict LRU entries when cap is reached."""
    manager = ServerManager(max_servers=2, idle_ttl=9999)

    created: list[str] = []

    def _make_mock(*args, **kwargs):
        root = kwargs.get("repository_root_path", "/tmp")
        created.append(root)
        return _mock_server(root)

    roots = [tmp_path / f"proj{i}" for i in range(3)]
    for r in roots:
        r.mkdir()
        (r / "pyproject.toml").touch()

    files = [r / "app.py" for r in roots]
    for f in files:
        f.touch()

    with patch("lsp_mcp.lsp.manager.GenericLanguageServer", side_effect=_make_mock):
        await manager.acquire(_make_spec(), str(files[0]))
        await manager.acquire(_make_spec(), str(files[1]))
        # Adding a third should evict the LRU (first)
        await manager.acquire(_make_spec(), str(files[2]))

    assert len(manager._pool) <= 2
    await manager.aclose()
