## Why

There is no CI pipeline for this project. Pull requests and pushes to `main` are not automatically tested, making it easy to merge regressions. Adding GitHub Actions CI gives every contributor and Dependabot PR an automated test gate before merge.

## What Changes

- Add `.github/workflows/ci.yml` — runs the full pytest suite on push to `main` and on every pull request targeting `main`, across a Python version matrix matching `pyproject.toml` (`>=3.11,<3.14`: Python 3.11, 3.12, 3.13).
- Add `.github/dependabot.yml` — configures Dependabot to open weekly PRs when new GitHub Actions versions are released, keeping the pinned SHAs current without manual effort.

No source code, tests, or existing configuration files are modified.

## Capabilities

### New Capabilities

- `ci-pipeline`: Automated test execution on GitHub Actions; security-hardened workflow configuration (SHA-pinned actions, minimal permissions, no `pull_request_target`).

### Modified Capabilities

_(none — no existing requirement changes)_

## Impact

- **Files created**: `.github/workflows/ci.yml`, `.github/dependabot.yml`
- **No code changes**: existing source, tests, and configuration are untouched
- **Dependencies**: no new Python dependencies; uses `uv` (already in use) and `astral-sh/setup-uv` GitHub Action
- **GitHub branch protection**: once CI is in place, the branch-protection rule on `main` can require the `test` status check to pass before merge — this is a manual configuration step outside the scope of this change
