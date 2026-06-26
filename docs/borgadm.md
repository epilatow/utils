# borgadm - manage borg backup repositories and scheduled backups

## SYNOPSIS

`borgadm <command> ...`

## DESCRIPTION

borgadm is a wrapper around borgbackup designed to manage backup sets, where a
set is a group of separate borg backups (archives) created with different
create options. It creates backups from the named sets, verifies repository
and archive integrity, prunes old and partial archives by retention policy,
and restores archives via extract or rsync. A passphrase- and SSH-key-based
workflow handles authentication to local and remote repositories, and it can
schedule unattended backups and checks through crony(1) on macOS (launchd) and
Linux (systemd).

The sets are defined in the config file; each `create` run writes one archive
per set, named {BACKUP_NAME}-{set_name}-YYYYMMDD_HHMMSS_NofM. The NofM suffix
records a set's position so a timestamp is "full" only when every configured
set is present and "partial" otherwise. The list, check, prune, and restore
subcommands operate on these timestamps, and prune can also clean up partial
or unrecognized ("unknown") archives.

Configuration lives in ~/.borgadm, an INI-style file naming the repository,
the backup sets, retention counts, credential file paths, and check
thresholds. The `automate` subcommand deploys borgadm's scheduled create and
check jobs through crony(1) (launchd on macOS, systemd on Linux);
`environment` prints the shell exports needed to run the borg CLI directly
against the configured repository.

## GETTING STARTED

Generate a starter config at ~/.borgadm, then edit it to set at least
BORG_REPO and your BACKUP_SETS (see CONFIGURATION):

```text
    borgadm config init
```

Check the config parses and is complete before the first backup:

```text
    borgadm config validate
```

To create a backup of every configured set and prune old archives run:

```text
    borgadm create
```

To inspect what is in the repository and verify its integrity:

```text
    borgadm list
    borgadm check full
```

To restore the latest full backup into a directory:

```text
    borgadm extract /path/to/restore
```

To schedule unattended backups and checks via crony(1) run:

```text
    borgadm automate enable
```

## COMMON ARGUMENTS

- **`archive`**\
  A full archive name as shown by `borgadm list`, or a YYYYMMDD_HHMMSS
  timestamp standing for every archive at that time.
- **`--config CONFIG`**\
  Path to the borgadm config file to read. (default: ~/.borgadm)
- **`--dry-run`**\
  Report what the operation would do without changing anything.
- **`--keep-daily KEEP_DAILY`**\
  Number of daily backups to keep, overriding the PRUNE_KEEP_DAILY config
  value for this run.
- **`--keep-hourly KEEP_HOURLY`**\
  Number of hourly backups to keep, overriding the PRUNE_KEEP_HOURLY config
  value for this run.
- **`--keep-monthly KEEP_MONTHLY`**\
  Number of monthly backups to keep, overriding the PRUNE_KEEP_MONTHLY config
  value for this run.
- **`--keep-weekly KEEP_WEEKLY`**\
  Number of weekly backups to keep, overriding the PRUNE_KEEP_WEEKLY config
  value for this run.
- **`--keep-yearly KEEP_YEARLY`**\
  Number of yearly backups to keep, overriding the PRUNE_KEEP_YEARLY config
  value for this run.
- **`--latest`**\
  Restrict the operation to the latest full backup set (the newest timestamp
  with every configured set present).
- **`--progress`**\
  Show borg's progress output while the operation runs.
- **`--timestamp-messages`**\
  Prefix each stdout/stderr message with a timestamp.
- **`--verbose`**\
  Log informational messages, not just warnings.

## SUBCOMMANDS

### `automate enable [--config CONFIG] [--verbose] [--timestamp-messages]`

Write borgadm's crony(1) bundle and deploy the scheduled backup-creation and
check jobs (via launchd on macOS, systemd on Linux). On macOS the create job
runs with Full Disk Access permissions.

### `automate disable [--config CONFIG] [--verbose] [--timestamp-messages]`

Tear down borgadm's scheduled backup and check jobs and remove its crony(1)
bundle.

### `automate status [--config CONFIG] [--verbose] [--timestamp-messages]`

Report whether automated backups and checks are deployed, querying crony(1)
for their status.

### `break-lock [--config CONFIG] [--verbose] [--timestamp-messages]`

Forcibly release a stale lock left on the repository by an interrupted borg
run.

### `check age [--bypass-lock | --no-bypass-lock] [--config CONFIG] [--verbose] [--timestamp-messages] [seconds]`

Verify that the latest full backup is no older than the configured (or given)
maximum age.

- **`seconds`**\
  Maximum backup age, in seconds
- **`--bypass-lock, --no-bypass-lock`**\
  skip the repo lock instead of waiting for it (faster, but may race a
  concurrent backup)

### `check archives [--progress] [--bypass-lock | --no-bypass-lock] [--config CONFIG] [--verbose] [--timestamp-messages] [--latest | archive ...]`

