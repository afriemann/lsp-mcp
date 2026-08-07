## Why

All eight tool descriptions in `lsp_mcp/server.py` describe only *what* each tool does, not *when* to use it or *why* it is better than grepping files directly. Agents reading these descriptions have no trigger condition to act on, no stated semantic advantage over text search, and no cue to reach for the tool proactively — causing them to fall back to grep or file reads when a single LSP call would be more accurate and complete.

## What Changes

- **Rewrite all eight `@mcp.tool(description=…)` strings** in `lsp_mcp/server.py` to lead with a trigger condition ("Use before…", "Use when…", "Use instead of…"), state the semantic advantage concisely (one clause), and keep each description ≤ 3 sentences.
- **No behavioural changes** — no new tools, no schema changes, no logic edits.
- **No breaking changes** — descriptions are metadata; MCP tool schemas and signatures are untouched.

## Capabilities

### New Capabilities

- `tool-description-quality`: Requirement that each MCP tool description includes a trigger condition, a semantic advantage statement, and correct parameter guidance — so agents use tools proactively rather than reactively.

### Modified Capabilities

<!-- None — existing mcp-tools requirements are behavioural; description text is metadata not covered by them. -->

## Impact

- One file changed: `lsp_mcp/server.py`
- No API, schema, or dependency impact
- Verified by existing test suite (no new tests required — descriptions are strings, not logic)
