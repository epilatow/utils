#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "tomlkit"]
# ///
# This is AI generated code

"""Unit tests for crony.platform.fda (the Crony.app FDA wrapper)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import crony.platform.fda as fda  # noqa: E402
from crony.errors import PreconditionError  # noqa: E402

_script_path = REPO_ROOT / "src" / "crony" / "platform" / "fda.py"


class TestNeedsRebuild:
    """`needs_rebuild` compares the checked-in source against the
    stored hash, with the binary and hash files redirected to tmp."""

    @pytest.fixture
    def env(self, tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
        src = tmp_path / "Crony.c"
        binary = tmp_path / "Crony"
        hash_file = tmp_path / ".Crony.source-sha256"
        with (
            patch.object(fda, "source_path", return_value=src),
            patch.object(fda, "wrapper_binary", return_value=binary),
            patch.object(fda, "_hash_path", return_value=hash_file),
        ):
            yield src, binary, hash_file

    def test_no_binary(self, env: tuple[Path, Path, Path]) -> None:
        src, _binary, _hash_file = env
        src.write_text("int main(){}")
        stale, reason = fda.needs_rebuild()
        assert stale is True
        assert "binary" in reason

    def test_no_hash(self, env: tuple[Path, Path, Path]) -> None:
        src, binary, _hash_file = env
        src.write_text("int main(){}")
        binary.write_text("binary")
        stale, reason = fda.needs_rebuild()
        assert stale is True
        assert "hash" in reason

    def test_hash_mismatch(self, env: tuple[Path, Path, Path]) -> None:
        src, binary, hash_file = env
        src.write_text("int main(){}")
        binary.write_text("binary")
        hash_file.write_text("stale\n")
        stale, reason = fda.needs_rebuild()
        assert stale is True
        assert "mismatch" in reason

    def test_current(self, env: tuple[Path, Path, Path]) -> None:
        src, binary, hash_file = env
        src.write_text("int main(){}")
        binary.write_text("binary")
        hash_file.write_text(fda._source_sha256() + "\n")
        stale, reason = fda.needs_rebuild()
        assert stale is False
        assert reason == ""

    @pytest.mark.usefixtures("env")
    def test_missing_source_raises(self) -> None:
        # src not written -> does not exist
        with pytest.raises(PreconditionError, match="source missing"):
            fda.needs_rebuild()


class TestWrapperState:
    """`wrapper_state` separates MISSING (no binary) from STALE (binary
    present, source changed) by cheap file checks, and probes the grant
    only when the binary is current (OK vs MISSING_FDA_GRANT)."""

    @pytest.fixture
    def env(self, tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
        src = tmp_path / "Crony.c"
        binary = tmp_path / "Crony"
        hash_file = tmp_path / ".Crony.source-sha256"
        src.write_text("int main(){}")
        with (
            patch.object(fda, "source_path", return_value=src),
            patch.object(fda, "wrapper_binary", return_value=binary),
            patch.object(fda, "_hash_path", return_value=hash_file),
        ):
            yield src, binary, hash_file

    @pytest.mark.usefixtures("env")
    def test_missing_when_no_binary(self) -> None:
        assert fda.wrapper_state() is fda.FDAWrapper.MISSING

    def test_stale_when_hash_absent(self, env: tuple[Path, Path, Path]) -> None:
        _src, binary, _hash_file = env
        binary.write_text("binary")
        assert fda.wrapper_state() is fda.FDAWrapper.STALE

    def test_stale_when_hash_mismatch(
        self, env: tuple[Path, Path, Path]
    ) -> None:
        _src, binary, hash_file = env
        binary.write_text("binary")
        hash_file.write_text("stale\n")
        assert fda.wrapper_state() is fda.FDAWrapper.STALE

    def test_ok_when_current_and_granted(
        self, env: tuple[Path, Path, Path]
    ) -> None:
        _src, binary, hash_file = env
        binary.write_text("binary")
        hash_file.write_text(fda._source_sha256() + "\n")
        with patch.object(fda, "probe_fda", return_value=True):
            assert fda.wrapper_state() is fda.FDAWrapper.OK

    def test_missing_grant_when_current_but_denied(
        self, env: tuple[Path, Path, Path]
    ) -> None:
        _src, binary, hash_file = env
        binary.write_text("binary")
        hash_file.write_text(fda._source_sha256() + "\n")
        with patch.object(fda, "probe_fda", return_value=False):
            assert fda.wrapper_state() is fda.FDAWrapper.MISSING_FDA_GRANT

    def test_stale_does_not_probe_grant(
        self, env: tuple[Path, Path, Path]
    ) -> None:
        # A stale binary is decided by file checks alone -- the grant
        # probe (a subprocess) must not run.
        _src, binary, _hash_file = env
        binary.write_text("binary")
        with patch.object(fda, "probe_fda", autospec=True) as probe:
            assert fda.wrapper_state() is fda.FDAWrapper.STALE
        probe.assert_not_called()


class TestIsMissing:
    """`FDAWrapper.is_missing` groups the states that leave a job unable
    to read what it needs."""

    def test_missing_states_are_missing(self) -> None:
        assert fda.FDAWrapper.MISSING.is_missing
        assert fda.FDAWrapper.MISSING_FDA_GRANT.is_missing

    def test_present_states_are_not_missing(self) -> None:
        assert not fda.FDAWrapper.OK.is_missing
        assert not fda.FDAWrapper.STALE.is_missing


class TestBuildWrapper:
    """`build_wrapper` compiles then signs, recording the hash only on
    success, with the compiler/codesign invocations mocked so the test
    runs on any platform."""

    @pytest.fixture
    def env(self, tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
        src = tmp_path / "Crony.c"
        binary = tmp_path / "Crony"
        hash_file = tmp_path / ".Crony.source-sha256"
        src.write_text("int main(){}")
        with (
            patch.object(fda, "source_path", return_value=src),
            patch.object(fda, "wrapper_binary", return_value=binary),
            patch.object(fda, "_hash_path", return_value=hash_file),
            patch.object(fda, "_app_path", return_value=tmp_path / "Crony.app"),
        ):
            yield src, binary, hash_file

    @staticmethod
    def _ok(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stderr="")

    def test_compiles_then_signs(self, env: tuple[Path, Path, Path]) -> None:
        src, binary, _hash_file = env
        with (
            patch("crony.platform.fda.shutil.which", return_value="/cc"),
            patch(
                "crony.platform.fda.subprocess.run",
                autospec=True,
                side_effect=self._ok,
            ) as run,
        ):
            fda.build_wrapper()
        calls = [c.args[0] for c in run.call_args_list]
        assert calls[0][0] == "cc"
        assert str(src) in calls[0]
        assert str(binary) in calls[0]
        assert calls[1][0] == "codesign"
        assert calls[1][-1] == str(env[0].parent / "Crony.app")

    def test_writes_hash_after_success(
        self, env: tuple[Path, Path, Path]
    ) -> None:
        _src, _binary, hash_file = env
        with (
            patch("crony.platform.fda.shutil.which", return_value="/cc"),
            patch(
                "crony.platform.fda.subprocess.run",
                autospec=True,
                side_effect=self._ok,
            ),
        ):
            fda.build_wrapper()
        assert hash_file.read_text().strip() == fda._source_sha256()

    def test_skips_when_current(self, env: tuple[Path, Path, Path]) -> None:
        _src, binary, hash_file = env
        binary.write_text("binary")
        hash_file.write_text(fda._source_sha256() + "\n")
        with patch("crony.platform.fda.subprocess.run", autospec=True) as run:
            fda.build_wrapper()
        run.assert_not_called()

    @pytest.mark.usefixtures("env")
    def test_raises_without_cc(self) -> None:
        with (
            patch("crony.platform.fda.shutil.which", return_value=None),
            pytest.raises(PreconditionError, match="cc.*not found"),
        ):
            fda.build_wrapper()

    def test_compile_failure_raises_without_hash(
        self, env: tuple[Path, Path, Path]
    ) -> None:
        _src, _binary, hash_file = env

        def fail(*_a: object, **_k: object) -> Any:  # noqa: ANN401
            return subprocess.CompletedProcess(
                args=[], returncode=1, stderr="boom"
            )

        with (
            patch("crony.platform.fda.shutil.which", return_value="/cc"),
            patch(
                "crony.platform.fda.subprocess.run",
                autospec=True,
                side_effect=fail,
            ),
            pytest.raises(PreconditionError, match="failed to compile"),
        ):
            fda.build_wrapper()
        assert not hash_file.exists()


class TestProbe:
    """`probe_fda` runs the wrapper's `--check-fda` and reads its exit
    code; the subprocess is mocked so it runs on any platform."""

    def test_granted(self) -> None:
        with patch(
            "crony.platform.fda.subprocess.run",
            autospec=True,
            return_value=subprocess.CompletedProcess([], 0),
        ):
            assert fda.probe_fda() is True

    def test_denied(self) -> None:
        with patch(
            "crony.platform.fda.subprocess.run",
            autospec=True,
            return_value=subprocess.CompletedProcess([], fda.FDA_EXIT_CODE),
        ):
            assert fda.probe_fda() is False

    def test_grant_instructions_name_the_bundle(self) -> None:
        msg = fda.grant_instructions()
        assert "Crony.app" in msg
        assert "Privacy_AllFiles" in msg


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="Crony.app wrapper uses macOS-specific APIs (mach-o/dyld.h)",
)
class TestWrapperBinary:
    """Integration tests for the compiled Crony.app wrapper binary: it
    disclaims TCC responsibility, then either probes FDA (`--check-fda`)
    or execs the command it was handed."""

    SOURCE = REPO_ROOT / "Applications/Crony.app/Contents/MacOS/Crony.c"

    @pytest.fixture
    def tree(self, tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
        """Compile the real source, add a mock target command, and yield
        (binary, mock_cmd, fake_home)."""
        binary = tmp_path / "Crony"
        result = subprocess.run(
            [
                "cc",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-O2",
                "-o",
                str(binary),
                str(self.SOURCE),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"compile failed: {result.stderr}"

        # The mock target records the args it was handed (args.txt) and
        # the disclaim marker once per invocation (calls.txt), so tests
        # can confirm the command runs once, inside the disclaimed
        # re-spawn, with the right argv.
        mock_cmd = tmp_path / "cmd"
        mock_cmd.write_text(
            "#!/bin/bash\n"
            'echo "$@" > "$(dirname "$0")/args.txt"\n'
            'echo "${CRONY_FDA_DISCLAIMED:-unset}" >> '
            '"$(dirname "$0")/calls.txt"\n'
        )
        mock_cmd.chmod(0o755)

        # Fake HOME with no TCC dir -> FDA check sees ENOENT -> proceeds.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        yield binary, mock_cmd, fake_home

    def test_source_compiles_warning_clean(self, tmp_path: Path) -> None:
        binary = tmp_path / "Crony"
        result = subprocess.run(
            [
                "cc",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pedantic",
                "-O2",
                "-o",
                str(binary),
                str(self.SOURCE),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_compiles_to_macho(self, tree: tuple[Path, Path, Path]) -> None:
        binary, _, _ = tree
        out = subprocess.run(
            ["file", str(binary)], capture_output=True, text=True
        ).stdout
        assert "Mach-O" in out

    def test_run_mode_forwards_args_to_command(
        self, tree: tuple[Path, Path, Path]
    ) -> None:
        binary, mock_cmd, fake_home = tree
        result = subprocess.run(
            [str(binary), str(mock_cmd), "run", "default.j"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )
        assert result.returncode == 0, result.stderr
        assert (mock_cmd.parent / "args.txt").read_text().strip() == (
            "run default.j"
        )

    def test_disclaim_respawn_runs_command_once(
        self, tree: tuple[Path, Path, Path]
    ) -> None:
        binary, mock_cmd, fake_home = tree
        result = subprocess.run(
            [str(binary), str(mock_cmd)],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )
        assert result.returncode == 0, result.stderr
        # The single command invocation runs inside the disclaimed
        # re-spawn (marker set), and the marker breaks the loop.
        calls = (mock_cmd.parent / "calls.txt").read_text().splitlines()
        assert calls == ["1"]

    def test_disclaim_marker_skips_respawn(
        self, tree: tuple[Path, Path, Path]
    ) -> None:
        binary, mock_cmd, fake_home = tree
        result = subprocess.run(
            [str(binary), str(mock_cmd)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HOME": str(fake_home),
                "CRONY_FDA_DISCLAIMED": "1",
            },
        )
        assert result.returncode == 0, result.stderr
        calls = (mock_cmd.parent / "calls.txt").read_text().splitlines()
        assert calls == ["1"]

    def test_probe_mode_granted_runs_nothing(
        self, tree: tuple[Path, Path, Path]
    ) -> None:
        binary, mock_cmd, fake_home = tree
        result = subprocess.run(
            [str(binary), "--check-fda"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )
        # Granted (no TCC dir -> indeterminate -> proceed) exits 0 and
        # runs no command.
        assert result.returncode == 0, result.stderr
        assert not (mock_cmd.parent / "args.txt").exists()

    def test_probe_mode_denied_is_silent(
        self, tree: tuple[Path, Path, Path]
    ) -> None:
        binary, _, fake_home = tree
        tcc = fake_home / "Library" / "Application Support" / "com.apple.TCC"
        tcc.mkdir(parents=True)
        tcc.chmod(0o000)
        try:
            result = subprocess.run(
                [str(binary), "--check-fda"],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": str(fake_home)},
            )
        finally:
            tcc.chmod(0o700)
        assert result.returncode == fda.FDA_EXIT_CODE
        assert result.stderr == ""

    def test_run_mode_denied_logs_guidance(
        self, tree: tuple[Path, Path, Path]
    ) -> None:
        binary, mock_cmd, fake_home = tree
        tcc = fake_home / "Library" / "Application Support" / "com.apple.TCC"
        tcc.mkdir(parents=True)
        tcc.chmod(0o000)
        try:
            result = subprocess.run(
                [str(binary), str(mock_cmd)],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": str(fake_home)},
            )
        finally:
            tcc.chmod(0o700)
        assert result.returncode == fda.FDA_EXIT_CODE
        assert "Full Disk Access" in result.stderr

    def test_forwards_termination_signal_to_command(
        self, tree: tuple[Path, Path, Path]
    ) -> None:
        binary, mock_cmd, fake_home = tree
        got_term = mock_cmd.parent / "cmd_got_term"
        started = mock_cmd.parent / "cmd_started"
        # Background the sleep and wait on it: a bare foreground sleep
        # would defer the trap until it finished, masking forwarding.
        mock_cmd.write_text(
            "#!/bin/bash\n"
            f'trap \'echo term > "{got_term}"; kill "$SP" 2>/dev/null;'
            " exit 42' TERM\n"
            f'touch "{started}"\n'
            "sleep 30 & SP=$!\n"
            'wait "$SP"\n'
        )
        mock_cmd.chmod(0o755)
        proc = subprocess.Popen(
            [str(binary), str(mock_cmd)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "HOME": str(fake_home)},
        )
        try:
            deadline = time.monotonic() + 5
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert started.exists(), "mock command never started"
            proc.terminate()
            rc = proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        assert got_term.exists(), "SIGTERM did not reach command (orphaned)"
        assert rc == 42, f"wrapper did not propagate command exit: {rc}"

    def test_missing_command_exits_with_error(
        self, tree: tuple[Path, Path, Path]
    ) -> None:
        binary, _, fake_home = tree
        result = subprocess.run(
            [str(binary), str(binary.parent / "does-not-exist")],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )
        assert result.returncode != 0

    def test_no_command_exits_with_error(
        self, tree: tuple[Path, Path, Path]
    ) -> None:
        binary, _, fake_home = tree
        result = subprocess.run(
            [str(binary)],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )
        assert result.returncode != 0
        assert "no command" in result.stderr


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="build_wrapper compiles and codesigns a macOS app bundle",
)
class TestBuildWrapperDarwin:
    """End-to-end exercise of `build_wrapper` on darwin: it compiles and
    ad-hoc-signs a real copy of Crony.app through the crony code path,
    and the result probes FDA and reports a current wrapper state."""

    @pytest.fixture
    def bundle(self, tmp_path: Path) -> Iterator[Path]:
        """A writable copy of the real Crony.app with paths redirected,
        so the build does not touch the checked-in bundle."""
        app = tmp_path / "Crony.app"
        shutil.copytree(REPO_ROOT / "Applications" / "Crony.app", app)
        macos = app / "Contents" / "MacOS"
        with (
            patch.object(fda, "_app_path", return_value=app),
            patch.object(fda, "source_path", return_value=macos / "Crony.c"),
            patch.object(fda, "wrapper_binary", return_value=macos / "Crony"),
            patch.object(
                fda, "_hash_path", return_value=macos / ".Crony.source-sha256"
            ),
        ):
            yield app

    def test_builds_and_probes(self, bundle: Path) -> None:
        fda.build_wrapper()
        binary = bundle / "Contents" / "MacOS" / "Crony"
        assert binary.exists()
        out = subprocess.run(
            ["file", str(binary)], capture_output=True, text=True
        ).stdout
        assert "Mach-O" in out
        # The build leaves a current binary: needs_rebuild is satisfied
        # and the state reflects only the grant (OK or, on a test host
        # without the grant, MISSING_FDA_GRANT) -- never MISSING / STALE.
        assert fda.needs_rebuild()[0] is False
        assert fda.wrapper_state() in (
            fda.FDAWrapper.OK,
            fda.FDAWrapper.MISSING_FDA_GRANT,
        )

    def test_rebuild_is_idempotent(self, bundle: Path) -> None:
        fda.build_wrapper()
        hash_file = bundle / "Contents" / "MacOS" / ".Crony.source-sha256"
        first = hash_file.read_text()
        # A second build is a no-op (source unchanged), leaving the
        # recorded hash identical.
        fda.build_wrapper()
        assert hash_file.read_text() == first


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
