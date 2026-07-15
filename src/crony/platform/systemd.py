# This is AI generated code

"""systemd (Linux) scheduler backend.

Each entity is a `.service` unit; scheduled entries also get a `.timer`
that arms it. Schedule-less entries install only the static `.service`,
which sits dormant until `crony trigger` or a parent group fires it.
"""

import configparser
import os
import shlex
import subprocess
from pathlib import Path

from crony.platform.scheduler import (
    UNIT_PREFIX,
    RenderedUnit,
    RenderedUnits,
    Scheduler,
    SchedulerWarning,
    UnitLastExit,
)
from crony.unit import (
    Interval,
    PriorityClass,
    Timing,
    UnitSpec,
)

# --quiet drops the success-path symlink chatter. Enable (create the
# boot symlink) and restart (activate now) are separate calls: `enable
# --now` only *starts* an inactive unit, so it would leave an
# already-active timer on its stale activation; `restart` re-activates it
# unconditionally so a monotonic anchor (OnActiveSec) is re-seeded on
# every apply. --now disable stops and de-links in one call.
_SYSTEMCTL_ENABLE = ["systemctl", "--user", "--quiet", "enable"]
_SYSTEMCTL_RESTART = ["systemctl", "--user", "--quiet", "restart"]
_SYSTEMCTL_DISABLE = ["systemctl", "--user", "--quiet", "disable", "--now"]


def _service_filename(name: str) -> str:
    """Basename of the systemd `.service` unit for `name`."""
    return f"{UNIT_PREFIX}-{name}.service"


def _timer_filename(name: str) -> str:
    """Basename of the systemd `.timer` unit for `name`."""
    return f"{UNIT_PREFIX}-{name}.timer"


def _priority_block(priority: PriorityClass) -> str:
    """[Service] priority directives for a job, or '' for NORMAL.

    Linux has no app-vs-background QoS throttling to undo, so HIGH
    only records intent in a comment (CPU + IO stay at defaults);
    LOW lowers both CPU and IO scheduling.
    """
    if priority is PriorityClass.HIGH:
        return "# crony priority=high: CPU + IO left at defaults\n"
    if priority is PriorityClass.LOW:
        return "Nice=10\nIOSchedulingClass=idle\n"
    return ""


def _render_service(
    name: str,
    cmd: tuple[str, ...],
    priority: PriorityClass = PriorityClass.NORMAL,
) -> str:
    """Render the systemd `.service` unit. Independent of schedule.

    `cmd` is the argv ExecStart runs. The unit description carries the
    human-readable name. `priority` adds CPU / IO scheduling directives
    inherited by the spawned command.
    """
    return (
        "[Unit]\n"
        f"Description=crony job {name}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={shlex.join(cmd)}\n"
        "WorkingDirectory=%h\n"
        f"{_priority_block(priority)}"
    )


