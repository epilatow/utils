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

## Prerequisites

- `uv` (the test suite and every PEP 723 script run through it).
- A pinned pandoc, fetched into `.tools/` (gitignored) by
  **`scripts/pandoc install`** -- run it once after cloning. The man-page
  freshness gate renders with this exact binary and nothing else (not a system
  / Homebrew pandoc), so the gate fails until it is installed. See Testing for
  why the version is pinned.

## Repo layout

This repo is a personal-utilities collection. Top-level structure:

- `bin/` -- executable utility entry points, mostly Python with PEP 723
  shebangs. An entry is usually a single file; some also import shared
  first-party code from `src/` (see below).
- `src/` -- first-party importable Python packages (e.g. `common`), shared
  across utilities. These packages carry no shebang and are never executed
  directly (the `src/<module>.py` alias symlinks below are the exception --
  they point at executable `bin/` and `scripts/` entries). Each `bin/` entry
  that uses them prepends `<repo>/src` to `sys.path` -- via
  `Path(__file__).resolve()`, which follows any symlink to the real file so
  the repo-root walk is correct even when the entry is invoked through a
  symlink -- before importing `common`. `[tool.mypy] mypy_path = ["src"]` in
  `pyproject.toml` lets the code-quality gate's `mypy --strict` resolve these
  imports statically.
- `src/<module>.py` alias symlinks -- each extension-less executable entry
  (under `bin/` or `scripts/`) has a matching `src/<module>.py` symlink
  pointing at it (e.g. `src/linkfiles.py -> ../bin/linkfiles`,
  `src/firefox_cookies.py -> ../bin/firefox-cookies`,
  `src/render_docs.py -> ../scripts/render-docs`). The alias gives the script
  an importable, `mypy`-resolvable module name, so the test suite imports it
  as a typed module (`import linkfiles`) instead of loading it through
  `SourceFileLoader`, and so ruff/mypy auto-discover it as a `.py` file
  instead of needing a manual `python-targets` entry. `crony` is the exception
  -- its logic already lives in the `src/crony` package, so it has no alias
  and `bin/crony` stays a manual `python-targets` entry.
- `scripts/` -- developer/build tooling, not user-facing utilities.
  `scripts/render-docs` generates docs from the utilities' argparse parsers (a
  roff man page and a GitHub-browsable GFM doc; see Testing); each utility is
  a `ManSpec`, and adding one is appending to the script's `_SPECS`.
  `scripts/pandoc` manages the pinned pandoc the man-page gate needs
  (`pandoc install` fetches it into `.tools/`; see Testing), with the pin in
  `scripts/pandoc-pin.json`. Like `bin/` entries these are PEP 723 executables
  with a `src/<module>.py` alias for import + lint coverage.
- `share/man/man<N>/` -- roff man pages generated from the argparse parsers
  via pandoc (currently `share/man/man1/crony.1`). Generated build artifacts,
  never hand-edited.
- `docs/` -- GitHub-browsable GFM docs generated from the argparse parsers
  (currently `docs/crony.md`), for browsing on GitHub and eventual linking
  from a README. Built in pure Python (no pandoc) and run through mdformat, so
  they satisfy the prose mdformat / markdownlint gates like any hand-written
  doc. Generated build artifacts, never hand-edited; the freshness gate (see
  Testing) fails if they drift.
- `tests/` -- pytest suite. Shared fixtures live in `conftest.py`; the full
  suite runs via `tests/run_all.py`.
