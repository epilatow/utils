# This is AI generated code

"""launchd (macOS) scheduler backend.

Each entity is a single LaunchAgent plist: a scheduled entry carries a
`StartInterval` / `StartCalendarInterval`, a schedule-less one just sits
dormant (`RunAtLoad=false`) until something fires it.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
from pathlib import Path

from crony.platform.scheduler import (
    UNIT_CONFIG,
    UNIT_PREFIX,
    Scheduler,
    UnitLastExit,
    UnitState,
)
from crony.unit import (
    Interval,
    PriorityClass,
    Schedule,
    Timing,
    UnitSpec,
)


def label(name: str) -> str:
    """launchd Label for a job/group."""
    return f"org.{UNIT_PREFIX}.{name}"


def plist_filename(name: str) -> str:
    """Basename of the LaunchAgent plist for `name`."""
    return f"{label(name)}.plist"


def _priority_keys(priority: PriorityClass) -> dict[str, object]:
    """LaunchAgent priority keys for a job, or {} for NORMAL.

    HIGH runs the job at app-like QoS with normal CPU + IO
    (ProcessType=Interactive avoids the Background QoS throttling that
    can drastically slow IO-bound work); LOW throttles it. The keys
    are inherited by the command the runner spawns.
    """
    if priority is PriorityClass.HIGH:
        return {
            "ProcessType": "Interactive",
            "LowPriorityIO": False,
            "Nice": 0,
        }
    if priority is PriorityClass.LOW:
        return {
            "ProcessType": "Background",
            "LowPriorityIO": True,
            "Nice": 10,
        }
    return {}


def render_plist(
    name: str,
    cmd: tuple[str, ...],
    timing: Timing | None,
    priority: PriorityClass = PriorityClass.NORMAL,
) -> str:
    """Render the LaunchAgent plist XML for a job or group.

    The Label uses the full namespaced name for human readability;
    `cmd` is the argv the unit executes.

    Serialized with `plistlib` so the XML is well-formed by
    construction (escaping, typed values, DOCTYPE); `sort_keys`
    keeps the byte output deterministic for the drift check.
    """
    # launchd execs ProgramArguments[0] through xpcproxy, which
    # enforces AMFI launch constraints. uv ships ad-hoc-signed, and
    # after `uv self update` swaps the binary for a new cdhash that
    # first launchd-driven launch is killed (OS_REASON_CODESIGNING)
    # before crony runs -- silently breaking every scheduled unit
    # until something relaunches it. Going through /bin/sh (a
    # platform binary that always launches) makes uv an ordinary
    # exec, like a terminal invocation, which the constraint check
    # doesn't reach. `exec` so sh is replaced by the command (one
    # process; its pid and exit code propagate straight to launchd).
    inner = shlex.join(cmd)
    contents: dict[str, object] = {
        "Label": label(name),
        "ProgramArguments": ["/bin/sh", "-c", f"exec {inner}"],
        "RunAtLoad": False,
        "KeepAlive": False,
        "AbandonProcessGroup": False,
    }
    contents.update(_priority_keys(priority))
    if isinstance(timing, Interval):
        contents["StartInterval"] = timing.total_seconds
    elif isinstance(timing, Schedule):
        contents["StartCalendarInterval"] = timing.to_plist_calendar()
    return plistlib.dumps(
        contents, fmt=plistlib.FMT_XML, sort_keys=True
    ).decode("utf-8")


def _plist_argv(content: str) -> list[str] | None:
    """Recover the argv embedded in a plist, or None when it isn't in the
    shape `render_plist` produces.

    The inverse of `render_plist`'s embedding: it unwraps the
    `/bin/sh -c 'exec <argv>'` ProgramArguments back to the argv list."""
    try:
        data = plistlib.loads(content.encode("utf-8"))
    except (plistlib.InvalidFileException, ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    args = data.get("ProgramArguments")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return None
    if len(args) != 3 or args[:2] != ["/bin/sh", "-c"]:
        return None
    try:
        inner = shlex.split(args[2])
    except ValueError:
        return None
    if not inner or inner[0] != "exec":
        return None
    return inner[1:]


def _launchctl_print_disabled() -> str:
    """Stdout of `launchctl print-disabled gui/<uid>`; empty on failure."""
    try:
        r = subprocess.run(
            ["launchctl", "print-disabled", f"gui/{os.getuid()}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return r.stdout


def _launchctl_list() -> str:
    """Stdout of `launchctl list`; empty on failure."""
    try:
        r = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return r.stdout


def _is_disabled(lbl: str) -> bool:
    """True if `launchctl print-disabled` reports `lbl` disabled."""
    out = _launchctl_print_disabled()
    return f'"{lbl}" => disabled' in out or f'"{lbl}" => true' in out


def _is_loaded(lbl: str) -> bool:
    """True if `launchctl list` shows `lbl` as loaded.

    `launchctl list` lines have the form `<pid>\\t<exit>\\t<label>`.
    A pid of `-` means loaded-but-idle (the normal between-fires state
    for a calendar-scheduled agent), so we don't filter on pid here.
    Trailing whitespace is tolerated for forward-compat with future
    launchctl output formats.
    """
    out = _launchctl_list()
    for raw in out.splitlines():
        line = raw.rstrip()
        if "\t" in line and line.split("\t")[-1] == lbl:
            return True
    return False


class LaunchdScheduler(Scheduler):
    """launchd backend: one LaunchAgent plist per entity."""

    # A reload is unload+load; unload terminates the running job's
    # process group, so reloading a job's own unit kills its runner.
    reload_terminates_running_job = True

    @staticmethod
    def default_unit_dir() -> Path:
        return Path.home() / "Library" / "LaunchAgents"

    def render(self, spec: UnitSpec) -> dict[str, str]:
        name = str(spec.name)
        return {
            plist_filename(name): render_plist(
                name,
                spec.cmd,
                spec.timing,
                spec.priority,
            )
        }

    def installed_cmd(self, name: str) -> list[str] | None:
        p = self.unit_dir / plist_filename(name)
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            return None
        return _plist_argv(content)

    def unit_config_path(self, name: str) -> Path | None:
        p = self.unit_dir / plist_filename(name)
        return p if p.is_file() else None

    def unit_timer_path(self, _name: str) -> Path | None:
        # A LaunchAgent carries its own schedule keys; there is no
        # separate timer unit.
        return None

    def dispatch_unit_path(self, name: str) -> Path:
        # launchctl kickstart targets the loaded plist by label.
        return self.unit_dir / plist_filename(name)

    def unit_name(self, name: str, _scheduled: bool | None, /) -> str:
        # One label per entity, regardless of schedule.
        return label(name)

    def installed_names(self) -> set[str]:
        names: set[str] = set()
        if not self.unit_dir.exists():
            return names
        prefix, suffix = f"org.{UNIT_PREFIX}.", ".plist"
        for p in self.unit_dir.iterdir():
            if p.name.startswith(prefix) and p.name.endswith(suffix):
                names.add(p.name[len(prefix) : -len(suffix)])
        return names

    def state(self, name: str) -> UnitState:
        lbl = label(name)
        if _is_disabled(lbl):
            return UnitState.DISABLED
        if _is_loaded(lbl):
            return UnitState.ENABLED
        return UnitState.NONE

    def unit_last_exits(self) -> dict[str, UnitLastExit]:
        # `launchctl list` lines are `<pid>\t<status>\t<label>`. The
        # status column is the last completed run's wait status: 0 / a
        # positive exit code, or a negative number whose magnitude is
        # the terminating signal. A numeric pid means a launch is in
        # flight (its status is stale) -- skip it, leaving the unit out.
        out: dict[str, UnitLastExit] = {}
        prefix = f"org.{UNIT_PREFIX}."
        for raw in _launchctl_list().splitlines():
            parts = raw.rstrip().split("\t")
            if len(parts) != 3:
                continue
            pid_s, status_s, lbl = parts
            if not lbl.startswith(prefix) or pid_s.strip() not in ("-", ""):
                continue
            try:
                status = int(status_s)
            except ValueError:
                continue
            out[lbl[len(prefix) :]] = UnitLastExit(exit_status=status)
        return out

    def drifted_units(self, spec: UnitSpec) -> frozenset[str]:
        # launchd has only the plist (CONFIG); the schedule lives in it,
        # so there is never a separate timer to drift.
        config = frozenset({UNIT_CONFIG})
        name = str(spec.name)
        path = self.unit_dir / plist_filename(name)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return config
        if content != self.render(spec)[plist_filename(name)]:
            return config
        # A grouped (schedule-less) plist must still be loaded to be
        # kickstartable, so an unloaded unit is drift here too.
        if self.state(name) == UnitState.NONE:
            return config
        return frozenset()

    def _gui(self, name: str) -> str:
        return f"gui/{os.getuid()}/{label(name)}"

    def activate(
        self, name: str, *, prior_disabled: bool, scheduled: bool
    ) -> None:
        del scheduled  # a plist with no Start* keys loads fine, dormant
        plist = self.unit_dir / plist_filename(name)
        # Validate before asking launchd to load (`-s` keeps stdout
        # quiet on success). unload-then-load tolerates "not loaded".
        subprocess.run(["plutil", "-s", str(plist)], check=True)
        subprocess.run(
            ["launchctl", "unload", str(plist)], stderr=subprocess.DEVNULL
        )
        subprocess.run(["launchctl", "load", str(plist)], check=True)
        if prior_disabled:
            subprocess.run(
                ["launchctl", "unload", str(plist)], stderr=subprocess.DEVNULL
            )
            subprocess.run(
                ["launchctl", "disable", self._gui(name)], check=True
            )

    def deactivate(self, name: str) -> None:
        plist = self.unit_dir / plist_filename(name)
        if plist.exists():
            subprocess.run(
                ["launchctl", "unload", str(plist)], stderr=subprocess.DEVNULL
            )

    def remove_files(self, name: str) -> None:
        self.deactivate(name)
        (self.unit_dir / plist_filename(name)).unlink(missing_ok=True)

    def verify(self) -> None:
        # launchd loads a logged-in user's agents automatically; there is
        # no logout-survival toggle to check, so nothing to warn about.
        return

    def enable(self, name: str) -> None:
        plist = self.unit_dir / plist_filename(name)
        subprocess.run(["launchctl", "enable", self._gui(name)], check=True)
        subprocess.run(
            ["launchctl", "unload", str(plist)], stderr=subprocess.DEVNULL
        )
        subprocess.run(["launchctl", "load", str(plist)], check=True)

    def disable(self, name: str) -> None:
        plist = self.unit_dir / plist_filename(name)
        # Unload first so the persistent disable record takes effect;
        # otherwise the still-loaded plist keeps firing.
        subprocess.run(
            ["launchctl", "unload", str(plist)], stderr=subprocess.DEVNULL
        )
        subprocess.run(["launchctl", "disable", self._gui(name)], check=True)

    def trigger(self, name: str) -> None:
        # kickstart invokes now; `start` only queues the next fire.
        subprocess.run(["launchctl", "kickstart", self._gui(name)], check=True)

    def prune_units(self, name: str, keep: set[str]) -> None:
        # One plist per name, which render always produces, so there is
        # normally nothing to prune.
        fn = plist_filename(name)
        if fn not in keep:
            self.deactivate(name)
            (self.unit_dir / fn).unlink(missing_ok=True)