def _render_timer(name: str, timing: Timing, jitter: Interval | None) -> str:
    """Render the systemd `.timer` unit.

    `jitter` is the fixed per-job start-time offset for a jittered interval
    job, or None. When set, the first fire is delayed by it instead of by
    the full interval, spreading a herd of same-interval jobs off each
    unit's own offset. It has no effect on a calendar timer."""
    if isinstance(timing, Interval):
        # OnUnitActiveSec alone measures from the last service
        # activation, so a timer whose service has never run has no
        # anchor and never elapses. OnActiveSec seeds the first firing
        # relative to timer activation (enable / boot); a jittered job
        # seeds it with its per-job offset so same-interval jobs do not
        # fire in lockstep, while OnUnitActiveSec keeps the recurring
        # cadence at the full interval off each completed run.
        first = jitter if jitter is not None else timing
        spec_line = f"OnActiveSec={first}\nOnUnitActiveSec={timing}\n"
    else:
        spec_line = f"OnCalendar={timing}\n"
    return (
        "[Unit]\n"
        f"Description=crony timer for {name}\n"
        "\n"
        "[Timer]\n"
        f"{spec_line}"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _service_argv(content: str) -> list[str] | None:
    """Recover the argv from a `.service`'s ExecStart, or None when it
    isn't in the shape `_render_service` produces.

    The inverse of `_render_service`'s ExecStart embedding."""
    parser = configparser.ConfigParser(
        interpolation=None, delimiters=("=",), strict=False
    )
    try:
        parser.read_string(content)
    except configparser.Error:
        return None
    exec_start = parser.get("Service", "ExecStart", fallback=None)
    if not isinstance(exec_start, str):
        return None
    try:
        return shlex.split(exec_start)
    except ValueError:
        return None


def _is_enabled(unit: str) -> str:
    """Return `systemctl --user is-enabled <unit>` output, '' on failure."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-enabled", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return ""
    return r.stdout.strip()


def _show_services(units: list[str]) -> list[dict[str, str]]:
    """Parse `systemctl --user show` property blocks for `units`.

    One `key=value` block per requested unit (blank-line separated),
    in request order; an unknown unit still yields a block carrying
    its `Id`. Returns [] when systemctl is absent or times out."""
    if not units:
        return []
    try:
        r = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "-p",
                "Id",
                "-p",
                "ActiveState",
                "-p",
                "Result",
                "-p",
                "ExecMainStatus",
                *units,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return []
    blocks: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if not line:
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        key, _, value = line.partition("=")
        cur[key] = value
    if cur:
        blocks.append(cur)
    return blocks


def _show_timer(unit: str) -> dict[str, str] | None:
    """Parse `systemctl --user show` for a `.timer`'s liveness and next
    firing. Returns the `key=value` property map, or None when systemctl
    is absent or times out (indeterminate)."""
    try:
        r = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "-p",
                "ActiveState",
                "-p",
                "NextElapseUSecMonotonic",
                "-p",
                "NextElapseUSecRealtime",
                unit,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return None
    props: dict[str, str] = {}
    for line in r.stdout.splitlines():
        key, _, value = line.partition("=")
        props[key] = value
    return props


def _current_user() -> str:
    """Best-effort resolution of the invoking user's name, for the
    linger check. Falls back through env vars and getpwuid so the
    typical Unix shell environment is enough; '' when none answers."""
    for env_key in ("USER", "LOGNAME"):
        v = os.environ.get(env_key)
        if v:
            return v
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except ImportError, KeyError:
        return ""


def _linger_enabled(user: str) -> bool | None:
    """True / False / None for "is linger enabled for `user`?"

    Checks the world-readable sentinel file first so the common case is
    a single stat() with no subprocess, then falls back to `loginctl
    show-user --property=Linger`. None when neither path can answer (no
    logind, command missing, etc.)."""
    sentinel = Path(f"/var/lib/systemd/linger/{user}")
    if sentinel.exists():
        return True
    try:
        result = subprocess.run(
            ["loginctl", "show-user", user, "--property=Linger"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip()
    if line.endswith("=yes"):
        return True
    if line.endswith("=no"):
        return False
    return None


class SystemdScheduler(Scheduler):
    """systemd backend: a `.service` per entity, plus a `.timer` when
    the entity carries a schedule."""

    # A reload is `daemon-reload` plus enabling and restarting the timer;
    # restarting the `.timer` re-arms the schedule but does not touch the
    # `.service`, so a job can rewrite its own unit while in flight
    # without killing its runner.
    reload_terminates_running_job = False

    @staticmethod
    def default_unit_dir() -> Path:
        return Path.home() / ".config" / "systemd" / "user"

    def render_units(self, spec: UnitSpec) -> RenderedUnits:
        # A `.service` defines / runs the job; a scheduled entry also gets
        # a `.timer` that arms it. An unscheduled entry renders the
        # `.service` alone -- no `.timer`. A stale `.timer` from a
        # scheduled -> unscheduled transition is found by
        # `_discover_unit_files` (not a fixed slot list) and pruned on
        # install, and read back as drift.
        name = str(spec.name)
        units = [
            RenderedUnit(
                Path(_service_filename(name)),
                _render_service(name, spec.cmd, spec.priority),
            )
        ]
        if spec.timing is not None:
            jitter = spec.jitter.offset if spec.jitter is not None else None
            units.append(
                RenderedUnit(
                    Path(_timer_filename(name)),
                    _render_timer(name, spec.timing, jitter),
                )
            )
        return RenderedUnits(tuple(units))

    def config_filename(self, name: str) -> Path:
        return Path(_service_filename(name))

    def _discover_unit_files(self, name: str) -> list[Path]:
        if not self.unit_dir.exists():
            return []
        prefix = f"{UNIT_PREFIX}-{name}."
        return [
            Path(p.name)
            for p in self.unit_dir.iterdir()
            if p.name.startswith(prefix)
            and p.name.endswith((".service", ".timer"))
        ]

    def installed_cmd(self, name: str) -> list[str] | None:
        service = self.unit_dir / _service_filename(name)
        try:
            content = service.read_text(encoding="utf-8")
        except OSError:
            return None
        return _service_argv(content)

    def dispatch_unit_path(self, name: str) -> Path:
        # `systemctl --user start crony-<name>.service` fires the
        # service, not the timer (the scheduler-arm side).
        return self.unit_dir / _service_filename(name)

    def unit_name(self, name: str, scheduled: bool | None, /) -> str:
        # A scheduled entry is driven by its `.timer`; a grouped /
        # transit entry installs only the `.service`. None means the
        # caller can't decide which, so there is no name to report.
        if scheduled is None:
            return ""
        fn = _timer_filename if scheduled else _service_filename
        return fn(name)

    def installed_names(self) -> set[str]:
        names: set[str] = set()
        if not self.unit_dir.exists():
            return names
        prefix = f"{UNIT_PREFIX}-"
        for suffix in (".service", ".timer"):
            for p in self.unit_dir.iterdir():
                if p.name.startswith(prefix) and p.name.endswith(suffix):
                    names.add(p.name[len(prefix) : -len(suffix)])
        return names

    def is_loaded(self, name: str) -> bool:
        # A scheduled entry arms an enabled `.timer`; a schedule-less one
        # (grouped / transit or disabled) installs no timer, only a
        # static `.service`. Query the timer when it is installed, else
        # fall back to the service (systemd reports a unit with no
        # [Install] section `static`), so a grouped / disabled entry
        # still reads loaded. Anything else means the scheduler has no
        # unit. The operator-disabled state is not read here -- it is
        # flagged off the snapshot, not the scheduler.
        timer = self.unit_dir / _timer_filename(name)
        unit = (
            _timer_filename(name) if timer.exists() else _service_filename(name)
        )
        return _is_enabled(unit) in ("enabled", "static")

    def schedule_armed(self, name: str) -> bool:
        # A scheduled entry installs a `.timer` and is armed only when
        # that timer has a live next firing: an active timer with a next
        # elapse on either clock is armed; an active timer whose only
        # monotonic anchor never elapses reports NextElapseUSecMonotonic=
        # infinity (and no realtime elapse) and will never fire -- loaded
        # but dead. An inactive / unqueryable timer is not confirmed
        # armed. A grouped / disabled entry installs no timer -- its
        # schedule rides on the loaded `.service`, so it is armed whenever
        # the scheduler has it loaded.
        timer = self.unit_dir / _timer_filename(name)
        if not timer.is_file():
            return self.is_loaded(name)
        props = _show_timer(_timer_filename(name))
        if props is None or props.get("ActiveState") != "active":
            return False
        if props.get("NextElapseUSecRealtime", ""):
            return True
        monotonic = props.get("NextElapseUSecMonotonic", "")
        return bool(monotonic and monotonic not in ("infinity", "0"))

    def unit_last_exits(self) -> dict[str, UnitLastExit]:
        # The `.service` is the unit that runs the job; query it (not
        # the `.timer`). ExecMainStatus is the exit code for a normal
        # exit and the signal number for a kill; Result distinguishes
        # them. Normalize to the launchctl convention -- exit codes
        # positive, signals negated -- so both backends report the exit
        # uniformly. A unit with a launch in flight (its status is stale)
        # or no readable status is left out.
        services = {_service_filename(n): n for n in self.installed_names()}
        out: dict[str, UnitLastExit] = {}
        for blk in _show_services(list(services)):
            name = services.get(blk.get("Id", ""))
            if name is None:
                continue
            if blk.get("ActiveState", "") in (
                "active",
                "activating",
                "reloading",
            ):
                continue
            try:
                raw = int(blk.get("ExecMainStatus", ""))
            except ValueError:
                continue
            killed = blk.get("Result", "") not in ("success", "exit-code")
            out[name] = UnitLastExit(exit_status=-raw if killed else raw)
        return out

    def activate(self, name: str, *, scheduled: bool) -> None:
        # --quiet suppresses systemctl's success-path "Created symlink"
        # chatter; real errors still print.
        self._run_checked(["systemctl", "--user", "--quiet", "daemon-reload"])
        # Only a scheduled entry has a `.timer`; a schedule-less `.service`
        # (grouped or disabled) sits dormant. Enable creates the boot
        # symlink; restart re-activates the timer so its monotonic anchor
        # (OnActiveSec) is re-seeded even when the timer was already
        # running -- a plain `enable --now` would no-op the start on an
        # active timer and leave an interval schedule stuck on its stale
        # activation, unable to fire.
        if scheduled:
            timer = _timer_filename(name)
            self._run_checked(_SYSTEMCTL_ENABLE + [timer])
            self._run_checked(_SYSTEMCTL_RESTART + [timer])

    def deactivate(self, name: str) -> None:
        subprocess.run(
            _SYSTEMCTL_DISABLE + [_timer_filename(name)],
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["systemctl", "--user", "--quiet", "daemon-reload"],
            stderr=subprocess.DEVNULL,
        )

    def remove_files(self, name: str) -> None:
        # `deactivate` disables the canonical service / timer; a discovered
        # `.timer` under any other suffix (a leftover) is disabled here too
        # so no enabled `timers.target.wants` symlink survives its unlink.
        self.deactivate(name)
        for filename in self._discover_unit_files(name):
            if filename.suffix == ".timer":
                subprocess.run(
                    _SYSTEMCTL_DISABLE + [str(filename)],
                    stderr=subprocess.DEVNULL,
                )
            (self.unit_dir / filename).unlink(missing_ok=True)

    def verify(self) -> None:
        # Linger is what keeps the user's systemd manager running across
        # logouts; without it a scheduled timer only fires while the
        # user is logged in, so a "daily 03:00" job silently no-ops.
        user = _current_user()
        enabled = _linger_enabled(user) if user else None
        if enabled is True:
            return
        if enabled is False:
            raise SchedulerWarning(
                f"linger is disabled for {user!r} -- scheduled jobs only "
                "fire while you have an active login session. "
                f"Fix: sudo loginctl enable-linger {user}"
            )
        who = repr(user) if user else "the current user"
        raise SchedulerWarning(
            f"could not determine whether linger is enabled for {who}; "
            "scheduled jobs may only fire while you have an active login "
            "session"
        )

    def trigger(self, name: str) -> None:
        # The timer's job is to fire the .service; start it directly.
        # --no-block enqueues the start job and returns without waiting
        # for it: the .service is Type=oneshot, for which a plain
        # `systemctl start` blocks until ExecStart exits. trigger's
        # contract is to return once the platform accepts the fire, not
        # once the job finishes -- a caller that wants completion waits
        # for it separately.
        self._run_checked(
            [
                "systemctl",
                "--user",
                "start",
                "--no-block",
                _service_filename(name),
            ]
        )

    def prune_units(self, name: str, keep: set[str]) -> None:
        # Remove every discovered unit file not in `keep`: an orphaned
        # .timer (scheduled -> unscheduled), or any stale file left by an
        # old naming scheme. A .timer is disabled in the scheduler before
        # it is unlinked.
        for filename in self._discover_unit_files(name):
            if str(filename) in keep:
                continue
            if filename.suffix == ".timer":
                subprocess.run(
                    _SYSTEMCTL_DISABLE + [str(filename)],
                    stderr=subprocess.DEVNULL,
                )
            (self.unit_dir / filename).unlink(missing_ok=True)
