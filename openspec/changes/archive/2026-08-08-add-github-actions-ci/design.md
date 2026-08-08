# Design — add-github-actions-ci

## Context

`lsp-mcp` is a Python MCP server (managed with `uv`, lockfile committed as `uv.lock`) that
currently has **no CI**. Pushes to `main` and pull requests merge without any automated test
gate, so regressions — including those introduced by Dependabot's own dependency-bump PRs —
can land unverified.

This change adds two files and modifies no source, tests, or existing config:

1. `.github/workflows/ci.yml` — runs `pytest` across a Python version matrix on push to `main`
   and on PRs targeting `main`.
2. `.github/dependabot.yml` — weekly Dependabot updates scoped to **GitHub Actions versions only**.

The scope is deliberately small: a test gate plus the mechanism that keeps that gate's own
pinned action SHAs current. It is **not** a full quality pipeline — linting, type-checking,
coverage, and release automation are explicitly out of scope (see below).

## Goals & Constraints

- **Goal:** every push to `main` and every PR runs the full `pytest` suite on all supported
  Python versions before merge.
- **Goal:** the CI's own supply-chain surface (third-party actions) stays maintained without
  manual SHA-bumping.
- **Constraint (attributed, verified):** the trigger, action-pinning, permission, and Dependabot
  decisions below were made and verified upstream (action `action.yml` inputs inspected, commit
  SHAs resolved, Dependabot behaviour evidenced by a real landed PR). They are recorded here as
  givens and are the security-relevant contract of this change.
