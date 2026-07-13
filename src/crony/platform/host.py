# This is AI generated code

"""The host-platform abstraction.

Beyond rendering and managing scheduler units (crony.platform.scheduler),
crony reaches for host-OS services that diverge by platform.
`HostPlatform` is the seam those services live behind: it is selected by
the same platform string via `get_host` (in the package `__init__`), and
`crony.platform.darwin` and `crony.platform.linux` implement it. The
services it exposes are the runner's wait on a spawned job's pid, the OS
keychain secret lookup, the sleep-inhibitor wrap for keep-awake jobs,
and the desktop-interaction primitives interactive jobs use (idle /
screen-lock probes and the approval / failure dialogs).

This is the host-services analog of `scheduler.Scheduler`: where the
scheduler renders and manages units, the host platform brokers the
non-unit OS services the runner and config tooling reach for. Each
backend documents how it realizes a service; this interface states only
the contract.
"""

import abc
import enum
import socket

from crony.platform.fda import FDAWrapper


class PidWait(enum.Enum):
    """Outcome of `HostPlatform.wait_for_pid_exit`.

    EXITED: the pid is gone. TIMED_OUT: the wait's deadline elapsed
    while the pid was still alive.
    """

    EXITED = "exited"
    TIMED_OUT = "timed_out"


class HostPlatform(abc.ABC):
    """Host-OS services crony needs that diverge by platform."""

    @staticmethod
    def _hostname_fallback() -> str:
        """The short hostname, the `machine_id` fallback a backend uses
        when it cannot read the OS machine identity. Guaranteed
        non-empty. Protected: the backends' `machine_id` implementations
        share it, but it is not part of the public host interface."""
        return socket.gethostname().split(".")[0] or "localhost"

    @abc.abstractmethod
    def machine_id(self) -> str:
        """A stable, per-host-unique identifier for this machine. Opaque
        -- only its stability across time and its distinctness across
        hosts are guaranteed -- and always non-empty."""

    @property
    @abc.abstractmethod
    def supports_interactive(self) -> bool:
        """True when the host has a desktop session crony can pop modal
        dialogs on -- gating both the interactive-job approval prompt
        and the dialog-popup notify channel. Where it is False, the idle
        / lock probes and the dialog methods are unavailable and raise."""

    @abc.abstractmethod
    def wait_for_pid_exit(self, pid: int, timeout: float | None) -> PidWait:
        """Block until `pid` exits.

        Returns `PidWait.EXITED` once the pid is gone (it exited or never
        existed) and `PidWait.TIMED_OUT` if `timeout` seconds elapse
        first. `timeout=None` waits indefinitely. Backends choose their
        own wait mechanism; see each for any pid-reuse caveat.
        """

    @abc.abstractmethod
    def keychain_secret(self, service: str, account: str | None) -> str | None:
        """Return a secret from the OS keychain by (service, account),
        or None when the host has no keychain, the lookup fails, or no
        item matches. `account` disambiguates when several items share a
        service name. The credential resolver tries this before its env
        / file fallback, so None simply means "fall through"."""

    @abc.abstractmethod
    def keep_awake_argv(
        self, argv: list[str], label: str
    ) -> tuple[list[str], str | None]:
        """Wrap `argv` in the host's sleep-inhibitor so the machine
        stays awake while the command runs, returning (wrapped_argv,
        note). The wrapper propagates the command's exit code and tears
        down when killed. When the inhibitor binary is unavailable,
        return `argv` unwrapped with a `note` explaining why -- a
        missing inhibitor must never fail the job. `label` names the job
        for the inhibitor's bookkeeping. (Lid-close on battery still
        sleeps the machine; no userspace mechanism prevents that.)"""

    @abc.abstractmethod
    def full_disk_access_argv(self, argv: list[str]) -> list[str]:
        """Wrap `argv` so the command runs with macOS Full Disk Access,
        or return it unchanged where FDA does not apply.

        On darwin the command is routed through Crony.app, the wrapper
        that holds the grant. The grant is a run precondition: this
        raises `PreconditionError` when the wrapper is missing or the
        grant is not in effect -- the standard run-precondition signal,
        which a blocked fire records as `canceled`. A stale-but-present
        wrapper still runs. Off darwin FDA is a no-op and `argv` is
        returned unchanged."""

    @abc.abstractmethod
    def prepare_full_disk_access(self) -> str | None:
        """Build the FDA wrapper if needed and check the grant, for
        `crony apply`. Returns a warning to log (the grant is missing,
        or the toolchain can't build the wrapper) or None when FDA is
        ready / not applicable. Off darwin this is a no-op (None)."""

    @abc.abstractmethod
    def full_disk_access_state(self) -> FDAWrapper:
        """The FDA wrapper's state for `crony status` (see `FDAWrapper`).
        Off darwin there is no wrapper, so always `FDAWrapper.OK`."""

    @abc.abstractmethod
    def hid_idle_seconds(self) -> float:
        """Seconds since the last user input event -- the interactive
        wait's presence signal. Only meaningful where
        `supports_interactive` (raises otherwise)."""

    @abc.abstractmethod
    def screen_locked(self) -> bool:
        """True when the login session is screen-locked. Only meaningful
        where `supports_interactive` (raises otherwise)."""

    @abc.abstractmethod
    def show_dialog(self, title: str, body: str, buttons: list[str]) -> str:
        """Pop a modal dialog and block for the user's choice. `buttons`
        is ordered first..last; the last is the default and the first is
        the cancel button. Returns the clicked button's label, or ""
        when the dialog is canceled, dismissed, or cannot be shown.
        Only meaningful where `supports_interactive` (raises
        otherwise)."""

    @abc.abstractmethod
    def show_failure_dialog(self, title: str, body: str) -> None:
        """Pop a detached one-button failure dialog and return at once,
        without waiting for dismissal, so notifying never stalls the
        runner. Only meaningful where `supports_interactive` (raises
        otherwise)."""
