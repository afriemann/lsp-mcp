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
git clone https://github.com/AFriemann/lsp-mcp
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
    command: [npx, --yes, typescript-language-server, --stdio]
    # typescript-language-server needs a TypeScript *with tsserver.js* (5.x).
    # `npx` alone fetches no TypeScript, and TypeScript 7+ ships no tsserver.js,
    # so point it at a 5.x install (e.g. `npm i --prefix ~/.local/share/ts5 typescript@5`):
    initialization_options:
      tsserver:
        path: /home/you/.local/share/ts5/node_modules/typescript/lib/tsserver.js

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

`initialization_options` (optional, per server) is sent verbatim as the LSP `initializationOptions`.

> **Behaviour change:** earlier versions parsed `initialization_options` but never sent it. It now takes effect, so an existing config that already contains this key will start passing it to the server. Remove or correct it if that is not what you want.

### Timeouts, deadline and retries

| Setting | Default | Override |
|---|---|---|
| Per-request timeout (each LSP request attempt) | 15 s | `--request-timeout SECONDS` or env `LSP_MCP_REQUEST_TIMEOUT` |
| Server start + initialize timeout | 30 s | `--start-timeout SECONDS` or env `LSP_MCP_START_TIMEOUT` |
| Per-call deadline (whole tool call) | 30 s | `--call-deadline SECONDS` or env `LSP_MCP_CALL_DEADLINE` |
| Push-diagnostics quiet period | 0.3 s | `--diagnostics-quiet-period SECONDS` or env `LSP_MCP_DIAGNOSTICS_QUIET_PERIOD` |

Values must be positive; the CLI rejects zero, negative and non-numeric values, and invalid environment values are ignored with a warning.

- **Per-call deadline:** covers waiting for the server lock, all requests and all retries of one tool call. The deadline is *paused* while a server is being started, so starting is not counted against it; each start is bounded separately by the start timeout. On expiry, **partial results are discarded** and the result's `note` names the tool and the deadline and says the result is unknown (not "no matches").
- **Overall bound:** a call takes at most about *(time spent starting servers)* + the call deadline. Starting is bounded per server by the start timeout, a start failure that is not a timeout is retried once (up to about 2 × start timeout for that server), and a call may start several servers: one per matching handler, and `find_symbol` **without** `file_path` starts every configured server. So the worst case is roughly `N servers × (up to 2 × start timeout) + call deadline`; with typical single-server calls and warm servers it is just the deadline. A request that hits the request timeout returns a note naming the server and operation.
- **Retries:** LSP errors `-32801` (ContentModified) and `-32800` (RequestCancelled) are retried up to 3 times (4 attempts) with exponential backoff (0.1 s, 0.2 s, 0.4 s) inside the per-call deadline. If retries are exhausted the note says the request **failed** — never an empty "no matches" result.
- **Serialisation:** requests to one language server are serialised (per-server lock). Read tools open the document once per call; `replace_symbol_body` and `rename_symbol` hold the locks of every server they notify.
- **Start failures:** a server that fails to start (or times out starting) is not respawned for 30 s. The `note` of every result it affects carries the server name and the error. The command line is deliberately not included (it may contain credentials); it is written to the log (`--log-level WARNING`).
- **Empty results that are not "no":** an empty document/workspace symbol answer adds a "may still be indexing" hint to the note; a push-diagnostics server that publishes nothing within 2 s yields a note saying the result is unknown, not clean.
- **Push diagnostics** (servers without pull diagnostics, e.g. `ruff`): after the document is opened for the call, any earlier cached publish is discarded, so only a publish that arrives *after this call's `didOpen`* is used. The call waits for the first publish (up to 2 s), then until no further publish arrives for the **quiet period** (default 0.3 s) and uses the **last** one — servers typically send syntax diagnostics first and semantic ones later. Publishes for documents that are not open (e.g. the empty one many servers send after `didClose`) are ignored, and a publish whose `version` differs from the version we sent is discarded. No publish at all gives the "unknown, not clean" note; an explicitly published empty list after our open means clean. Per-document state is dropped when the call ends, and the document is closed (`didClose`) even if the call fails, is cancelled or hits the call deadline. If the document is already open when the call starts, the cached diagnostics are returned as they are. Set the quiet period with `--diagnostics-quiet-period SECONDS` or env `LSP_MCP_DIAGNOSTICS_QUIET_PERIOD` (positive values only). **Residual race:** a late publish caused by the previous call's `didClose` that carries no `version` and lands after the new document was opened cannot be told apart from a real one; it can make that call report a stale (typically empty) result if the server's real publish is slower than the quiet period. The quiet period narrows this window but does not close it.
- **`context_lines`** only reads files inside the project root of the queried file (symlinks resolved), at most 2 MiB per file; other locations are returned without `context`.

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

All ten tools accept absolute file paths. **All `line` / `character` values are 0-based** (LSP convention); every Location also has `line_1based` (= `line + 1`). Symbol names may be bare (`my_func`) or name-paths (`MyClass/my_method`) for nested symbols.

