# This is AI generated code

"""Typed value objects describing a scheduled unit.

bin/crony owns the domain model (Job, JobGroup, Config) and the command
handlers; this module holds the platform-neutral building blocks the
launchd / systemd scheduler modules render from. The two identity forms
(EntityRef, EntityName), the schedule and interval, the priority class,
and the UnitSpec that bundles them all carry validation and round-trip
(from_str / __str__) so the rest of crony passes structured values
instead of bare strings.

Schedule and Interval validate eagerly but keep the spec as written:
``str(value)`` round-trips the user's input exactly (what systemd
consumes and what status displays), while the launchd-specific
projections (``Schedule.to_plist_calendar`` / ``Interval.total_seconds``)
are parsed on demand. So "daily" stays "daily" rather than canonicalizing
to a different-but-equivalent form.

Every entity -- job or group -- maps to exactly one scheduled unit; the
group -> children fan-out happens at run time, so the platform layer
never needs to be job/group aware.
"""

import enum
import re
import uuid
from dataclasses import dataclass, field

# Bundle names come from filenames and are the namespace prefix, so the
# "." (name separator) and ":" (ref separator) are both excluded.
_BUNDLE_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
# Short names may contain dots; the full name splits on the first one.
_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def name_is_dotted_prefix(prefix: str, name: str) -> bool:
    """True when `name` extends `prefix` by one or more dotted components
    -- `prefix` is a proper dotted-prefix of `name` (`foo` of `foo.bar`).

    crony embeds an entity's name in its on-disk unit filenames, so a
    scheme that derives an extra unit by extending an entity's name would
    give one member of such a pair a filename that clashes with the
    other's. crony forbids the pair -- in config validation, and at apply
    time against what is already on disk.
    """
    return name.startswith(prefix + ".")


@dataclass(frozen=True)
class EntityRef:
    """Stable per-(bundle, uuid) identity for a Job or JobGroup.

    Bundle-scoped because UUIDs are unique within a bundle but can
    repeat across bundles. Frozen so it can key dicts and live in
    sets. ``str(ref)`` renders the ``<bundle>:<uuid>`` form and
    ``from_str`` parses it back; the two round-trip. That form is
    both the display token for refs with no recoverable name and
    ``crony _run``'s positional argv. The colon separator
    distinguishes it from the dot-separated name form.
    """

    bundle: str
    uuid: str

    def __str__(self) -> str:
        return f"{self.bundle}:{self.uuid}"

    @classmethod
    def from_str(cls, arg: str) -> EntityRef | None:
        """Parse ``<bundle>:<uuid>`` into an EntityRef, or return None
        when ``arg`` is not that shape.

        Both pieces are validated -- bundle against ``_BUNDLE_NAME_RE``
        and uuid as a canonical 8-4-4-4-12 string -- because the
        parsed ref composes a state-dir path that ``shutil.rmtree``
        later trusts; an unvalidated ``../../etc`` would escape
        ``STATE_DIR/<bundle>/``.
        """
        if ":" not in arg:
            return None
        bundle, _, entity_uuid = arg.partition(":")
        if not bundle or not entity_uuid:
            return None
        if not _BUNDLE_NAME_RE.match(bundle):
            return None
        try:
            parsed = uuid.UUID(entity_uuid)
        except ValueError:
            return None
        if entity_uuid != str(parsed):
            return None
        return cls(bundle, entity_uuid)


@dataclass(frozen=True)
class EntityName:
    """The human ``<bundle>.<short>`` name for a Job or JobGroup.

    The dot-separated counterpart to EntityRef. ``str(name)`` renders
    ``<bundle>.<short>`` and ``from_str`` parses it back, splitting on
    the first dot so a short name may itself contain dots. Frozen for
    use as a dict key.
    """

    bundle: str
    short: str

    def __str__(self) -> str:
        return f"{self.bundle}.{self.short}"

    @classmethod
    def from_str(cls, arg: str) -> EntityName:
        """Parse ``<bundle>.<short>`` into an EntityName.

        Splits on the first dot (so ``a.b.c`` is bundle ``a`` / short
        ``b.c``). Raises ValueError when the input lacks a bundle
        separator or either part fails validation -- the bare
        ``<short>`` form (which the CLI resolves to the default
        bundle) is not a full name and is handled by the caller.
        """
        bundle, sep, short = arg.partition(".")
        if not sep or not bundle or not short:
            raise ValueError(f"not a <bundle>.<short> name: {arg!r}")
        if not _BUNDLE_NAME_RE.match(bundle):
            raise ValueError(f"invalid bundle name: {bundle!r}")
        if not _NAME_RE.match(short):
            raise ValueError(f"invalid short name: {short!r}")
        return cls(bundle, short)


class EntityKind(enum.StrEnum):
    """Whether an entry is a single job or a job-group. The in-memory
    `kind` field's type on the constructed node, and on disk the `kind`
    key the snapshot model discriminates the job vs group shape on. A
    StrEnum so it serializes as its plain value and on-disk records
    round-trip unchanged."""

    JOB = "job"
    GROUP = "group"


