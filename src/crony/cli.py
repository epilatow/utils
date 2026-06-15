# This is AI generated code

"""crony's command-line interface.

Builds the argument parser, owns the command dispatch table, and runs the
top-level entry that parses argv, routes to the matching command handler,
and maps exceptions to a stable process exit code. Also configures the
broken-pipe-aware root logger and ignores SIGPIPE so output piped to a
closed consumer (e.g. `head`) terminates cleanly rather than killing the
process mid-line.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from collections.abc import Callable
from typing import Any

import crony.commands
import crony.errors
import crony.model
import crony.runner
import crony.runtime
from common.argparse_ext import StrictArgumentParser
from common.cli import cli_entrypoint

# Handle broken pipes gracefully (e.g., when piping to `head`). Ignore
# SIGPIPE so writes to a closed pipe raise BrokenPipeError instead of
# killing the process mid-output. The error is then absorbed at each
# output site (logging handlers and the runner shim's subprocess
# pump) so atexit handlers still run and warning text isn't truncated
# mid-line. subprocess.Popen's restore_signals=True default resets
# SIGPIPE back to SIG_DFL in spawned children, so subprocess behavior
# is unchanged.
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

# =============================================================================
# CRONY DESIGN
# =============================================================================
# Single source of truth: the same prose that documents the design is
# also rendered into `crony --help`.

_CRONY_DESIGN: str = """\
crony is a user-level scheduled-job manager for macOS (launchd
LaunchAgents) and Linux (systemd user timers). It reads one or
more TOML config bundles and applies the corresponding platform
units, runs jobs through a uniform shim (so logs, locks, gates,
and notifications behave the same on both platforms), and reports
state via `crony status`. The default columns are:
  JOB / UUID  the full namespaced name `<bundle>.<short>`, or the
            `<bundle>:<UUID>` form when the entry has no recoverable
            name or its name is shadowed by a collision; indented
            two spaces per group-nesting level when the entry is in
            an active target's dispatch tree, in execution order
  CONFIG    synced | stale | broken | missing | orphan | masked | error
  SCHEDULE  the cron / interval / `grouped` value for the entry,
            or `disabled` when the operator has turned it off
  STATUS    ok      | fail     | timeout  | gated   | canceled |
            crashed | running  | pending  | never   | unknown
  LAST RAN  compact relative time of the last run (e.g. `5m ago`)

`status=crashed` means the scheduler's most recent launch ended without
recording a run -- killed by a signal (OOM, a manual kill, macOS
terminating a `uv` whose cdhash changed under a loaded unit), or
exited before the runner wrote its record. The last-run.json that
survives is from an earlier launch, so LAST RAN reads `unknown`.

`config=masked` only appears with `crony status -a/--all` (no
remnants to clean up); a filter-excluded entry with leftover
unit / state-dir reports as `config=orphan` in the default
view, with `masked-by` explaining the filter axis. `config=error`
flags an entry whose bundle config was rejected (e.g. unknown key
or invalid name); the entry's installed unit, if any, is left
untouched. `crony apply <errored-name>` refuses with a clear
"config error" message; `crony destroy <errored-name>` is
accepted so a prior install can still be cleaned up; the
remaining lifecycle commands (`enable`, `disable`, `trigger`)
operate on the installed unit as usual since they need no
parsed config field. Several
opt-in columns are available via `--cols`, including
`groups` (group memberships), `kind` (job vs group), `masked-by`
(why the entry is filter-excluded on this host: `host` and / or
`platform` joined with `,`, or one of `unused` / `empty`),
and applied-vs-pending splits of the schedule column. See
`crony status --help` for the full column reference.

Subcommands fall into two categories:
  - config-derived state (apply / destroy):  manage unit-file presence
  - runtime state (enable / disable):        manage scheduler arming
Plus operational subcommands: status, logs, trigger,
notify-test, config.

`trigger` asks the platform scheduler to fire the unit
immediately, the same path a scheduled fire would take. To run a
job ad-hoc, use `crony trigger <name>` -- this guarantees the run
uses the same execution context (env, gate, timeout, working dir)
as a scheduled fire. `trigger --wait` blocks until the next
completion via kernel-level pid-exit notification -- no polling.
Groups dispatch each child through the same mechanism, with a
cumulative deadline pinned at apply time as 1.05 *
sum-of-children's-timeouts.