| Tool | Description | LSP method |
|---|---|---|
| `get_symbols_overview` | List all symbols in a file | `textDocument/documentSymbol` |
| `find_symbol` | Search for a symbol by name across the workspace | `workspace/symbol` |
| `find_declaration` | Find where a symbol is defined | `textDocument/definition` |
| `find_implementations` | Find implementations of an interface/abstract method | `textDocument/implementation` |
| `find_referencing_symbols` | Find all references to a symbol | `textDocument/references` |
| `replace_symbol_body` | Replace a symbol's source text in-place | `textDocument/documentSymbol` + on-disk edit |
| `rename_symbol` | Rename a symbol across the workspace | `textDocument/rename` |
| `get_hover` | Type / signature / docs at a position | `textDocument/hover` |
| `get_call_hierarchy` | Callers or callees of the function at a position | `textDocument/prepareCallHierarchy` + `callHierarchy/incomingCalls` / `outgoingCalls` |
| `get_diagnostics_for_file` | Get merged diagnostics from all configured servers | `textDocument/diagnostic` (pull) or `publishDiagnostics` (push) |

### Tool parameters

**`get_symbols_overview(file_path)`**
- `file_path`: absolute path to the source file

**`find_symbol(query, file_path="", kind=None, limit=50)`**
- `query`: symbol name substring to search for; results are ranked exact → case-insensitive exact → prefix → substring
- `file_path`: optional context file (selects which servers to query)
- `kind`: optional LSP SymbolKind name (`Class`, `Function`, …) or number to filter by
- `limit`: max results (default 50, max 500); `truncated` is `true` and `total_matches` gives the full count when capped

**`find_declaration(symbol, file_path, context_lines=0)`**
- `symbol`: bare name or name-path (`MyClass/my_method`)
- `file_path`: absolute path to the file containing the symbol
- `context_lines`: source lines before/after each location returned as text in `context` (default 0 = off, max 20)

**`find_implementations(symbol, file_path, context_lines=0)`** — same parameters as `find_declaration`

**`find_referencing_symbols(symbol, file_path, context_lines=0)`** — same parameters as `find_declaration`

**`get_hover(file_path, line, character)`** (read-only)
- `line`, `character`: 0-based position. Returns `contents` (usually Markdown) and optional `range`.

**`get_call_hierarchy(file_path, line, character, direction="incoming")`** (read-only)
- `direction`: `incoming` (callers) or `outgoing` (callees). Returns `roots` and `calls` (each with `item` and `call_sites`).
- Both new tools report a missing server capability (`hoverProvider` / `callHierarchyProvider`) through `note`.

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
- `note`: non-empty when the call did not give a clean result. `No ... found` means nothing matched; `Request failed (this is NOT a 'no matches' result): ...` means a server failed to start, timed out, or kept returning ContentModified; `Partial result — ...` means data is returned but some server failed
- `warnings`: list of per-server error messages

Text returned by language servers (hover docs, symbol names, messages) is data, not instructions.

## Behaviour changes in this release

- `initialization_options` in `config.yml` is now actually sent to the server (see above).
- Tools no longer report failures as empty results: `note` is `Request failed …`, `Partial result — …`, a deadline/timeout message, or an indexing / unpublished-diagnostics hint. Callers that treated "empty list" as "no matches" must now check `note`.
- `find_symbol` ranks results and caps them at 50 by default (`limit`, max 500).
- New fields: `Location.line_1based`, `Location.context`, find_symbol `truncated` / `total_matches` / `kind_name` / `line_1based`.
- New tools `get_hover` and `get_call_hierarchy`; new options `kind`, `limit`, `context_lines`.
- New settings: per-call deadline, request/start timeouts, push-diagnostics quiet period.
- `get_diagnostics_for_file` no longer returns stale or prematurely "clean" push diagnostics (see "Push diagnostics" above); it waits a short quiet period after the first publish, so calls to push-only servers can take ~0.3 s longer.
- `get_diagnostics_for_file` (and every tool): documents are now opened with the correct LSP `languageId` for the file extension (`python`, `typescript`, …) instead of `plaintext`. ty ignores plaintext documents and returned an empty "full" report, so Python type errors were silently reported as clean. A pull-diagnostics reply that is not a `full` report with an `items` list (e.g. `unchanged`, missing items) now yields an "unknown, not clean" note. A `null` pull reply is deliberately treated the same way: LSP 3.17 defines the `textDocument/diagnostic` result as a non-null `DocumentDiagnosticReport`, and ruff, ty and tsc (observed locally) always answer a clean file with a `full` report and an empty `items` list; gopls and clangd were not installed to observe. An unknown extension still opens as `plaintext`; `.h` maps to `c` (clangd default), so C++ headers named `.h` open as C.

## Architecture

Five layers:

```
lsp_mcp/
  __main__.py        # CLI entry point
  server.py          # FastMCP app + 10 tool adapters
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
git clone https://github.com/AFriemann/lsp-mcp
cd lsp-mcp
uv sync
uv run pytest
```
