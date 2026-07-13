"""Full Disk Access wrapper management for crony on macOS.

A job carrying the `full-disk-access` flag has its command run on darwin
through `Crony.app` -- a small code-signed Mach-O that holds the FDA
grant (a `uv run` Python script cannot) and disclaims TCC responsibility
so the grant applies under any launcher. This module builds that wrapper
from its checked-in C source and locates the compiled binary; the C
source documents the TCC mechanism in full
(Applications/Crony.app/Contents/MacOS/Crony.c).

The build is darwin-only (mach-o/dyld.h, codesign); callers gate on the
platform before invoking `build_wrapper`.
"""

import enum
import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

import crony.errors

logger = logging.getLogger(__name__)


class FDAWrapper(enum.Enum):
    """The state of the Crony.app Full Disk Access wrapper, for a job
    that needs it.

    OK                The wrapper is built, current, and holds the grant.
    MISSING           Not compiled (no binary) -- `crony apply` builds it.
    STALE             Compiled but the source has changed; the old binary
                      still runs and keeps its grant, but `crony apply`
                      should rebuild it.
    MISSING_FDA_GRANT Built and current, but Full Disk Access is not
                      granted -- the grant is added in System Settings.

    `MISSING` and `MISSING_FDA_GRANT` both leave a full-disk-access job
    unable to read what it needs (they are the `is_missing` group); a
    `STALE` wrapper still runs.
    """

    OK = "ok"
    MISSING = "missing"
    STALE = "stale"
    MISSING_FDA_GRANT = "missing-grant"

    @property
    def is_missing(self) -> bool:
        """Whether the job cannot run as configured: the wrapper is
        absent, or present but without the grant."""
        return self in (FDAWrapper.MISSING, FDAWrapper.MISSING_FDA_GRANT)


# Exit code the wrapper returns when FDA is not granted; mirrors
# FDA_EXIT_CODE in Crony.c. 77 is the conventional skip / not-configured
# code, outside crony's own run-outcome range.
_FDA_EXIT_CODE = 77

# First argument that switches the wrapper into probe mode (test FDA,
# run nothing); mirrors CHECK_FDA_FLAG in Crony.c.
_CHECK_FDA_FLAG = "--check-fda"


def _repo_root() -> Path:
    """Repo root, derived from this module's location at
    <repo>/src/crony/platform/fda.py."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _app_path() -> Path:
    return _repo_root() / "Applications" / "Crony.app"


def _source_path() -> Path:
    """The checked-in C source for the wrapper."""
    return _app_path() / "Contents" / "MacOS" / "Crony.c"


def wrapper_binary() -> Path:
    """The compiled wrapper binary -- the program crony's runner prepends
    to an FDA job's command, and the program the FDA probe runs."""
    return _app_path() / "Contents" / "MacOS" / "Crony"


def _hash_path() -> Path:
    return wrapper_binary().parent / ".Crony.source-sha256"


def _source_sha256() -> str:
    return hashlib.sha256(_source_path().read_bytes()).hexdigest()


def _needs_rebuild() -> tuple[bool, str]:
    """Whether the wrapper binary must be (re)compiled, and why.

    The reason is empty when the binary is current. Raises
    PreconditionError if the C source is missing (an incomplete
    checkout).
    """
    src = _source_path()
    if not src.exists():
        raise crony.errors.PreconditionError(
            f"Crony.app wrapper source missing: {src}"
        )
    if not wrapper_binary().exists():
        return True, "binary does not exist"
    hash_file = _hash_path()
    if not hash_file.exists():
        return True, "source hash file does not exist"
    if _source_sha256() != hash_file.read_text().strip():
        return True, "source hash mismatch (source changed)"
    return False, ""


def wrapper_state() -> FDAWrapper:
    """The wrapper's full state, including the grant.

    `MISSING` when there is no binary, `STALE` when the binary is out of
    date (these are decided by cheap file checks). Only when the binary
    is current is the grant probed -- `--check-fda`, one short-lived
    subprocess -- yielding `OK` (granted) or `MISSING_FDA_GRANT`.
    """
    if not _source_path().exists() or not wrapper_binary().exists():
        return FDAWrapper.MISSING
    hash_file = _hash_path()
    if not hash_file.exists():
        return FDAWrapper.STALE
    if _source_sha256() != hash_file.read_text().strip():
        return FDAWrapper.STALE
    if not _probe_fda():
        return FDAWrapper.MISSING_FDA_GRANT
    return FDAWrapper.OK


def build_wrapper() -> None:
    """Compile and ad-hoc-sign Crony.app when its binary is stale.

    A no-op when the binary already matches the current source. Requires
    the Xcode Command Line Tools (cc + codesign); raises
    PreconditionError when cc is absent or the compile / sign fails.
    """
    stale, reason = _needs_rebuild()
    if not stale:
        return
    if shutil.which("cc") is None:
        raise crony.errors.PreconditionError(
            "C compiler (cc) not found; install the Xcode Command Line "
            "Tools (xcode-select --install) so crony can build Crony.app "
            "for full-disk-access jobs."
        )
    src = _source_path()
    binary = wrapper_binary()
    hash_file = _hash_path()

    # Drop stale artifacts first so a failed build can't leave a hash
    # file that masks an outdated or missing binary.
    hash_file.unlink(missing_ok=True)
    binary.unlink(missing_ok=True)

    logger.info(f"Compiling Crony.app wrapper ({reason}): {src}")
    _run_build_step(
        [
            "cc",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-o",
            str(binary),
            str(src),
        ],
        "compile Crony.app wrapper",
    )
    _run_build_step(
        ["codesign", "--force", "--deep", "--sign", "-", str(_app_path())],
        "sign Crony.app",
    )
    # Record the hash only after a successful compile + sign.
    hash_file.write_text(_source_sha256() + "\n")
    logger.info("Crony.app wrapper compiled (FDA may need granting)")


def _probe_fda() -> bool:
    """Whether the Full Disk Access grant is in effect, by running the
    wrapper's `--check-fda` probe. The wrapper must already be built.

    The probe disclaims TCC responsibility exactly as a real run does,
    so its verdict reflects what a full-disk-access job would actually
    get. Returns True when granted, False when denied.
    """
    result = subprocess.run(
        [str(wrapper_binary()), _CHECK_FDA_FLAG],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def grant_instructions() -> str:
    """One-line-per-step guidance for granting FDA to Crony.app, for
    crony to print when the probe reports the grant missing."""
    return (
        "Crony.app does not have Full Disk Access -- full-disk-access "
        "jobs will fail until it is granted:\n"
        '  1. open "x-apple.systempreferences:com.apple.settings.'
        'PrivacySecurity.extension?Privacy_AllFiles"\n'
        f"  2. add and enable: {_app_path()}"
    )


def _run_build_step(cmd: list[str], what: str) -> None:
    """Run a build subprocess, surfacing failures as a crony error with
    the tool's stderr rather than an uncaught CalledProcessError."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise crony.errors.PreconditionError(
            f"failed to {what} (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