Verify archive metadata with borg check --archives-only, over the given
archives, the latest full set (--latest), or every archive in the repository.

- **`--bypass-lock, --no-bypass-lock`**\
  skip the repo lock instead of waiting for it (faster, but may race a
  concurrent backup)

### `check prune [--bypass-lock | --no-bypass-lock] [--config CONFIG] [--verbose] [--timestamp-messages]`

Report any partial or unpruned archives left in the repository.

- **`--bypass-lock, --no-bypass-lock`**\
  skip the repo lock instead of waiting for it (faster, but may race a
  concurrent backup)

### `check repo [--progress] [--bypass-lock | --no-bypass-lock] [--config CONFIG] [--verbose] [--timestamp-messages]`

Verify repository metadata with borg check --repository-only.

- **`--bypass-lock, --no-bypass-lock`**\
  skip the repo lock instead of waiting for it (faster, but may race a
  concurrent backup)

### `check full [--progress] [--bypass-lock | --no-bypass-lock] [--config CONFIG] [--verbose] [--timestamp-messages]`

Verify both repository and archive metadata with a full borg check.

- **`--bypass-lock, --no-bypass-lock`**\
  skip the repo lock instead of waiting for it (faster, but may race a
  concurrent backup)

### `compact [--progress] [--config CONFIG] [--verbose] [--timestamp-messages]`

Compact the repository to reclaim space left behind by deleted archives.

### `config init [--force] [--config CONFIG] [--verbose] [--timestamp-messages]`

Write the shipped example config to ~/.borgadm, refusing to overwrite an
existing file unless --force is given.

- **`--force`**\
  Overwrite an existing config file

### `config validate [--config CONFIG] [--verbose] [--timestamp-messages]`

Load the config file (see --config) and report whether it is structurally
valid, listing any errors. Does not connect to the repository.

### `delete [--dry-run] [--progress] [--config CONFIG] [--verbose] [--timestamp-messages] (--latest | archive)`

Delete the given archive, or the latest full backup set (--latest).

### `create [--no-prune] [--dry-run] [--progress] [--keep-hourly KEEP_HOURLY] [--keep-daily KEEP_DAILY] [--keep-weekly KEEP_WEEKLY] [--keep-monthly KEEP_MONTHLY] [--keep-yearly KEEP_YEARLY] [--config CONFIG] [--verbose] [--timestamp-messages]`

Create a full backup, writing one archive per configured backup set, then
prune old archives unless --no-prune is given.

- **`--no-prune`**\
  Skip backup pruning

### `extract [--delete] [--dry-run] [--progress] [--bypass-lock | --no-bypass-lock] [--config CONFIG] [--verbose] [--timestamp-messages] target_dir [patterns ...]`

Extract the latest full backup into a target directory, optionally filtered by
include/exclude patterns.

- **`target_dir`**\
  Target directory for extracted backup
- **`patterns`**\
  include/exclude paths matching PATTERN, see 'borg help patterns' for details
- **`--delete`**\
  Delete files in destination not in backup
- **`--bypass-lock, --no-bypass-lock`**\
  skip the repo lock instead of waiting for it (faster, but may race a
  concurrent backup) (default: True)

### `list [--latest] [--full-names] [--keep-tags | --no-keep-tags] [--keep-hourly KEEP_HOURLY] [--keep-daily KEEP_DAILY] [--keep-weekly KEEP_WEEKLY] [--keep-monthly KEEP_MONTHLY] [--keep-yearly KEEP_YEARLY] [--include-partial | --no-include-partial | --only-partial] [--bypass-lock | --no-bypass-lock] [--config CONFIG] [--verbose] [--timestamp-messages]`

List backups grouped by timestamp, distinguishing full from partial sets and
showing prune keep tags.

- **`--full-names`**\
  List full names of backups (instead of just timestamps)
- **`--keep-tags, --no-keep-tags`**\
  Include keep tags (indicating what would be kept and deleted during a prune)
  (default: True)
- **`--include-partial, --no-include-partial`**\
  Include partial backups (default: True)
- **`--only-partial`**\
  Only list partial
- **`--bypass-lock, --no-bypass-lock`**\
  skip the repo lock instead of waiting for it (faster, but may race a
  concurrent backup)

### `log-files [--config CONFIG] [--verbose] [--timestamp-messages]`

Print the borgadm log file paths, including those of any enabled automations.

### `repair delete-cache [--config CONFIG] [--verbose] [--timestamp-messages]`

Delete the local borg cache for the repository, forcing it to be rebuilt on
the next access.

### `repair repo [--progress] [--yes] [--config CONFIG] [--verbose] [--timestamp-messages]`

Repair repository metadata with borg check --repair --repository-only;
requires --yes.

- **`--yes`**\
  confirm you understand the risks of --repair

### `repair archives [--progress] [--yes] [--config CONFIG] [--verbose] [--timestamp-messages]`

Repair archive metadata with borg check --repair --archives-only; requires
--yes.

