## MODIFIED Requirements

### Requirement: Launch LSP server processes from configured command
The system SHALL launch each LSP server as a subprocess using the `command` list from the server's configuration entry, with no modification to its argument values, boundaries or order, including spaces, quotes and shell metacharacters. The system SHALL use `AsyncLanguageServer` (not `SyncLanguageServer`) as the multilspy base to avoid the known deadlock in issue #124.

#### Scenario: Server launched from configured command
- **WHEN** a tool call requires a server with a configured executable and argument list
- **THEN** the system launches that executable directly with each argument intact, without shell interpretation or character splitting

#### Scenario: Async variant used
- **WHEN** the server process is started
- **THEN** it is managed through `AsyncLanguageServer` and its `start_server()` async context manager
