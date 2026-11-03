# Proposal

## Why

Language-server startup timeouts expose no persistent evidence explaining whether process launch or initialization stalled. Operators need logs independent of the MCP client's stderr capture.

## What Changes

- Persist bounded, private diagnostic logs alongside existing stderr output.
- Record startup stages, timing, retries and failures with server and project context.
- Retain language-server stderr and `window/logMessage` without enabling full protocol tracing or logging document payloads.
- Preserve timeouts, retry policy and MCP response behaviour.

## Capabilities

### Modified Capabilities

- `lsp-lifecycle`: add persistent startup diagnostics and language-server message retention.

## Impact

Production changes are limited to `lsp_mcp/__main__.py`, `lsp_mcp/lsp/manager.py` and `lsp_mcp/lsp/generic_server.py`; accompanying changes cover their CLI, manager and generic-server tests, README and OpenSpec artifacts. No dependencies or deployment configuration change. Logs can contain paths and third-party diagnostic text; private permissions, bounded retention and selective capture limit exposure.