- **`--yes`**\
  confirm you understand the risks of --repair

### `repair full [--progress] [--yes] [--config CONFIG] [--verbose] [--timestamp-messages]`

Repair both repository and archive metadata with borg check --repair; requires
--yes.

- **`--yes`**\
  confirm you understand the risks of --repair

### `prune [--dry-run] [--progress] [--keep-hourly KEEP_HOURLY] [--keep-daily KEEP_DAILY] [--keep-weekly KEEP_WEEKLY] [--keep-monthly KEEP_MONTHLY] [--keep-yearly KEEP_YEARLY] [--cleanup-unknown] [--config CONFIG] [--verbose] [--timestamp-messages]`

Prune partial and aged-out archives according to the configured retention
policy, optionally removing unknown archives.

- **`--cleanup-unknown`**\
  Delete archives whose names start with the configured BACKUP_NAME prefix but
  don't match the expected {BACKUP_NAME}-{set_name}-YYYYMMDD_HHMMSS_NofM
  shape. Default: warn but leave in place. Either way, an unknown archive in
  the repo causes list and prune to exit with the WARNING status.

### `rsync [--delete] [--dry-run] [--progress] [--bypass-lock | --no-bypass-lock] [--config CONFIG] [--verbose] [--timestamp-messages] target_dir`

Mirror the contents of the latest archive to a target directory with rsync
(Linux only).

- **`target_dir`**\
  Target directory for extracted backup
- **`--delete`**\
  Delete files in destination not in backup
- **`--bypass-lock, --no-bypass-lock`**\
  skip the repo lock instead of waiting for it (faster, but may race a
  concurrent backup) (default: True)

### `environment [--config CONFIG] [--verbose] [--timestamp-messages]`

Print the shell commands needed to run the borg CLI directly against the
configured repository.

## CONFIGURATION

- **`BORG_REPO`**\
  Path or ssh URL of the borg repository to manage (required).
- **`BORG_REPO_HOSTKEY`**\
  known_hosts entry for the remote repository host.
- **`BORG_PASSPHRASE_FILE`**\
  File holding the repository passphrase (default: ~/.borg_passphrase).
- **`BORG_SSHKEY_FILE`**\
  SSH private key used to reach a remote repository (default:
  ~/.ssh/id_borg.net).
- **`BORG_REMOTE_PATH`**\
  Path to the borg executable on the remote host.
- **`BORG_CMD_TIMEOUT`**\
  Timeout for borg commands, in seconds (default: 14400).
- **`CMD_TIMEOUT`**\
  Timeout for non-borg commands, in seconds (default: 60).
- **`LOCK_CHECK_TIMEOUT`**\
  Seconds to wait probing whether the repository lock is held (default: 5).
- **`BACKUP_NAME`**\
  Prefix for archive names (default: home).
- **`BACKUP_ROOT`**\
  Directory `create` runs from and that backup-set paths are resolved against
  (default: ~).
- **`CHECK_AGE_SECONDS`**\
  Maximum age `check age` tolerates for the latest full backup, in seconds
  (default: 86400).
- **`PRUNE_KEEP_HOURLY`**\
  Hourly archives `prune` retains (default: 24).
- **`PRUNE_KEEP_DAILY`**\
  Daily archives `prune` retains (default: 7).
- **`PRUNE_KEEP_WEEKLY`**\
  Weekly archives `prune` retains (default: 4).
- **`PRUNE_KEEP_MONTHLY`**\
  Monthly archives `prune` retains (default: 12).
- **`PRUNE_KEEP_YEARLY`**\
  Yearly archives `prune` retains (default: 2).
- **`BACKUP_MOUNTS`**\
  JSON list of paths (relative to BACKUP_ROOT) that must be mountpoints before
  `create` proceeds.
- **`BACKUP_SETS`**\
  JSON object mapping each backup-set name to its `paths`, optional
  `excludes`, and optional `create_options`; `create` writes one archive per
  set (required).

## FILES

- **`~/.borgadm`**\
  INI-style configuration file (see CONFIGURATION).
- **`~/.borg_passphrase`**\
  Default repository passphrase file (BORG_PASSPHRASE_FILE).
- **`~/.ssh/id_borg.net`**\
  Default SSH key for a remote repository (BORG_SSHKEY_FILE).
- **`$TMPDIR/borgadm.log`**\
  Run log written on every invocation.

## EXIT STATUS

| Code | Meaning                            |
| :--- | :--------------------------------- |
| `0`  | Success                            |
| `1`  | Warning                            |
| `2`  | Usage/argument error               |
| `3`  | Configuration error                |
| `4`  | General error                      |
| `5`  | Subprocess error                   |
| `7`  | Crashed (unhandled exception)      |
| `10` | Check failed: no full backups      |
| `11` | Check failed: backup too old       |
| `12` | Check failed: repo metadata        |
| `13` | Check failed: archive metadata     |
| `14` | Check failed: unpruned archives    |
| `15` | Check failed: full repo + archives |
