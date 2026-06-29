# linkfiles - symlink a source directory's contents into a target

## SYNOPSIS

`linkfiles <command> ...`

## DESCRIPTION

linkfiles symlinks the contents of a source directory tree into a target
directory, recreating the tree there. Installations are tracked, so later runs
can add new links, audit existing ones, and clean up dangling links. While
linkfiles can be used to link files from any source to any destination, its
primary purpose is linking repository files into $HOME/ (as dotfiles) and
$HOME/.local/.

By default linkfiles descends into the source and links the leaf files,
recreating any subdirectories in the target; --no-recurse links each top-level
entry directly instead. --dotfiles dot-prefixes each top-level entry in the
target (`bashrc` becomes `.bashrc`). Files matched by an ignore file
(.gitignore, .hgignore, or .linkfiles.ignore, in or above the source) are
skipped, as are editor backup and swap files.

## GETTING STARTED

To install files from repos in your $HOME you can do:

```text
    linkfiles install --dotfiles .../files $HOME/
    linkfiles install .../bin $HOME/.local/bin
    linkfiles install .../share $HOME/.local/share
```

To update already installed link trees after the source directories are
updated run:

```text
    linkfiles install
```

## COMMON ARGUMENTS

- **`--dry-run`**\
  Show what would be done without making changes.
- **`-v, --verbose`**\
  Print every entry; default suppresses ok lines.

## SUBCOMMANDS

### `install [--dry-run] [-f] [-v] [--dotfiles] [--no-recurse] [source_dir] [target_dir]`

Link the contents of source_dir into target_dir, recording the install and its
flags so it can be audited and removed later. To install links from a new
directory, both source_dir and target_dir are required. If neither are
specified, re-sync all previously installed directories.

- **`-f, --force`**\
  Replace conflicting symlinks (not files).
- **`--dotfiles`**\
  Dot-prefix each top-level entry under the target.
- **`--no-recurse`**\
  Link only top-level entries; do not descend.
- **`source_dir`**\
  Source directory to install entries from.
- **`target_dir`**\
  Target directory to link the source's entries into.

### `remove [--all] [--dry-run] [-v] [source_dir] [target_dir]`

Remove the links of one tracked install -- identified by its source_dir and
target_dir, required together -- and untrack it. Pass --all instead to remove
every tracked install.

- **`--all`**\
  Remove every tracked install (instead of one source/target).
- **`source_dir`**\
  Source directory whose entries should be removed.
- **`target_dir`**\
  Target directory the install was linked into.

### `audit [-v]`

Report the status of every tracked install; exits non-zero on conflicts,
missing entries, or stale managed links.

## FILES

- **`~/.linkfiles.installed`**\
  Tracks installed directory pairs (source + target) and installation options.
- **`~/.linkfiles.linked`**\
  Tracks installed links (source + target pairs).
- **`$REPO/.linkfiles.ignore`**\
  A file containing gitignore style patterns used to specify repo files that
  should be ignored by linkfiles. Used when source_dir is $REPO or a child of
  $REPO.

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
| `10` | Conflicts or missing entries  |
