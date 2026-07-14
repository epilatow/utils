# crony - user-level scheduled-job manager

## SYNOPSIS

`crony <command> ...`

## DESCRIPTION

Crony is a multi-platform user-level scheduled-job manager. It supports jobs
on macOS/darwin (via launchd) and linux (via systemd). It reads one or more
TOML config bundles and can deploy the corresponding platform units. It runs
jobs through a uniform shim, which provides consistent logging, execution
gates, timeouts, environment management, etc. Jobs can be grouped, and groups
or jobs run on a schedule. Jobs can also be managed manually (enabled/disabled
independently of the underlying scheduler, and run at will via the `trigger`
subcommand). Crony supports the following notification mechanisms for job
failures: email/smtp, ntfy, and pop-ups (on macOS/darwin).

Subcommands fall into categories:

- configuration (`config`): manage config files
- deployment (`apply`, `destroy`): deploy configured jobs
- runtime state (`enable`, `disable`): manage scheduler arming
- operational (`status`, `logs`, `trigger`): manage deployed jobs

Configuration is managed via bundles:

- `~/.config/crony/config.toml` -> bundle name "default"
- `~/.config/crony/config/<x>.toml` -> bundle name "\<x>"

Bundles are independent. Job and group names are namespaced as
\<bundle>.\<name>; bare CLI input (`crony trigger foo`) is shorthand for
`default.foo`. The `-b/--bundle <name>` flag on various subcommands scopes
name resolution to that bundle.

## GETTING STARTED

To use crony, start by generating a crony config file with
`crony config init`. Then edit the config file to define jobs and notification
mechanisms. You can validate the config file with `crony config validate`.
Internally, crony tracks jobs via UUIDs, so every job needs one. You can
auto-assign UUIDs in a config file with `crony config update` (or generate one
with `crony config generate-uuid` and add it manually).

Once a crony config exists you can inspect defined and deployed jobs (and
their execution status) with `crony status`. To deploy configured jobs run
`crony apply`. If you change the configuration for a deployed job,
`crony status` will report that the job is `stale` and you can update the
deployed configuration by running `crony apply` again. To see execution logs
(file path, content, etc.) for a job use `crony logs`. To manually trigger a
job run use `crony trigger`. To disable a job (without destroying it), use
`crony disable` (you can subsequently re-enable it with `crony enable`). To
remove deployed jobs run `crony destroy`. (Destroying jobs removes all
previous job state: log files, last run information, etc.)

## PLATFORM SPECIFICS

On systemd-based platforms, for scheduled jobs to execute when a user is not
logged in, "linger" must be enabled. Enabling linger requires sudo access and
can be done via the following command: `sudo loginctl enable-linger $USER`.
The `crony status` and `crony config validate` commands will check if systemd
linger is enabled, and if not will emit a warning asking the user to enable
it. While linger is disabled, a scheduled job whose time arrives while the
user is logged out will not run then; instead it runs immediately the next
time the user logs in.

## COMMON ARGUMENTS

- **`job`**\
  A job (or group) identity: the full namespaced name `<bundle>.<name>`, or
  the `<bundle>:<uuid>` form for an entry with no recoverable name. A bare
  `name` is shorthand for `default.<name>`. Subcommands that accept more than
  one take a space-separated list (shown as `[job ...]`).
- **`--all`**\
  Act on every entry instead of an explicit name list. Scope to one bundle
  with -b/--bundle.
- **`-b, --bundle BUNDLE`**\
  Operate on config bundle BUNDLE. Where the subcommand takes names, bare
  names resolve within BUNDLE and a qualified name must match it.

## SUBCOMMANDS

### `config init [--force] [-b BUNDLE]`

Generate a default config file. With --bundle, write config/\<bundle>.toml
instead of config.toml.

- **`--force`**\
  Overwrite an existing config file.

### `config validate [-b BUNDLE | --file PATH]`

Lint config; report orphans, linger, etc. With --bundle, cross-host checks
(orphans, linger) are skipped.

- **`--file PATH`**\
  Structurally validate a single TOML file as a bundle (named after its stem,
  or 'default' for the installed config.toml), independent of the config dir.
  Exits non-zero on any error. Lets a tool pre-flight a bundle.

### `config update [-b BUNDLE]`

Assign UUIDs to jobs and groups that lack one. Without --bundle every bundle
file is scanned; with it, only that bundle's file.

### `config generate-uuid`

Print one freshly-minted UUID.

### `apply [-v] [-b BUNDLE] [job ...]`

Render and activate platform units to match config. With no job arguments this
is a full sync (install missing, fix drift, remove orphans); with names it
surgically updates just those entries.

- **`-v, --verbose`**\
  Also print 'unchanged' lines.

### `destroy [--all] [--orphans] [-b BUNDLE] [job ...]`

Remove platform units -- the platform unit files and the entry's state dir
both go away. Requires a target: job(s), --all, or --orphans.

- **`--orphans`**\
  Limit removal to entries with on-disk remnants that no config selects on
  this host. Combinable with -b/--bundle; mutually exclusive with positional
  names and --all.