class PriorityClass(enum.Enum):
    """A unit's scheduling priority.

    A neutral classification each scheduler maps to its own knobs
    (launchd plist ProcessType / Nice, systemd Nice + IO class).
    """

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @classmethod
    def from_str(cls, value: str) -> PriorityClass:
        try:
            return cls(value)
        except ValueError:
            allowed = ", ".join(p.value for p in cls)
            raise ValueError(
                f"invalid priority {value!r}; expected one of {allowed}"
            ) from None

    def __str__(self) -> str:
        return self.value


_INTERVAL_TOKEN_RE = re.compile(
    # Alternation is left-to-right; longer alternatives MUST precede a
    # shorter alternative that is a prefix of them. "months?" precedes
    # "minutes?" so "1month" isn't consumed as minutes-`m` plus garbage.
    r"\s*(\d+)\s*"
    r"(seconds?|sec|s"
    r"|months?"
    r"|minutes?|min|m"
    r"|hours?|hr|h"
    r"|days?|d"
    r"|weeks?|w"
    r"|years?|y"
    r"|M)",
)

_INTERVAL_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
    "M": 2592000,
    "month": 2592000,
    "months": 2592000,
    "y": 31536000,
    "year": 31536000,
    "years": 31536000,
}


@dataclass(frozen=True)
class Interval:
    """A systemd time-span (e.g. "30min", "1h30m"), kept as written.

    ``from_str`` validates the spec and computes ``total_seconds`` (for
    launchd's StartInterval); ``str(interval)`` returns the original
    spec, which systemd consumes for OnUnitActiveSec.
    """

    source: str
    total_seconds: int

    @classmethod
    def from_str(cls, spec: str) -> Interval:
        text = spec.strip()
        return cls(text, _interval_seconds(text))

    def __str__(self) -> str:
        return self.source


def _interval_seconds(text: str) -> int:
    """Parse a systemd time-span into a positive number of seconds.

    Raises ValueError on empty, unparseable, or non-positive input.
    """
    if not text:
        raise ValueError("empty interval")
    total = 0
    pos = 0
    matched = False
    while pos < len(text):
        m = _INTERVAL_TOKEN_RE.match(text, pos)
        if not m:
            if text[pos].isspace():
                pos += 1
                continue
            raise ValueError(
                f"unparseable interval at offset {pos}: {text[pos:]!r}"
            )
        unit = m.group(2)
        # 'M' (capital) is months; lowercase 'm' is minutes.
        unit_key = unit if unit == "M" else unit.lower()
        total += int(m.group(1)) * _INTERVAL_UNIT_SECONDS[unit_key]
        pos = m.end()
        matched = True
    if not matched:
        raise ValueError(f"no time components in interval: {text!r}")
    if total <= 0:
        raise ValueError(f"interval must be positive: {text!r}")
    return total


# Keyword OnCalendar specs expand directly to a launchd
# StartCalendarInterval. (systemd consumes the keyword spec itself.)
_ONCALENDAR_KEYWORD_CALENDAR: dict[str, dict[str, int]] = {
    "hourly": {"Minute": 0},
    "daily": {"Minute": 0, "Hour": 0},
    "weekly": {"Minute": 0, "Hour": 0, "Weekday": 1},
    "monthly": {"Minute": 0, "Hour": 0, "Day": 1},
    "yearly": {"Minute": 0, "Hour": 0, "Day": 1, "Month": 1},
    "annually": {"Minute": 0, "Hour": 0, "Day": 1, "Month": 1},
}

# Day-of-week name -> launchd Weekday integer (Sunday = 0).
_DOW_TO_INT: dict[str, int] = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}

_SCHEDULE_TIME_RE = re.compile(r"^(\*|\d{1,2}):(\d{2})(?::(\d{2}))?$")
_SCHEDULE_DATE_RE = re.compile(r"^(\*|\d{4})-(\*|\d{1,2})-(\*|\d{1,2})$")


@dataclass(frozen=True)
class Schedule:
    """An OnCalendar schedule, kept as written but validated eagerly.

    ``from_str`` accepts the keywords (hourly..yearly), a bare
    ``[H|*]:MM[:SS]``, and ``[DOW ][YYYY|*]-[M|*]-[D|*] [H|*]:MM[:SS]``,
    and rejects step / range / list forms (``/`` ``..`` ``,``) on every
    platform because launchd cannot express them. ``str(schedule)``
    returns the spec as written (what systemd consumes for OnCalendar
    and what status shows); ``to_plist_calendar`` returns the launchd
    StartCalendarInterval, parsed and validated once at construction.
    launchd has no Year or Second field, so those components (valid on
    systemd) are dropped for launchd.
    """

    source: str
    # The launchd projection, parsed once in __post_init__. Excluded
    # from eq/hash/repr (it is a pure function of `source`).
    _calendar: dict[str, int] = field(
        init=False, compare=False, repr=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_calendar", _oncalendar_to_calendar(self.source)
        )

    @classmethod
    def from_str(cls, spec: str) -> Schedule:
        text = spec.strip()
        if not text:
            raise ValueError("empty schedule")
        if "\n" in text or "\r" in text:
            raise ValueError(f"schedule must be one line: {spec!r}")
        return cls(text)

    def __str__(self) -> str:
        return self.source

    def to_plist_calendar(self) -> dict[str, int]:
        return dict(self._calendar)


