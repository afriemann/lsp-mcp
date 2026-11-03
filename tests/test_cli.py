"""CLI validation for timeout options."""

from __future__ import annotations

import logging
import os
import re
import stat
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lsp_mcp import __main__ as cli
from lsp_mcp.__main__ import main
from lsp_mcp.lsp.generic_server import GenericLanguageServer


@pytest.fixture
def logging_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    app_logger = logging.getLogger("lsp_mcp")
    handlers, level, propagate = (
        app_logger.handlers[:],
        app_logger.level,
        app_logger.propagate,
    )
    yield tmp_path / "lsp-mcp"
    for handler in app_logger.handlers:
        if handler not in handlers:
            handler.close()
    app_logger.handlers = handlers
    app_logger.setLevel(level)
    app_logger.propagate = propagate


# spec: openspec/specs/lsp-lifecycle/spec.md
def test_persists_and_rotates_private_diagnostics(logging_state, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_LOG_MAX_BYTES", 512, raising=False)
    logging_state.mkdir(mode=0o755)
    for name in ["lsp-mcp.log", "lsp-mcp.log.1", "lsp-mcp.lock"]:
        path = logging_state / name
        path.touch(mode=0o644)
    app = MagicMock()
    monkeypatch.setattr("lsp_mcp.server.build_app", lambda **kwargs: app)
    monkeypatch.setattr(sys, "argv", ["lsp-mcp", "--log-level", "ERROR"])
    main()
    app.run.assert_called_once_with(transport="stdio")
    server = GenericLanguageServer(
        command=["x"], repository_root_path=str(logging_state)
    )
    server.server._log("LSP stderr: 100% done %s %d")
    assert "100% done %s %d" in (logging_state / "lsp-mcp.log").read_text()
    log = logging.getLogger("lsp_mcp.test")
    log.info("oversized %s", "é" * 1024)
    for index in range(20):
        log.info("diagnostic-%s %s", index, "x" * 100)
    files = list(logging_state.glob("*.log*"))
    assert len(files) == 4, "retain the active log and three bounded backups"
    assert stat.S_IMODE(logging_state.stat().st_mode) == 0o700
    for path in files:
        assert path.stat().st_size <= 512
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = (logging_state / "lsp-mcp.log").read_text()
    assert f"{os.getpid()} lsp_mcp.test INFO diagnostic-19" in text
    assert capsys.readouterr().out == ""


# spec: openspec/specs/lsp-lifecycle/spec.md
@pytest.mark.parametrize(
    "unsafe,destination",
    [
        (kind, name)
        for kind in ["symlink", "hardlink", "foreign"]
        for name in ["lsp-mcp.log", "lsp-mcp.log.1", "lsp-mcp.lock"]
    ]
    + [("symlink", "directory"), ("foreign", "directory")],
)
def test_rejects_unsafe_log_destinations(
    logging_state, monkeypatch, capsys, unsafe, destination
):
    target = logging_state.parent / "untouched"
    target.write_text("private target")
    target.chmod(0o644)
    target_directory = logging_state.parent / "untouched-directory"
    target_directory.mkdir(mode=0o755)
    if destination == "directory":
        path = logging_state
        if unsafe == "foreign":
            path.mkdir()
    else:
        logging_state.mkdir()
        path = logging_state / destination
    if unsafe == "symlink":
        path.symlink_to(target_directory if destination == "directory" else target)
    elif unsafe == "hardlink":
        os.link(target, path)
    else:
        if destination != "directory":
            path.write_text("private target")
        inode = path.stat().st_ino

        def foreign_owner(info):
            values = list(info)
            if info.st_ino == inode:
                values[4] = os.getuid() + 1
            return os.stat_result(values)

        original_stat, original_fstat = os.stat, os.fstat
        monkeypatch.setattr(
            cli.os, "stat", lambda *a, **kw: foreign_owner(original_stat(*a, **kw))
        )
        monkeypatch.setattr(
            cli.os, "fstat", lambda *a: foreign_owner(original_fstat(*a))
        )
    cli._configure_logging("ERROR")
    logging.getLogger("lsp_mcp.test").info("unsafe destination diagnostic")
    assert "unsafe destination diagnostic" in capsys.readouterr().err
    assert target.read_text() == "private target"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert stat.S_IMODE(target_directory.stat().st_mode) == 0o755


def _write_diagnostics(state, prefix):
    os.environ["XDG_STATE_HOME"] = str(state)
    cli._LOG_MAX_BYTES = 512
    cli._configure_logging("ERROR")
    for index in range(10):
        logging.getLogger("lsp_mcp.test").info("writer-%s-%s", prefix, index)


# spec: openspec/specs/lsp-lifecycle/spec.md
def test_coordinates_simultaneous_writers(logging_state, capsys):
    import fcntl

    with ProcessPoolExecutor(max_workers=2) as pool:
        list(pool.map(_write_diagnostics, [logging_state.parent] * 2, ["a", "b"]))
    path = logging_state / "lsp-mcp.log"
    assert path.exists(), "parallel MCP processes persist diagnostics"
    text = path.read_text()
    assert "writer-" in text
    files = list(logging_state.glob("*.log*"))
    assert len(files) <= 4
    for candidate in files:
        assert candidate.stat().st_size <= 512
        for line in candidate.read_text().splitlines():
            if "writer-" in line:
                assert re.fullmatch(r".+ lsp_mcp.test INFO writer-[ab]-\d+", line)
    assert stat.S_IMODE((logging_state / "lsp-mcp.lock").stat().st_mode) == 0o600
    cli._configure_logging("ERROR")
    before = path.read_bytes()
    with (logging_state / "lsp-mcp.lock").open("rb") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logging.getLogger("lsp_mcp.test").info("contended diagnostic")
        assert "contended diagnostic" in capsys.readouterr().err
        assert path.read_bytes() == before


# spec: openspec/specs/lsp-lifecycle/spec.md
@pytest.mark.parametrize(
    "failure", ["home", "setup", "oversized", "lock", "busy", "rotation", "write"]
)
def test_persistence_failure_falls_back_to_stderr(
    logging_state, monkeypatch, capsys, failure
):
    import fcntl

    if failure == "setup":
        logging_state.write_text("not a directory")
    elif failure == "oversized":
        monkeypatch.setattr(cli, "_LOG_MAX_BYTES", 512)
        logging_state.mkdir()
        (logging_state / "lsp-mcp.log.1").write_text("x" * 513)
    elif failure == "home":
        monkeypatch.delenv("XDG_STATE_HOME")

        def missing_home():
            raise RuntimeError("no home directory")

        monkeypatch.setattr(Path, "home", missing_home)
    cli._configure_logging("ERROR")

    failures = []

    def fail(*args, **kwargs):
        failures.append(failure)
        if failure == "busy":
            raise BlockingIOError("lock busy")
        raise OSError("storage unavailable")

    if failure in {"lock", "busy"}:
        monkeypatch.setattr(fcntl, "flock", fail)
    elif failure == "rotation":
        monkeypatch.setattr(cli, "_LOG_MAX_BYTES", 1024)
        (logging_state / "lsp-mcp.log").write_text("x" * 1000)
        monkeypatch.setattr(cli.os, "rename", fail)
    elif failure == "write":
        monkeypatch.setattr(cli.os, "write", fail)
    logging.getLogger("lsp_mcp.test").info("fallback diagnostic")
    assert "fallback diagnostic" in capsys.readouterr().err
    if failure == "rotation":
        assert failures == ["rotation"]
    if failure == "oversized":
        assert (logging_state / "lsp-mcp.log.1").read_text() == "x" * 513
    monkeypatch.undo()
    logging.getLogger("lsp_mcp.test").info("subsequent diagnostic")
    if failure == "busy":
        assert "subsequent diagnostic" in (logging_state / "lsp-mcp.log").read_text()
    else:
        stderr = capsys.readouterr().err
        assert "subsequent diagnostic" in stderr
        assert "persistence unavailable" not in stderr
    logging.getLogger("lsp_mcp.test").error("single error diagnostic")
    assert capsys.readouterr().err.count("single error diagnostic") == 1


@pytest.mark.parametrize(
    "flag", ["--request-timeout", "--start-timeout", "--call-deadline"]
)
@pytest.mark.parametrize("value", ["0", "-1", "nan", "abc"])
def test_non_positive_timeouts_rejected(
    monkeypatch: pytest.MonkeyPatch, flag: str, value: str
) -> None:
    monkeypatch.setattr(sys, "argv", ["lsp-mcp", flag, value])
    with pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 2
