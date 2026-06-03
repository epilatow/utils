# This is AI generated code

"""systemd (Linux) scheduler backend.

Each entity is a `.service` unit; scheduled entries also get a `.timer`
that arms it. Schedule-less entries install only the static `.service`,
which sits dormant until `crony trigger` or a parent group fires it.
"""

from __future__ import annotations

from pathlib import Path

from crony.platform.scheduler import UNIT_PREFIX, Scheduler
from crony.unit import EntityRef, Interval, PriorityClass, Timing, UnitSpec


def service_filename(name: str) -> str:
    """Basename of the systemd `.service` unit for `name`."""
    return f"{UNIT_PREFIX}-{name}.service"


def timer_filename(name: str) -> str:
    """Basename of the systemd `.timer` unit for `name`."""
    return f"{UNIT_PREFIX}-{name}.timer"


def _priority_block(priority: PriorityClass | None) -> str:
    """[Service] priority directives for a job, or '' for normal.

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
    ref: EntityRef,
    priority: PriorityClass | None = None,
    *,
    uv_path: Path,
    crony_path: Path,
) -> str:
    """Render the systemd `.service` unit. Independent of schedule.

    ExecStart invokes uv with absolute paths and addresses the
    entity by `<bundle>:<uuid>` so the runner skips the name->uuid
    lookup -- same reason as for the plist (PATH for a systemd user
    service is minimal and need not contain uv). The unit description
    carries the human-readable name. `priority` adds CPU / IO
    scheduling directives inherited by the spawned command.
    """
    return (
        "[Unit]\n"
        f"Description=crony job {name}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={uv_path} run --script {crony_path} run "
        f"{ref}\n"
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


class SystemdScheduler(Scheduler):
    """systemd backend: a `.service` per entity, plus a `.timer` when
    the entity carries a schedule."""

    def render(
        self, spec: UnitSpec, *, uv_path: Path, crony_path: Path
    ) -> dict[str, str]:
        name = str(spec.name)
        units = {
            service_filename(name): render_service(
                name,
                spec.ref,
                spec.priority,
                uv_path=uv_path,
                crony_path=crony_path,
            )
        }
        if spec.timing is not None:
            units[timer_filename(name)] = render_timer(name, spec.timing)
        return units
