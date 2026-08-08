# ci-pipeline Specification

## Purpose
TBD - created by archiving change add-github-actions-ci. Update Purpose after archive.
## Requirements
### Requirement: Test workflow triggers correctly

The CI workflow SHALL run the test suite when code is pushed to the `main` branch or when a pull
request targeting `main` is opened, synchronised, or reopened. The `pull_request_target` event
MUST NOT be used as a trigger.

#### Scenario: Push to main triggers tests

- **WHEN** a commit is pushed to the `main` branch
- **THEN** the `test` job runs for all matrix Python versions

#### Scenario: Pull request triggers tests

- **WHEN** a pull request targeting `main` is opened or updated
- **THEN** the `test` job runs for all matrix Python versions using the PR's code

#### Scenario: pull_request_target is absent

- **WHEN** the workflow file is inspected
- **THEN** no `pull_request_target` event appears in the `on:` block

---

### Requirement: Test matrix covers all supported Python versions

The `test` job SHALL run on Python 3.11, 3.12, and 3.13 — the full range declared in
`pyproject.toml` (`requires-python = ">=3.11,<3.14"`). The matrix SHALL use `fail-fast: false`
so a failure on one version does not cancel the others.

#### Scenario: All three Python versions are in the matrix

- **WHEN** the workflow file is inspected
- **THEN** the matrix includes `"3.11"`, `"3.12"`, and `"3.13"` and no other versions

#### Scenario: Matrix fail-fast is disabled

- **WHEN** the workflow file is inspected
- **THEN** the matrix strategy has `fail-fast: false`

---

### Requirement: CI uses the committed lockfile

The `test` job SHALL run `uv sync --frozen` before executing pytest so that the installed
dependency set matches `uv.lock` exactly. CI MUST fail if `uv.lock` is out of sync with
`pyproject.toml`.

#### Scenario: uv sync uses frozen flag

- **WHEN** the workflow file is inspected
- **THEN** a step runs `uv sync --frozen` before the step that runs pytest

---

### Requirement: Workflow is security-hardened

The workflow MUST implement all of the following hardening measures:

1. All third-party actions MUST be pinned to a full commit SHA with the version tag in an inline comment.
2. `permissions: contents: read` MUST be set at the workflow level.
3. The `actions/checkout` step MUST set `persist-credentials: false`.
4. The `test` job MUST set `timeout-minutes: 10`.
5. A `concurrency` group scoped to workflow + ref MUST be configured with `cancel-in-progress: true`.
6. No `${{ github.event.* }}` expressions MUST appear in any `run:` block.

#### Scenario: Actions are SHA-pinned

- **WHEN** the workflow file is inspected
- **THEN** every `uses:` line references a full 40-character commit SHA, not a tag or branch
- **AND** every SHA has an inline comment identifying the version tag (e.g. `# v4`)

#### Scenario: Workflow-level permissions are read-only

- **WHEN** the workflow file is inspected
- **THEN** `permissions: contents: read` is present at the top-level workflow scope

#### Scenario: Checkout does not persist credentials

- **WHEN** the workflow file is inspected
- **THEN** the `actions/checkout` step has `persist-credentials: false` in its `with:` block

#### Scenario: Test job has a timeout

- **WHEN** the workflow file is inspected
- **THEN** the `test` job has `timeout-minutes: 10`

#### Scenario: Concurrency cancels stale runs

- **WHEN** the workflow file is inspected
- **THEN** a `concurrency:` block is present with `cancel-in-progress: true`

---

### Requirement: Dependabot keeps GitHub Actions versions current

A `.github/dependabot.yml` file SHALL configure Dependabot to open weekly PRs when new versions of
the pinned GitHub Actions are released. The Python (`uv`) package ecosystem MUST NOT be added to
this file because GitHub's automatic Dependabot security updates already cover it.

#### Scenario: Dependabot targets the github-actions ecosystem

- **WHEN** the `dependabot.yml` file is inspected
- **THEN** it contains exactly one `package-ecosystem: github-actions` entry with a weekly schedule

#### Scenario: Python ecosystem is absent from Dependabot config

- **WHEN** the `dependabot.yml` file is inspected
- **THEN** no `package-ecosystem: uv`, `pip`, or `python` entry is present

