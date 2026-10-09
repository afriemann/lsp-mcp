## MODIFIED Requirements

### Requirement: Each MCP tool description includes a trigger condition and semantic advantage
Each of the ten MCP tool descriptions registered in `server.py` SHALL open with a trigger condition — a sentence beginning "Use …" (for example "Use before…", "Use when…", "Use to…", "Use instead of…") — that tells an agent when to reach for the tool proactively rather than falling back to grep or full file reads. The description SHALL also state, in one clause, the semantic advantage over text search where one exists, SHALL state inputs and outputs, SHALL state that line/character positions are 0-based (for tools that take or return positions), and SHALL state how failure is signalled through `note`. Descriptions are no longer limited to three sentences.

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

#### Scenario: Position convention and failure signalling stated
- **WHEN** an agent reads any position-bearing tool description
- **THEN** it states that line/character are 0-based and explains what a non-empty `note` means
