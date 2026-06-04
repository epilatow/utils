# This is AI generated code

"""launchd (macOS) scheduler backend.

Each entity is a single LaunchAgent plist: a scheduled entry carries a
`StartInterval` / `StartCalendarInterval`, a schedule-less one just sits
dormant (`RunAtLoad=false`) until something fires it.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from crony.platform.scheduler import (
    UNIT_PREFIX,
    Scheduler,
    UnitState,
    exec_paths_from_argv,
)
from crony.unit import (
    EntityRef,
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


def _priority_keys(priority: PriorityClass | None) -> dict[str, object]:
    """LaunchAgent priority keys for a job, or {} for normal.

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
    ref: EntityRef,
    timing: Timing | None,
    priority: PriorityClass | None = None,
    *,
    uv_path: Path,
    crony_path: Path,
) -> str:
    """Render the LaunchAgent plist XML for a job or group.

    The Label uses the full namespaced name for human readability;
    the runner gets the entity's `<bundle>:<uuid>` ref so it can
    locate the state dir directly without scanning.

    ProgramArguments invokes uv with absolute paths rather than
    relying on the script's `env -S uv run --script` shebang.
    launchd's per-agent PATH is `/usr/bin:/bin:/usr/sbin:/sbin`,
    which doesn't contain uv, so the shebang's `env` lookup fails
    with exit 127 before crony can run at all.

    Serialized with `plistlib` so the XML is well-formed by
    construction (escaping, typed values, DOCTYPE); `sort_keys`
    keeps the byte output deterministic for the drift check.
    """
    contents: dict[str, object] = {
        "Label": label(name),
        "ProgramArguments": [
            str(uv_path),
            "run",
            "--script",
            str(crony_path),
            "run",
            str(ref),
        ],
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


def _extract_exec_paths(content: str) -> tuple[Path, Path] | None:
    """Recover the `(uv, crony)` paths baked into a plist's argv, or
    None when it isn't a crony-shaped plist."""
    try:
        data = plistlib.loads(content.encode("utf-8"))
    except (plistlib.InvalidFileException, ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    args = data.get("ProgramArguments")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return None
    return exec_paths_from_argv(list(args))


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

    def render(
        self, spec: UnitSpec, *, uv_path: Path, crony_path: Path
    ) -> dict[str, str]:
        name = str(spec.name)
        return {
            plist_filename(name): render_plist(
                name,
                spec.ref,
                spec.timing,
                spec.priority,
                uv_path=uv_path,
                crony_path=crony_path,
            )
        }

    def unit_config_path(self, name: str) -> Path | None:
        p = self.unit_dir / plist_filename(name)
        return p if p.is_file() else None

    def dispatch_unit_path(self, name: str) -> Path:
        # launchctl kickstart targets the loaded plist by label.
        return self.unit_dir / plist_filename(name)

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

    def is_stale(self, spec: UnitSpec) -> bool:
        name = str(spec.name)
        path = self.unit_dir / plist_filename(name)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return True
        extracted = _extract_exec_paths(content)
        if extracted is None:
            return True
        uv_path, crony_path = extracted
        if not uv_path.is_file() or not crony_path.is_file():
            return True
        rendered = self.render(spec, uv_path=uv_path, crony_path=crony_path)
        if content != rendered[plist_filename(name)]:
            return True
        # A grouped (schedule-less) plist must still be loaded to be
        # kickstartable, so an unloaded unit is drift here too.
        return self.state(name) == UnitState.NONE

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
