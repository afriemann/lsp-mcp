## ADDED Requirements

### Requirement: Start is bounded, reaped and cooled down
Server start including the initialize handshake SHALL be bounded by a start timeout (default 30 s, configurable). On any start failure, timeout or cancellation the language-server subprocess SHALL be terminated. A failed start SHALL be remembered for a cool-down (30 s) during which the server is not respawned, and the failure reason (error text only, never the command line) SHALL be available to tool results. Only the system's own start deadline SHALL be labelled a start timeout.

#### Scenario: Start times out
- **WHEN** the initialize handshake does not finish within the start timeout
- **THEN** the process is terminated, the acquire returns no server and the reason says the start timed out

#### Scenario: Cool-down expires
- **WHEN** the cool-down has elapsed after a failed start
- **THEN** the next acquire starts the server again

### Requirement: initialization_options are sent to the server
The `initialization_options` mapping configured for a server SHALL be sent verbatim as `initializationOptions` in the `initialize` request.

#### Scenario: Options on the wire
- **WHEN** a server is configured with `initialization_options: {tsserver: {path: /p}}`
- **THEN** the initialize request carries `initializationOptions` equal to that mapping
