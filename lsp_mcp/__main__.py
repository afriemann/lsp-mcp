"""Entry point for lsp-mcp: load config, build and run the stdio MCP server."""

from __future__ import annotations

import argparse
import logging
import sys


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

    app = build_app(config_path=args.config)
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
