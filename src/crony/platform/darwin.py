# This is AI generated code

"""macOS (`darwin`) host-platform backend.

Implements the `HostPlatform` services on darwin: a kqueue-based
pid-exit wait and a Keychain-backed secret lookup.
"""

from __future__ import annotations

import select
import subprocess

from crony.platform.host import HostPlatform, PidWait


class DarwinHost(HostPlatform):
    """darwin host services."""

    def wait_for_pid_exit(self, pid: int, timeout: float | None) -> PidWait:
        # mypy's `select` stubs are platform-specific: kqueue, kevent,
        # and the KQ_* constants are absent from the Linux stubs. This
        # backend is only instantiated when current_platform() is
        # "darwin", so the runtime contract holds; silence the static
        # checker on each darwin-only attribute.
        kq = select.kqueue()  # type: ignore[attr-defined, unused-ignore]
        try:
            ev = select.kevent(  # type: ignore[attr-defined, unused-ignore]
                pid,
                filter=select.KQ_FILTER_PROC,  # type: ignore[attr-defined, unused-ignore]
                flags=(
                    select.KQ_EV_ADD  # type: ignore[attr-defined, unused-ignore]
                    | select.KQ_EV_ENABLE  # type: ignore[attr-defined, unused-ignore]
                ),
                fflags=select.KQ_NOTE_EXIT,  # type: ignore[attr-defined, unused-ignore]
            )
            try:
                kq.control([ev], 0, 0)
            except ProcessLookupError:
                return PidWait.EXITED
            events = kq.control([], 1, timeout)
            return PidWait.EXITED if events else PidWait.TIMED_OUT
        finally:
            kq.close()

    def keychain_secret(self, service: str, account: str | None) -> str | None:
        argv = ["security", "find-generic-password", "-s", service]
        if account is not None:
            argv.extend(["-a", account])
        argv.append("-w")
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if r.returncode == 0:
            return r.stdout.rstrip("\n")
        return None
