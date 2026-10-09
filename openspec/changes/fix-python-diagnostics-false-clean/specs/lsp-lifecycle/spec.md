## ADDED Requirements

### Requirement: Documents are opened with the correct languageId
The system SHALL send, in `textDocument/didOpen`, a `languageId` derived from the file extension (`python`, `typescript`, `javascript`, …), falling back to `plaintext` only for unknown extensions. Matching SHALL be case-insensitive and SHALL also recognise extensionless file names (`Makefile` -> `makefile`, `Dockerfile` -> `dockerfile`). `.h` maps to `c` (known trade-off).

#### Scenario: Python file
- **WHEN** a `.py` file is opened for a request
- **THEN** `didOpen` carries `languageId` `python`

#### Scenario: Case-insensitive and extensionless names
- **WHEN** `A.PY`, `Makefile` or `Dockerfile` is opened
- **THEN** `didOpen` carries `python`, `makefile` and `dockerfile` respectively

#### Scenario: Still-open buffer keeps its languageId
- **WHEN** a document is opened a second time while already open
- **THEN** no second `didOpen` is sent and the buffer keeps its original languageId

### Requirement: Unusable pull-diagnostics replies are not clean
A `textDocument/diagnostic` reply SHALL count as an answer only if it is a `full` report with an `items` list (or a bare list); any other reply SHALL produce a note that the result is unknown, not clean.

#### Scenario: Null reply
- **WHEN** the server answers `null` (the LSP 3.17 result type is non-null)
- **THEN** the result has no diagnostics and a note saying unknown, not clean

#### Scenario: Unchanged report
- **WHEN** the server answers `{kind: "unchanged"}` although no previous result id was sent
- **THEN** the result has no diagnostics and a note saying unknown, not clean
