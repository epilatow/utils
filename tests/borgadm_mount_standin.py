"""A stand-in for `borg mount -f`, for the borgadm tests.

`tests/test_borgadm.py` runs this file with the test interpreter in place of
`borg`, so borgadm's command handling -- its foreground mounts above all --
is exercised against a real process, and imports it for the shared
control-dir layout and record type.

It is driven through files in the control dir next to the mountpoint (the
last argument; see `control_dir`): `mode` picks the behavior, the stand-in
"serves" by writing `SERVED_FILE` into the mountpoint and touching `mounted`,
and the test touches `die`, `kill` or `unmount` to end it. Every invocation
appends an `Attempt` to `attempts.jsonl`, and -- standing in for the ssh borg
would start -- writes `SSH_LOG_LINES` to the file named by `-E` in BORG_RSH,
if there is one.

Modes:

- `bare`: serve, but write no file into the mountpoint.
- `fail`: write an error and exit 2 without mounting.
- `hang`: neither mount nor exit (for a minute).
- `lock`: exit on borg's LockTimeout when given the short lock-wait recorded
  in `short`; serve otherwise.
- `orphan`: serve, ignoring SIGTERM, after starting a child of its own that
  holds its stdout and stderr open for a minute -- the way a command a
  SIGKILLed sudo leaves behind holds sudo's.
- `quit`: exit 0 without mounting.
- `stubborn`: serve, ignoring SIGTERM.
- anything else: serve.
"""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TypedDict

SSH_LOG_LINES = [f"ssh {i:02}" for i in range(1, 13)]
SERVED_FILE = "served.txt"


class Attempt(TypedDict):
    """One invocation, as the stand-in records it."""

    pid: int
    argv: list[str]
    cache: str | None
    security: str | None
    rsh: str | None


def control_dir(mount_dir: Path) -> Path:
    return mount_dir.parent / "ctl"


def _say(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _record(ctl: Path, args: list[str]) -> None:
    attempt = Attempt(
        pid=os.getpid(),
        argv=args,
        cache=os.environ.get("BORG_CACHE_DIR"),
        security=os.environ.get("BORG_SECURITY_DIR"),
        rsh=os.environ.get("BORG_RSH"),
    )
    with open(ctl / "attempts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(attempt) + "\n")


def _write_ssh_log() -> None:
    rsh = shlex.split(os.environ.get("BORG_RSH", ""))
    if "-E" in rsh:
        log = Path(rsh[rsh.index("-E") + 1])
        with log.open("a", encoding="utf-8") as f:
            f.writelines(f"{line}\n" for line in SSH_LOG_LINES)


def _serve(ctl: Path, mount_dir: Path, *, with_file: bool = True) -> int:
    _say("starting")
    if with_file:
        (mount_dir / SERVED_FILE).write_text("archive data\n", encoding="utf-8")
    (ctl / "mounted").touch()
    while not (ctl / "unmount").exists():
        if (ctl / "die").exists():
            _say("Traceback: boom")
            return 2
        if (ctl / "kill").exists():
            os.kill(os.getpid(), signal.SIGKILL)
        time.sleep(0.01)
    (ctl / "mounted").unlink()
    return 0


def main(args: list[str]) -> int:
    mount_dir = Path(args[-1])
    ctl = control_dir(mount_dir)
    mode = (ctl / "mode").read_text(encoding="utf-8")
    _record(ctl, args)
    if args[0] == "linger":
        time.sleep(60)
        return 0
    _write_ssh_log()
    if mode == "orphan":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        subprocess.Popen([sys.executable, __file__, "linger", args[-1]])
        return _serve(ctl, mount_dir)
    if mode == "bare":
        return _serve(ctl, mount_dir, with_file=False)
    if mode == "fail":
        _say("repository error: boom")
        return 2
    if mode == "hang":
        time.sleep(60)
        return 0
    if mode == "quit":
        return 0
    if mode == "lock":
        wait = args[args.index("--lock-wait") + 1]
        if wait == (ctl / "short").read_text(encoding="utf-8"):
            _say("Failed to create/acquire the lock /r/lock (timeout).")
            return 2
    if mode == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    return _serve(ctl, mount_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