### `enable [--all] [-b BUNDLE] [job ...]`

Re-arm the named jobs' schedules (clear the operator-disable). Requires a
target: job(s) or --all.

### `disable [--all] [-b BUNDLE] [job ...]`

Disarm the named jobs' schedules: loaded and triggerable, but not firing on
their own. Requires a target: job(s) or --all.

### `trigger [--all] [-b BUNDLE] [-w] [--trigger-timeout TRIGGER_TIMEOUT] [job ...]`

Ask the platform scheduler to fire the named jobs now, via the same path a
scheduled fire uses. Requires a target: job(s) or --all.

- **`-w, --wait`**\
  Block until each named entry's next completion and exit with that exit code
  (worst across multiple names).
- **`--trigger-timeout TRIGGER_TIMEOUT`**\
  Override [defaults].trigger_timeout_sec (seconds to wait for a runner to
  come online after kickstart). Only meaningful with --wait.

### `status [-a] [--cols COLS] [-b BUNDLE] [--config-current | --config-pending] [--exclude-healthy] [job ...]`

Print resolved state per job. With no job arguments every job is shown; pass
job names to restrict the table. Many columns are source-selected -- they show
the pending (config) value by default, or the applied value under
--config-current -- and a trailing `^` on a value marks a divergence between
the two.

- **`-a, --all`**\
  List all defined entries, including ones masked on the current host. Entries
  can be masked because they are unused or associated with a different
  platform and/or host.
- **`--cols COLS`**\
  Comma-separated columns to display. See the Status Columns reference for
  valid names, aliases, and descriptions.
- **`--config-current`**\
  For dual-source columns (name, schedule, groups, ...), show the
  currently-active (applied) value.
- **`--config-pending`**\
  For dual-source columns (name, schedule, groups, ...), show the pending
  value from the config (the default).
- **`--exclude-healthy`**\
  Drop rows that are config=synced, enabled in the scheduler, and last in
  ok/never/gated. Output is flat (no tree indent). Always exits 0 -- this is a
  filter on the display, not a gate.

### `logs [-n N] [-s SINCE] [-t | -l] [-p] job`

Print a job's recent log output.

- **`-n N`**\
  Print the last N lines (default: 200, or 10 with --tail).
- **`-s, --since SINCE`**\
  "1h", "2d", or ISO timestamp.
- **`-t, --tail`**\
  Follow appended output.
- **`-l, --latest`**\
  Print only the latest run's entry.
- **`-p, --path`**\
  Print the log file path and exit (no content).

### `notify-test [--channel CHANNEL] [-b BUNDLE]`

Send a synthetic failure notification.

- **`--channel CHANNEL`**\
  Send through one specific channel. Bare name (`ntfy`) resolves against the
  selected bundle; fully qualified `<bundle>.<channel>` overrides --bundle.

## STATUS COLUMNS

### Default Columns

- **`job-or-uuid`**\
  Normally the full job name `<bundle>.<short>`, but in the case of a job
  naming conflict or a broken job with no recoverable name this column may
  report `<bundle>:<UUID>`.
- **`config`**\
  See "CONFIG values".
- **`schedule`**\
  See "SCHEDULE values".
- **`status`**\
  See "STATUS values".
- **`last-ran`**\
  Relative time of the last job start.

### Optional Columns

- **`<flag>`**\
  One opt-in true/false column per capability flag (`--cols interactive`,
  etc.). Request by name; the `all` alias omits these in favor of the compact
  `flags` column. See "FLAG values".
- **`flags`**\
  Comma-separated list of capability flags enabled for the job. See "FLAG
  values".
- **`groups`**\
  Comma-separated list of job groups containing this job. A job can only have
  one unmasked parent, but can have multiple masked parents. Empty when the
  job isn't part of any group.
- **`job`**\
  Full job name: `<bundle>.<short>`. This name may not be usable with
  subcommands if a pending configuration update will assign this name to a new
  job, in which case you can use the `<bundle>:<UUID>` name to directly
  address this job. May be empty for a broken job with no recoverable name.
- **`kind`**\
  Job type: "job" or "group".
- **`log-file`**\
  Filesystem path of the job's log file.
- **`masked-by`**\
  A comma-separated list of reasons why a job is masked (CONFIG = masked) on
  the current host. See "MASKED values".
- **`priority`**\
  Job scheduling priority: high | normal | low. Empty for groups.
- **`stale`**\
  A comma-separated list of the snapshot fields that have diverged between the
  pending config and the applied unit (CONFIG = stale). Each is named the way
  that field is known -- the config-file knob, a capability flag, a status
  column, or its dash-spelled snapshot attribute.
- **`timeout`**\
  Job wallclock cap: `<n>s`. The job will be killed if its wallclock execution
  time exceeds this cap. May be `none` for uncapped jobs.
- **`unit-config-1`**\
  Filesystem path of the platform config unit. Empty when no config unit
  exists on disk.
- **`unit-config-2`**\
  Filesystem path of the platform's second unit -- the systemd timer, or the
  launchd start-time-jitter companion for a jittered interval job. Empty for a
  job with no second unit (an unscheduled or grouped job, or a calendar /
  short-interval job on macOS/darwin).
