## MODIFIED Requirements

### Requirement: replace_symbol_body replaces the full range of a named symbol
The system SHALL implement `replace_symbol_body(symbol, file_path, new_body)` by: resolving `symbol` to its document-symbol node, computing UTF-16-correct string indices, replacing the text in-place, writing the file, and sending `didChange` to the server. The edit span SHALL be determined as follows: if the symbol's `full_range` starts before its `selection_range` (indicating decorator lines precede the `def`/`class` keyword) AND `new_body` does not begin with `@`, the system SHALL start the edit at `selection_range.start_line` so that the pre-existing decorators are preserved. If `new_body` begins with `@`, the system SHALL use the full `range` (replacing decorators too). This rule ensures decorators are never silently discarded when the caller does not intend to replace them. When a caller wishes to add, remove, or change decorators, it MUST supply a `new_body` that starts with `@` and includes all desired decorator lines explicitly.

#### Scenario: Undecorated symbol replaced
- **WHEN** `replace_symbol_body` is called on a symbol whose `full_range` and `selection_range` start on the same line (no decorators), and `new_body` starts with `def`
- **THEN** the entire symbol range is replaced with `new_body` unchanged

#### Scenario: Decorator preserved when new_body starts with def
- **WHEN** `replace_symbol_body` is called on a decorated function (its `full_range` starts before its `selection_range`) and `new_body` starts with `def` (no `@`)
- **THEN** the decorator line(s) are retained; only the `def` line and body are replaced

#### Scenario: Stacked decorators preserved when new_body starts with def
- **WHEN** `replace_symbol_body` is called on a function with multiple stacked decorators and `new_body` starts with `def`
- **THEN** all decorator lines are retained; only the `def` line and body are replaced

#### Scenario: Decorator replaced when new_body starts with @
- **WHEN** `replace_symbol_body` is called on a decorated function and `new_body` starts with `@`
- **THEN** the full range (including all existing decorators) is replaced with `new_body`

#### Scenario: Symbol not found
- **WHEN** the symbol name does not match any node in the document-symbol tree
- **THEN** the tool returns an error response indicating the symbol was not found; no file is modified
