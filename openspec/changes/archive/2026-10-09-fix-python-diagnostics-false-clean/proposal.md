# Proposal

## Why

`get_diagnostics_for_file` reported Python files with type errors as clean: documents were opened with `languageId: "plaintext"`, which ty ignores (it answers an empty `full` pull report), and unusable pull replies were read as empty.

## What Changes

- `didOpen` uses a languageId derived from the file extension.
- A pull reply that is not a `full` report with an `items` list yields an "unknown, not clean" note.

## Capabilities

### New Capabilities

### Modified Capabilities
- `lsp-lifecycle`: document open languageId; pull-diagnostics parsing.

## Impact

`lsp/generic_server.py`, `dispatch/router.py`, README, tests.
