## Why

`lsp_replace_symbol_body` silently strips Python decorators (e.g. `@tool`, `@property`, `@dataclass`) when the caller supplies a replacement that starts with `def` rather than `@`. The root cause is that the LSP `DocumentSymbol.range` (`full_range`) includes any leading decorator lines, so the splice unconditionally overwrites them. This breaks real-world usage wherever decorated functions are edited via the tool.

Separately, the MCP tool descriptions shown to clients lack important detail — particularly around decorator behaviour, nested name-path syntax, and the meaning of `file_path` — making the tools harder to use correctly.

## What Changes

- **router.py** (`Dispatcher.replace_symbol_body`): when the symbol's `full_range` starts before its `selection_range` (i.e. decorators are present) AND the incoming `new_body` does not itself start with `@`, start the edit at `selection_range.start_line` instead of `full_range.start_line`. Decorators are therefore preserved unless the caller explicitly replaces them.
- **server.py** (`replace_symbol_body` description + docstring): document the decorator-preservation behaviour so callers understand what to pass.
- **server.py** (all 8 tool descriptions): review and improve for clarity, accuracy, and actionable guidance.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `mcp-tools`: `replace_symbol_body` requirement updated — the edit range is now conditional on whether `new_body` includes its own decorators, to preserve pre-existing decorators.

## Impact

- `lsp_mcp/dispatch/router.py` — logic change in `replace_symbol_body`
- `lsp_mcp/server.py` — description strings for all 8 tools
- `tests/test_router.py` — new test case for decorator-preservation scenario
- This is a bug fix. Callers passing `@`-prefixed `new_body` are unaffected. Callers passing `def`-prefixed `new_body` on decorated symbols will see changed behaviour: decorators previously lost are now preserved. Any caller that explicitly relied on the stripping behaviour would need to include decorators in their `new_body` (which was always the correct usage).
- **Partial-decorator replacement** requires the caller to read the current decorators first (e.g. via `get_symbols_overview`) and include them all explicitly in an `@`-prefixed `new_body`.