- **`unit-name`**\
  Platform unit identifier.
- **`uuid`**\
  The job's `<bundle>:<UUID>` name.

### Column Aliases

- **`default`**\
  The columns shown when `--cols` is omitted: job-or-uuid, config, schedule,
  status, last-ran.
- **`all`**\
  Every column except the per-flag columns (use the compact `flags` instead),
  `masked-by` (kept only when a masked entry is present), and the optional
  `unit-config-2` (shown only where a second unit is present). Naming an
  excluded column explicitly still shows it.
- **`unit-files`**\
  unit-config-1, plus the optional unit-config-2 where present.

### CONFIG values

- **`synced`**\
  A deployed job's configuration is up-to-date.
- **`stale`**\
  A deployed job's configuration has diverged from its configuration file
  definition, but the job is still runnable. Run `apply` to update the
  deployed configuration.
- **`broken`**\
  A deployed job's configuration is broken and un-runnable. Run `apply` to fix
  the deployed configuration.
- **`missing`**\
  A job exists in the config file but has not yet been deployed.
- **`orphan`**\
  A deployed job (or some job-related resource) is not defined in any
  configuration file. Run `destroy --orphans` to clean up the deployed
  configuration.
- **`masked`**\
  A job can't be deployed on the current host due to configuration filters
  (usually a mismatched `platform` or `host` directive).
- **`error`**\
  The job configuration file definition (i.e. the pending or requested
  configuration) is broken, or its dependencies can't be met (e.g.
  full-disk-access has been requested on macOS/darwin, but the Crony.app
  wrapper doesn't have full-disk-access). If the job was previously deployed,
  it will continue to run and can be managed, but the deployed configuration
  can't be updated with `apply` until the pending configuration issue is
  fixed.

### SCHEDULE values

- **`OnCalendar schedule`**\
  A (restricted) systemd OnCalendar schedule for job execution.
- **`interval=<x>`**\
  A systemd time-span interval for job execution.
- **`grouped`**\
  A job/group with no schedule of its own, it runs when triggered by a parent
  job group.
- **`disabled`**\
  A job that has been disabled via the `disable` subcommand; it will not be
  run via any schedule. It can be run manually via the `trigger` subcommand,
  and re-enabled via the `enable` subcommand.

### STATUS values

- **`ok`**\
  The job's last run completed successfully. For a job group: the group got
  every child it was asked to run running (a disabled child is skipped, not
  run) -- whatever those children then did is reported on their own rows, and
  a failing child does not fail its group.
- **`fail`**\
  The job's last run failed (exited with a non-zero status). For a job group:
  the group could not fire one of its children -- that child's unit or
  snapshot is missing on this host, or the scheduler refused to fire it.
- **`timeout`**\
  The job was killed after exceeding its wallclock execution timeout. For a
  job group: the group ran out of time on a child before it ever saw it
  running -- its cumulative budget was spent, or the scheduler never started
  the child.
- **`gated`**\
  The job was skipped due to an execution gate. This is not considered as a
  job failure.
- **`canceled`**\
  An interactive job run that was canceled / skipped by the user.
- **`crashed`**\
  The scheduler failed to launch a job, or the job was killed/crashed before
  it could save its exit status to disk.
- **`running`**\
  The job is currently running.
- **`pending`**\
  An interactive job is either waiting for an active user, or waiting for that
  user to confirm execution (via a pop-up dialog).
- **`never`**\
  A newly deployed job that hasn't been run yet.
- **`unknown`**\
  We're unable to determine the job status.

### FLAG values

- **`interactive`**\
  macOS/Darwin only. Delay job execution until an active user is detected, and
  then request the user to confirm execution of the job via a pop-up.
- **`keep-awake`**\
  Prevent the system from sleeping while the job is executing.
- **`full-disk-access`**\
  macOS/Darwin only. Execute the job with TCC Full Disk Access permissions.

### MASKED values

- **`host`**\
  The job has been scoped to a different host.
- **`platform`**\
  The job has been scoped to a different platform.
- **`unused`**\
  The job is not scheduled to run (directly or via a job group).
- **`empty`**\
  The job group doesn't contain any unmasked jobs.

### Colors

On a color-capable TTY (and NO_COLOR unset) some cells are colored; redirected
or piped output is plain, where drift shows as a trailing `^` plus a footnote
legend instead.

- **`red`**\
  CONFIG broken / error / missing / orphan; STATUS canceled / crashed / fail /
  timeout; SCHEDULE disabled.
- **`yellow`**\
  CONFIG stale, plus any cell that diverged from the applied state (on a color
  stream its `^` marker is dropped in favor of the color).

## EXIT STATUS

| Code | Meaning                       |
| :--- | :---------------------------- |
| `0`  | Success                       |
| `1`  | Warning                       |
| `2`  | Usage/argument error          |
| `3`  | Configuration error           |
| `4`  | General error                 |
| `5`  | Subprocess error              |
| `6`  | Operation timed out           |
| `7`  | Crashed (unhandled exception) |
