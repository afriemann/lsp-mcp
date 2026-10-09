"""Entry point for lsp-mcp: load config, build and run the stdio MCP server."""

from __future__ import annotations

import argparse
import logging
import math
import sys


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
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: WARNING)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    from .server import build_app

    app = build_app(
        config_path=args.config,
        request_timeout=args.request_timeout,
        start_timeout=args.start_timeout,
        call_deadline=args.call_deadline,
    )
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
