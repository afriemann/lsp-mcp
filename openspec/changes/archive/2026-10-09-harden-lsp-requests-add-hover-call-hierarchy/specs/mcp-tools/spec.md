## REMOVED Requirements

### Requirement: MCP server exposes eight tools via stdio transport
**Reason**: Two read-only tools (`get_hover`, `get_call_hierarchy`) are added; replaced by the ten-tool requirement below.
**Migration**: None for existing callers; all eight original tools keep their names and required arguments.

## ADDED Requirements

### Requirement: MCP server exposes ten tools via stdio transport
The system SHALL expose exactly the following ten tools via a `FastMCP` stdio server: `get_symbols_overview`, `find_symbol`, `find_referencing_symbols`, `find_declaration`, `find_implementations`, `replace_symbol_body`, `rename_symbol`, `get_diagnostics_for_file`, `get_hover`, `get_call_hierarchy`. Each tool SHALL have a type-hinted signature that generates a valid MCP input schema. The server SHALL be invocable as `uvx lsp-mcp` and via `uv run`.

#### Scenario: Server starts and exposes all ten tools
- **WHEN** `uvx lsp-mcp` is run against a valid config file
- **THEN** an MCP client can discover all ten tools with their input schemas via the MCP protocol

#### Scenario: LSP servers start lazily
- **WHEN** the MCP server starts
- **THEN** no LSP subprocess is spawned until the first tool call for a relevant file

### Requirement: Failures are never reported as empty results
When a language-server request fails (start failure, timeout, error response, retries exhausted, call deadline exceeded), the tool SHALL return an explicit `note` stating the request failed and why (server name and error, never the server command line). It SHALL NOT return an empty result with a note that implies "no matches". When some servers fail but another returns data, the `note` SHALL flag the result as partial. An empty documentSymbol/workspaceSymbol answer SHALL add a hint that the server may still be indexing, and a push-diagnostics server that publishes nothing SHALL yield a note that the result is unknown, not clean.

#### Scenario: Retries exhausted
- **WHEN** a server keeps answering `-32801` until the retry budget is used
- **THEN** the result is empty and `note` states the request failed, naming the server, the operation and the code

#### Scenario: Server failed to start
- **WHEN** a server cannot start for the requested file
- **THEN** `note` contains `Server '<name>' failed to start` and the error, and not the command line

#### Scenario: No push diagnostics published
- **WHEN** a push-only server publishes nothing within the settle interval
- **THEN** `note` says no diagnostics were published and the result is unknown, not clean

### Requirement: Requests are retried, serialised and bounded in time
The system SHALL retry LSP errors `-32801` and `-32800` up to 3 times with exponential backoff, SHALL serialise requests per language server, SHALL bound each request attempt (default 15 s), server start (default 30 s) and each whole tool call including lock wait and retries (default 30 s, excluding server start), and SHALL make all three configurable via CLI flags and environment variables that reject non-positive values. A timeout SHALL be reported in `note` naming the server and operation; only the system's own deadline SHALL be reported as a timeout.

#### Scenario: Concurrent calls match sequential calls
- **WHEN** several tool calls run concurrently on one file
- **THEN** each returns the same result as when run sequentially

#### Scenario: Server never answers
- **WHEN** a server never answers a request
- **THEN** the tool returns within the deadline with a note naming the server and operation, and the server lock is released

#### Scenario: Call deadline exceeded
- **WHEN** a tool call (including waiting for the server lock, requests and retries) exceeds the per-call deadline
- **THEN** partial results are discarded and `note` names the tool and the deadline and states that the result is unknown, not "no matches"

#### Scenario: Non-positive timeout rejected
- **WHEN** `--request-timeout 0` is passed
- **THEN** the CLI exits with a usage error

### Requirement: find_symbol ranks, filters and limits results
`find_symbol` SHALL order results exact name, case-insensitive exact, prefix, substring, then others; SHALL accept an optional `kind` (LSP SymbolKind name or number) and `limit` (default 50, max 500); and SHALL report `truncated` and `total_matches` when capped. Its call signature SHALL remain backward compatible.

#### Scenario: Exact match first
- **WHEN** the server returns `my_foo_bar`, `foo_bar`, `Foo`, `foo` for query `foo`
- **THEN** the order is `foo`, `Foo`, `foo_bar`, `my_foo_bar`

#### Scenario: Limit applied
- **WHEN** more matches exist than `limit`
- **THEN** only `limit` are returned and `truncated` is true

### Requirement: Locations carry 1-based lines and optional context
Every Location SHALL include `line_1based` (= `line + 1`). Location tools SHALL accept `context_lines` (default 0, max 20) and then return surrounding source text in `context`, reading only regular files that, after symlink resolution, lie under the project root and at most 2 MiB of each; other locations SHALL be returned without context.

#### Scenario: Out-of-root path
- **WHEN** a server returns a location outside the project root and `context_lines` is 1
- **THEN** the location is returned with no `context`

### Requirement: get_hover returns type and documentation at a position
`get_hover(file_path, line, character)` (0-based) SHALL return hover contents as text and optional range via `textDocument/hover`, and SHALL report a missing `hoverProvider` capability or an empty answer through `note`.

#### Scenario: Hover available
- **WHEN** the server answers with MarkupContent
- **THEN** `contents` holds its text

### Requirement: get_call_hierarchy returns callers or callees
`get_call_hierarchy(file_path, line, character, direction)` SHALL use `prepareCallHierarchy` and `incomingCalls`/`outgoingCalls`, return the prepared roots and the calls with call-site locations, aggregate over every prepared item, flag partial failure in `note`, report a missing `callHierarchyProvider`, and reject a direction other than `incoming`/`outgoing` with a note.

#### Scenario: One item fails
- **WHEN** one of two prepared items keeps failing
- **THEN** the other item's calls are returned and `note` starts with `Partial result`

### Requirement: Deadline is paused while servers start
Time spent acquiring (starting) a language server SHALL NOT count against the per-call deadline; the deadline SHALL resume with the budget that remained when acquisition began, for every acquisition within one call.

#### Scenario: Slow acquisitions do not shorten the budget
- **WHEN** a call acquires two servers that each take longer than the deadline to start and the request then hangs
- **THEN** the deadline fires only after the original remaining budget has elapsed following the last acquisition

### Requirement: Edit tools lock every server they notify
`replace_symbol_body` and `rename_symbol` SHALL hold the locks of all capable servers for the file (acquired in a stable order independent of handler order) for the duration of the edit, so a `didChange` never races another call, and concurrent edit calls listing servers in opposite orders SHALL NOT deadlock. When the call deadline expires during an edit tool, every lock SHALL be released and no partial edit SHALL be applied.

#### Scenario: Opposite handler order
- **WHEN** two edit calls run concurrently with the same servers in opposite handler order
- **THEN** both complete without deadlock

#### Scenario: Deadline during an edit
- **WHEN** the deadline expires while resolving the symbol
- **THEN** the file is unchanged, all server locks are free, and `note` reports the deadline
