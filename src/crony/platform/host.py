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

from __future__ import annotations

import abc
import enum


class PidWait(enum.Enum):
    """Outcome of `HostPlatform.wait_for_pid_exit`.

    EXITED: the pid is gone. TIMED_OUT: the wait's deadline elapsed
    while the pid was still alive.
    """

    EXITED = "exited"
    TIMED_OUT = "timed_out"


class HostPlatform(abc.ABC):
    """Host-OS services crony needs that diverge by platform."""

    @property
    @abc.abstractmethod
    def supports_interactive(self) -> bool:
        """True when the host has a desktop session crony can pop modal
        dialogs on -- gating both the interactive-job approval prompt
        and the dialog-popup notify channel. Where it is False, the idle
        / lock probes and the dialog methods are unavailable and raise."""

    @abc.abstractmethod
    def wait_for_pid_exit(self, pid: int, timeout: float | None) -> PidWait:
        """Block until `pid` exits, via a kernel-level exit notification
        rather than polling.

        Returns `PidWait.EXITED` once the pid is gone -- whether it
        exited, never existed, or raced into reuse -- and
        `PidWait.TIMED_OUT` if `timeout` seconds elapse first.
        `timeout=None` waits indefinitely.
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
