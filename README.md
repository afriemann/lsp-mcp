# lsp-mcp

An MCP server that exposes LSP-backed code navigation and editing tools to LLM agents. Uses a **single global config** file to route any file extension to any language server(s) — no per-project configuration required.

## Why

[Serena](https://github.com/oraios/serena) is excellent but requires per-project `.serena/` files to control which language server each project uses. `lsp-mcp` lets you define the policy once at `~/.config/lsp-mcp/config.yml` and apply it to any project — including ones you don't own.

## Installation

### With `uvx` (recommended — no install needed)

```bash
uvx lsp-mcp --help
```

### With `uv run`

```bash
git clone https://github.com/you/lsp-mcp
uv run --project /path/to/lsp-mcp lsp-mcp
```

## Configuration

Create `~/.config/lsp-mcp/config.yml` (or `$XDG_CONFIG_HOME/lsp-mcp/config.yml`):

```yaml
servers:
  # Declare every language server you want to use.
  # command: list of tokens (no shell quoting needed).
  ty:
    command: [uvx, ty, server]

  ruff:
    command: [ruff, server]

  typescript-language-server:
    command: [npx, typescript-language-server, --stdio]

  gopls:
    command: [gopls]

file_handlers:
  # Map glob patterns to ordered lists of servers.
  # Navigation tools: first server returning a non-empty result wins.
  # Diagnostics: results from all servers are merged.
  "*.py":
    - server: ty          # type checker — first priority
    - server: ruff        # linter — second priority (also provides diagnostics)

  "*.ts":
    - server: typescript-language-server

  "*.js":
    - server: typescript-language-server

  "*.go":
    - server: gopls
```

### Pattern matching

- `*.ext` — matched against the **basename** only (e.g. `*.py` matches any `.py` file in any directory).
- `src/*.py` — matched against the full path (use for path-style filtering).
- Multiple handlers for the same pattern are concatenated in order; duplicates are removed by first occurrence.

### Server binaries

Binaries are resolved lazily at server start time. A missing binary degrades just that one server — the others still work.

## Adding to opencode

In your `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "mcp": {
    "lsp-mcp": {
      "type": "local",
      "command": "uvx",
      "args": ["lsp-mcp"]
    }
  }
}
```

Or with a local clone:

```jsonc
{
  "mcp": {
    "lsp-mcp": {
      "type": "local",
      "command": "uv",
      "args": ["run", "--project", "/home/you/git/lsp-mcp", "lsp-mcp"]
    }
  }
}
```

## Tools

All eight tools accept absolute file paths. Symbol names may be bare (`my_func`) or name-paths (`MyClass/my_method`) for nested symbols.

| Tool | Description | LSP method |
|---|---|---|
| `get_symbols_overview` | List all symbols in a file | `textDocument/documentSymbol` |
| `find_symbol` | Search for a symbol by name across the workspace | `workspace/symbol` |
| `find_declaration` | Find where a symbol is defined | `textDocument/definition` |
| `find_implementations` | Find implementations of an interface/abstract method | `textDocument/implementation` |
| `find_referencing_symbols` | Find all references to a symbol | `textDocument/references` |
| `replace_symbol_body` | Replace a symbol's source text in-place | `textDocument/documentSymbol` + on-disk edit |
| `rename_symbol` | Rename a symbol across the workspace | `textDocument/rename` |
| `get_diagnostics_for_file` | Get merged diagnostics from all configured servers | `textDocument/diagnostic` (pull) or `publishDiagnostics` (push) |

### Tool parameters

**`get_symbols_overview(file_path)`**
- `file_path`: absolute path to the source file

**`find_symbol(query, file_path="")`**
- `query`: symbol name substring to search for
- `file_path`: optional context file (selects which servers to query)

**`find_declaration(symbol, file_path)`**
- `symbol`: bare name or name-path (`MyClass/my_method`)
- `file_path`: absolute path to the file containing the symbol

**`find_implementations(symbol, file_path)`** — same parameters as `find_declaration`

**`find_referencing_symbols(symbol, file_path)`** — same parameters as `find_declaration`

**`replace_symbol_body(symbol, new_body, file_path)`**
- `symbol`: bare name or name-path
- `new_body`: full replacement source text (including the signature line)
- `file_path`: absolute path to the file

**`rename_symbol(symbol, new_name, file_path)`**
- `symbol`: bare name or name-path
- `new_name`: new identifier
- `file_path`: absolute path to the file

**`get_diagnostics_for_file(file_path)`**
- `file_path`: absolute path to the source file

### Response shape

Every tool returns a JSON object with:
- The result data (`symbols`, `locations`, `diagnostics`, `changed_files`, `success`)
- `note`: explanatory string when no result was found or no capable server is configured
- `warnings`: list of non-fatal per-server error messages

## Architecture

Five layers:

```
lsp_mcp/
  __main__.py        # CLI entry point
  server.py          # FastMCP app + 8 tool adapters
  types.py           # Shared response models
  dispatch/          # Routing (first-wins / merge), symbol resolution, edits
  lsp/               # GenericLanguageServer, ServerManager pool, capabilities
  config/            # Config loading, validation, glob resolver
```

- **Config** defines the routing policy. One global file, no per-project files.
- **ServerManager** maintains a persistent pool of running server processes keyed by `(server_name, project_root)`. Servers start on demand, are reused across calls, and are idle-evicted after 15 minutes.
- **GenericLanguageServer** subclasses multilspy's `LanguageServer` to launch any arbitrary command.
- **Dispatcher** routes each tool call: resolves the file's matching servers, filters by capability, applies first-wins or merge semantics.
- **Edits** apply on-disk changes with UTF-16-correct offset handling and notify open servers via `didChange`.

## Development

```bash
git clone https://github.com/you/lsp-mcp
cd lsp-mcp
uv sync
uv run pytest
```
