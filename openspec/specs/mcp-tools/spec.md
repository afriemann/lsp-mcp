# mcp-tools Specification

## Purpose
TBD - created by archiving change initial-design. Update Purpose after archive.
## Requirements
### Requirement: Resolve symbol name-paths to LSP positions
Before any position-based LSP call, the system SHALL resolve a symbol name (or Serena-style name path such as `MyClass/my_method`) to an LSP position by retrieving the file's document-symbol tree and matching path segments by `name`. The `selectionRange.start` of the matched node SHALL be used as the request position for navigation tools; the full `range` SHALL be used as the edit span for `replace_symbol_body`. When a bare name matches more than one node, the system SHALL return the list of candidates rather than guessing.

#### Scenario: Unambiguous name-path resolved
- **WHEN** the document-symbol tree contains exactly one node matching `MyClass/my_method`
- **THEN** the resolved LSP position is `selectionRange.start` of that node

#### Scenario: Ambiguous bare name
- **WHEN** the document-symbol tree contains two nodes named `run` at the same level
- **THEN** the system returns both candidates and does not invoke the LSP request

#### Scenario: Dotted name-path traverses hierarchy
- **WHEN** the name path is `App/run` and the tree has class `App` containing method `run`
- **THEN** the first segment matches `App`, the second matches `run` within `App`'s children

### Requirement: Apply file edits using UTF-16-correct offset conversion
When applying on-disk edits derived from LSP ranges (for `replace_symbol_body` and `rename_symbol`), the system SHALL convert `{line, character}` LSP positions to Python string indices using a helper that accounts for **UTF-16 code unit** offsets, not byte offsets or Unicode code point counts. The conversion helper SHALL be tested against lines containing non-BMP characters.

#### Scenario: ASCII-only line
- **WHEN** converting LSP `{line: 0, character: 5}` on a line containing only ASCII
- **THEN** the Python string index equals 5

#### Scenario: Non-BMP character before offset
- **WHEN** a line contains an emoji (U+1F600, two UTF-16 code units) at position 0 and the LSP character offset is 2
- **THEN** the Python string index is 1 (one code point for the emoji), not 2

### Requirement: Keep LSP server in sync after file edits
After writing any on-disk edit, the system SHALL send a `textDocument/didChange` notification (or `didClose`/`didOpen` sequence) to every server that has the file open, so subsequent calls to the same server see the updated content.

#### Scenario: Server re-synced after replace_symbol_body
- **WHEN** `replace_symbol_body` writes a new function body to disk
- **THEN** a `textDocument/didChange` notification is sent to the server that provided the symbol range, before any further tool call on that file

### Requirement: Apply multi-file rename edits bottom-up
When `rename_symbol` returns a `WorkspaceEdit` spanning multiple files or multiple ranges within a file, the system SHALL apply each file's edits in descending offset order (bottom-up) so that earlier edits do not invalidate the offsets of later ones.

#### Scenario: Single-file rename with multiple occurrences
- **WHEN** renaming a symbol that appears three times in `src/app.py` at lines 10, 25, and 40
- **THEN** the edit at line 40 is applied first, then line 25, then line 10

#### Scenario: Multi-file rename returns changed files list
- **WHEN** renaming a symbol that appears in `src/app.py` and `tests/test_app.py`
- **THEN** both files are edited and the tool response includes both file paths in the `changed_files` list

### Requirement: get_symbols_overview returns document symbol tree
The system SHALL implement `get_symbols_overview(file_path)` using `textDocument/documentSymbol`. It SHALL route to the first configured server for the file that advertises `documentSymbolProvider` and returns a non-empty result. The response SHALL be the hierarchical symbol tree for the file.

#### Scenario: Symbol tree returned
- **WHEN** `get_symbols_overview` is called on `src/app.py` and `ty` advertises `documentSymbolProvider`
- **THEN** the response contains a hierarchical list of symbols (classes, functions, variables) in the file

#### Scenario: First server returns empty, falls through
- **WHEN** the first configured server returns an empty symbol list and the second returns a non-empty list
- **THEN** the second server's result is returned

### Requirement: find_symbol searches workspace-level symbol index
The system SHALL implement `find_symbol(query, file_path)` using `workspace/symbol`. It SHALL route to the first configured server for `file_path` that advertises `workspaceSymbolProvider` and returns a non-empty result.

#### Scenario: Symbol found by name query
- **WHEN** `find_symbol` is called with `query="MyClass"` and the server's workspace index contains `MyClass`
- **THEN** the response includes the location and kind of `MyClass`

### Requirement: find_declaration returns go-to-definition result
The system SHALL implement `find_declaration(symbol, file_path)` using `textDocument/definition`. It SHALL resolve `symbol` to an LSP position, route to the first configured server with `definitionProvider`, and return the first non-empty result.

#### Scenario: Declaration found
- **WHEN** `find_declaration` is called with `symbol="App/run"` and `ty` has `definitionProvider`
- **THEN** the response contains the file path and line/character range of `run`'s definition

### Requirement: find_implementations returns implementation locations
The system SHALL implement `find_implementations(symbol, file_path)` using `textDocument/implementation`. It SHALL resolve `symbol` to an LSP position, route to the first configured server with `implementationProvider`, and return the first non-empty result.

