# darwin-tz-watchdog - restart macOS UserEventAgent-Aqua on a stale timezone

## SYNOPSIS

`darwin-tz-watchdog [-v] [--dry-run]`

## DESCRIPTION

darwin-tz-watchdog restarts macOS UserEventAgent-Aqua when its cached timezone
has gone stale. UserEventAgent-Aqua dispatches user-level launchd
StartCalendarInterval triggers, but it reads the timezone once at startup and
never re-reads it when /etc/localtime changes. After a timezone switch (e.g.
travel) calendar-interval jobs keep firing against the stale cached zone -- a
job set for 02:30 local fires at 02:30 in the previously cached zone,
potentially hours off -- until the agent is restarted. This watchdog detects
that condition and restarts the agent so it picks up the current zone.

It compares the mtime of the /etc/localtime symlink (bumped whenever the
system timezone changes) against UserEventAgent-Aqua's start time; if the
timezone link is newer the agent is stale and is restarted by signaling the
process this user owns, which launchd then respawns against the current zone.
With nothing stale to act on it exits silently, so it is cheap to run
periodically from a scheduler. Use --dry-run to report what would happen
without touching the agent, and -v to print the decision inputs (pid, times,
staleness).

## GETTING STARTED

Schedule darwin-tz-watchdog with crony(1) so it runs automatically -- e.g. a
daily, darwin-only job:

```text
    mkdir -p ~/.config/crony/config/
    cat > ~/.config/crony/config/darwin-tz-watchdog.toml <<-EOF
    [defaults]
    notify-channels = ["default"]

    [job.doit]
    command   = "darwin-tz-watchdog"
    env.PATH  = "\$PATH:\$HOME/.local/bin"
    interval = "1d"
    uuid = "$(crony config generate-uuid)"

    [target.platform.darwin]
    jobs = ["doit"]
    EOF
    crony apply -b darwin-tz-watchdog
```

## PLATFORM SPECIFICS

darwin-tz-watchdog targets a macOS-only bug, so it does real work only on
macOS/darwin. On every other platform it still parses its arguments (so --help
behaves identically everywhere) and then exits successfully without doing
anything, which lets a cross-platform scheduler invoke it unconditionally.

## OPTIONS

- **`-v, --verbose`**\
  Print decision diagnostics (pid, times, staleness).
- **`--dry-run`**\
  Report what would happen without signaling the agent.

## EXIT STATUS

| Code | Meaning                       |
| :--- | :---------------------------- |
| `0`  | Success                       |
| `4`  | General error                 |
| `7`  | Crashed (unhandled exception) |
