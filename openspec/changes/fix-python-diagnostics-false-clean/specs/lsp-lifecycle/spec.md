## ADDED Requirements

### Requirement: Documents are opened with the correct languageId
The system SHALL send, in `textDocument/didOpen`, a `languageId` derived from the file extension (`python`, `typescript`, `javascript`, …), falling back to `plaintext` only for unknown extensions.

#### Scenario: Python file
- **WHEN** a `.py` file is opened for a request
- **THEN** `didOpen` carries `languageId` `python`

### Requirement: Unusable pull-diagnostics replies are not clean
A `textDocument/diagnostic` reply SHALL count as an answer only if it is a `full` report with an `items` list (or a bare list); any other reply SHALL produce a note that the result is unknown, not clean.

#### Scenario: Unchanged report
- **WHEN** the server answers `{kind: "unchanged"}` although no previous result id was sent
- **THEN** the result has no diagnostics and a note saying unknown, not clean
