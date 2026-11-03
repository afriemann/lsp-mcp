# Spec Delta

## ADDED Requirements

### Requirement: Persist bounded private diagnostics
The system SHALL persist application diagnostics at INFO or above to `${XDG_STATE_HOME:-~/.local/state}/lsp-mcp/lsp-mcp.log`, with a 5 MiB file limit and three backups. The application directory SHALL be owner-only (0700), and logs, backups and coordination files SHALL be owner-only regular files (0600). Existing stderr verbosity SHALL remain controlled by `--log-level`.

#### Scenario: Persists and rotates private diagnostics
- **WHEN** application diagnostics reach the file limit
- **THEN** records are retained in the active file and up to three private backups, with timestamp, PID, logger and severity, without writing to MCP stdout

#### Scenario: Rejects unsafe log destinations
- **WHEN** the application directory or a log, backup or coordination file is a link or owned by another user
- **THEN** persistence is refused without modifying the unsafe target and diagnostics remain available on stderr

#### Scenario: Coordinates simultaneous writers
- **WHEN** multiple MCP processes use the same log destination
- **THEN** rotation and appends do not race, and records unable to obtain immediate coordination are delivered to stderr

### Requirement: Logging failure preserves service behaviour
The system SHALL preserve MCP startup and tool behaviour when persistent logging cannot initialize or write, reporting the persistence failure and affected diagnostics on stderr independently of the configured stderr level. The system SHALL avoid logging command arguments, initialization options or full protocol payloads intentionally.

#### Scenario: Persistence failure falls back to stderr
- **WHEN** setup, locking, rotation or writing cannot persist a diagnostic
- **THEN** stderr receives the diagnostic and a persistence warning, and application execution continues

### Requirement: Record language-server startup evidence
The system SHALL record process launch, initialization and readiness boundaries, and startup attempts, elapsed time, retries, cooldown skips and failure classification with server and project context, without changing deadlines or recovery behaviour.

#### Scenario: Records successful startup stages
- **WHEN** a language server launches and initializes successfully
- **THEN** diagnostics identify the completed launch and initialization stages and readiness with server, project and elapsed time

#### Scenario: Records failed startup attempts
- **WHEN** startup fails, times out, is cancelled or is suppressed during cooldown
- **THEN** diagnostics identify the attempt and last startup boundary, failure classification, elapsed time and retry or cooldown decision

### Requirement: Retain selected language-server messages
The system SHALL retain language-server stderr and `window/logMessage` text with server and project context without enabling protocol tracing. Third-party diagnostic text is not guaranteed to exclude sensitive content.

#### Scenario: Captures stderr without protocol payloads
- **WHEN** the dependency emits child stderr and protocol callbacks
- **THEN** only the child-stderr diagnostic route is forwarded, with protocol payloads excluded even at DEBUG

#### Scenario: Captures language-server log messages
- **WHEN** the language server sends `window/logMessage`
- **THEN** its text and severity are logged with server and project context
