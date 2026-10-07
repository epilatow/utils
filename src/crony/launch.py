# This is AI generated code

"""Crony's launcher commands and job PATH isolation."""

import shlex
from dataclasses import dataclass
from pathlib import Path

LAUNCHER_PATH_PREFIX = "LAUNCHER_PATH_PREFIX"
LAUNCHER_ORIGINAL_PATH = "LAUNCHER_ORIGINAL_PATH"


@dataclass(frozen=True)
class LauncherPath:
    """Crony's two ordered launcher directories."""

    crony_dir: Path
    uv_dir: Path

    def __post_init__(self) -> None:
        for directory in (self.crony_dir, self.uv_dir):
            if any(c in str(directory) for c in (":", "\n", "\r", "\0")):
                raise ValueError(
                    f"invalid launcher PATH directory: {directory}"
                )

    @classmethod
    def from_executables(cls, crony: Path, uv: Path) -> LauncherPath:
        return cls(crony.parent, uv.parent)

    @property
    def prefix(self) -> str:
        return f"{self.crony_dir}:{self.uv_dir}:"


@dataclass(frozen=True)
class UnitLaunch:
    """An installed crony command and its ordered launcher PATH prefix."""

    argv: tuple[str, ...]
    path: LauncherPath


def shell_command(
    argv: tuple[str, ...],
    path: LauncherPath,
) -> str:
    """Quote literal directories and argv; expand only the inherited PATH."""
    prefix = (
        f'export {LAUNCHER_ORIGINAL_PATH}="$PATH"; '
        f"export {LAUNCHER_PATH_PREFIX}={shlex.quote(path.prefix)}; "
        f'export PATH="${LAUNCHER_PATH_PREFIX}$PATH"; '
    )
    return f"{prefix}exec {shlex.join(argv)}"


def target_argv(
    argv: tuple[str, ...],
    path: LauncherPath,
) -> tuple[str, ...]:
    """Build the complete target argv, with shell env setup and final exec."""
    return "/bin/sh", "-c", shell_command(argv, path)


def parse_argv(argv: list[str]) -> UnitLaunch | None:
    """Interpret only crony's current shell target format."""
    if len(argv) == 3 and argv[:2] == ["/bin/sh", "-c"]:
        return parse_shell(argv[2])
    return None


def parse_shell(command: str) -> UnitLaunch | None:
    """Parse only our canonical wrapper, without evaluating shell input."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
        if (
            len(tokens) >= 11
            and tokens[:4]
            == ["export", f"{LAUNCHER_ORIGINAL_PATH}=$PATH", ";", "export"]
            and tokens[4].startswith(f"{LAUNCHER_PATH_PREFIX}=")
            and tokens[4].endswith(":")
            and tokens[5:10]
            == [
                ";",
                "export",
                f"PATH=${LAUNCHER_PATH_PREFIX}$PATH",
                ";",
                "exec",
            ]
        ):
            directories = (
                tokens[4]
                .removeprefix(f"{LAUNCHER_PATH_PREFIX}=")
                .removesuffix(":")
                .split(":")
            )
            if len(directories) != 2:
                return None
            path = LauncherPath(Path(directories[0]), Path(directories[1]))
            launch = UnitLaunch(tuple(tokens[10:]), path)
        else:
            return None
    except ValueError:
        return None
    if not launch.argv or shell_command(launch.argv, launch.path) != command:
        return None
    return launch


def restore_job_env(env: dict[str, str]) -> None:
    """Restore the scheduler's PATH and remove launcher env markers."""
    env.pop(LAUNCHER_PATH_PREFIX, None)
    original = env.pop(LAUNCHER_ORIGINAL_PATH, None)
    # The launcher saves PATH before adding its directories. uv run then
    # prepends its private virtualenv bin before Python starts, potentially
    # once for each guard/runner launch. Restore the saved value so jobs don't
    # use crony's private environment.
    if original is not None:
        env["PATH"] = original