A capped entry's unit invokes the runner through an internal
hard-timeout guard that kills the whole run if it exceeds the
entry's timeout plus a short padding. The runner's own timeout
normally fires first and records a clean `timeout` result; the
guard is a last-resort backstop for a runner that wedges before
honoring its deadline. An uncapped entry (`job-timeout-sec = 0`)
runs without the guard, as does an interactive job -- its pending
wait / delay has no wallclock bound for the guard to respect.

Interactive jobs (`interactive = true` on `[job.<name>]`) sit
pending in the background after their fire and prompt the user
via a desktop dialog (Run / Delay / Cancel) before running.
The dialog and idle detection are macOS-only, so an interactive
job runs only on darwin -- a non-darwin host skips it at
selection. The runner waits for the user to be
continuously active for `interactive_active` (default 10min),
then prompts; "Delay Job" sleeps `interactive_delay` (default
1h) and re-waits. `crony status` reports such a job as
`pending` during the wait. `crony trigger` bypasses the wait
and runs the command immediately.

Apply pins each entry's runtime parameters into a JSON snapshot
in the entry's state dir (`STATE_DIR/<bundle>/<uuid>/snapshot.json`,
alongside `run.log`, `last-run.json`, etc.). Apply also seeds an
empty `run.log` so you can `tail -f` it immediately, before the
entry's first run. The state dir is keyed on the entry's `uuid`
so renaming a job in the config preserves its history -- only
deleting the entry (or changing its `uuid`) loses it. The runner
reads the snapshot, never the live config: editing the toml without `apply`
therefore has no effect on running units, and `crony status`
reports any divergence between live config and on-disk snapshot
as `config=stale` via direct dataclass equality. The platform
unit file gets the same drift treatment: a hand-edited unit file
surfaces as `config=stale`, while one whose baked uv / crony
binary is gone, that the scheduler has no unit loaded for (so it
can't be triggered at all), or whose schedule-arming timer file is
gone (so a scheduled entry never fires) reads `config=broken`; a
deleted config unit reads `broken` while still loaded, `missing`
once unloaded. Either way `crony apply` re-renders / re-installs /
reloads it.

One apply case is held back: a job whose own command runs
`crony apply` (a self-maintaining schedule) cannot reload its own
unit on launchd, where reloading a unit terminates the job running
from it. If such an apply would change that unit it makes no change
at all -- snapshot included, so disk stays consistent -- prints a
warning, and exits WARNING; the next `crony apply` not run by that
job reconciles the now-`config=stale` unit. A self-apply that
changes only the snapshot (no unit change) is applied normally, as
is any apply under systemd, which reloads without stopping running
units.

Config bundles:
  ~/.config/crony/config.toml     -> bundle name "default"
  ~/.config/crony/config/<x>.toml -> bundle name "<x>"

Bundles are independent: each has its own [defaults], targets, and
notify config; jobs/groups defined in one bundle don't see another's.
Job and group names are namespaced as <bundle>.<short>; bare CLI
input (`crony trigger foo`) is shorthand for `default.foo` and only
ever resolves to the default bundle. The `-b/--bundle <name>` flag
on multi-job subcommands (apply, destroy, status, enable, disable,
trigger) reshapes that resolution: bare names resolve in `<name>`,
qualified names must match it, and a bare invocation scopes the
operation to that bundle's selection.

State (logs, locks, last-run records, applied snapshots) lives at
~/.local/state/crony/.\
"""


logger = logging.getLogger(__name__)


class _BrokenPipeAwareStreamHandler(logging.StreamHandler[Any]):
    """StreamHandler that silently disables itself once its downstream
    pipe consumer closes (e.g., piping to `head`). The first
    BrokenPipeError swaps the stream for /dev/null so subsequent log
    records are absorbed without spamming tracebacks.
    """

    def handleError(self, record: logging.LogRecord) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, BrokenPipeError):
            try:
                self.stream.close()
            except (BrokenPipeError, OSError):
                pass
            self.stream = open(os.devnull, "w")
            return
        super().handleError(record)


def _initialize_logger() -> None:
    """Configure root logger with a broken-pipe-aware handler."""
    handler = _BrokenPipeAwareStreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


_initialize_logger()


# =============================================================================
# ARGUMENT PARSER
# =============================================================================


def _build_parser() -> StrictArgumentParser:
    """Build and return the argument parser."""
    parser = StrictArgumentParser(
        description=(
            "User-level scheduled-job manager for macOS "
            "(launchd LaunchAgents) and Linux (systemd user timers)."
        ),
        epilog=f"{crony.errors.ExitCode.epilog()}\n\n{_CRONY_DESIGN}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # `metavar=` overrides argparse's auto-generated `{a,b,c,...}`
    # choices summary so internal subcommands like `_run` (the
    # platform unit's entry point, not user-facing) don't leak
    # into the top-level help.
    subparsers = parser.add_command_subparsers(
        metavar="<command>",
        help="Subcommands",
    )

    # config (parent for init / validate)
    p_config = subparsers.add_parser(
        "config",
        help="Manage the config file",
    )
    config_subparsers = p_config.add_command_subparsers(
        metavar="<action>",
    )

    p_config_init = config_subparsers.add_parser(
        "init",
        help="Generate a default config file",
    )
    p_config_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config file",
    )
    p_config_init.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Write to config/<bundle>.toml instead of config.toml "
            "(creates the dropin dir if missing)"
        ),
    )

    p_config_validate = config_subparsers.add_parser(
        "validate",
        help="Lint config; report orphans, linger, etc",
    )
    p_config_validate_scope = p_config_validate.add_mutually_exclusive_group()
    p_config_validate_scope.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Restrict per-bundle warnings to one bundle. "
            "Cross-host checks (orphans, linger) are skipped in "
            "this mode -- run without --bundle for those."
        ),
    )
    p_config_validate_scope.add_argument(
        "--file",
        default=None,
        metavar="PATH",
        help=(
            "Structurally validate a single TOML file as a bundle "
            "(named after its stem, or 'default' for the installed "
            "config.toml), independent of the config dir. Exits "
            "non-zero on any error. Lets a tool pre-flight a bundle."
        ),
    )

    p_config_update = config_subparsers.add_parser(
        "update",
        help="Assign UUIDs to jobs and groups that lack one",
    )
    p_config_update.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Scope the rewrite to one bundle file. Without this "
            "flag every bundle file is scanned."
        ),
    )

    config_subparsers.add_parser(
        "generate-uuid",
        help="Print one freshly-minted UUID",
    )

    # apply
    p_apply = subparsers.add_parser(
        "apply",
        help="Render and activate platform units to match config",
    )
    p_apply.add_argument(
        "jobs",
        nargs="*",
        help=(
            "Job/group names. With no args: full sync (install "
            "missing, fix drift, remove orphans). With names: "
            "surgical update of those entries only."
        ),
    )
    p_apply.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Also print 'unchanged' lines (default: suppressed)",
    )
    p_apply.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Scope the apply to one bundle. Bare names resolve in "
            "<bundle>; qualified names must match it. With no "
            "positional args, only that bundle's selection and "
            "orphans are touched."
        ),
    )

    # destroy
    p_destroy = subparsers.add_parser(
        "destroy",
        help="Remove platform units",
    )
    p_destroy.add_argument(
        "jobs",
        nargs="*",
        help=(
            "Job/group names. With no args: factory reset. "
            "With names: surgical removal."
        ),
    )
    p_destroy.add_argument(
        "--orphans",
        action="store_true",
        help=(
            "Limit removal to entries with on-disk remnants "
            "that no live config selects on this host. "
            "Combinable with -b/--bundle; mutually exclusive "
            "with positional names."
        ),
    )
    p_destroy.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Scope the destroy to one bundle. Bare names resolve in "
            "<bundle>; qualified names must match it. With no "
            "positional args, only that bundle's on-disk remnants "
            "are removed."
        ),
    )

    # enable
    p_enable = subparsers.add_parser(
        "enable",
        help="Re-arm the named jobs' schedules",
    )
    p_enable.add_argument(
        "jobs",
        nargs="*",
        help=(
            "Job names to enable. Omit when --bundle is given to "
            "enable every stamped entry in that bundle."
        ),
    )
    p_enable.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Scope the enable to one bundle. With no positional "
            "args, enables every stamped entry in "
            "<bundle>; with positional args, bare names resolve in "
            "<bundle> and qualified names must match it."
        ),
    )

    # disable
    p_disable = subparsers.add_parser(
        "disable",
        help="Disarm the named jobs' schedules (still triggerable)",
    )
    p_disable.add_argument(
        "jobs",
        nargs="*",
        help=(
            "Job names to disable. Omit when --bundle is given to "
            "disable every stamped entry in that bundle."
        ),
    )
    p_disable.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Scope the disable to one bundle. With no positional "
            "args, disables every stamped entry in "
            "<bundle>; with positional args, bare names resolve in "
            "<bundle> and qualified names must match it."
        ),
    )

    # trigger
    p_trigger = subparsers.add_parser(
        "trigger",
        help="Ask the platform scheduler to fire the named jobs now",
    )
    p_trigger.add_argument(
        "jobs",
        nargs="*",
        help=(
            "Job names to trigger via the platform scheduler. Omit "
            "when --bundle is given to trigger every stamped entry "
            "in that bundle."
        ),
    )
    p_trigger.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Scope the trigger to one bundle. With no positional "
            "args, fires every stamped entry in <bundle>; with "
            "positional args, bare names resolve in <bundle> and "
            "qualified names must match it."
        ),
    )
    p_trigger.add_argument(
        "-w",
        "--wait",
        action="store_true",
        help=(
            "Block until each named entry's next completion and "
            "exit with that exit code (worst across multiple names)"
        ),
    )
    p_trigger.add_argument(
        "--trigger-timeout",
        type=int,
        default=None,
        dest="trigger_timeout",
        help=(
            "Override [defaults].trigger_timeout_sec (seconds to "
            "wait for a runner to come online after kickstart). "
            "Only meaningful with --wait."
        ),
    )

    # status
    p_status = subparsers.add_parser(
        "status",
        help="Print resolved state per job",
        epilog=crony.commands.STATUS_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_status.add_argument(
        "jobs",
        nargs="*",
        help="Restrict to these jobs (default: all)",
    )
    p_status.add_argument(
        "-a",
        "--all",
        dest="show_masked",
        action="store_true",
        help=(
            "List all defined entries, including ones masked on the "
            "current host. Entries can be masked because they are "
            "unused or associated with a different platform and/or "
            "host."
        ),
    )
    p_status.add_argument(
        "--cols",
        default=None,
        help=(
            "Comma-separated columns to display. See the Columns "
            "sections in the help epilog for valid names, aliases, "
            "and descriptions."
        ),
    )
    p_status.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Restrict the table to one bundle. Bare names resolve "
            "in <bundle>; qualified names must match it."
        ),
    )
    p_status.add_argument(
        "--config-current",
        action="store_true",
        help=(
            "dual-source columns (name, schedule, groups, ...) show "
            "the currently-active (applied) value; `^` still flags "
            "divergence from the pending config"
        ),
    )
    p_status.add_argument(
        "--config-pending",
        action="store_true",
        help=(
            "dual-source columns (name, schedule, groups, ...) show "
            "the pending value from the live config; `^` still flags "
            "divergence from the applied state"
        ),
    )
    p_status.add_argument(
        "--exclude-healthy",
        action="store_true",
        help=(
            "Drop rows that are config=synced, enabled in the "
            "scheduler, and last in ok/never/gated. Output is "
            "flat (no tree indent). Always exits 0 -- this is a "
            "filter on the display, not a gate."
        ),
    )

    # logs
    p_logs = subparsers.add_parser(
        "logs",
        help="Print a job's recent log output",
    )
    p_logs.add_argument(
        "name",
        help="Job name",
    )
    p_logs.add_argument(
        "-n",
        type=int,
        default=None,
        help="Print the last N lines (default: 200, or 10 with --tail)",
    )
    p_logs.add_argument(
        "-s",
        "--since",
        default=None,
        help='"1h", "2d", or ISO timestamp',
    )
    p_logs.add_argument(
        "-t",
        "--tail",
        action="store_true",
        help="Follow appended output",
    )
    p_logs.add_argument(
        "-p",
        "--path",
        action="store_true",
        help="Print the log file path and exit (no content)",
    )
    p_logs.add_argument(
        "-l",
        "--latest",
        action="store_true",
        help="Print only the latest run's entry",
    )

    # _run -- internal entry point for platform units; hidden from
    # `crony --help` since end users should never invoke it directly
    # (use `crony trigger` instead, which goes through the platform
    # scheduler so the run uses the same execution context as a
    # scheduled fire). The leading underscore marks it private, matching
    # `_run-guard`. The legacy `run` spelling is registered alongside as
    # a transitional alias so units baked before the rename keep firing
    # until re-applied (see RUN_SUBCOMMAND_LEGACY).
    # No `help=` here -- argparse omits subparsers without a help
    # string from the top-level subcommand listing. Passing
    # argparse.SUPPRESS surfaces the literal "==SUPPRESS==" string
    # instead of hiding the entry.
    for _run_name in (
        crony.model.RUN_SUBCOMMAND,
        crony.model.RUN_SUBCOMMAND_LEGACY,
    ):
        p_run = subparsers.add_parser(_run_name)
        p_run.add_argument(
            "ref",
            help="Entity address `<bundle>:<uuid>` (internal-only form)",
        )
        p_run.add_argument(
            "--dry-run",
            action="store_true",
            help="Acquire lock, run gate, but do not exec",
        )
        p_run.add_argument(
            "--skip-gate",
            action="store_true",
            help="Skip gate check (force run)",
        )

    # _run-guard -- internal hard-timeout backstop wrapping `_run`;
    # rendered into the platform unit by apply, never invoked by hand.
    # Hidden from `crony --help` for the same reason as `_run` (no
    # `help=`). Takes the cap then the full inner command via REMAINDER
    # so the inner `--script` / flags aren't parsed as guard options.
    p_guard = subparsers.add_parser(crony.model.GUARD_SUBCOMMAND)
    p_guard.add_argument(
        "cap",
        type=int,
        help="Hard wallclock cap in seconds (internal-only form)",
    )
    p_guard.add_argument(
        "argv",
        nargs=argparse.REMAINDER,
        help="The `crony _run` command to run under the cap",
    )

    # notify-test
    p_nt = subparsers.add_parser(
        "notify-test",
        help="Send a synthetic failure notification",
    )
    p_nt.add_argument(
        "--channel",
        default=None,
        help=(
            "Send through one specific channel. Bare name "
            "(`ntfy`) resolves against the selected bundle; "
            "fully qualified `<bundle>.<channel>` overrides --bundle."
        ),
    )
    p_nt.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Send through this bundle's notify config "
            "(default: the `default` bundle)"
        ),
    )

    return parser


# =============================================================================
# COMMAND DISPATCH
# =============================================================================

_COMMAND_CALLBACKS: dict[str, Callable[..., None]] = {
    "config init": crony.commands.do_init,
    "config validate": crony.commands.do_validate,
    "config update": crony.commands.do_config_update,
    "config generate-uuid": crony.commands.do_generate_uuid,
    "apply": crony.commands.do_apply,
    "destroy": crony.commands.do_destroy,
    "enable": crony.commands.do_enable,
    "disable": crony.commands.do_disable,
    "trigger": crony.commands.do_trigger,
    "status": crony.commands.do_status,
    "logs": crony.commands.do_logs,
    crony.model.RUN_SUBCOMMAND: crony.runner.do_run,
    crony.model.RUN_SUBCOMMAND_LEGACY: crony.runner.do_run,
    crony.model.GUARD_SUBCOMMAND: crony.runner.do_run_guard,
    "notify-test": crony.commands.do_notify_test,
}


@cli_entrypoint
def cli() -> int:
    """CLI entry point with exception handling."""
    args_dict = vars(_build_parser().parse_command())
    command = args_dict.pop("command")

    try:
        _COMMAND_CALLBACKS[command](**args_dict)
    except SystemExit as e:
        # `crony _run` raises SystemExit(<wrapped-rc>) so an
        # arbitrary exit code from the executed job (0-255) reaches
        # the platform scheduler unmodified rather than being
        # squashed to ExitCode.SUCCESS.
        if isinstance(e.code, int):
            return e.code
        return (
            crony.errors.ExitCode.SUCCESS
            if e.code is None
            else crony.errors.ExitCode.ERROR
        )
    except crony.errors.CronyError as e:
        logger.error("ERROR: %s", e)
        return e.exit_code
    return crony.errors.ExitCode.SUCCESS
