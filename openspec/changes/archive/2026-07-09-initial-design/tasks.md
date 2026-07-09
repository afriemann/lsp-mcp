## 1. Project Scaffolding

- [x] 1.1 Set up `pyproject.toml` with `[project]` metadata, `[project.scripts]` entry `lsp-mcp = "lsp_mcp.__main__:main"`, and dependencies: `multilspy`, `mcp`, `pyyaml`
- [x] 1.2 Create the `lsp_mcp/` package with `__init__.py` and the module skeleton: `__main__.py`, `server.py`, `types.py`, `tools/`, `dispatch/`, `lsp/`, `config/`
- [x] 1.3 Add `uv.lock` and verify `uv run lsp-mcp --help` exits without error

## 2. Config Layer (`config/`)

- [x] 2.1 Implement `config/model.py`: `ServerSpec`, `FileHandler`, and `Config` dataclasses matching the agreed YAML structure
- [x] 2.2 Implement `config/loader.py`: load `~/.config/lsp-mcp/config.yml` (respecting `$XDG_CONFIG_HOME`); fatal startup error on missing or unparseable file with actionable message
- [x] 2.3 Implement config validation: reject undefined server references in `file_handlers`, empty `command` lists, and invalid glob patterns; fatal on failure
- [x] 2.4 Implement `config/resolver.py`: `resolve(file_path, config) -> list[ServerSpec]` — basename matching for `*.ext` patterns, full-path matching for path-style globs, multi-handler concatenation, deduplication by first occurrence
- [x] 2.5 Write unit tests for `config/`: loading success, missing file error, validation errors (undefined server, empty command), resolver (single match, multi-match merge, dedup, no match)

## 3. LSP Generic Server (`lsp/generic_server.py`)

- [x] 3.1 Confirm the exact multilspy override hook for providing an arbitrary launch command (inspect `LanguageServer` subclasses in the installed multilspy source)
- [x] 3.2 Implement `GenericLanguageServer(AsyncLanguageServer)`: launch from configured `command` list, standard `InitializeParams` with `rootUri`/`workspaceFolders`, capture `InitializeResult.capabilities` into `self.capabilities`
- [x] 3.3 Add `raw_request(method, params)` method wrapping the multilspy protocol handler for LSP methods not wrapped by multilspy (`workspace/symbol`, `textDocument/implementation`, `textDocument/rename`, `textDocument/diagnostic`)
- [x] 3.4 Register `textDocument/publishDiagnostics` notification handler; store latest diagnostics per URI in a per-instance dict
- [x] 3.5 Write unit tests for `GenericLanguageServer`: capabilities captured at init, push diagnostics stored per URI

## 4. Capability Set (`lsp/capabilities.py`)

- [x] 4.1 Implement `CapabilitySet` wrapping a raw `dict` from `InitializeResult.capabilities`, with `supports(tool: ToolKind) -> bool` predicates covering all eight tools (including the push-vs-pull diagnostics distinction)
- [x] 4.2 Write unit tests: `supports` returns correct bool for each tool kind, both `True` (bool) and `{prepareProvider: ...}` shapes for `renameProvider`, `diagnosticProvider` present vs absent

## 5. Server Manager Pool (`lsp/manager.py`)

- [x] 5.1 Implement `ServerEntry` dataclass: `server`, `exit_stack`, `lock`, `last_used`, `state`
- [x] 5.2 Implement `ServerManager.acquire(server_name, root) -> ServerEntry`: start under per-key `asyncio.Lock` if absent, enter `start_server()` into `AsyncExitStack`, mark `ready`
- [x] 5.3 Implement idle eviction: background task closes entries idle beyond TTL and enforces max-server cap via LRU eviction
- [x] 5.4 Implement crash handling: detect transport/closed error, evict entry, retry start once; on second failure return `failed` state without raising
- [x] 5.5 Implement `ServerManager.aclose()`: close all pool entries gracefully
- [x] 5.6 Implement project root inference: walk parent dirs for `.git`, `pyproject.toml`, `go.mod`, `package.json`, `tsconfig.json`; fall back to file's directory
- [x] 5.7 Write unit tests: reuse of running server across calls, separate processes per distinct root, concurrent start blocked by lock (one process), LRU eviction fires at cap, `aclose()` calls `aclose()` on all entries

## 6. UTF-16 Offset Conversion Helper (`dispatch/offsets.py`)

