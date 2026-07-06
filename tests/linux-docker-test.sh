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
#   - systemd + systemd-sysv: booted as PID 1 so the crony e2e suite can
#     drive a real `systemctl --user`, and for systemd-analyze
#     (test_crony_platform_systemd.py's TestSystemdAnalyzeVerify).
#   - dbus + dbus-user-session: the per-user systemd manager the crony
#     e2e timer tests need; started via lingering (see below).
#   - borgbackup: the borgadm `--e2e` suite forks the real borg binary.
#   - the pinned pandoc (via `scripts/pandoc install`): test_render_docs.py
#     renders the roff man page and compares it to the checked-in copy; the
#     version is pinned (scripts/pandoc-pin.json) because pandoc's roff
#     output varies between versions.
#   - Node >= 20 (from the base image): the repo-shared markdown gate runs
#     `npx markdownlint-cli2`, and markdownlint-cli2 requires Node >= 20.
#   - git: uv builds the repo-shared gate from its `git+https://` source
#     (see pyproject.toml), so git must be present to resolve deps.
#   - a non-root user: test_secure_archiver asserts a 0o555 dir is "not
#     writable", which root bypasses -- so the suite runs as `tester`.
#
# The container boots systemd as PID 1 (needs --privileged + the cgroup
# mount), then starts a lingering user manager for `tester` so the crony
# e2e can reach `systemctl --user`. Without a user manager those tests
# skip rather than run; here they run.
#
# We extract `git archive HEAD` (the clean committed tree) and re-init a
# throwaway git repo from it in the container, so tests that shell out to
# git (test_render_docs enumerates `bin/` via `git ls-files`) work as on
# a real CI checkout. `git add -A` honors `.gitignore`, and uv's project
# venv lives outside the tree (UV_PROJECT_ENVIRONMENT), so neither a stray
# `.venv` nor build output enters the snapshot.
#
# Tests the committed HEAD (a clean `git archive`, not the working tree).
# Extra args pass through to run_all.py, e.g.:
#   tests/linux-docker-test.sh --e2e
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
cid=""
cleanup() {
    [ -n "$cid" ] && docker rm -f "$cid" >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT
git -C "$repo_root" archive HEAD -o "$work/repo.tar"

# Boot systemd as PID 1 so `systemctl --user` works. The initial process
# installs the toolchain and unpacks the repo, then execs /sbin/init;
# subsequent `docker exec` calls drive the booted system.
cid=$(docker run -d --privileged --cgroupns=host \
    --tmpfs /run --tmpfs /run/lock \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    -v "$work":/in:ro \
    node:20-bookworm \
    bash -c '
        set -e
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq git borgbackup systemd systemd-sysv \
            dbus dbus-user-session sudo ca-certificates curl >/dev/null
        curl -LsSf https://astral.sh/uv/install.sh \
            | env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null 2>&1
        useradd -m tester
        mkdir -p /app
        tar -xf /in/repo.tar -C /app
        # Re-init a throwaway git repo from the extracted tree so tests
        # that shell out to git (test_render_docs enumerates bin/ via
        # `git ls-files`) work as they do on a real CI checkout, which has
        # a .git. `git add -A` honors .gitignore, so no stray build output
        # enters the snapshot.
        git -C /app init -q
        git -C /app add -A
        git -C /app -c user.email=ci@crony.test -c user.name=crony-ci \
            commit -qm "linux-docker-test snapshot" >/dev/null
        chown -R tester:tester /app
        exec /sbin/init
    ')

# Wait for the toolchain install + systemd boot to finish. The install
# runs before `exec /sbin/init`, so systemctl is absent until then.
echo "### waiting for systemd to boot in the container ..."
for _ in $(seq 1 120); do
    state=$(docker exec "$cid" systemctl is-system-running 2>/dev/null || true)
    case "$state" in
        running | degraded) break ;;
    esac
    sleep 2
done

# Start a lingering user manager for tester so `systemctl --user` (the
# crony e2e timer tests) has a bus to talk to.
uid=$(docker exec "$cid" id -u tester)
docker exec "$cid" loginctl enable-linger tester
docker exec "$cid" systemctl start "user@${uid}.service"

echo "### $(docker exec "$cid" uname -srm)"
docker exec -u tester \
    -e HOME=/home/tester \
    -e XDG_RUNTIME_DIR="/run/user/${uid}" \
    -e UV_PROJECT_ENVIRONMENT=/home/tester/uvenv \
    -e RUN_ARGS="$*" \
    "$cid" \
    bash -c 'cd /app && uv run scripts/pandoc install \
        && exec uv run tests/run_all.py $RUN_ARGS'