def _check_range(name: str, value: int, low: int, high: int) -> None:
    if not low <= value <= high:
        raise ValueError(f"{name} {value} out of range {low}-{high}")


def _oncalendar_to_calendar(text: str) -> dict[str, int]:
    """Parse a supported OnCalendar spec into a launchd
    StartCalendarInterval, validating ranges and rejecting the
    step / range / list forms launchd cannot express.

    Seconds are parsed and range-checked, and the year is parsed, but
    both are omitted from the result -- launchd has no field for them.
    """
    if text in _ONCALENDAR_KEYWORD_CALENDAR:
        return dict(_ONCALENDAR_KEYWORD_CALENDAR[text])
    if "/" in text or ".." in text or "," in text:
        raise ValueError(
            f"OnCalendar pattern {text!r} uses step / range / list "
            "features crony does not render to launchd; use a single "
            "concrete time or weekday/day entry"
        )
    parts = text.split()
    cal: dict[str, int] = {}
    if parts and parts[0].lower() in _DOW_TO_INT:
        cal["Weekday"] = _DOW_TO_INT[parts[0].lower()]
        parts = parts[1:]
    if len(parts) == 2:
        date_m = _SCHEDULE_DATE_RE.fullmatch(parts[0])
        if not date_m:
            raise _unsupported_schedule(text)
        if date_m.group(2) != "*":
            month = int(date_m.group(2))
            _check_range("month", month, 1, 12)
            cal["Month"] = month
        if date_m.group(3) != "*":
            day = int(date_m.group(3))
            _check_range("day", day, 1, 31)
            cal["Day"] = day
        parts = parts[1:]
    if len(parts) != 1:
        raise _unsupported_schedule(text)
    time_m = _SCHEDULE_TIME_RE.fullmatch(parts[0])
    if not time_m:
        raise _unsupported_schedule(text)
    minute = int(time_m.group(2))
    _check_range("minute", minute, 0, 59)
    cal["Minute"] = minute
    if time_m.group(1) != "*":
        hour = int(time_m.group(1))
        _check_range("hour", hour, 0, 23)
        cal["Hour"] = hour
    if time_m.group(3) is not None:
        _check_range("second", int(time_m.group(3)), 0, 59)
    return cal


def _unsupported_schedule(text: str) -> ValueError:
    return ValueError(
        f"crony does not render OnCalendar pattern {text!r}; try a "
        "simpler form like '03:15', '*-*-* 03:15', or 'Mon *-*-* 09:00'"
    )


# How a unit fires: a calendar Schedule or a repeat Interval. The two
# are mutually exclusive, so a single value (or None, for an on-demand
# unit) models it without an "at most one set" invariant. isinstance
# discriminates the variant.
Timing = Schedule | Interval


@dataclass(frozen=True)
class JitterSpec:
    """The start-time jitter a jittered interval unit carries.

    A pure carrier the model fills in and each backend renders per its own
    mechanism -- it holds no policy (the eligibility decision and the
    offset computation live in the model) and interprets neither field.

    offset      The fixed per-job first-fire offset as an Interval, in
                `[1, N)` for an interval of `N`. A backend delays the first
                fire by it (systemd `OnActiveSec`, launchd a companion that
                fires at it and triggers the service).
    cmd         The argv a phasing companion runs -- an opaque crony
                invocation the model bakes, exactly like `UnitSpec.cmd`.
                Consumed by backends that implement jitter via a separate
                unit (e.g. launchd).
    """

    offset: Interval
    cmd: tuple[str, ...]


@dataclass(frozen=True)
class UnitSpec:
    """One scheduled unit, described without crony's job/group model.

    name        The unit's full name; basis for its platform label.
    cmd         The argv the unit runs.
    timing      A Schedule, an Interval, or None for an on-demand unit
                (a transit group, or a job fired only by explicit
                trigger).
    priority    The unit's priority class. NORMAL when no special
                scheduling is requested (groups always render NORMAL);
                only HIGH / LOW emit platform directives.
    jitter      The start-time jitter the model computed for this unit, or
                None when it is not jittered (a calendar / short-interval /
                grouped / disabled entry). An eligibility decision the
                model owns; the backends only render it.
    """

    name: EntityName
    cmd: tuple[str, ...]
    timing: Timing | None
    priority: PriorityClass
    jitter: JitterSpec | None = None
