# utils

A personal collection of command-line utilities. Each entry under `bin/` is a
self-contained tool run through [uv](https://docs.astral.sh/uv/). See
[DEVELOPMENT.md](DEVELOPMENT.md) for how the repo is laid out, built, and
tested.

## Installation

To use the utilities in this repo:

```bash
# Update PATH and MANPATH
PATH=$PATH:$HOME/.local/bin
MANPATH=$MANPATH:$HOME/.local/share/man

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone this repo
git clone https://github.com/epilatow/utils

# Use linkfiles to link the utilities and man pages into ~/.local
utils/bin/linkfiles install utils/bin $HOME/.local/bin
utils/bin/linkfiles install utils/share $HOME/.local/share
```

## Documented utilities

- **[borgadm](docs/borgadm.md)**\
  borgadm is a wrapper around borgbackup designed to manage backup sets, where
  a set is a group of separate borg backups (archives) created with different
  create options. It creates backups from the named sets, verifies repository
  and archive integrity, prunes old and partial archives by retention policy,
  and restores archives via extract or rsync. A passphrase- and SSH-key-based
  workflow handles authentication to local and remote repositories, and it can
  schedule unattended backups and checks through crony(1) on macOS (launchd)
  and Linux (systemd).
- **[crony](docs/crony.md)**\
  Crony is a multi-platform user-level scheduled-job manager. It supports jobs
  on macOS/darwin (via launchd) and linux (via systemd). It reads one or more
  TOML config bundles and can deploy the corresponding platform units. It runs
  jobs through a uniform shim, which provides consistent logging, execution
  gates, timeouts, environment management, etc. Jobs can be grouped, and
  groups or jobs run on a schedule. Jobs can also be managed manually
  (enabled/disabled independently of the underlying scheduler, and run at will
  via the `trigger` subcommand). Crony supports the following notification
  mechanisms for job failures: email/smtp, ntfy, and pop-ups (on
  macOS/darwin).
- **[darwin-tz-watchdog](docs/darwin-tz-watchdog.md)**\
  darwin-tz-watchdog restarts macOS UserEventAgent-Aqua when its cached
  timezone has gone stale. UserEventAgent-Aqua dispatches user-level launchd
  StartCalendarInterval triggers, but it reads the timezone once at startup
  and never re-reads it when /etc/localtime changes. After a timezone switch
  (e.g. travel) calendar-interval jobs keep firing against the stale cached
  zone -- a job set for 02:30 local fires at 02:30 in the previously cached
  zone, potentially hours off -- until the agent is restarted. This watchdog
  detects that condition and restarts the agent so it picks up the current
  zone.
- **[firefox-cookies](docs/firefox-cookies.md)**\
  firefox-cookies extracts cookies from a Firefox profile and writes them to
  stdout in Netscape or JSON format. It reads both the on-disk cookie database
  (cookies.sqlite) and the session-store backup (recovery.jsonlz4), so the
  session cookies Firefox keeps only in memory are included alongside the
  persisted ones. Cookies can be filtered by domain and by container, and the
  profile is auto-detected or selected by name or path.
- **[linkfiles](docs/linkfiles.md)**\
  linkfiles symlinks the contents of a source directory tree into a target
  directory, recreating the tree there. Installations are tracked, so later
  runs can add new links, audit existing ones, and clean up dangling links.
  While linkfiles can be used to link files from any source to any
  destination, its primary purpose is linking repository files into $HOME/ (as
  dotfiles) and $HOME/.local/.
- **[secure-archiver](docs/secure-archiver.md)**\
  secure-archiver builds 7z encrypted archives from a TOML config, bundling
  local files and secrets fetched from 1Password into each archive and
  encrypting it with a password read from 1Password. Each archive is
  timestamped and paired with a plaintext readme describing how to open it,
  and old revisions are pruned to a configurable count. The 1Password CLI
  (`op`) and the 7-Zip CLI (`7zz`) must be on PATH.

## Undocumented utilities
