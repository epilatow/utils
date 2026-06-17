#!/bin/bash
# Run the full test suite on Linux in a throwaway Docker container.
#
# This is a MANUAL tool, not part of the automated run. `run_all.py`
# discovers only `tests/test_*.py`, and CI (.github/workflows/test.yml)
# invokes `run_all.py` directly -- neither references this script, so it
# never runs in CI or by default. It exists so a non-Linux dev host (or
# an agent on macOS) can reproduce the CI Linux leg before merging a
# cross-platform change.
#
# It mirrors the CI Linux leg (ubuntu-latest running `run_all.py --e2e`).
# CI gets most of its toolchain implicitly from the GitHub runner; a bare
# container has to add it, so the deps below are explicit. Each one is
# required by a specific phase:
#   - systemd (systemd-analyze): test_crony_platform_systemd.py's
#     TestSystemdAnalyzeVerify, which validates rendered systemd units --
#     it is skipped (not run) without systemd-analyze.
#   - borgbackup: the borgadm `--e2e` suite forks the real borg binary.
#   - pandoc 3.10: test_render_docs.py renders the roff man page and
#     compares it to the checked-in copy; the version is pinned because
#     pandoc's roff output varies between versions.
#   - Node >= 20 (from the base image): the repo-shared markdown gate runs
#     `npx markdownlint-cli2`, and markdownlint-cli2 requires Node >= 20.
#   - git: uv builds the repo-shared gate from its `git+https://` source
#     (see pyproject.toml), so git must be present to resolve deps.
#   - a non-root user: test_secure_archiver asserts a 0o555 dir is "not
#     writable", which root bypasses -- so the suite runs as `tester`.
#
# We extract `git archive HEAD` (a clean tree with no `.git`), so the
# repo-shared quality gate discovers files by walking the tree rather than
# via `git ls-files`; keeping uv's project venv out of the tree
# (UV_PROJECT_ENVIRONMENT) keeps that walk from linting a stray .venv.
#
# Tests the committed HEAD (a clean `git archive`, not the working tree).
# Extra args pass through to run_all.py, e.g.:
#   tests/linux-docker-test.sh --e2e
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git -C "$repo_root" archive HEAD -o "$work/repo.tar"

# `bash -s` reads the in-container script from stdin (the quoted heredoc,
# so $RUN_ARGS stays literal and expands inside the container from -e).
docker run --rm -i \
    -e RUN_ARGS="$*" \
    -v "$work":/in:ro \
    node:20-bookworm \
    bash -s <<'IN_CONTAINER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git borgbackup systemd sudo ca-certificates curl >/dev/null
# uv into a system dir so the non-root test user can run it.
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null 2>&1
useradd -m tester
mkdir -p /app
tar -xf /in/repo.tar -C /app
chown -R tester:tester /app
echo "### $(uname -srm)  node=$(node --version)  uv=$(uv --version)"
echo "### systemd-analyze=$(command -v systemd-analyze)  borg=$(command -v borg)"
# Non-root, with uv's project venv outside /app (see header for both).
# Fetch the pinned pandoc into /app/.tools (the same installer local devs
# and CI use) before the suite, so the man-page gate renders with it.
sudo -u tester env HOME=/home/tester UV_PROJECT_ENVIRONMENT=/home/tester/uvenv \
    bash -c "cd /app && uv run scripts/pandoc install && exec uv run tests/run_all.py $RUN_ARGS"
IN_CONTAINER
