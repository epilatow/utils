"""A stand-in for `borg mount -f`, for the borgadm tests.

`tests/test_borgadm.py` runs this file with the test interpreter in place of
`borg`, so borgadm's foreground-mount handling is exercised against a real
process, and imports it for the shared control-dir layout and record type.

It is driven through files in the control dir next to the mountpoint (the
last argument; see `control_dir`): `mode` picks the behavior, the stand-in
touches `mounted` once it "serves", and the test touches `die`, `kill` or
`unmount` to end it. Every invocation appends an `Attempt` to
`attempts.jsonl`.

Modes:

- `fail`: write an error and exit 2 without mounting.
- `hang`: neither mount nor exit (for a minute).
- `lock`: exit on borg's LockTimeout when given the short lock-wait recorded
  in `short`; serve otherwise.
- `stubborn`: serve, ignoring SIGTERM.
- anything else: serve.
"""

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import TypedDict


class Attempt(TypedDict):
    """One invocation, as the stand-in records it."""

    pid: int
    argv: list[str]
    cache: str | None
    security: str | None


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
    )
    with open(ctl / "attempts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(attempt) + "\n")


def _serve(ctl: Path) -> int:
    _say("starting")
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
    ctl = control_dir(Path(args[-1]))
    mode = (ctl / "mode").read_text(encoding="utf-8")
    _record(ctl, args)
    if mode == "fail":
        _say("repository error: boom")
        return 2
    if mode == "hang":
        time.sleep(60)
        return 0
    if mode == "lock":
        wait = args[args.index("--lock-wait") + 1]
        if wait == (ctl / "short").read_text(encoding="utf-8"):
            _say("Failed to create/acquire the lock /r/lock (timeout).")
            return 2
    if mode == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    return _serve(ctl)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
