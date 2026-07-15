# This is AI generated code

"""macOS (`darwin`) host-platform backend.

Implements the `HostPlatform` services on darwin: a kqueue-based
pid-exit wait, a Keychain-backed secret lookup, a `caffeinate`
sleep-inhibitor wrap, and the desktop-interaction primitives (HID idle
/ screen-lock probes via `ioreg`, approval and failure dialogs via
`osascript`).
"""

import select
import shutil
import subprocess

import crony.errors
import crony.platform.fda
from crony.platform.fda import FDAWrapper
from crony.platform.host import HostPlatform, PidWait


def _applescript_escape(s: str) -> str:
    """Escape `s` for inclusion in an AppleScript "..." literal.

    Backslash and double-quote are the only specials inside such a
    literal. This is NOT shell escaping -- the script is passed as a
    single argv entry to `osascript -e`, so no shell parser sees it;
    `shlex.quote` would emit POSIX single-quoted bytes that AppleScript
    would parse as something else entirely.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


class DarwinHost(HostPlatform):
    """darwin host services."""

    def machine_id(self) -> str:
        # IOPlatformUUID is the per-machine hardware UUID, stable for the
        # life of the machine. `ioreg -rd1 -c IOPlatformExpertDevice`
        # prints one record whose line reads `"IOPlatformUUID" = "<uuid>"`.
        try:
            proc = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except FileNotFoundError, subprocess.TimeoutExpired:
            return self._hostname_fallback()
        for line in proc.stdout.splitlines():
            if '"IOPlatformUUID"' not in line:
                continue
            _, _, rhs = line.partition("=")
            value = rhs.strip().strip('"')
            if value:
                return value
        return self._hostname_fallback()

    @property
    def supports_interactive(self) -> bool:
        return True

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
        except FileNotFoundError, subprocess.TimeoutExpired:
            return None
        if r.returncode == 0:
            return r.stdout.rstrip("\n")
        return None

    def keep_awake_available(self) -> bool:
        # caffeinate needs no privilege, so keep-awake is available
        # whenever the tool is present (it ships with macOS).
        return shutil.which("caffeinate") is not None

    def keep_awake_argv(
        self, argv: list[str], _label: str
    ) -> tuple[list[str], str | None]:
        # caffeinate -i -s prevents idle sleep always and system sleep
        # on AC; it has no job-label field, so _label is unused.
        tool = shutil.which("caffeinate")
        if tool is None:
            return argv, "keep_awake: caffeinate not found; running unwrapped"
        return [tool, "-i", "-s", *argv], None

    def full_disk_access_argv(self, argv: list[str]) -> list[str]:
        # A missing wrapper or a denied grant is a run precondition --
        # the job can't read what it needs -- so raise; the runner
        # records it as canceled. A stale-but-present wrapper still runs
        # (the old binary keeps its grant); its staleness shows in
        # status. (`wrapper_state` probes the grant only when current.)
        state = crony.platform.fda.wrapper_state()
        if state is FDAWrapper.MISSING:
            raise crony.errors.PreconditionError(
                "Crony.app wrapper is not built; run `crony apply` to "
                "build it before a full-disk-access job runs."
            )
        if state is FDAWrapper.MISSING_FDA_GRANT:
            raise crony.errors.PreconditionError(
                crony.platform.fda.grant_instructions()
            )
        return [str(crony.platform.fda.wrapper_binary()), *argv]

    def prepare_full_disk_access(self) -> str | None:
        try:
            crony.platform.fda.build_wrapper()
        except crony.errors.PreconditionError as exc:
            return str(exc)
        if crony.platform.fda.wrapper_state() is FDAWrapper.MISSING_FDA_GRANT:
            return crony.platform.fda.grant_instructions()
        return None

    def full_disk_access_state(self) -> FDAWrapper:
        return crony.platform.fda.wrapper_state()

    def hid_idle_seconds(self) -> float:
        # Reads HIDIdleTime (nanoseconds) from IOHIDSystem. Returns 0.0
        # when ioreg is unavailable or unparseable -- treating an unknown
        # idle state as "user is active now" is the safer default for the
        # wait loop (err toward prompting sooner, not never).
        try:
            proc = subprocess.run(
                ["ioreg", "-c", "IOHIDSystem"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except FileNotFoundError, subprocess.TimeoutExpired:
            return 0.0
        for line in proc.stdout.splitlines():
            if '"HIDIdleTime"' not in line:
                continue
            _, _, rhs = line.partition("=")
            try:
                return int(rhs.strip()) / 1_000_000_000
            except ValueError:
                return 0.0
        return 0.0

    def screen_locked(self) -> bool:
        # Reads CGSSessionScreenIsLocked from IOConsoleUsers. Returns
        # False on any failure -- assuming unlocked on infrastructure
        # error matches "user is present" and delegates the final
        # present-check to HIDIdleTime, the primary signal.
        try:
            proc = subprocess.run(
                ["ioreg", "-n", "Root", "-d", "1"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except FileNotFoundError, subprocess.TimeoutExpired:
            return False
        return '"CGSSessionScreenIsLocked"=Yes' in proc.stdout

    def show_dialog(self, title: str, body: str, buttons: list[str]) -> str:
        btn_list = ", ".join(f'"{_applescript_escape(b)}"' for b in buttons)
        script = (
            f'display dialog "{_applescript_escape(body)}" '
            f'with title "{_applescript_escape(title)}" '
            f"buttons {{{btn_list}}} "
            f'default button "{_applescript_escape(buttons[-1])}" '
            f'cancel button "{_applescript_escape(buttons[0])}"'
        )
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return ""
        # Clicking the cancel button exits osascript non-zero (the
        # AppleScript "User canceled" error); a dismissed or unshowable
        # dialog lands here too. All map to "" (no choice).
        if proc.returncode != 0:
            return ""
        # osascript prints `button returned:<label>` for a click. Match
        # the label exactly (not a substring of stdout) so a button
        # whose name is a substring of another can't shadow it.
        marker = "button returned:"
        idx = proc.stdout.find(marker)
        if idx == -1:
            return ""
        label = proc.stdout[idx + len(marker) :].splitlines()[0].strip()
        return label if label in buttons else ""

    def show_failure_dialog(self, title: str, body: str) -> None:
        script = (
            f'display dialog "{_applescript_escape(body)}" '
            f'with title "{_applescript_escape(title)}" '
            f'buttons {{"OK"}} default button "OK" with icon stop'
        )
        # start_new_session detaches the modal so it survives the runner
        # exiting; Popen returns as soon as osascript is launched.
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
