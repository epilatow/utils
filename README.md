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
- **[linkfiles](docs/linkfiles.md)**\
  linkfiles symlinks the contents of a source directory tree into a target
  directory, recreating the tree there. Installations are tracked, so later
  runs can add new links, audit existing ones, and clean up dangling links.
  While linkfiles can be used to link files from any source to any
  destination, its primary purpose is linking repository files into $HOME/ (as
  dotfiles) and $HOME/.local/.

## Undocumented utilities

- **borgadm**\
  Borg backup manager
- **darwin-tz-watchdog**\
  Restart macOS UserEventAgent-Aqua when its cached timezone is stale relative
  to /etc/localtime.
- **firefox-cookies**\
  Firefox cookie extraction utility
- **secure-archiver**\
  Generate 7z encrypted archives containing files and 1Password data that are
  encrypted using passwords stored in 1Password
