# This is AI generated code

"""systemd (Linux) scheduler backend.

Each entity is a `.service` unit; scheduled entries also get a `.timer`
that arms it. Schedule-less entries install only the static `.service`,
which sits dormant until `crony trigger` or a parent group fires it.
"""

from __future__ import annotations

import configparser
import os
import shlex
import subprocess
from pathlib import Path

from crony.platform.scheduler import (
    UNIT_PREFIX,
    Scheduler,
    SchedulerWarning,
    UnitLastExit,
    UnitState,
)
from crony.unit import (
    Interval,
    PriorityClass,
    Timing,
    UnitSpec,
)

# --now enables/disables the unit and starts/stops it in one call;
# --quiet drops the success-path symlink chatter.
_SYSTEMCTL_ENABLE = ["systemctl", "--user", "--quiet", "enable", "--now"]
_SYSTEMCTL_DISABLE = ["systemctl", "--user", "--quiet", "disable", "--now"]


def service_filename(name: str) -> str:
    """Basename of the systemd `.service` unit for `name`."""
    return f"{UNIT_PREFIX}-{name}.service"


def timer_filename(name: str) -> str:
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


def render_service(
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


def render_timer(name: str, timing: Timing) -> str:
    """Render the systemd `.timer` unit."""
    if isinstance(timing, Interval):
        spec_line = f"OnUnitActiveSec={timing}\n"
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
    isn't in the shape `render_service` produces.

    The inverse of `render_service`'s ExecStart embedding."""
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
    except (FileNotFoundError, subprocess.TimeoutExpired):
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
    except (FileNotFoundError, subprocess.TimeoutExpired):
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
    except (ImportError, KeyError):
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
    except (FileNotFoundError, subprocess.TimeoutExpired):
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

    # A reload is `daemon-reload` plus (re)enabling the timer; neither
    # stops a running `.service`, so a job can rewrite its own unit while
    # in flight without killing its runner.
    reload_terminates_running_job = False

    @staticmethod
    def default_unit_dir() -> Path:
        return Path.home() / ".config" / "systemd" / "user"

    def render_config(self, spec: UnitSpec) -> tuple[Path, str]:
        name = str(spec.name)
        return Path(service_filename(name)), render_service(
            name,
            spec.cmd,
            spec.priority,
        )

    def render_timer(self, spec: UnitSpec) -> tuple[Path, str] | None:
        if spec.timing is None:
            return None
        name = str(spec.name)
        return Path(timer_filename(name)), render_timer(name, spec.timing)

    def installed_cmd(self, name: str) -> list[str] | None:
        service = self.unit_dir / service_filename(name)
        try:
            content = service.read_text(encoding="utf-8")
        except OSError:
            return None
        return _service_argv(content)

    def unit_config_path(self, name: str) -> Path | None:
        # The `.service` defines and runs the job (every entry has one,
        # scheduled or grouped); the schedule lives in the separate
        # `.timer` reported by unit_timer_path.
        service = self.unit_dir / service_filename(name)
        return service if service.is_file() else None

    def unit_timer_path(self, name: str) -> Path | None:
        timer = self.unit_dir / timer_filename(name)
        return timer if timer.is_file() else None

    def dispatch_unit_path(self, name: str) -> Path:
        # `systemctl --user start crony-<name>.service` fires the
        # service, not the timer (the scheduler-arm side).
        return self.unit_dir / service_filename(name)

    def unit_name(self, name: str, scheduled: bool | None, /) -> str:
        # A scheduled entry is driven by its `.timer`; a grouped /
        # transit entry installs only the `.service`. None means the
        # caller can't decide which, so there is no name to report.
        if scheduled is None:
            return ""
        fn = timer_filename if scheduled else service_filename
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

    def state(self, name: str) -> UnitState:
        st = _is_enabled(timer_filename(name))
        if st == "enabled":
            return UnitState.ENABLED
        if st in ("disabled", "masked"):
            return UnitState.DISABLED
        # A schedule-less entry has only a static `.service` (no timer to
        # enable); systemd reports it `static`, which counts as known.
        if st == "static":
            return UnitState.ENABLED
        return UnitState.NONE

    def unit_last_exits(self) -> dict[str, UnitLastExit]:
        # The `.service` is the unit that runs the job; query it (not
        # the `.timer`). ExecMainStatus is the exit code for a normal
        # exit and the signal number for a kill; Result distinguishes
        # them. Normalize to the launchctl convention -- exit codes
        # positive, signals negated -- so RuntimeState.crashed compares
        # uniformly across backends. A unit with a launch in flight (its
        # status is stale) or no readable status is left out.
        services = {service_filename(n): n for n in self.installed_names()}
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

    def activate(
        self, name: str, *, prior_disabled: bool, scheduled: bool
    ) -> None:
        # --quiet suppresses systemctl's success-path "Created symlink"
        # chatter; real errors still print.
        subprocess.run(
            ["systemctl", "--user", "--quiet", "daemon-reload"], check=True
        )
        # enable --now only applies to the .timer, rendered only for
        # scheduled entries; a schedule-less .service sits dormant.
        if scheduled and not prior_disabled:
            subprocess.run(
                _SYSTEMCTL_ENABLE + [timer_filename(name)], check=True
            )

    def deactivate(self, name: str) -> None:
        subprocess.run(
            _SYSTEMCTL_DISABLE + [timer_filename(name)],
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["systemctl", "--user", "--quiet", "daemon-reload"],
            stderr=subprocess.DEVNULL,
        )

    def remove_files(self, name: str) -> None:
        self.deactivate(name)
        (self.unit_dir / service_filename(name)).unlink(missing_ok=True)
        (self.unit_dir / timer_filename(name)).unlink(missing_ok=True)

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

    def enable(self, name: str) -> None:
        subprocess.run(_SYSTEMCTL_ENABLE + [timer_filename(name)], check=True)

    def disable(self, name: str) -> None:
        subprocess.run(_SYSTEMCTL_DISABLE + [timer_filename(name)], check=True)

    def trigger(self, name: str) -> None:
        # The timer's job is to fire the .service; start it directly.
        subprocess.run(
            ["systemctl", "--user", "start", service_filename(name)],
            check=True,
        )

    def prune_units(self, name: str, keep: set[str]) -> None:
        # The .service is always kept; an orphaned .timer (scheduled ->
        # unscheduled) is disabled in the scheduler before unlinking.
        timer = self.unit_dir / timer_filename(name)
        if timer.name not in keep and timer.is_file():
            subprocess.run(
                _SYSTEMCTL_DISABLE + [timer.name], stderr=subprocess.DEVNULL
            )
            timer.unlink()
