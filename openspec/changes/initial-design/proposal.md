## Why

Language Server Protocol tooling for LLM agents currently requires per-project configuration to control which servers are used — there is no way to enforce a global language→server policy across projects the user does not own. The only existing alternative (Serena) hardcodes server selection per language name with per-project overrides only. We need an MCP server that applies a single global configuration to every project it services.

## What Changes

- New standalone MCP server `lsp-mcp`, written in Python and distributed via `uvx`/`uv run`.
- Global configuration at `~/.config/lsp-mcp/config.yml` maps file extensions to ordered lists of LSP servers; applies to every project, regardless of ownership.
- Multiple LSP servers per file extension are supported (e.g. both `ty` and `ruff` for `*.py`); server capabilities are auto-discovered from LSP-advertised capabilities on connect.
- Eight MCP tools exposed to agents: symbol overview, symbol search, find references, go-to-definition, find implementations, replace symbol body, rename symbol, diagnostics.
- Navigation tools return first-server-wins results; diagnostics are merged from all configured servers.
- No per-project configuration files required.

## Capabilities

### New Capabilities

- `config`: global extension→server configuration loading, validation, and resolution from `~/.config/lsp-mcp/config.yml`
- `lsp-lifecycle`: LSP server process management — spawn, initialize handshake, document synchronisation, capability discovery, and graceful shutdown
- `mcp-tools`: the eight MCP tools exposed to agents, each implemented over the LSP lifecycle layer

### Modified Capabilities

<!-- none — this is a new project -->

## Impact

- New Python package, no runtime dependency on Serena or any existing MCP server.
- Runtime dependencies: `multilspy` (PyPI) as LSP client foundation, `mcp` Python SDK, `pyyaml`.
- Configured as a local MCP server in `opencode.jsonc` via `uvx lsp-mcp` or `uv run`.
- LSP servers themselves (ty, ruff, gopls, typescript-language-server, etc.) must be separately installed on the host; `lsp-mcp` invokes them as subprocesses per the user's config.
