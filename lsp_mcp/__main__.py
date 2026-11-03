"""Entry point for lsp-mcp: load config, build and run the stdio MCP server."""

from __future__ import annotations

import argparse
import logging
import math
import os
import stat
import sys
from contextlib import ExitStack
from itertools import pairwise
from pathlib import Path

_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUPS = 3
_LOG_FORMAT = "%(asctime)s %(process)d %(name)s %(levelname)s %(message)s"


class _PrivateRotatingHandler(logging.Handler):
    """Reopen each append under a stable lock so processes share rotation safely."""

    def __init__(self, stderr_level: int) -> None:
        super().__init__(logging.INFO)
        self.disabled = False
        self.warned = False
        self.stderr_level = stderr_level
        self.directory: Path | None = None
        self.setFormatter(logging.Formatter(_LOG_FORMAT))

    @staticmethod
    def _validate(info: os.stat_result, *, directory: bool = False) -> None:
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("unsafe log destination type or ownership")
        if not directory and info.st_nlink != 1:
            raise OSError("unsafe linked log destination")

    def _open_file(self, directory_fd: int, name: str) -> int:
        fd = os.open(
            name,
            os.O_CREAT | os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            self._validate(os.fstat(fd))
            os.fchmod(fd, 0o600)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _append(self, data: bytes) -> None:
        import fcntl

        if self.directory is None:
            state = os.environ.get("XDG_STATE_HOME", "")
            base = Path(state) if os.path.isabs(state) else Path.home() / ".local/state"
            self.directory = base / "lsp-mcp"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with ExitStack() as stack:
            directory_fd = os.open(
                self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            stack.callback(os.close, directory_fd)
            self._validate(os.fstat(directory_fd), directory=True)
            os.fchmod(directory_fd, 0o700)
            lock_fd = self._open_file(directory_fd, "lsp-mcp.lock")
            stack.callback(os.close, lock_fd)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            names = ["lsp-mcp.log"] + [
                f"lsp-mcp.log.{index}" for index in range(1, _LOG_BACKUPS + 1)
            ]
            # Validate every destination before chmod or rotation can touch it.
            for name in names:
                try:
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                self._validate(info)
                if info.st_size > _LOG_MAX_BYTES:
                    raise OSError("existing diagnostic file exceeds the size limit")
            for name in names:
                try:
                    os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                fd = self._open_file(directory_fd, name)
                os.close(fd)
            fd = self._open_file(directory_fd, names[0])
            try:
                rotate = os.fstat(fd).st_size + len(data) > _LOG_MAX_BYTES
            finally:
                os.close(fd)
            if rotate:
                for source, destination in reversed(list(pairwise(names))):
                    try:
                        os.rename(
                            source,
                            destination,
                            src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd,
                        )
                    except FileNotFoundError:
                        continue
            fd = self._open_file(directory_fd, names[0])
            stack.callback(os.close, fd)
            while data:
                written = os.write(fd, data)
                if written <= 0:
                    raise OSError("incomplete diagnostic write")
                data = data[written:]

    def emit(self, record: logging.LogRecord) -> None:
        text = str(record.msg)
        try:
            text = self.format(record)
            if self.disabled:
                raise OSError("persistent logging disabled until restart")
            data = (text + "\n").encode("utf-8", errors="replace")
            if len(data) > _LOG_MAX_BYTES:
                marker = b" [truncated]\n"
                data = (
                    data[: max(0, _LOG_MAX_BYTES - len(marker))]
                    .decode("utf-8", errors="ignore")
                    .encode("utf-8")
                    + marker
                )
            self._append(data)
        except (OSError, ImportError, RuntimeError, TypeError, ValueError) as exc:
            if not isinstance(exc, BlockingIOError):
                self.disabled = True
            # Bypass logging to prevent recursion and preserve records below stderr's threshold.
            try:
                if not self.warned:
                    sys.stderr.write(f"lsp-mcp persistence unavailable: {exc}\n")
                    self.warned = True
                if record.levelno < self.stderr_level:
                    sys.stderr.write(f"{text}\n")
            except OSError:
                pass


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level), format=_LOG_FORMAT, stream=sys.stderr
    )
    app_logger = logging.getLogger("lsp_mcp")
    for handler in app_logger.handlers[:]:
        app_logger.removeHandler(handler)
        handler.close()
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(getattr(logging, level))
    stderr.setFormatter(logging.Formatter(_LOG_FORMAT))
    app_logger.addHandler(stderr)
    app_logger.addHandler(_PrivateRotatingHandler(getattr(logging, level)))
    app_logger.setLevel(min(logging.INFO, getattr(logging, level)))
    app_logger.propagate = False
    app_logger.info("Persistent diagnostics enabled")


def _positive_seconds(text: str) -> float:
    """argparse type: a finite number of seconds > 0."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number, got {text!r}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lsp-mcp",
        description="MCP server that exposes LSP-backed code navigation and editing tools.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config.yml (default: ~/.config/lsp-mcp/config.yml)",
    )
    parser.add_argument(
        "--request-timeout",
        type=_positive_seconds,
        metavar="SECONDS",
        help="Per-request LSP timeout (default 15; env LSP_MCP_REQUEST_TIMEOUT)",
    )
    parser.add_argument(
        "--start-timeout",
        type=_positive_seconds,
        metavar="SECONDS",
        help="Language-server start+initialize timeout (default 30; env LSP_MCP_START_TIMEOUT)",
    )
    parser.add_argument(
        "--call-deadline",
        type=_positive_seconds,
        metavar="SECONDS",
        help="Overall per-tool-call deadline incl. lock wait and retries "
        "(default 30; env LSP_MCP_CALL_DEADLINE)",
    )
    parser.add_argument(
        "--diagnostics-quiet-period",
        type=_positive_seconds,
        metavar="SECONDS",
        help="After the first pushed diagnostics, wait this long for further "
        "publishes and use the last one (default 0.3; "
        "env LSP_MCP_DIAGNOSTICS_QUIET_PERIOD)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: WARNING)",
    )
    args = parser.parse_args()

    _configure_logging(args.log_level)

    from .server import build_app

    app = build_app(
        config_path=args.config,
        request_timeout=args.request_timeout,
        start_timeout=args.start_timeout,
        call_deadline=args.call_deadline,
        diag_quiet_period=args.diagnostics_quiet_period,
    )
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
