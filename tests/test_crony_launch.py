#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "tomlkit", "pydantic>=2"]
# ///
# This is AI generated code

"""Unit launcher parsing and actual shell/shebang execution."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony import model, runtime
from crony.config import JobFlags
from crony.launch import (
    LauncherPath,
    parse_argv,
    parse_shell,
    shell_command,
    target_argv,
)
from crony.platform import get_scheduler, systemd
from crony.unit import (
    EntityName,
    EntityRef,
    Interval,
    PriorityClass,
    UnitSpec,
)

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "crony" / "launch.py"


@pytest.mark.parametrize("directory", ["/bin", "/home/a b/$x%q'\\; exec nope"])
def test_crony_shell_round_trip(directory: str) -> None:
    path = LauncherPath(Path(directory), Path(directory))
    argv = ("crony", "_run", "x:y", "a; b", "$var")
    shell = shell_command(argv, path)
    parsed = parse_shell(shell)
    assert parsed is not None and parsed.argv == argv and parsed.path == path
    assert parse_argv(list(target_argv(argv, path))) == parsed


@pytest.mark.parametrize("directory", ["/a:b", "/a\nb", "/a\rb", "/a\0b"])
def test_invalid_path_directory_is_rejected(directory: str) -> None:
    with pytest.raises(ValueError, match="invalid launcher PATH"):
        LauncherPath(Path(directory), Path("/uv"))


@pytest.mark.parametrize(
    "command",
    [
        (
            "export LAUNCHER_PATH_PREFIX=/a:/b:; "
            'export PATH="$LAUNCHER_PATH_PREFIX$OTHER"; exec crony _run x:y'
        ),
        (
            "export LAUNCHER_PATH_PREFIX=/a:/b:/c:; "
            'export PATH="$LAUNCHER_PATH_PREFIX$PATH"; exec crony _run x:y'
        ),
        (
            "export LAUNCHER_PATH_PREFIX=/a:/b:; "
            'export PATH="$LAUNCHER_PATH_PREFIX$PATH"; exec "$COMMAND"'
        ),
        (
            "export LAUNCHER_PATH_PREFIX=/a:/b:; "
            'export PATH="$LAUNCHER_PATH_PREFIX$PATH"; '
            "exec crony; touch /tmp/unwanted"
        ),
    ],
)
def test_noncanonical_shell_is_rejected(command: str) -> None:
    command = f'export LAUNCHER_ORIGINAL_PATH="$PATH"; {command}'
    assert parse_shell(command) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["/uv", "run", "--script", "/crony", "_run", "x:y"],
        ["/bin/sh", "-c", "exec /uv run --script /crony _run x:y"],
        ["/bin/sh", "-c", "exec crony _run x:y"],
        [
            "/bin/sh",
            "-c",
            (
                "export LAUNCHER_PATH_PREFIX=/a:/b:; "
                'export PATH="$LAUNCHER_PATH_PREFIX$PATH"; exec crony _run x:y'
            ),
        ],
    ],
)
def test_obsolete_targets_are_not_interpreted(argv: list[str]) -> None:
    assert parse_argv(argv) is None


@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize("same_directory", [False, True])
@pytest.mark.parametrize("jitter", [False, True])
def test_rendered_unit_executes_shebang_with_runtime_path(
    tmp_path: Path, platform: str, same_directory: bool, jitter: bool
) -> None:
    crony_dir = tmp_path / "crony space $literal%q'\\; exec nope"
    uv_dir = crony_dir if same_directory else tmp_path / "uv space"
    crony_dir.mkdir()
    if uv_dir != crony_dir:
        uv_dir.mkdir()
    (crony_dir / "crony").symlink_to(REPO_ROOT / "bin" / "crony")
    uv = uv_dir / "uv"
    uv.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$PATH" "$UV_CACHE_DIR" '
        '"$LAUNCHER_ORIGINAL_PATH" "$LAUNCHER_PATH_PREFIX" "$@"\n'
    )
    uv.chmod(0o755)
    path = LauncherPath(crony_dir, uv_dir)
    name, ref = EntityName("default", "j"), EntityRef("default", "u-test")
    timing = Interval("30min", 1800) if jitter else None
    spec = UnitSpec(
        name=name,
        cmd=target_argv(model._guarded_argv(ref, 120, False), path),
        timing=timing,
        priority=PriorityClass.NORMAL,
        platform_properties=model._unit_platform_properties(
            platform, JobFlags(0)
        ),
        jitter=model._jitter_spec(name, ref, timing, path),
    )
    sched = get_scheduler(platform, tmp_path / "units")
    units = sched.render_units(spec)
    for unit in units.units:
        if platform == "linux" and unit.filename.suffix == ".timer":
            continue
        if platform == "darwin":
            argv = plistlib.loads(unit.content.encode())["ProgramArguments"]
        else:
            argv = systemd._service_argv(unit.content)
        assert argv is not None
        parsed = parse_argv(list(argv))
        assert parsed is not None and parsed.path == path
        inherited = f"{crony_dir}:/usr/bin:{uv_dir}:/bin"
        result = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HOME": str(tmp_path / "home with spaces"),
                "PATH": inherited,
                "UV_CACHE_DIR": "/job-cache",
            },
        )
        lines = result.stdout.splitlines()
        assert lines[:2] == [f"{crony_dir}:{uv_dir}:{inherited}", "/job-cache"]
        assert lines[2:4] == [inherited, path.prefix]
        assert lines[4:9] == [
            "run",
            "--cache-dir",
            str(tmp_path / "home with spaces/.cache/crony/uv"),
            "--script",
            str(crony_dir / "crony"),
        ]
        assert lines[9:] == list(parsed.argv[1:])


def test_designated_executables_cannot_fall_through_or_be_shadowed(
    tmp_path: Path,
) -> None:
    crony_dir, uv_dir = tmp_path / "crony", tmp_path / "uv"
    crony_dir.mkdir()
    uv_dir.mkdir()
    for binary in (crony_dir / "crony", uv_dir / "uv"):
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
    path = LauncherPath(crony_dir, uv_dir)
    assert runtime._launcher_executables(path) == (
        uv_dir / "uv",
        crony_dir / "crony",
    )
    shadow = crony_dir / "uv"
    shadow.write_text("#!/bin/sh\nexit 0\n")
    shadow.chmod(0o755)
    assert runtime._launcher_executables(path)[0] is None
    shadow.unlink()
    (uv_dir / "uv").unlink()
    assert runtime._launcher_executables(path)[0] is None
    (crony_dir / "crony").chmod(0o644)
    assert runtime._launcher_executables(path)[1] is None


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