- [x] 6.1 Implement `position_to_index(text: str, line: int, character: int) -> int` using UTF-16 code unit counting
- [x] 6.2 Write unit tests: ASCII-only lines (character == Python index), line with emoji at start (2 UTF-16 code units, 1 code point), line with CJK and mixed characters

## 7. Symbol Resolution and Edit Application (`dispatch/symbols.py`, `dispatch/edits.py`)

- [x] 7.1 Implement `resolve_symbol(name_path: str, symbols_tree) -> SymbolNode | list[SymbolNode]`: walk hierarchical document-symbol tree matching path segments; return list of candidates when ambiguous
- [x] 7.2 Implement `apply_edit(file_path, range, new_text, servers)`: read file, convert LSP range to indices via `position_to_index`, splice, write, send `didChange` to each server that has the file open
- [x] 7.3 Implement multi-file rename application in `apply_workspace_edit(workspace_edit, servers)`: group edits by file, apply each file's edits bottom-up (descending offset order), send `didChange` per file; return list of changed file paths
- [x] 7.4 Write unit tests: name-path traversal (nested class/method), ambiguous bare name returns candidates, `apply_edit` on multi-byte line (UTF-16 correct), bottom-up ordering for multi-occurrence rename in same file

## 8. Dispatch / Router (`dispatch/router.py`)

- [x] 8.1 Implement the routing pipeline: `abspath(file_path)` → project root inference → config glob resolve → server acquire → capability filter → per-tool LSP call → normalise to `types.py` model
- [x] 8.2 Implement first-wins semantics: query capable servers in order, return first non-empty result (empty ≠ win); fall through to next server
- [x] 8.3 Implement merge semantics for `get_diagnostics_for_file`: collect from all capable servers, tag each diagnostic with `source_server`, deduplicate identical entries
- [x] 8.4 Implement no-capable-server handling: return empty result with `note` field explaining missing capability and file type
- [x] 8.5 Implement partial-failure tolerance: skip servers that fail to start; include `warnings` list in result naming each failed server
- [x] 8.6 Implement `get_diagnostics_for_file` dual-path: pull (`textDocument/diagnostic`) if `diagnosticProvider` advertised; otherwise `didOpen` + bounded settle wait + read push cache
- [x] 8.7 Write integration tests for each of the 8 tool dispatch paths, including: first-wins fallthrough, merge for diagnostics, no-capable-server returns explanatory note, partial-failure returns warnings

## 9. MCP Tool Adapters (`server.py`, `tools/`)

- [x] 9.1 Confirm `FastMCP` decorator and stdio-run API for the installed `mcp` SDK version (inspect installed package source)
- [x] 9.2 Implement `server.py`: build `FastMCP("lsp-mcp")` app; register all eight tools as thin async adapters with type-hinted signatures
- [x] 9.3 Implement `__main__.py`: load+validate config → construct `ServerManager` → register tools → run stdio loop → `aclose()` manager on exit
- [ ] 9.4 Verify `uvx lsp-mcp` starts the stdio MCP server and MCP tool list is discoverable (e.g. using `mcp dev` or a simple MCP client call)
- [x] 9.5 Verify clean startup (no LSP subprocess spawned before first tool call) and clean shutdown (no orphaned child processes after exit)

## 10. End-to-End Smoke Tests

- [ ] 10.1 Write a smoke test against a real `ty` server on a small Python fixture: `get_symbols_overview`, `find_declaration`, `get_diagnostics_for_file`
- [ ] 10.2 Write a smoke test against a real `gopls` server on a small Go fixture: `get_symbols_overview`, `find_referencing_symbols`
- [ ] 10.3 Verify multi-server merge for `get_diagnostics_for_file` using both `ty` and `ruff` configured for the same `*.py` fixture

## 11. Packaging and Documentation

- [ ] 11.1 Verify `uvx lsp-mcp` works end-to-end from a clean environment (no pre-installed deps)
- [x] 11.2 Write `README.md`: installation (`uvx`/`uv run`), config file format with annotated example, how to add to `opencode.jsonc`, the eight tools with their parameters
- [x] 11.3 Write `STYLE.md` capturing non-lint-encodable conventions: async-first patterns, layer boundary rules (no LSP logic in tool adapters, no MCP logic in dispatch), error-never-raise policy for tool results
