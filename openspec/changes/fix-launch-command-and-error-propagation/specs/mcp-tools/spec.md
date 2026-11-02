## MODIFIED Requirements

### Requirement: Partial server failure returns result with warnings
The system SHALL continue with available servers when others fail and include their error summaries in `warnings`. A completed answer, including a successful empty answer, SHALL remain an MCP success with partial-failure guidance. A total execution failure with no completed answer SHALL return an explanatory empty payload as an MCP tool error, without propagating an exception from dispatch.

#### Scenario: One of two servers fails to start
- **WHEN** `ruff` fails to start for a `*.py` file but `ty` starts successfully and completes the requested operation
- **THEN** the tool call returns `ty`'s result as MCP success plus a `warnings` entry noting that `ruff` was unavailable

#### Scenario: Successful empty answer alongside failure
- **WHEN** one server fails but another completes the operation with no matches or explicitly reports no diagnostics
- **THEN** the empty response remains MCP success with partial-failure guidance and warnings

### Requirement: Failures are never reported as empty results
The system SHALL signal total execution failure as an MCP tool error (`isError: true`), retaining `note` and `warnings` with server/operation/error details, never the command line. Successful payloads SHALL retain their shape. Missing capabilities, symbol absence/ambiguity and unknown diagnostics alone SHALL remain informational. Completed partial answers SHALL carry partial-failure guidance; whole-call deadline expiry SHALL still discard partial data.
An empty documentSymbol/workspaceSymbol answer SHALL retain the indexing hint; an unpublished push-diagnostics answer SHALL retain its unknown-not-clean note. Failure notes SHALL NOT imply no matches.

#### Scenario: Retries exhausted
- **WHEN** a server keeps answering `-32801` until the retry budget is used
- **THEN** the result is an MCP error with an empty payload and `note` naming the server, operation and code

#### Scenario: Server failed to start
- **WHEN** a server cannot start for the requested file and no other server completes the operation
- **THEN** the result is an MCP error whose `note` contains `Server '<name>' failed to start` and the error, and not the command line

#### Scenario: No push diagnostics published
- **WHEN** a push-only server publishes nothing within the settle interval and no execution failure occurs
- **THEN** the response is MCP success with a `note` saying no diagnostics were published and the result is unknown, not clean

#### Scenario: Successful empty and informational responses
- **WHEN** an operation completes with no matches, no capable server is configured, a symbol is absent or ambiguous, or diagnostics are unknown without execution failure
- **THEN** the response remains MCP success with its existing payload fields and explanatory notes, without exposing internal failure metadata

#### Scenario: Total execution failure across tools
- **WHEN** any tool exhausts its available servers through startup/request errors, timeouts or retries, or its whole-call deadline expires
- **THEN** the tool returns an MCP error preserving the explanatory payload, rather than a successful empty answer; intermediate symbol resolution or hierarchy preparation alone does not count as a completed operation
