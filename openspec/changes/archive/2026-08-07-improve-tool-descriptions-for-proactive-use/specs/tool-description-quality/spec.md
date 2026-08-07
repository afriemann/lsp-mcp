## ADDED Requirements

### Requirement: Each MCP tool description includes a trigger condition and semantic advantage
Each of the eight MCP tool descriptions registered in `server.py` SHALL open with a trigger condition — a clause beginning with "Use before…", "Use when…", or "Use instead of…" — that tells an agent when to reach for the tool proactively rather than falling back to grep or full file reads. The description SHALL also state, in one clause, the semantic advantage over text search (e.g. resolves aliases, updates all references atomically, covers non-BMP characters). Each description SHALL be at most three sentences.

#### Scenario: get_symbols_overview has trigger condition
- **WHEN** an agent reads the `get_symbols_overview` tool description
- **THEN** the description begins with a clause indicating the tool should be used before reading an entire file to navigate its structure

#### Scenario: find_declaration has trigger condition
- **WHEN** an agent reads the `find_declaration` tool description
- **THEN** the description begins with a clause indicating the tool should be used when navigating to where a symbol is defined

#### Scenario: find_referencing_symbols has trigger condition
- **WHEN** an agent reads the `find_referencing_symbols` tool description
- **THEN** the description begins with a clause indicating the tool should be used before renaming or deleting a symbol

#### Scenario: rename_symbol has semantic advantage stated
- **WHEN** an agent reads the `rename_symbol` tool description
- **THEN** the description states that the language server updates every reference atomically, including imports and aliased usages that text search would miss

#### Scenario: get_diagnostics_for_file has trigger condition
- **WHEN** an agent reads the `get_diagnostics_for_file` tool description
- **THEN** the description begins with a clause indicating the tool should be used after editing a file to surface errors without running a separate build step

### Requirement: Tool descriptions preserve correct parameter guidance
Each tool description SHALL retain accurate parameter-level guidance: `file_path` parameters that require an absolute path SHALL be documented as such, and the `note` field check instruction SHALL remain in every description. The decorator rule for `replace_symbol_body` SHALL be preserved verbatim. No existing parameter-level detail SHALL be removed.

#### Scenario: file_path absolute path requirement preserved
- **WHEN** an agent reads any tool description where file_path must be absolute
- **THEN** the description states "absolute" path is required for that parameter

#### Scenario: note field check instruction preserved
- **WHEN** an agent reads any tool description
- **THEN** the description instructs the agent to check the note field first and states what a non-empty note means

#### Scenario: replace_symbol_body decorator rule preserved
- **WHEN** an agent reads the `replace_symbol_body` tool description
- **THEN** the description explains that new_body starting without '@' preserves existing decorators and new_body starting with '@' replaces them
