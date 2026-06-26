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
import textwrap
from collections.abc import Callable
from typing import Any

import crony.commands
import crony.config
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
# CRONY OVERVIEW
# =============================================================================
# The `crony --help` overview, appended after the exit-code epilog. A
# concise summary only -- per-subcommand and per-column detail lives in
# each subcommand's own `--help`.

_OVERVIEW: str = """\
Crony is a multi-platform user-level scheduled-job manager. It supports
jobs on macOS/darwin (via launchd) and linux (via systemd). It reads one
or more TOML config bundles and can deploy the corresponding platform
units. It runs jobs through a uniform shim, which provides consistent
logging, execution gates, timeouts, environment management, etc. Jobs can
be grouped, and groups or jobs run on a schedule. Jobs can also be managed
manually (enabled/disabled independently of the underlying scheduler, and
run at will via the `trigger` subcommand). Crony supports the following
notification mechanisms for job failures: email/smtp, ntfy, and pop-ups
(on macOS/darwin).

Subcommands fall into categories:
  - configuration (config):              manage config files
  - deployment (apply, destroy):         deploy configured jobs
  - runtime state (enable, disable):     manage scheduler arming
  - operational (status, logs, trigger): manage deployed jobs

Configuration is managed via bundles:
  ~/.config/crony/config.toml     -> bundle name "default"
  ~/.config/crony/config/<x>.toml -> bundle name "<x>"

Bundles are independent. Job and group names are namespaced as
<bundle>.<name>; bare CLI input (`crony trigger foo`) is shorthand for
`default.foo`. The `-b/--bundle <name>` flag on various subcommands scopes
name resolution to that bundle.\
"""

# A short usage walkthrough and the systemd-linger caveat, rendered after
# the overview in `crony --help` and as the GETTING STARTED / PLATFORM
# SPECIFICS man-page sections (the man page reads these via the ManSpec in
# scripts/render-docs, so this is the single source for both surfaces).
_GETTING_STARTED: str = """\
To use crony, start by generating a crony config file with `crony config
init`. Then edit the config file to define jobs and notification mechanisms.
You can validate the config file with `crony config validate`. Internally,
crony tracks jobs via UUIDs, so every job needs one. You can auto-assign
UUIDs in a config file with `crony config update` (or generate one with
`crony config generate-uuid` and add it manually).

Once a crony config exists you can inspect defined and deployed jobs (and
their execution status) with `crony status`. To deploy configured jobs run
`crony apply`. If you change the configuration for a deployed job, `crony
status` will report that the job is `stale` and you can update the deployed
configuration by running `crony apply` again. To see execution logs (file
path, content, etc.) for a job use `crony logs`. To manually trigger a job
run use `crony trigger`. To disable a job (without destroying it), use `crony
disable` (you can subsequently re-enable it with `crony enable`). To remove
deployed jobs run `crony destroy`. (Destroying jobs removes all previous job
state: log files, last run information, etc.)\
"""

