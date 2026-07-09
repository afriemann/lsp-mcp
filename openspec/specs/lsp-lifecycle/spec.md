# lsp-lifecycle Specification

## Purpose
TBD - created by archiving change initial-design. Update Purpose after archive.
## Requirements
### Requirement: Launch LSP server processes from configured command
The system SHALL launch each LSP server as a subprocess using the `command` list from the server's configuration entry, with no modification to that list. The system SHALL use `AsyncLanguageServer` (not `SyncLanguageServer`) as the multilspy base to avoid the known deadlock in issue #124.

#### Scenario: Server launched from configured command
- **WHEN** a tool call requires a server named `ty` with `command: [uvx, ty, server]`
- **THEN** the system spawns the process `uvx ty server` as a direct subprocess (not via shell)

#### Scenario: Async variant used
- **WHEN** the server process is started
- **THEN** it is managed through `AsyncLanguageServer` and its `start_server()` async context manager

### Requirement: Complete LSP initialize handshake on server start
On startup, the system SHALL send a standard `initialize` request with `rootUri` and `workspaceFolders` set to the inferred project root, and a full client capabilities block. The server SHALL be considered ready only after the `initialized` notification is acknowledged.

#### Scenario: Server ready after handshake
- **WHEN** the server process starts and the initialize handshake completes
- **THEN** the server is marked `ready` and available for tool calls

#### Scenario: Handshake fails
- **WHEN** the server process starts but the initialize handshake does not complete within the configured timeout
- **THEN** the server is marked `failed` for that `(server_name, project_root)` pair and the tool call receives a degraded result with an explanatory note

### Requirement: Capture server capabilities from InitializeResult
During the initialize handshake, the system SHALL capture the `capabilities` object from `InitializeResult` and store it for the server's lifetime. The stored capabilities SHALL not change after initialization.

#### Scenario: Capabilities captured at init
- **WHEN** `ty` responds to `initialize` with `capabilities: {definitionProvider: true}`
- **THEN** the system records `definitionProvider: true` for that server

#### Scenario: Capabilities stable after init
- **WHEN** capabilities are queried after initialization
- **THEN** the same values captured during `initialize` are returned without re-querying the server

### Requirement: Infer project root from file path
When a tool is invoked with a `file_path`, the system SHALL infer the project root by walking parent directories looking for a project marker file (`.git`, `pyproject.toml`, `go.mod`, `package.json`, `tsconfig.json`). If no marker is found, the file's containing directory SHALL be used as the project root.

#### Scenario: Git repo root found
- **WHEN** `file_path` is `/home/user/myproject/src/app.py` and `/home/user/myproject/.git` exists
- **THEN** the inferred project root is `/home/user/myproject`

#### Scenario: No marker found
- **WHEN** no project marker exists in any ancestor of `file_path`
- **THEN** the project root is the file's containing directory

### Requirement: Maintain a persistent server pool keyed by (server_name, project_root)
The system SHALL maintain a pool of running server processes, with at most one running process per `(server_name, project_root)` pair. A server process SHALL be started on the first tool call that requires it and SHALL remain running across subsequent tool calls.

#### Scenario: Server reused across calls
- **WHEN** two sequential tool calls both require `ty` for the same project root
- **THEN** the same server process handles both calls; no new process is started for the second call

#### Scenario: Separate processes for different roots
- **WHEN** tool calls require `ty` for `/project-a` and `ty` for `/project-b`
- **THEN** two separate `ty` processes are maintained, one per root

### Requirement: Prevent concurrent double-start of the same server
The system SHALL serialise server starts for each `(server_name, project_root)` key using a per-key lock. If two tool calls race to start the same server, only one start runs; the second call awaits the first.

#### Scenario: Concurrent calls do not double-start
- **WHEN** two tool calls arrive simultaneously for the same `(server, root)` pair and no server is running
- **THEN** exactly one server process is started; both calls proceed against that single process

### Requirement: Evict idle servers and cap total concurrent processes
The system SHALL evict server pool entries that have been idle beyond a configured TTL. When the pool reaches its maximum server cap, the system SHALL evict the least-recently-used entry to make room for a new one.

#### Scenario: Idle server evicted
- **WHEN** a server pool entry has not been used for longer than the idle TTL
- **THEN** the server process is gracefully shut down and the entry removed from the pool

#### Scenario: LRU eviction on cap
- **WHEN** a new `(server, root)` pair is needed but the pool is at maximum capacity
- **THEN** the least-recently-used entry is shut down before the new server is started

### Requirement: Retry once on server crash
When a tool-call request returns a transport or connection-closed error indicating the server process has crashed, the system SHALL evict the pool entry, start a new process for the same `(server_name, project_root)` pair, and retry the request once. If the retry also fails, the system SHALL return a degraded result for that server without raising.

#### Scenario: Server crash triggers one retry
- **WHEN** a request to a running server raises a transport/closed error
- **THEN** the system evicts the entry, starts a fresh process, retries the request once, and returns its result

#### Scenario: Second failure degrades gracefully
- **WHEN** the retry request also fails
- **THEN** the tool call returns a degraded result with a warning note; no exception propagates to the agent

### Requirement: Gracefully shut down all server processes on MCP server stop
When the MCP server stops, the system SHALL close every active `ServerEntry` in the pool by calling its `AsyncExitStack.aclose()`, ensuring no LSP child processes are orphaned.

#### Scenario: All servers closed on shutdown
- **WHEN** the MCP server process is stopped
- **THEN** every server process in the pool receives a graceful shutdown signal and terminates; no orphaned child processes remain

### Requirement: Handle push-style diagnostics (publishDiagnostics)
The system SHALL register a `textDocument/publishDiagnostics` notification handler on every `GenericLanguageServer` instance and store the latest diagnostics per URI. When a tool requests diagnostics for a file and the server does not advertise `diagnosticProvider`, the system SHALL ensure the file is open (`didOpen`), wait a bounded interval for pushed diagnostics to arrive, and return the cached result.

#### Scenario: Diagnostics cached from push notification
- **WHEN** a server pushes a `publishDiagnostics` notification for `src/app.py`
- **THEN** the latest diagnostics for that URI are stored in the server's per-URI cache

#### Scenario: Push-only server returns diagnostics after settle wait
- **WHEN** `get_diagnostics_for_file` is called for a server that does not advertise `diagnosticProvider`
- **THEN** the system opens the file, waits the bounded settle interval, and returns whatever diagnostics were pushed during that interval

### Requirement: Support pull-style diagnostics (diagnosticProvider, LSP 3.17+)
When a server advertises `diagnosticProvider` in its `InitializeResult.capabilities`, the system SHALL issue a `textDocument/diagnostic` pull request and return its result directly, without waiting for push notifications.

#### Scenario: Pull-capable server queried directly
- **WHEN** `get_diagnostics_for_file` is called for a server advertising `diagnosticProvider`
- **THEN** the system sends a `textDocument/diagnostic` request and returns the response without a settle wait

