## 1. Design.md skip justification

- [x] 1.1 Confirm design.md criteria: pure string-only change, one file (`lsp_mcp/server.py`), no API contracts, no data models, no new dependencies — all four criteria met; design.md skipped.

## 2. Rewrite tool descriptions

- [x] 2.1 Rewrite `get_symbols_overview` description to lead with trigger condition and state semantic advantage
- [x] 2.2 Rewrite `find_symbol` description to lead with trigger condition and state semantic advantage
- [x] 2.3 Rewrite `find_declaration` description to lead with trigger condition and state semantic advantage
- [x] 2.4 Rewrite `find_implementations` description to lead with trigger condition and state semantic advantage
- [x] 2.5 Rewrite `find_referencing_symbols` description to lead with trigger condition and state semantic advantage
- [x] 2.6 Rewrite `replace_symbol_body` description to lead with trigger condition while preserving decorator rule
- [x] 2.7 Rewrite `rename_symbol` description to lead with trigger condition and state semantic advantage
- [x] 2.8 Rewrite `get_diagnostics_for_file` description to lead with trigger condition and state semantic advantage

## 3. Verify

- [x] 3.1 Run the test suite (`uv run pytest`) and confirm all tests pass — 88/88 passed
- [x] 3.2 Confirm each rewritten description is ≤ 3 sentences, leads with a trigger condition, and preserves note field + absolute path guidance
