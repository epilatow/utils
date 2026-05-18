# Development Guide

Development conventions for working in this repo are layered:

- DEVELOPMENT.md (this file) -- Repo-specific development conventions.
  Required reading for humans + agents.
- [DEVELOPMENT_SHARED.md](DEVELOPMENT_SHARED.md) -- Shared / cross-repo
  development conventions. Required reading for humans + agents.
- [DEVELOPMENT_AGENT.md](DEVELOPMENT_AGENT.md) -- Repo-specific agent
  development conventions.
- [DEVELOPMENT_SHARED_AGENT.md](DEVELOPMENT_SHARED_AGENT.md) -- Shared /
  cross-repo agent development conventions.
- Precedence:
  - **DEVELOPMENT.md takes precedence over DEVELOPMENT_SHARED.md**
  - **DEVELOPMENT_AGENT.md takes precedence over DEVELOPMENT_SHARED_AGENT.md**
- **Do not update DEVELOPMENT_SHARED.md and DEVELOPMENT_SHARED_AGENT.md.**
  Updates to these files are mechanically synced and will be overwritten.

## Repo layout

This repo is a personal-utilities collection. Top-level structure:

- `bin/` -- executable utilities, mostly Python with PEP 723 shebangs. Each
  utility is a single file (or a single file plus a backup sibling like
  `bin/borgadm~`).
- `tests/` -- pytest suite. Shared fixtures live in `conftest.py`; the full
  suite runs via `tests/run_all.py`.
- `Applications/` -- macOS app bundles built and consumed by some of the
  utilities (e.g. BorgAdm.app for `borgadm`'s TCC / Full Disk Access flow).
- `ruff.toml` -- ruff config shared across all Python in the repo.
- `tmp/` -- gitignored scratch for plans, review inputs, and other ephemeral
  working files.

All code in this repo is cross-platform and may execute on macOS or Linux.
Platform-specific code (launchd vs systemd, macOS-only commands, etc.) is
gated by platform checks.

## Testing

- pytest, with the suite runnable via `tests/run_all.py`. The delivered
  `epilatow-repo-shared` gates run as a phase of that script and also via
  `uv run pytest _repo_shared/tests`.
- The test suite owns ruff and mypy enforcement -- a green run is the gate for
  "ready to commit".
- Tests carrying the `@pytest.mark.e2e` marker are end-to-end suites that
  subprocess the script under test (currently just the borgadm suite under
  `tests/test_borgadm.py`). They are slow (tens of minutes serially) and are
  excluded from the default `tests/run_all.py` run. Run them via
  `tests/run_all.py --e2e` (or the individual test file with `--e2e`). When
  making changes to a utility that has an e2e suite, run with `--e2e` before
  declaring the change complete.

## Conventions

- Python `line-length` is 80 (`ruff.toml`), intentionally diverging from the
  shared `DEVELOPMENT_SHARED.md`'s 79 -- this is the repo's pre-onboarding
  setting and per the layered-docs precedence rule this file's value wins. The
  delivered ruff gate reads `ruff.toml`, so 80 is what it enforces.
