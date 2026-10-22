#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15,<3.16"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.platform's package-level surface (the
get_scheduler / get_host factories). Backend-specific tests live in
test_crony_platform_{launchd,systemd}.py (schedulers) and
test_crony_platform_host_{darwin,linux}.py (host platforms)."""

import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import (
    DarwinHost,
    LaunchdScheduler,
    LinuxHost,
    SystemdScheduler,
    current_host,
    current_platform,
    get_host,
    get_scheduler,
)

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "crony" / "platform" / "__init__.py"


class TestGetScheduler:
    def test_unsupported_platform_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported platform"):
            get_scheduler("plan9", Path("/unused"))

    def test_darwin_returns_launchd(self) -> None:
        assert isinstance(
            get_scheduler("darwin", Path("/unused")), LaunchdScheduler
        )

    def test_linux_returns_systemd(self) -> None:
        assert isinstance(
            get_scheduler("linux", Path("/unused")), SystemdScheduler
        )


class TestGetHost:
    def test_unsupported_platform_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported platform"):
            get_host("plan9")

    def test_darwin_returns_darwin_host(self) -> None:
        assert isinstance(get_host("darwin"), DarwinHost)

    def test_linux_returns_linux_host(self) -> None:
        assert isinstance(get_host("linux"), LinuxHost)


class TestPlatformDetection:
    def test_current_platform(self) -> None:
        assert current_platform() in ("darwin", "linux")

    def test_current_host(self) -> None:
        h = current_host()
        assert isinstance(h, str)
        assert len(h) > 0
        assert "." not in h


@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGUSR2])
def test_stop_signals_the_instance_without_disarming(
    platform: str, signum: int
) -> None:
    name = "default.j"
    signal_name = signal.Signals(signum).name
    expected = (
        [
            "launchctl",
            "kill",
            signal_name,
            f"gui/{os.getuid()}/org.crony.{name}",
        ]
        if platform == "darwin"
        else [
            "systemctl",
            "--user",
            "kill",
            "--kill-whom=main",
            f"--signal={signal_name}",
            f"crony-{name}.service",
        ]
    )
    with patch.object(
        subprocess,
        "run",
        autospec=True,
        return_value=subprocess.CompletedProcess(expected, 0),
    ) as run:
        get_scheduler(platform, Path("/unused")).stop(name, signum)
    run.assert_called_once_with(expected, check=False)


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
