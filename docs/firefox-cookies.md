# firefox-cookies - extract cookies from a Firefox profile

## SYNOPSIS

```text
firefox-cookies {list,list-domains,list-profiles,list-containers} ...
```

## DESCRIPTION

firefox-cookies extracts cookies from a Firefox profile and writes them to
stdout in Netscape or JSON format. It reads both the on-disk cookie database
(cookies.sqlite) and the session-store backup (recovery.jsonlz4), so the
session cookies Firefox keeps only in memory are included alongside the
persisted ones. Cookies can be filtered by domain and by container, and the
profile is auto-detected or selected by name or path.

Firefox organizes cookies in a three-level hierarchy. A profile is a
self-contained browser instance with its own cookies, history, and settings;
most installs have a single profile, but Firefox supports many (they are
indexed in profiles.ini and listed by list-profiles). Within a profile,
containers are optional isolated cookie jars: the same site can keep separate
cookies in, say, a Work and a Personal container. Containers are off until
enabled -- typically through the Multi-Account Containers extension -- and
without them every cookie lives in the default context, container ID 0.
Domains are the individual hosts that have set cookies inside a given profile
and container. list-containers and list-domains summarize what a profile holds
at each level.

With no --profile, the default profile is chosen the same way Firefox itself
chooses one: the profile that profiles.ini's install section marks as the
default, falling back to a profile's own default flag, and finally to the
first profile listed when none is marked. Selection is keyed off that marker,
not the profile's name -- the default is usually a random-suffixed name such
as 8f3k2a1b.default-release, not literally "default". Pass --profile to
override with a profile name (matched case-insensitively) or a filesystem path
to a profile directory.

Because Firefox locks cookies.sqlite while it is running, the database is
copied to a temporary location before it is read, so cookies can be extracted
without closing the browser. In a whole-profile dump (no --container), a
cookie present in several containers is emitted once -- the default-context
copy, or the one from the lowest container ID when no default-context copy
exists -- and the dropped containers are reported.

## GETTING STARTED

Dump all cookies from the default profile (in Netscape format):

```text
    firefox-cookies list
```

List the available profiles:

```text
    firefox-cookies list-profiles
```

List the containers in use by the default profile:

```text
    firefox-cookies list-containers
```

Extract the trainingpeaks.com login authentication cookie (Production_tpAuth),
from the TrainingPeaks container, in the default profile:

```text
    firefox-cookies list -c TrainingPeaks -d trainingpeaks.com \
        Production_tpAuth
```

## COMMON ARGUMENTS

- **`--no-header`**\
  Omit the column header row.
- **`-c, --container CONTAINER`**\
  Container ID, or name (case-insensitive; an exact name wins, otherwise a
  unique substring match).
- **`-d, --domain DOMAINS`**\
  Filter by domain; matches the domain and its subdomains (repeatable).
- **`-p, --profile PROFILE`**\
  Profile name (case-insensitive) or path to a profile directory (default:
  auto-detect).
- **`-s, --source {db,recovery}`**\
  Which cookie stores to read; repeatable (default: both). `db` reads the
  persistent on-disk database -- the cookies Firefox has already written to
  disk. `recovery` reads the session-store backup, which holds the session
  cookies Firefox keeps only in memory for the current session.

## SUBCOMMANDS

### `list [-p PROFILE] [-c CONTAINER] [-d DOMAINS] [-s {db,recovery}] [--format {netscape,json}] [NAME]`

Extract cookies from the resolved profile and write them to stdout. The
default Netscape format emits a commented header followed by one tab-separated
row per cookie, with the columns host, subdomain flag, path, secure flag,
expiry, name, and value. The JSON format (--format json) emits an array of
cookie objects with host, name, value, path, expiry, and secure, plus httpOnly
and sameSite. An optional NAME positional keeps only cookies whose name
matches it: a case-insensitive exact match if any name equals NAME, otherwise
every name containing NAME.

- **`--format {netscape,json}`**\
  Output format.
- **`NAME`**\
  Optional cookie name to filter by (case-insensitive: an exact name wins,
  otherwise every name containing NAME).

### `list-domains [-p PROFILE] [-c CONTAINER] [-s {db,recovery}] [--no-header]`

List the domains that have cookies in the resolved profile, one per line. The
columns are: cookie-count, container-id, and domain, sorted by domain.
container-id 0 is the default (no-container) context.

### `list-profiles [--no-header]`

List the Firefox profiles found in profiles.ini, one per line. The columns
are: default, name, and path. The default column holds a * on the profile
profiles.ini marks as the default.

### `list-containers [-p PROFILE] [-s {db,recovery}] [--no-header]`

List the containers defined in the resolved profile, one per line. The columns
are: cookie-count, container-id, and container-name, sorted by container-name.

## FILES

- **`<profile>/cookies.sqlite`**\
  The profile's persistent cookie database (the `db` source). Copied to a
  temporary location before reading, so it can be queried while Firefox is
  running.
- **`<profile>/sessionstore-backups/recovery.jsonlz4`**\
  The Session Restore backup (mozlz4-compressed). Holds the session cookies
  Firefox keeps only in memory (the `recovery` source).
- **`<profile>/containers.json`**\
  The profile's container definitions, used to resolve container names and
  IDs.
- **`<firefox-dir>/profiles.ini`**\
  The profile index, read to locate and resolve profiles. \<firefox-dir> is
  ~/Library/Application Support/Firefox on macOS and ~/.mozilla/firefox on
  Linux.

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
