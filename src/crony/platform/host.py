# This is AI generated code

"""The host-platform abstraction.

Beyond rendering and managing scheduler units (crony.platform.scheduler),
crony reaches for host-OS services that diverge by platform.
`HostPlatform` is the seam those services live behind: it is selected by
the same platform string via `get_host` (in the package `__init__`), and
`crony.platform.darwin` and `crony.platform.linux` implement it. The
services it exposes are the runner's wait on a spawned job's pid and the
OS keychain secret lookup.

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