#### Scenario: Implementation found
- **WHEN** `find_implementations` is called for an interface method and the server has `implementationProvider`
- **THEN** the response lists all concrete implementation locations

### Requirement: find_referencing_symbols returns all references
The system SHALL implement `find_referencing_symbols(symbol, file_path)` using `textDocument/references`. It SHALL resolve `symbol` to an LSP position, route to the first configured server with `referencesProvider`, and return the first non-empty result.

#### Scenario: References returned
- **WHEN** `find_referencing_symbols` is called for `my_func` in `src/app.py`
- **THEN** the response lists all call sites and usages of `my_func` across the project

### Requirement: replace_symbol_body replaces the full range of a named symbol
The system SHALL implement `replace_symbol_body(symbol, file_path, new_body)` by: resolving `symbol` to its document-symbol node, extracting the full `range`, computing UTF-16-correct string indices, replacing the text in-place, writing the file, and sending `didChange` to the server.

#### Scenario: Function body replaced
- **WHEN** `replace_symbol_body` is called with `symbol="greet"`, `file_path="src/app.py"`, and a new function body
- **THEN** the entire text span of `greet` (from its `range.start` to `range.end`) is replaced with `new_body` on disk, and the file is syntactically updated

#### Scenario: Symbol not found
- **WHEN** the symbol name does not match any node in the document-symbol tree
- **THEN** the tool returns an error response indicating the symbol was not found; no file is modified

### Requirement: rename_symbol renames a symbol across the workspace
The system SHALL implement `rename_symbol(symbol, file_path, new_name)` by: resolving `symbol` to its `selectionRange.start`, issuing `textDocument/rename` on the first configured server with `renameProvider`, applying the returned `WorkspaceEdit` bottom-up across all affected files, sending `didChange` for each modified file, and returning the list of changed file paths.

#### Scenario: Symbol renamed across project
- **WHEN** `rename_symbol` is called and the server returns a `WorkspaceEdit` with edits in two files
- **THEN** both files are updated on disk, the tool response lists both files, and no capability-lacking server is attempted

#### Scenario: No server has renameProvider
- **WHEN** no configured server for the file advertises `renameProvider`
- **THEN** the tool returns an empty result with a note explaining that no configured server supports rename for that file type; no files are modified

### Requirement: get_diagnostics_for_file merges diagnostics from all servers
The system SHALL implement `get_diagnostics_for_file(file_path)` by collecting diagnostics from all configured servers for the file (not just the first). Each diagnostic SHALL be tagged with the name of the server that produced it. Identical diagnostics from multiple servers SHALL be de-duplicated. The result SHALL be a merged list of all diagnostics with their source tags.

#### Scenario: Diagnostics merged from two servers
- **WHEN** `ty` returns two type errors and `ruff` returns one style warning for `src/app.py`
- **THEN** the merged result contains three diagnostics tagged `ty` and `ruff` respectively

#### Scenario: De-duplication of identical diagnostics
- **WHEN** two servers both report the same error at the same line and column
- **THEN** the merged result contains that diagnostic once

### Requirement: Navigation tools return empty result with note when no capable server is configured
When a navigation tool is invoked and no configured server for the file advertises the required capability, the system SHALL return an empty result accompanied by a `note` field explaining which capability was absent and which file type was requested. The system SHALL NOT raise an exception.

#### Scenario: No server advertises definitionProvider
- **WHEN** `find_declaration` is called for a `*.go` file and no configured server advertises `definitionProvider`
- **THEN** the response is an empty result with a note such as "no configured server for `*.go` advertises `definitionProvider`"

### Requirement: Partial server failure returns result with warnings
When a tool call requires multiple servers and some fail to start or return errors, the system SHALL proceed with the servers that are available and include a `warnings` field in the response listing the failed servers and their error summaries. A total failure across all servers SHALL return an explanatory empty result.

#### Scenario: One of two servers fails to start
- **WHEN** `ruff` fails to start for a `*.py` file but `ty` starts successfully
- **THEN** the tool call returns `ty`'s result plus a `warnings` entry noting that `ruff` was unavailable

### Requirement: MCP server exposes eight tools via stdio transport
The system SHALL expose exactly the following eight tools via a `FastMCP` stdio server: `get_symbols_overview`, `find_symbol`, `find_referencing_symbols`, `find_declaration`, `find_implementations`, `replace_symbol_body`, `rename_symbol`, `get_diagnostics_for_file`. Each tool SHALL have a type-hinted signature that generates a valid MCP input schema. The server SHALL be invocable as `uvx lsp-mcp` and via `uv run`.

#### Scenario: Server starts and exposes all eight tools
- **WHEN** `uvx lsp-mcp` is run against a valid config file
- **THEN** an MCP client can discover all eight tools with their input schemas via the MCP protocol

#### Scenario: LSP servers start lazily
- **WHEN** the MCP server starts
- **THEN** no LSP subprocess is spawned until the first tool call for a relevant file

### Requirement: Startup fails with actionable message on invalid config
When the config file is missing or fails validation, the system SHALL exit with a non-zero status and print an error message that identifies the problem and the expected config path; it SHALL NOT start the MCP server or expose any tools.

#### Scenario: Startup with missing config
- **WHEN** `uvx lsp-mcp` is run and the config file does not exist
- **THEN** the process exits non-zero with a message stating the expected config path and how to create a config

