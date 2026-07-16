# secure-archiver - build encrypted 7z archives keyed by 1Password passwords

## SYNOPSIS

`secure-archiver <command> ...`

## DESCRIPTION

secure-archiver builds 7z encrypted archives from a TOML config, bundling
local files and secrets fetched from 1Password into each archive and
encrypting it with a password read from 1Password. Each archive is timestamped
and paired with a plaintext readme describing how to open it, and old
revisions are pruned to a configurable count. The 1Password CLI (`op`) and the
7-Zip CLI (`7zz`) must be on PATH.

Each archive is defined by an [archive.NAME] section in the config (see the
example config written by `config init`). On every run it stages an archive's
files into a temporary directory, computes a SHA256 manifest of the contents,
and publishes a new revision only when the contents changed since the latest
existing archive -- unless --force-update forces a write. All archives
produced by a single run share one timestamp. The `automate` subcommand
schedules a weekly run through crony(1) (launchd on macOS, systemd on Linux).

## GETTING STARTED

To get started, write a sample config, edit it to point at your files and
1Password references, then create archives from it:

```text
    secure-archiver config init ./secure-archiver.toml
    secure-archiver config validate
    secure-archiver create
```

To schedule a weekly run through crony(1), deploy the automation bundle:

```text
    secure-archiver automate apply
```

On macOS the scheduled job runs interactively -- crony holds each run until
you are present and confirm it, so the 1Password vault-access prompts never
fire unattended. On Linux crony has no interactive support, so the job runs
unattended: your `op` must be usable without an interactive unlock (e.g. a
service-account token). The automate status and automate destroy subcommands
inspect and tear down the schedule.

## COMMON ARGUMENTS

- **`--config CONFIG`**\
  Path to the config TOML. When omitted, secure-archiver searches
  ./secure-archiver.toml, then ~/.secure-archiver.toml.

## SUBCOMMANDS

### `create [--config CONFIG] [--dry-run] [--force-update]`

Build the archives defined in the config, publishing a new timestamped
revision of each archive whose contents changed (or all of them with
--force-update) and pruning old revisions.

- **`--dry-run`**\
  Do all work but never modify output directory
- **`--force-update`**\
  Publish even if archive contents are unchanged

### `config init output_file`

Write a commented example config to output_file, which must not already exist.

- **`output_file`**\
  Path to write the example config

### `config validate [--config CONFIG]`

Load and validate the config file, reporting the resolved path on success and
the validation errors on failure.

### `automate apply [--config-only]`

Write secure-archiver's crony(1) bundle and deploy the scheduled create job
(via launchd on macOS, systemd on Linux). On macOS the job runs interactively
so the 1Password prompts reach you when a run starts.

- **`--config-only`**\
  only write the bundle file; skip crony apply

### `automate status [--config-only]`

Report whether the crony(1) bundle on disk is current, then query crony(1) for
the deployed job's status.

- **`--config-only`**\
  only check the bundle file; skip crony status

### `automate destroy [--config-only]`

Tear down secure-archiver's scheduled create job and remove its crony(1)
bundle.

- **`--config-only`**\
  only remove the bundle file; skip crony destroy

## FILES

- **`./secure-archiver.toml`**\
  Config file used when --config is omitted and present in the current
  directory.
- **`~/.secure-archiver.toml`**\
  Config file used when --config is omitted and no secure-archiver.toml exists
  in the current directory.

## EXIT STATUS

| Code | Meaning                       |
| :--- | :---------------------------- |
| `0`  | Success                       |
| `1`  | Warning                       |
| `2`  | Usage/argument error          |
| `3`  | Configuration error           |
| `4`  | General error                 |
| `5`  | Subprocess error              |
| `7`  | Crashed (unhandled exception) |
| `10` | File not found                |
