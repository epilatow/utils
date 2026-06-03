# This is AI generated code

"""launchd (macOS) scheduler backend.

Each entity is a single LaunchAgent plist: a scheduled entry carries a
`StartInterval` / `StartCalendarInterval`, a schedule-less one just sits
dormant (`RunAtLoad=false`) until something fires it.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

from crony.platform.scheduler import UNIT_PREFIX, Scheduler
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
