# This is AI generated code

"""launchd (macOS) scheduler backend.

Each entity is a single LaunchAgent plist: a scheduled entry carries a
`StartInterval` / `StartCalendarInterval`, a schedule-less one just sits
dormant (`RunAtLoad=false`) until something fires it.
"""

import os
import plistlib
import shlex
import subprocess
import time
from pathlib import Path

from crony.platform.scheduler import (
    UNIT_PREFIX,
    RenderedUnit,
    RenderedUnits,
    Scheduler,
    UnitLastExit,
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
    keeps the byte output deterministic across renders.
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
    except plistlib.InvalidFileException, ValueError, OSError:
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


def _launchctl_list() -> str:
    """Stdout of `launchctl list`; empty on failure."""
    try:
        r = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return ""
    return r.stdout


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


# `launchctl bootout` is asynchronous: it returns before launchd has
# finished deregistering the label, and `bootstrap` of a label still
# present in the domain fails with errno 5 (Input/output error). So a
# reload boots out, waits for the label to disappear (bounded), then
# bootstraps -- retrying the whole sequence a few times to absorb any
# residual teardown lag before surfacing a genuine failure.
_BOOTOUT_SETTLE_TIMEOUT_SEC = 5.0
_BOOTOUT_POLL_INTERVAL_SEC = 0.02
_BOOTSTRAP_ATTEMPTS = 3
_BOOTSTRAP_BACKOFF_SEC = 0.1
# errno launchctl returns when a label is still present in the domain
# (the asynchronous-teardown race) -- the one bootstrap failure a retry
# can clear. Other exit codes are genuine and surface at once.
_LAUNCHD_EIO = 5


class LaunchdScheduler(Scheduler):
    """launchd backend: one LaunchAgent plist per entity."""

    # A reload is bootout+bootstrap; bootout terminates the running job's
    # process group, so reloading a job's own unit kills its runner.
    reload_terminates_running_job = True

    @staticmethod
    def default_unit_dir() -> Path:
        return Path.home() / "Library" / "LaunchAgents"

    def render_units(self, spec: UnitSpec) -> RenderedUnits:
        # One LaunchAgent plist carries both the command and the schedule
        # keys, so the entity has a single unit slot.
        name = str(spec.name)
        return RenderedUnits(
            (
                RenderedUnit(
                    Path(plist_filename(name)),
                    render_plist(name, spec.cmd, spec.timing, spec.priority),
                ),
            )
        )

    def config_filename(self, name: str) -> Path:
        return Path(plist_filename(name))

    def _discover_unit_files(self, name: str) -> list[Path]:
        if not self.unit_dir.exists():
            return []
        prefix = f"{label(name)}."
        return [
            Path(p.name)
            for p in self.unit_dir.iterdir()
            if p.name.startswith(prefix) and p.name.endswith(".plist")
        ]

    def installed_cmd(self, name: str) -> list[str] | None:
        p = self.unit_dir / plist_filename(name)
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            return None
        return _plist_argv(content)

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

    def is_loaded(self, name: str) -> bool:
        return _is_loaded(label(name))

    def schedule_armed(self, name: str) -> bool:
        # launchd carries the schedule in the job's own plist (StartInterval
        # / StartCalendarInterval), not a separate arming unit that could be
        # loaded yet dead. A loaded agent is armed, so armed tracks loaded.
        return self.is_loaded(name)

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

    def _gui(self, name: str) -> str:
        return f"gui/{os.getuid()}/{label(name)}"

    def _gui_domain(self) -> str:
        return f"gui/{os.getuid()}"

    def _bootout(self, name: str) -> None:
        """Remove `name`'s service from the GUI domain. Tolerant of an
        already-absent service (a never-loaded or already-removed unit
        boots out non-zero, which is not an error here)."""
        subprocess.run(
            ["launchctl", "bootout", self._gui(name)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _await_unloaded(self, name: str) -> None:
        """Block until `name`'s label is no longer registered in the
        domain, bounded by a timeout. `bootout` returns before launchd
        finishes deregistering the label, and a `bootstrap` of a
        still-present label fails with errno 5; a stuck teardown can't
        hang the caller past the bound."""
        lbl = label(name)
        deadline = time.monotonic() + _BOOTOUT_SETTLE_TIMEOUT_SEC
        while _is_loaded(lbl) and time.monotonic() < deadline:
            time.sleep(_BOOTOUT_POLL_INTERVAL_SEC)

    def _bootstrap(self, name: str, plist: Path) -> None:
        """Load `plist` into the GUI domain, settling and retrying around
        the asynchronous-teardown errno-5 race: boot out any leftover
        instance, wait for the label to clear, then bootstrap; on the
        spurious errno 5 re-settle and retry before surfacing a genuine
        failure. The caller must have enabled the label first -- a
        disabled label's bootstrap fails with the same errno 5 and is not
        a transient the retry can clear, so this never runs on one."""
        cmd = ["launchctl", "bootstrap", self._gui_domain(), str(plist)]
        result: subprocess.CompletedProcess[str] | None = None
        for attempt in range(_BOOTSTRAP_ATTEMPTS):
            self._bootout(name)
            self._await_unloaded(name)
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return
            # Only the errno-5 race is a transient worth re-settling and
            # retrying; any other failure is genuine and surfaces now.
            if result.returncode != _LAUNCHD_EIO:
                break
            if attempt + 1 < _BOOTSTRAP_ATTEMPTS:
                time.sleep(_BOOTSTRAP_BACKOFF_SEC * (attempt + 1))
        assert result is not None  # the loop ran at least once
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )

    def activate(self, name: str, *, scheduled: bool) -> None:
        del scheduled  # a plist with no Start* keys loads fine, dormant
        plist = self.unit_dir / plist_filename(name)
        # Validate before asking launchd to load (`-s` keeps stdout
        # quiet on success). A disabled entry's plist is schedule-less
        # but still bootstrapped -- loaded and triggerable, just dormant.
        subprocess.run(["plutil", "-s", str(plist)], check=True)
        self._bootstrap(name, plist)

    def deactivate(self, name: str) -> None:
        if (self.unit_dir / plist_filename(name)).exists():
            self._bootout(name)

    def remove_files(self, name: str) -> None:
        self.deactivate(name)
        for filename in self._discover_unit_files(name):
            (self.unit_dir / filename).unlink(missing_ok=True)

    def verify(self) -> None:
        # launchd loads a logged-in user's agents automatically; there is
        # no logout-survival toggle to check, so nothing to warn about.
        return

    def trigger(self, name: str) -> None:
        # kickstart invokes now; `start` only queues the next fire.
        subprocess.run(["launchctl", "kickstart", self._gui(name)], check=True)

    def prune_units(self, name: str, keep: set[str]) -> None:
        # Remove every discovered plist not in `keep`. The config plist
        # render always produces, so normally nothing is pruned; a stale
        # file from an old naming scheme is found and cleaned. Unloading
        # the label first covers the config plist (the loaded unit).
        config = str(self.config_filename(name))
        for filename in self._discover_unit_files(name):
            if str(filename) in keep:
                continue
            if str(filename) == config:
                self.deactivate(name)
            (self.unit_dir / filename).unlink(missing_ok=True)
