## MODIFIED Requirements

### Requirement: Handle push-style diagnostics (publishDiagnostics)
The system SHALL register a `textDocument/publishDiagnostics` notification handler on every `GenericLanguageServer` instance and store the latest diagnostics per URI, accepting a publish only while the document is open and, when the publish carries a `version`, only if it equals the version last sent in `didOpen`/`didChange`. When a tool requests diagnostics for a file and the server does not advertise `diagnosticProvider`, the system SHALL open the file (`didOpen`), discard any previously cached diagnostics for the URI and reset its event before the first `await`, wait a bounded interval for the first publish, then wait until no further publish arrives for a quiet period (default 0.3 s, configurable via `--diagnostics-quiet-period` / `LSP_MCP_DIAGNOSTICS_QUIET_PERIOD`, positive values only) and return the last publish received. When no publish is accepted within the bounded interval the result SHALL carry a note that the outcome is unknown, not clean; an explicitly published empty list SHALL mean clean. Per-URI diagnostics state SHALL be dropped when the call ends, including on error, cancellation or call-deadline expiry, and the document opened for the call SHALL be released (`didClose` sent) in those cases too. When the document is already open on entry (an outer open), the cache SHALL NOT be reset and the cached diagnostics SHALL be returned.

#### Scenario: Diagnostics cached from push notification
- **WHEN** a server pushes a `publishDiagnostics` notification for `src/app.py`
- **THEN** the latest diagnostics for that URI are stored in the server's per-URI cache

#### Scenario: Push-only server returns diagnostics after settle wait
- **WHEN** `get_diagnostics_for_file` is called for a server that does not advertise `diagnosticProvider`
- **THEN** the system opens the file, waits for the first publish within the bounded settle interval and then for the quiet period, and returns the last diagnostics published after that open (last publish wins)

#### Scenario: Edit between calls gives fresh results
- **WHEN** a file is edited between two `get_diagnostics_for_file` calls
- **THEN** the second call returns diagnostics published after its own `didOpen`, never those of the first call

#### Scenario: Close publish does not make a file look clean
- **WHEN** the server answers the previous call's `didClose` with an empty publish
- **THEN** that publish is ignored and the next call does not report the file clean because of it

#### Scenario: Last publish wins
- **WHEN** a server sends syntax diagnostics and, within the quiet period, semantic diagnostics
- **THEN** the semantic (last) publish is returned

#### Scenario: Version mismatch discarded
- **WHEN** a publish carries a `version` different from the version last sent
- **THEN** it is discarded

#### Scenario: No publish
- **WHEN** no publish is accepted within the settle interval
- **THEN** the note says no diagnostics were published and the result is unknown, not clean

#### Scenario: Buffer released after deadline expiry
- **WHEN** the call deadline expires while waiting for diagnostics
- **THEN** the document is closed (`didClose` sent), no per-URI state remains, and the next call opens it afresh and receives a fresh publish

#### Scenario: Already-open document
- **WHEN** the document was already open before the call
- **THEN** the cached diagnostics are returned without a reset and without waiting for a new publish
