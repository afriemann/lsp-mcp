# Proposal

## Why

Live probing showed that a failed LSP request (`-32801 ContentModified` caused by concurrent tool calls on one file) was reported as an empty successful result, so an agent concludes a symbol does not exist. Server start failures were visible only in `warnings`, nothing bounded request time, and `initialization_options` from the config was parsed but never sent. Agents also lacked hover and call-hierarchy information, ranked/filtered symbol search, and 1-based line numbers.

## What Changes

- Retry `-32801`/`-32800` (bounded, with backoff); never turn a failed request, timeout, start failure or exhausted retry into an empty "no matches" result — `note` carries it.
- Serialise requests per language server; per-request timeout (15 s), server start timeout (30 s), per-call deadline (30 s); all configurable.
- Terminate the language-server process on any start failure, timeout or cancellation.
- `find_symbol`: ranked results, `kind` filter, `limit` with `truncated`.
- Locations gain `line_1based` and optional `context` (`context_lines`, confined to the project root).
- New read-only tools `get_hover` and `get_call_hierarchy` (ten tools in total).
- `initialization_options` is now actually sent (**BREAKING** for configs that already contain the key).
- Tool descriptions state the 0-based position convention and failure signalling, so they exceed the previous three-sentence cap.
- Server command lines are never placed in `note`/`warnings`.

## Capabilities

### New Capabilities

### Modified Capabilities
- `mcp-tools`: ten tools; failure signalling; ranking/limits; locations; hover; call hierarchy; timeouts/deadline/retries.
- `tool-description-quality`: sentence cap replaced by required content (trigger, convention, failure signalling).
- `lsp-lifecycle`: start timeout, process reaping, failure cool-down, initialization options.

## Impact

`lsp_mcp/dispatch/router.py`, `lsp/manager.py`, `lsp/generic_server.py`, `server.py`, `__main__.py`, `types.py`, README. No new dependencies.