- `Applications/` -- macOS app bundles built and consumed by some of the
  utilities. Crony.app is a Mach-O wrapper that lets a Python script (which
  can never hold the grant itself) run under a Full Disk Access grant: it is
  the generic disclaim-and-exec wrapper crony routes a full-disk-access job's
  command through (borgadm's scheduled backup relies on it via crony rather
  than carrying its own wrapper). Its C source (`Crony.c`) documents the TCC
  responsible-process handling that keeps the grant working when a scheduler
  such as crony runs the wrapper; the C is kept formatted to the repo's
  `.clang-format` (apply via `xcrun clang-format -i`).
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
- `tests/test_render_docs.py` is a freshness gate: for each utility it
  re-renders the roff man page (e.g. `share/man/man1/crony.1`) and the GFM doc
  (e.g. `docs/crony.md`) from the argparse parser and fails if either
  checked-in file differs. The GFM doc is built in pure Python (plus
  mdformat), so its comparison needs no pandoc; the roff comparison shells out
  to pandoc, so **pandoc is a required dev/CI dependency** -- a missing pinned
  pandoc fails the gate rather than skipping it. pandoc's roff output varies
  by version, so a single pandoc is pinned in **`scripts/pandoc-pin.json`**
  (the version plus the sha256 of each platform asset): run
  **`scripts/pandoc install`** to fetch exactly that release into `.tools/`
  (gitignored). Each download is checksum-verified against the pin before it
  is trusted, so an artifact that changed upstream is rejected. The tooling
  and the gate use **only** that binary -- never a `PATH` pandoc -- so the
  rendered man page can't vary with whatever pandoc happens to be installed;
  run the installer once before the gate will pass locally. CI and
  `tests/linux-docker-test.sh` run the same installer on Linux and macOS, so
  every environment renders with the identical binary (and CI exercises the
  installer as a side effect). After any change to a utility's CLI surface or
  `--help` text, run `scripts/render-docs` to regenerate the man page and GFM
  doc, and commit them alongside the code.
- Tests carrying the `@pytest.mark.e2e` marker are end-to-end suites that
  subprocess the script under test (currently just the borgadm suite under
  `tests/test_borgadm.py`). They are slow (tens of minutes serially) and are
  excluded from the default `tests/run_all.py` run. Run them via
  `tests/run_all.py --e2e` (or the individual test file with `--e2e`). When
  making changes to a utility that has an e2e suite, run with `--e2e` before
  declaring the change complete.
- CI runs `--e2e` on the Linux leg only. GitHub's hosted macOS runners are
  weak, throttled VMs that run this process-spawn-heavy suite ~20x slower than
  Linux and intermittently cross its 120s per-call timeout, while Linux runs
  the identical suite reliably in under a minute (borg is cross-platform, so
  coverage is the same). The macOS CI leg runs only the non-e2e suite -- which
  is where the macOS-specific code lives (the Crony.app C wrapper, the
  crony-backed `automate` subcommand) -- so a local `--e2e` run is the way to
  exercise the e2e suite on macOS.
- `tests/linux-docker-test.sh` reproduces the CI Linux leg from a non-Linux
  host: it runs the full suite (extra args pass through to `run_all.py`, e.g.
  `--e2e`) in a throwaway Linux container. It is a manual tool -- `run_all.py`
  discovers only `test_*.py` and CI invokes `run_all.py` directly, so it never
  runs automatically. The container adds what the GitHub runner supplies
  implicitly -- Node 20+ (the markdownlint gate), systemd-analyze (the
  systemd-unit verify test), borg (the e2e suite), the pinned pandoc (the
  `test_render_docs` man-page gate, via `scripts/pandoc install`), and git (uv
  builds the repo-shared gate from a git source) -- and runs as a non-root
  user (the secure_archiver permission test); the script header maps each dep
  to the test that needs it. It tests the committed HEAD, not the working
  tree.

### Updating the pinned pandoc

The pinned pandoc (`scripts/pandoc-pin.json`) should be reviewed and bumped
periodically so the generated man pages track current pandoc.
`scripts/pandoc update [version]` does the bump in a throwaway worktree off
`origin`'s default branch: it rewrites the pin (the new version plus each
platform asset's sha256, read from the release's published digests), fetches
and checksum-verifies the new pandoc (the given version, or the latest release
if omitted), regenerates the docs, runs the gate suite, and commits the
result.

- Without `--push` it leaves the committed bump in the worktree for you to
  review and land by hand.
- With `--push` it lands the bump on the default branch automatically -- but
  **only when the rendered man page is byte-identical** to before (a pure
  version bump with nothing to review). pandoc renders only the roff man page
  (the GFM doc is pure-Python and unaffected by a pandoc bump), so that is the
  artifact checked. If the new pandoc changes it, `--push` refuses and keeps
  the worktree for a human to inspect. A successful `--push` removes the
  worktree; pass `--keep-worktree` to retain it for inspection.

A no-op run (target already pinned) exits cleanly without touching the working
tree, so it is safe to run on a dirty checkout.

## Conventions

- Python `line-length` is 80 (`ruff.toml`), intentionally diverging from the
  shared `DEVELOPMENT_SHARED.md`'s 79 -- this is the repo's pre-onboarding
  setting and per the layered-docs precedence rule this file's value wins. The
  delivered ruff gate reads `ruff.toml`, so 80 is what it enforces.