_PLATFORM_SPECIFICS: str = """\
On systemd-based platforms, for scheduled jobs to execute when a user is not
logged in, "linger" must be enabled. Enabling linger requires sudo access and
can be done via the following command: `sudo loginctl enable-linger $USER`.
The `crony status` and `crony config validate` commands will check if systemd
linger is enabled, and if not will emit a warning asking the user to enable
it. While linger is disabled, a scheduled job whose time arrives while the
user is logged out will not run then; instead it runs immediately the next
time the user logs in.\
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


def _add_bundle_argument(container: argparse._ActionsContainer) -> None:
    """Add the shared `-b/--bundle` option to `container` (a parser or a
    mutually-exclusive group), so the flag is wired -- and its help reads
    -- identically wherever it appears. The lead fits both the name-scoping
    verbs and the bundle-selecting ones (e.g. `config init`); the
    name-resolution clause is conditional on the subcommand taking names."""
    container.add_argument(
        "-b",
        "--bundle",
        default=None,
        help=(
            "Operate on config bundle BUNDLE. Where the subcommand takes "
            "names, bare names resolve within BUNDLE and a qualified name "
            "must match it."
        ),
    )


def _add_jobs_argument(parser: argparse.ArgumentParser) -> None:
    """Add the variadic `jobs` positional (zero or more names, rendered as
    `job` in usage) to `parser`. Every name-taking verb operates on both
    jobs and groups, so the unified help lives here and can't drift between
    subcommands."""
    parser.add_argument(
        "jobs",
        nargs="*",
        metavar="job",
        help="Job/group names.",
    )


def _add_all_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared `--all` opt-in (dest `all_jobs`, to avoid shadowing
    the `all` builtin) so the flag is wired -- and its help reads --
    identically wherever it appears. A name-taking verb requires it
    before acting on every entry instead of an explicit name list."""
    parser.add_argument(
        "--all",
        dest="all_jobs",
        action="store_true",
        help=(
            "Act on every entry instead of an explicit name list. "
            "Scope to one bundle with -b/--bundle."
        ),
    )


