# This is AI generated code

"""Linux host-platform backend.

Implements the `HostPlatform` services on Linux: a pidfd-based pid-exit
wait. Linux has no keychain integration, so `keychain_secret` reports
None and the credential resolver falls through to its env / file path.
"""

from __future__ import annotations

import os
import select

from crony.platform.host import HostPlatform, PidWait


class LinuxHost(HostPlatform):
    """Linux host services."""

    def wait_for_pid_exit(self, pid: int, timeout: float | None) -> PidWait:
        try:
            fd = os.pidfd_open(pid)  # type: ignore[attr-defined, unused-ignore]
        except ProcessLookupError:
            return PidWait.EXITED
        try:
            poll = select.poll()
            poll.register(fd, select.POLLIN)
            ms = -1 if timeout is None else int(timeout * 1000)
            events = poll.poll(ms)
            return PidWait.EXITED if events else PidWait.TIMED_OUT
        finally:
            os.close(fd)

    def keychain_secret(
        self, _service: str, _account: str | None
    ) -> str | None:
        # No OS keychain integration on Linux; the resolver's env / file
        # fallback owns the secret here.
        return None
