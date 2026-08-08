## 1. Repository structure

- [x] 1.1 Create `.github/workflows/` directory

## 2. Test workflow

- [x] 2.1 Create `.github/workflows/ci.yml` with `on: push` (branches: [main]) and `on: pull_request` (branches: [main]) triggers — no `pull_request_target`
- [x] 2.2 Add workflow-level `permissions: contents: read`
- [x] 2.3 Add `concurrency:` group `${{ github.workflow }}-${{ github.ref }}` with `cancel-in-progress: true`
- [x] 2.4 Define `test` job: `runs-on: ubuntu-latest`, `timeout-minutes: 10`, matrix `python-version: ["3.11", "3.12", "3.13"]` with `fail-fast: false`
- [x] 2.5 Add `actions/checkout` step pinned to SHA `3d3c42e5aac5ba805825da76410c181273ba90b1` (# v7.0.1) with `persist-credentials: false`
- [x] 2.6 Add `astral-sh/setup-uv` step pinned to SHA `c771a70e6277c0a99b617c7a806ffedaca235ff9` (# v9.0.0) with `python-version: ${{ matrix.python-version }}` and `enable-cache: true`
- [x] 2.7 Add step: `uv sync --frozen`
- [x] 2.8 Add step: `uv run pytest`
- [x] 2.9 Verify no `${{ github.event.* }}` expressions appear in any `run:` block

## 3. Dependabot config

- [x] 3.1 Create `.github/dependabot.yml` with `package-ecosystem: github-actions`, `directory: /`, `schedule: interval: weekly` — no `uv` or Python ecosystem entry

## 4. Validation

- [x] 4.1 Confirm `uv run pytest` (88 tests) still passes after adding CI files
- [x] 4.2 Lint-check both YAML files (valid YAML, correct indentation)
