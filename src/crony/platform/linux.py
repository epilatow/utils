# This is AI generated code

"""Linux host-platform backend.

Implements the `HostPlatform` services on Linux: a pidfd-based pid-exit
wait and a `systemd-inhibit` sleep-inhibitor wrap. Linux has no keychain
integration, so `keychain_secret` reports None and the credential
resolver falls through to its env / file path. Desktop interaction is
unsupported (`supports_interactive` is False): the idle / lock probes
and dialogs raise.
"""

import os
import select
import shutil

from crony.platform.fda import FDAWrapper
from crony.platform.host import HostPlatform, PidWait

_NO_INTERACTIVE = "interactive jobs / dialogs are not supported on Linux"


class LinuxHost(HostPlatform):
    """Linux host services."""

    @property
    def supports_interactive(self) -> bool:
        return False

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

    def keep_awake_argv(
        self, argv: list[str], label: str
    ) -> tuple[list[str], str | None]:
        tool = shutil.which("systemd-inhibit")
        if tool is None:
            return (
                argv,
                "keep_awake: systemd-inhibit not found; running unwrapped",
            )
        return (
            [
                tool,
                "--what=sleep:idle",
                "--who=crony",
                f"--why=job {label}",
                "--mode=block",
                "--",
                *argv,
            ],
            None,
        )

    def full_disk_access_argv(self, argv: list[str]) -> list[str]:
        # Full Disk Access is a macOS TCC concept; on Linux a job reads
        # what its user can read, so the command runs unwrapped.
        return argv

    def prepare_full_disk_access(self) -> str | None:
        return None

    def full_disk_access_state(self) -> FDAWrapper:
        return FDAWrapper.OK

    def hid_idle_seconds(self) -> float:
        raise NotImplementedError(_NO_INTERACTIVE)

    def screen_locked(self) -> bool:
        raise NotImplementedError(_NO_INTERACTIVE)

    def show_dialog(self, _title: str, _body: str, _buttons: list[str]) -> str:
        raise NotImplementedError(_NO_INTERACTIVE)

    def show_failure_dialog(self, _title: str, _body: str) -> None:
        raise NotImplementedError(_NO_INTERACTIVE)
