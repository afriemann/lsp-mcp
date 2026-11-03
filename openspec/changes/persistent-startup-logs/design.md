# Design

## Decisions

Keep logging configuration in `__main__.py`. Give the application logger independent handlers: INFO persistence and stderr filtered by unchanged `--log-level`. Take the log path, rotation size and backup count from the forthcoming `lsp-lifecycle` delta requirement **Persist bounded private diagnostics**. Avoid lowering unrelated library logger thresholds. Include timestamp, PID, logger, severity and contextual event fields.

Choose a small stdlib rotating-handler adaptation over uncoordinated `RotatingFileHandler` instances, which can retain renamed descriptors and race backup renames. A stable adjacent lock file coordinates cooperating processes. Acquire its POSIX advisory lock nonblockingly; under lock, reopen the active file, check size, rotate within the specified limits, append and close. Never rotate/delete the lock file. Bound individual encoded records below the file limit. Contention falls back to stderr, accepting gaps in persistence rather than delaying startup. Unsupported locking also falls back; never substitute another log path.

Use a private application directory (0700), with owner-checked regular log, backup and lock files (0600), including existing files. Reject symlinks, hardlinks and foreign ownership; validate before chmod or rotation. Do not chmod shared state ancestors. Setup, permission, rotation and write failures produce a nonrecursive stderr warning and deliver affected records there regardless of `--log-level`; runtime storage failures disable persistence until restart. Recovery never changes server results.

`manager.py` supplies server/root/attempt context, monotonic elapsed time, retries and deadline classification. `generic_server.py` marks process-launch, initialization and ready boundaries without changing timeout, cleanup or retry control flow.

Assign `self.server.logger(source, target, message)` after base construction. Installed `_log()` uses `("client", "logger", message)`: forward only that route with the `LSP stderr: ` prefix. Handle `window/logMessage` separately; discard all protocol payload callbacks and retain trace-off. Omit command arguments and initialization options from application messages. Third-party text and exceptions can still contain sensitive information; selective capture is not redaction.

## Boundaries

Reject collectors, queues, dependencies and protocol tracing: unnecessary infrastructure. Disk usage is bounded; contention loses file records, not stderr. No lifecycle migration is needed.
