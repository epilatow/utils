# This is AI generated code

"""Linux host-platform backend.

Implements the `HostPlatform` services on Linux: a /proc-polling
pid-exit wait and a `systemd-inhibit` sleep-inhibitor wrap. Linux has
no keychain
integration, so `keychain_secret` reports None and the credential
resolver falls through to its env / file path. Desktop interaction is
unsupported (`supports_interactive` is False): the idle / lock probes
and dialogs raise.
"""

import shutil
import time

from crony.platform.fda import FDAWrapper
from crony.platform.host import HostPlatform, PidWait

_NO_INTERACTIVE = "interactive jobs / dialogs are not supported on Linux"

# Where systemd / dbus record the persistent machine identity. Either is
# a 32-char hex string stable for the life of the install.
_MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")

# Poll cadence for the /proc-based pid-exit wait (seconds).
_PROC_POLL_INTERVAL = 0.05


def _proc_pid_gone(pid: int) -> bool:
    """True once `pid` has exited, read from /proc.

    Matches the pidfd path's notion of "exited": a missing /proc entry
    (reaped or never existed) and a zombie (`Z`, exited but not yet
    reaped) both count as gone. /proc/<pid>/stat is `pid (comm) state
    ...`; comm may hold spaces and parens, so the state char is the one
    after the last `)`.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return True
    rparen = data.rfind(b")")
    if rparen == -1 or rparen + 2 >= len(data):
        return True
    return data[rparen + 2 : rparen + 3] == b"Z"


class LinuxHost(HostPlatform):
    """Linux host services."""

    def machine_id(self) -> str:
        for path in _MACHINE_ID_PATHS:
            try:
                with open(path, encoding="utf-8") as f:
                    value = f.read().strip()
            except OSError:
                continue
            if value:
                return value
        return self._hostname_fallback()

    @property
    def supports_interactive(self) -> bool:
        return False

    def wait_for_pid_exit(self, pid: int, timeout: float | None) -> PidWait:
        # Poll /proc for the exit. The edge-triggered alternative,
        # os.pidfd_open, is absent from the python-build-standalone
        # interpreters uv ships (every version), so it is not usable here.
        # The cost is a pid-reuse window: if `pid` exits and the number is
        # recycled before the next poll, the replacement reads as alive
        # and the wait runs to `timeout`. crony's job pids plus the
        # timeout bound make that immaterial.
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if _proc_pid_gone(pid):
                return PidWait.EXITED
            if deadline is None:
                time.sleep(_PROC_POLL_INTERVAL)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return PidWait.TIMED_OUT
            time.sleep(min(_PROC_POLL_INTERVAL, remaining))

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
