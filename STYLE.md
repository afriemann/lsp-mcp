# Style Guide — lsp-mcp

Non-lint-encodable conventions. Mechanical rules (formatting, import order) are enforced by pre-commit hooks; this file covers judgment-level choices.

## Layer boundaries

- **Tool adapters** (`server.py`) are thin: parse args, call dispatch, return `_tool_response(result)`. This boundary translates internal failure metadata to MCP errors without changing successful payloads. No LSP logic, no file I/O.
- **Dispatch layer** (`dispatch/`) holds all tool semantics: routing, capability checks, symbol resolution, edit application. No MCP imports.
- **LSP layer** (`lsp/`) handles process lifecycle, capability capture, and diagnostics. No routing decisions.
- **Config layer** (`config/`) handles YAML loading and glob resolution only. No asyncio.

These boundaries must not be crossed. If LSP logic appears in a tool adapter, extract it to dispatch. If routing logic appears in `lsp/`, move it up.

## Error handling policy

- Dispatch captures execution errors as result metadata, `note`, and `warnings` rather than raising. Tool adapters return MCP error results for total execution failure; completed empty answers and partial results remain successful.
- Fatal config errors call `sys.exit(1)` with an actionable message — before any server starts.
- All exceptions inside `acquire`, `raw_request`, and LSP calls are caught at the dispatch layer and converted to warnings.

## Async patterns

- All network/LSP I/O is `async`; the MCP SDK provides the event loop.
- `AsyncExitStack` is the only mechanism for keeping `start_server()` contexts alive outside their original scope.
- `asyncio.Lock` — one per pool key — prevents concurrent double-start. Do not use a global pool lock for individual key operations.
- Bounded waits keep timeout handling inside dispatch; the response classification depends on whether a tool-level answer completed, not whether an intermediate LSP request succeeded.

## Naming

- Module names are `snake_case`; class names are `PascalCase`.
- LSP method strings use the official LSP form (`textDocument/definition`, `workspace/symbol`).
- Pool keys are `(server_name, abs_project_root)` tuples — always use absolute paths.
- Result dataclasses end in `Result` (`DeclarationResult`, `DiagnosticsResult`).
- Tool kinds are `SCREAMING_SNAKE_CASE` members of `ToolKind`.

## Testing conventions

- Unit tests use `pytest` and `pytest-asyncio`; async tests are decorated automatically via `asyncio_mode = "auto"`.
- Language server processes are never started in unit tests — use `unittest.mock.patch` or mock objects.
- End-to-end smoke tests (in `tests/smoke/`) may start real servers and are excluded from `pytest` by default.
- Test file names: `test_<module_name>.py`.

## UTF-16 offsets

All position-to-index conversions go through `dispatch/offsets.py:position_to_index`. Never compute string indices from LSP `character` values directly — it breaks on non-BMP characters.

## Config immutability

`Config`, `ServerSpec`, and `FileHandler` are `frozen=True` dataclasses. They are loaded once at startup and shared as read-only. Do not add mutable state to them.

## Process launch

Pass a defensive list copy of the configured command to `multilspy`'s `ProcessLaunchInfo.cmd`. It uses `asyncio.create_subprocess_exec`: argument boundaries and literal values are preserved without shell interpretation. Do not join or quote the arguments into a command string.
