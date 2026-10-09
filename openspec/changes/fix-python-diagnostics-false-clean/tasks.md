## 1. Implementation
- [x] 1.1 languageId by extension and file name (case-insensitive)
- [x] 1.2 Pull reply validation (null/unchanged/missing items are unknown)

## 2. Tests
- [x] 2.1 language_id_for table incl. `.PY`, extensionless names, dotfiles
- [x] 2.2 didOpen languageId through the real open_file; ref-counted buffer keeps its id
- [x] 2.3 Wire-replicating stub for ty; unparsable pull replies; bare `[]` clean
- [x] 2.4 Live ty smoke test (skipped without uvx)

## 3. Docs and verification
- [x] 3.1 README (behaviour changes, null decision, `.h` trade-off)
- [x] 3.2 Full test run and `openspec validate`
