## ADDED Requirements

### Requirement: Load configuration from global YAML file
The system SHALL load its configuration from `~/.config/lsp-mcp/config.yml` at startup. When the `XDG_CONFIG_HOME` environment variable is set, the system SHALL substitute it for `~/.config` in the config file path.

#### Scenario: Config loaded from default location
- **WHEN** `$XDG_CONFIG_HOME` is not set and `~/.config/lsp-mcp/config.yml` exists
- **THEN** the system loads configuration from that path and starts successfully

#### Scenario: Config loaded from XDG location
- **WHEN** `$XDG_CONFIG_HOME=/custom/config` and `/custom/config/lsp-mcp/config.yml` exists
- **THEN** the system loads configuration from `/custom/config/lsp-mcp/config.yml`

### Requirement: Reject missing configuration file at startup
The system SHALL treat a missing configuration file as a fatal startup error and SHALL emit an actionable error message indicating the expected file path.

#### Scenario: Config file absent
- **WHEN** the expected configuration file does not exist
- **THEN** the system exits with a non-zero status and an error message stating the missing path and how to create it

### Requirement: Validate configuration structure on load
The system SHALL validate the configuration on load and SHALL reject configs that: reference an undefined server name in `file_handlers`, define a server with an empty `command` list, or specify a glob pattern that cannot be compiled. Validation failures SHALL be fatal startup errors with an actionable message identifying the offending entry.

#### Scenario: Undefined server reference in file_handlers
- **WHEN** a `file_handlers` entry references a server name not defined in `servers`
- **THEN** startup fails with an error naming the undefined server and the file_handlers entry that references it

#### Scenario: Empty command list
- **WHEN** a server definition has an empty `command` list
- **THEN** startup fails with an error identifying the server name

#### Scenario: Valid configuration
- **WHEN** all server names referenced in `file_handlers` are defined, all `command` lists are non-empty, and all patterns are valid globs
- **THEN** the system starts successfully

### Requirement: Support multiple servers per file extension
The configuration SHALL allow zero or more server entries per file handler, each referencing a defined server name. The order of entries in a file handler list SHALL determine the priority order used during dispatch.

#### Scenario: Multiple servers configured for an extension
- **WHEN** the config defines both `ty` and `ruff` for `*.py` in that order
- **THEN** `ty` is attempted first and `ruff` second for any `*.py` file

### Requirement: Resolve server list for a file path by glob matching
Given a file path, the system SHALL match it against all `file_handlers` patterns and return an ordered list of `ServerSpec` entries. Patterns using the `*.ext` form SHALL be matched against the **basename** of the file path. Path-style patterns containing `/` SHALL be matched against the full relative path. When multiple handlers match, their server lists SHALL be concatenated in the order the handlers appear in the config file. Duplicate server names across merged lists SHALL be de-duplicated, preserving the first occurrence.

#### Scenario: Single handler match by extension
- **WHEN** the file path is `src/main.py` and only `*.py` matches
- **THEN** the returned list contains the servers from that handler in config order

#### Scenario: Multiple handlers match
- **WHEN** both `*.py` and `src/*.py` match `src/main.py`
- **THEN** servers from both handlers are concatenated in file order, with duplicates removed keeping the first occurrence

#### Scenario: No handler matches
- **WHEN** no pattern in `file_handlers` matches the file path
- **THEN** an empty server list is returned

### Requirement: Resolve server binary lazily
The system SHALL NOT attempt to resolve a server's `command[0]` binary on `PATH` at startup or config load time. Binary resolution SHALL occur at the point the server process is spawned.

#### Scenario: Missing binary does not block startup
- **WHEN** a server's `command[0]` is not present on `PATH` at startup
- **THEN** the system starts successfully; the missing binary is reported only when a tool call attempts to start that server
