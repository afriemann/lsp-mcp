# Proposal

## Why

Configured language servers cannot start because their argument list is converted to a string that multilspy treats as individual characters. The resulting failures reach clients as successful MCP calls, making operational failure look like a completed search.

## What Changes

- Restore direct launch with intact configured argument boundaries.
- Propagate total execution failures as MCP tool errors while retaining diagnostic messages.
- Preserve successful empty answers and usable partial answers with warnings.

## Capabilities

### Modified Capabilities

- `lsp-lifecycle`: make argument preservation explicit at process launch.
- `mcp-tools`: distinguish failed operations from successful empty or partial responses at the protocol boundary.

## Impact

Changes affect the launcher, dispatcher, result models, MCP adapters, their regression tests, and existing documentation. No configuration, dependency, deployed vendor copy, or external deployment pin changes are included. Total failures intentionally change from MCP success to MCP error; successful payloads remain compatible.