- **Constraint:** no new Python dependencies; CI uses `uv` (already the project's tool) and two
  pinned third-party actions only.

## Why one change (two components)

The workflow and the Dependabot config are a single atomic change because they are two halves of
one capability: the workflow **establishes** the test gate, and the Dependabot config **maintains**
it. Shipping the workflow without Dependabot would let its SHA-pinned actions silently rot; shipping
Dependabot without a workflow would have nothing to update. Neither is independently useful, so they
land together.

## Component 1 — Test workflow (`ci.yml`)

### Trigger strategy

- `push` to `main` **and** `pull_request` targeting `main`.
- **Not** `pull_request_target`. `pull_request_target` runs with the base repository's write-scoped
  token *and* checks out untrusted fork code in the same context — a well-known privilege-escalation
  vector. A test-only workflow needs no write token and no secret access, so the plain
  `pull_request` trigger (read-only, fork code runs without secrets) is correct.

### Python version strategy

- Matrix: `3.11`, `3.12`, `3.13`, derived directly from `pyproject.toml`
  (`requires-python = ">=3.11,<3.14"`). The matrix is the enforceable expression of that range —
  if the range changes, the matrix must change with it.
- `fail-fast: false` so a failure on one interpreter does not cancel the others; a red run then
  shows *every* broken version at once rather than the first to fail.
- **No `actions/setup-python`.** `astral-sh/setup-uv@v9.0.0` exposes a `python-version` input
  ("The version of Python to set `UV_PYTHON` to") and a corresponding output; passing
  `python-version: ${{ matrix.python-version }}` to `setup-uv` alone provisions the interpreter.
  This drops the pinned-action surface from three actions to two (checkout + setup-uv).

### Lockfile reproducibility

- `uv sync --frozen` before `uv run pytest`. `--frozen` refuses to modify `uv.lock`: if pushed code
  would require a lockfile change that was not committed, CI fails. This makes "did you commit the
  lockfile update?" a machine-checked invariant rather than a review-time hope.

### Caching

- `enable-cache: true` on `setup-uv`. Caches downloaded wheels and provisioned interpreters.
  `multilspy` pulls a large transitive dependency tree, so cold builds are the dominant cost;
  caching materially reduces per-run wall time and network egress.

### Job shape (illustrative — not the implementation)

A single `test` job, `runs-on: ubuntu-latest`, matrixed over the three Python versions, with the
step order: checkout → setup-uv (python-version + cache) → `uv sync --frozen` → `uv run pytest`.

> Note on test scope: `testpaths = ["tests"]` collects everything under `tests/`. STYLE.md reserves
> `tests/smoke/` for real-server smoke tests excluded from `pytest`, but **no smoke tests exist yet
> and no exclude mechanism is configured**. This is acceptable now (nothing to exclude); when smoke
> tests are added, the exclude must be configured or CI will start spawning real language servers.
> Flagged as a maintenance boundary, not an action for this change.

## Component 2 — Dependabot config (`dependabot.yml`)

- `package-ecosystem: github-actions`, weekly schedule. Opens PRs to bump the pinned action SHAs as
  new versions ship, keeping Component 1's supply-chain surface current without manual effort.
- **Python (`uv`) ecosystem is deliberately excluded**, not omitted by oversight. GitHub's automatic
  Dependabot **security** updates are already active for this repo — evidenced by the
  `dependabot/uv/cryptography-50.0.0` PR opened by `app/dependabot` that recently landed. Adding an
  explicit `uv` ecosystem block would produce **duplicate** dependency PRs. If the project later
  wants scheduled (not just security-triggered) Python updates, that is a separate, deliberate
  decision — out of scope here.

## Security posture

Every item below is required, not optional. Together they define the workflow's threat contract; any
future edit to `ci.yml` must preserve them.

| Hardening | What | Why it matters |
| --- | --- | --- |
| SHA-pinned actions | Both actions pinned to a full commit SHA with the version tag in an inline comment: `actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4`, `astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0` | A mutable tag can be repointed at malicious code (tag-mutation supply-chain attack). A SHA is immutable; Dependabot bumps it deliberately. |
| Minimal permissions | `permissions: contents: read` at **workflow level** | All jobs inherit read-only; the default write-capable `GITHUB_TOKEN` scope is dropped. No job here needs write. |
| No persisted credentials | `persist-credentials: false` on `actions/checkout` | Test-only workflow with no downstream git operations; not persisting the token to `.git/config` removes a credential-leak surface. |
| Job timeout | `timeout-minutes: 10` on the test job | Bounds runaway billing. `multilspy` spawns subprocess language servers; unit tests mock them, but the timeout is belt-and-suspenders against a hung subprocess. |
| Concurrency cancellation | `concurrency: group: ${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true` | Cancels superseded runs on rapid re-pushes — cuts billing and prevents runner exhaustion on a public repo. |
| No event interpolation in `run:` | Never interpolate `${{ github.event.* }}` into shell `run:` blocks | Closes the script-injection vector where attacker-controlled PR fields (title, branch name) execute as shell. **A maintenance invariant** for any future job. |

Combined effect: fork PRs execute test code with a read-only, credential-free token, no write
permissions, a bounded runtime, and no path for untrusted event data to reach a shell.

## Out of scope (deferred by intent)

- **Linting / formatting / type-checking** (ruff, mypy). No lint tools are in `dev` deps yet; adding
  a lint job is a separate change.
- **Coverage** reporting / thresholds.
- **Release / publish** automation.
- **Branch protection** requiring the `test` check — a repo setting, configured manually once CI
  exists (noted in the proposal).

## Follow-ups (named, not scheduled)

1. **pre-commit config + ruff/mypy CI job.** STYLE.md line 3 asserts "Mechanical rules … are enforced
   by pre-commit hooks", but **no `.pre-commit-config.yaml` exists**. The claim is currently false.
   A follow-up should add the pre-commit config and a matching lint/type-check CI job, closing the
   gap between documented and actual enforcement.
2. **Smoke-test exclude.** When `tests/smoke/` gains real tests, configure the pytest exclude so CI
   does not spawn real language servers (see Component 1 note).

## Component breakdown

| # | Component | Work kind | Done-criterion |
| --- | --- | --- | --- |
| 1 | `.github/workflows/ci.yml` | CI/CD (YAML) | Workflow triggers on push-to-`main` and PR-to-`main`; `test` job matrixes Python 3.11/3.12/3.13 with `fail-fast: false`; uses SHA-pinned checkout + setup-uv (python-version input, cache enabled, `persist-credentials: false`); runs `uv sync --frozen` then `uv run pytest`; workflow-level `contents: read`, `timeout-minutes: 10`, and `concurrency` with `cancel-in-progress: true` all present; no `github.event.*` in any `run:` block. |
| 2 | `.github/dependabot.yml` | CI/CD (YAML) | Single `github-actions` ecosystem entry on a weekly schedule; **no** `uv`/Python ecosystem entry. |

Both are YAML additions for the `engineer` to implement; no source or existing config changes.
