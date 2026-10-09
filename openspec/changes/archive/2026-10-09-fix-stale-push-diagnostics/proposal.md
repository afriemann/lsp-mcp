# Proposal

## Why

Push diagnostics were cached per URI and never cleared, so a second `get_diagnostics_for_file` on the same file returned the previous publish immediately (stale errors or a stale "clean"), and the empty publish many servers send after `didClose` could make the next call report a file clean before it was analysed.

## What Changes

- Discard the URI's cache and arm a fresh event right after this call's `didOpen`.
- Wait for the first publish, then a quiet period (default 0.3 s, configurable) and use the last publish.
- Ignore publishes for documents that are not open, and publishes whose `version` mismatches the version we sent.
- Drop per-URI state when the call ends.

## Capabilities

### New Capabilities

### Modified Capabilities
- `lsp-lifecycle`: push-diagnostics handling requirement.

## Impact

`lsp/generic_server.py`, `dispatch/router.py`, `server.py`, `__main__.py`, README, tests.