def _validate_config_init(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Reject `config init --bundle default`: the default bundle is
    config.toml, which a `config/default.toml` drop-in would shadow."""
    if args.bundle == crony.config.DEFAULT_BUNDLE_NAME:
        parser.error(
            f"--bundle {crony.config.DEFAULT_BUNDLE_NAME!r} would shadow "
            f"config.toml; use plain `crony config init` (without "
            f"--bundle) for the default bundle"
        )


def _validate_destroy(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Require exactly one target selector -- job names, `--all`, or
    `--orphans` -- so a bare invocation can't silently wipe everything.
    `--all` is consumed once validated (the handler infers the whole-set
    sweep from an empty name list)."""
    modes = sum([bool(args.jobs), args.all_jobs, args.orphans])
    if modes == 0:
        parser.error("specify job names, --all, or --orphans")
    if modes > 1:
        parser.error("job names, --all, and --orphans are mutually exclusive")
    del args.all_jobs


def _require_names_or_all(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Shared precondition for the bulk verbs (enable / disable /
    trigger): require either positional names or `--all`, so an unscoped
    invocation can't silently act on every stamped entry. `--all` may be
    narrowed by `--bundle`; it is consumed once validated (the handler
    infers the whole-set action from an empty name list)."""
    if args.jobs and args.all_jobs:
        parser.error("--all cannot be combined with job names")
    if not args.jobs and not args.all_jobs:
        parser.error("specify job names or --all")
    del args.all_jobs


def _validate_enable(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    _require_names_or_all(parser, args)


def _validate_disable(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    _require_names_or_all(parser, args)


def _validate_trigger(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if args.trigger_timeout is not None and not args.wait:
        parser.error(
            "--trigger-timeout requires --wait (only meaningful in "
            "synchronous mode)"
        )
    _require_names_or_all(parser, args)


def _validate_notify_test(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Reject a fully-qualified --channel whose bundle contradicts an
    explicit --bundle."""
    if args.channel is None or "." not in args.channel:
        return
    # Split the bundle prefix here rather than via parse_full_name: that
    # raises UsageError on a malformed ref, and this validator runs
    # outside cli()'s exception mapping, so the raise would escape as a
    # crash. A malformed channel just isn't a contradiction -- the
    # handler reports it as a usage error during dispatch.
    channel_bundle, _, channel_short = args.channel.partition(".")
    if not channel_bundle or not channel_short:
        return
    if args.bundle is not None and channel_bundle != args.bundle:
        parser.error(
            f"--bundle {args.bundle!r} contradicts --channel "
            f"{args.channel!r} (bundle {channel_bundle!r}); pick one"
        )


def _build_parser() -> StrictArgumentParser:
    """Build and return the argument parser."""
    # The internal `_run` exit codes are hidden from the user-facing
    # exit-status block, the same way the man page filters them.
    exit_status = crony.errors.ExitCode.epilog(
        exclude=crony.errors.INTERNAL_EXIT_CODES
    )
    parser = StrictArgumentParser(
        description=(
            "User-level scheduled-job manager for macOS "
            "(launchd LaunchAgents) and Linux (systemd user timers)."
        ),
        epilog=(
            f"{exit_status}\n\n"
            f"Description:\n{textwrap.indent(_OVERVIEW, '  ')}\n\n"
            f"Getting Started:\n{textwrap.indent(_GETTING_STARTED, '  ')}\n\n"
            f"Platform Specifics:\n{textwrap.indent(_PLATFORM_SPECIFICS, '  ')}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # `metavar=` overrides argparse's auto-generated `{a,b,c,...}`
    # choices summary so internal subcommands like `_run` (the
    # platform unit's entry point, not user-facing) don't leak
    # into the top-level help.
    subparsers = parser.add_command_subparsers(
        metavar="<command>",
        help="Subcommands.",
    )

    # config (parent for init / validate)
    p_config = subparsers.add_parser(
        "config",
        help="Manage the config file.",
    )
    config_subparsers = p_config.add_command_subparsers(
        metavar="<action>",
    )

    p_config_init = config_subparsers.add_parser(
        "init",
        help="Generate a default config file.",
        description=(
            "Generate a default config file. With --bundle, write "
            "config/<bundle>.toml instead of config.toml."
        ),
    )
    p_config_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config file.",
    )
    _add_bundle_argument(p_config_init)
    p_config_init.add_validate_callback(_validate_config_init)

    p_config_validate = config_subparsers.add_parser(
        "validate",
        help="Lint config; report orphans, linger, etc.",
        description=(
            "Lint config; report orphans, linger, etc. With --bundle, "
            "cross-host checks (orphans, linger) are skipped."
        ),
    )
    p_config_validate_scope = p_config_validate.add_mutually_exclusive_group()
    _add_bundle_argument(p_config_validate_scope)
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
        help="Assign UUIDs to jobs and groups that lack one.",
        description=(
            "Assign UUIDs to jobs and groups that lack one. Without "
            "--bundle every bundle file is scanned; with it, only that "
            "bundle's file."
        ),
    )
    _add_bundle_argument(p_config_update)

    config_subparsers.add_parser(
        "generate-uuid",
        help="Print one freshly-minted UUID.",
    )

    # apply
    p_apply = subparsers.add_parser(
        "apply",
        help="Render and activate platform units to match config.",
        description=(
            "Render and activate platform units to match config. With "
            "no job arguments this is a full sync (install missing, fix "
            "drift, remove orphans); with names it surgically updates "
            "just those entries."
        ),
    )
    _add_jobs_argument(p_apply)
    p_apply.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Also print 'unchanged' lines (default: suppressed).",
    )
    _add_bundle_argument(p_apply)

    # destroy
    p_destroy = subparsers.add_parser(
        "destroy",
        help="Remove platform units.",
        description=(
            "Remove platform units -- the platform unit files and the "
            "entry's state dir both go away. Requires a target: job(s), "
            "--all, or --orphans."
        ),
    )
    _add_jobs_argument(p_destroy)
    _add_all_argument(p_destroy)
    p_destroy.add_argument(
        "--orphans",
        action="store_true",
        help=(
            "Limit removal to entries with on-disk remnants "
            "that no config selects on this host. "
            "Combinable with -b/--bundle; mutually exclusive "
            "with positional names and --all."
        ),
    )
    _add_bundle_argument(p_destroy)
    p_destroy.add_validate_callback(_validate_destroy)

    # enable
    p_enable = subparsers.add_parser(
        "enable",
        help="Re-arm the named jobs' schedules.",
        description=(
            "Re-arm the named jobs' schedules (clear the "
            "operator-disable). Requires a target: job(s) or --all."
        ),
    )
    _add_jobs_argument(p_enable)
    _add_all_argument(p_enable)
    _add_bundle_argument(p_enable)
    p_enable.add_validate_callback(_validate_enable)

    # disable
    p_disable = subparsers.add_parser(
        "disable",
        help="Disarm the named jobs' schedules (still triggerable).",
        description=(
            "Disarm the named jobs' schedules: loaded and triggerable, "
            "but not firing on their own. Requires a target: job(s) or "
            "--all."
        ),
    )
    _add_jobs_argument(p_disable)
    _add_all_argument(p_disable)
    _add_bundle_argument(p_disable)
    p_disable.add_validate_callback(_validate_disable)

    # trigger
    p_trigger = subparsers.add_parser(
        "trigger",
        help="Ask the platform scheduler to fire the named jobs now.",
        description=(
            "Ask the platform scheduler to fire the named jobs now, via "
            "the same path a scheduled fire uses. Requires a target: "
            "job(s) or --all."
        ),
    )
    _add_jobs_argument(p_trigger)
    _add_all_argument(p_trigger)
    _add_bundle_argument(p_trigger)
    p_trigger.add_argument(
        "-w",
        "--wait",
        action="store_true",
        help=(
            "Block until each named entry's next completion and "
            "exit with that exit code (worst across multiple names)."
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
    p_trigger.add_validate_callback(_validate_trigger)

    # status
    p_status = subparsers.add_parser(
        "status",
        help="Print resolved state per job.",
        description=(
            "Print resolved state per job. With no job arguments every "
            "job is shown; pass job names to restrict the table. Many "
            "columns are source-selected -- they show the pending "
            "(config) value by default, or the applied value under "
            "--config-current -- and a trailing `^` on a value marks a "
            "divergence between the two."
        ),
        epilog=crony.commands.STATUS_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_jobs_argument(p_status)
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
        type=crony.commands.parse_cols_arg,
        help=(
            "Comma-separated columns to display. See the Status Columns "
            "reference for valid names, aliases, and descriptions."
        ),
    )
    _add_bundle_argument(p_status)
    config_source = p_status.add_mutually_exclusive_group()
    config_source.add_argument(
        "--config-current",
        action="store_true",
        help=(
            "For dual-source columns (name, schedule, groups, ...), show "
            "the currently-active (applied) value."
        ),
    )
    config_source.add_argument(
        "--config-pending",
        action="store_true",
        help=(
            "For dual-source columns (name, schedule, groups, ...), show "
            "the pending value from the config (the default)."
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
        help="Print a job's recent log output.",
    )
    p_logs.add_argument(
        "job",
        help="Job name.",
    )
    p_logs.add_argument(
        "-n",
        type=int,
        default=None,
        help="Print the last N lines (default: 200, or 10 with --tail).",
    )
    p_logs.add_argument(
        "-s",
        "--since",
        default=None,
        type=crony.commands.parse_since_arg,
        help='"1h", "2d", or ISO timestamp.',
    )
    tail_or_latest = p_logs.add_mutually_exclusive_group()
    tail_or_latest.add_argument(
        "-t",
        "--tail",
        action="store_true",
        help="Follow appended output.",
    )
    tail_or_latest.add_argument(
        "-l",
        "--latest",
        action="store_true",
        help="Print only the latest run's entry.",
    )
    p_logs.add_argument(
        "-p",
        "--path",
        action="store_true",
        help="Print the log file path and exit (no content).",
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
            help="Entity address `<bundle>:<uuid>` (internal-only form).",
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
        help="Hard wallclock cap in seconds (internal-only form).",
    )
    p_guard.add_argument(
        "argv",
        nargs=argparse.REMAINDER,
        help="The `crony _run` command to run under the cap.",
    )

    # notify-test
    p_nt = subparsers.add_parser(
        "notify-test",
        help="Send a synthetic failure notification.",
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
    _add_bundle_argument(p_nt)
    p_nt.add_validate_callback(_validate_notify_test)

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
