## 1. Tests (Red Step)

- [x] 1.1 Write failing test `test_replace_symbol_body_preserves_decorator_when_new_body_starts_with_def` in `tests/test_router.py`
- [x] 1.2 Write failing test `test_replace_symbol_body_replaces_decorator_when_new_body_starts_with_at` in `tests/test_router.py`
- [x] 1.3 Write failing test `test_replace_symbol_body_replaces_undecorated_symbol` (regression: full_range == selection_range path still works)
- [x] 1.4 Write failing test `test_replace_symbol_body_preserves_stacked_decorators` (multiple stacked decorators)
- [x] 1.5 Confirm all new tests fail for the right reason against current code

## 2. Bug Fix — router.py

- [x] 2.1 In `Dispatcher.replace_symbol_body`, add conditional `edit_range` logic: when `full_range.start_line < selection_range.start_line` and `new_body` does not start with `@`, set `edit_range.start_line = selection_range.start_line` (use `selection_range.start_char`)
- [x] 2.2 Confirm all new tests pass green

## 3. Tool Descriptions — server.py

- [x] 3.1 Update `replace_symbol_body` description: document decorator-preservation contract, partial-decorator footgun, `new_body` format, `file_path` must be absolute, `note` field meaning
- [x] 3.2 Update `get_symbols_overview` description: absolute path, `note` field, `kind` values follow LSP SymbolKind enum, return structure
- [x] 3.3 Update `find_symbol` description: clarify `file_path` is routing context (not a filter), absolute path, return structure
- [x] 3.4 Update `find_declaration` description: absolute path, `note` field, return structure
- [x] 3.5 Update `find_implementations` description: absolute path, `note` field, return structure
- [x] 3.6 Update `find_referencing_symbols` description: absolute path, `note` field, return structure
- [x] 3.7 Update `rename_symbol` description: absolute path, `note` field, return structure
- [x] 3.8 Update `get_diagnostics_for_file` description: absolute path, `note` field, return structure

## 4. Verification

- [x] 4.1 Run full test suite and confirm all tests pass
- [x] 4.2 Run linters and fix any diagnostics
