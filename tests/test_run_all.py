#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "pytest-cov"]
# ///

"""
Unit tests for tests/run_all.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import run_all as ra

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent

# The script under test, for run_tests' coverage module name.
_script_path = REPO_ROOT / "tests" / "run_all.py"


# =============================================================
# Fixtures
# =============================================================


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a minimal repo structure for testing."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    return tmp_path


# =============================================================
# Tests: discover_test_files
# =============================================================


class TestDiscoverTestFiles:
    """Tests for test file discovery."""

    def test_finds_test_files(self, tmp_repo: Path) -> None:
        """Discover test_*.py files."""
        tests_dir = tmp_repo / "tests"
        (tests_dir / "test_alpha.py").write_text("pass\n")
        (tests_dir / "test_beta.py").write_text("pass\n")
        result = ra.discover_test_files(tests_dir)
        names = [p.name for p in result]
        assert names == ["test_alpha.py", "test_beta.py"]

    def test_skips_non_test_files(self, tmp_repo: Path) -> None:
        """Only return test_*.py files."""
        tests_dir = tmp_repo / "tests"
        (tests_dir / "conftest.py").write_text("pass\n")
        (tests_dir / "helper.py").write_text("pass\n")
        (tests_dir / "test_real.py").write_text("pass\n")
        result = ra.discover_test_files(tests_dir)
        names = [p.name for p in result]
        assert names == ["test_real.py"]

    def test_sorted_output(self, tmp_repo: Path) -> None:
        """Results are sorted alphabetically."""
        tests_dir = tmp_repo / "tests"
        for name in ["test_zebra.py", "test_alpha.py"]:
            (tests_dir / name).write_text("pass\n")
        result = ra.discover_test_files(tests_dir)
        names = [p.name for p in result]
        assert names == ["test_alpha.py", "test_zebra.py"]

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        """Return empty list when dir doesn't exist."""
        result = ra.discover_test_files(tmp_path / "nonexistent")
        assert result == []

    def test_empty_dir(self, tmp_repo: Path) -> None:
        """Return empty list when no test files."""
        tests_dir = tmp_repo / "tests"
        result = ra.discover_test_files(tests_dir)
        assert result == []


# =============================================================
# Tests: run_test_file
# =============================================================


class TestRunTestFile:
    """Tests for running a single test file."""

    def test_success(self, tmp_repo: Path) -> None:
        """Return success result for passing test."""
        tests_dir = tmp_repo / "tests"
        script = tests_dir / "test_ok.py"
        script.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
        script.chmod(0o755)
        result = ra.run_test_file(script)
        assert result.name == "test_ok"
        assert result.returncode == 0
        assert result.success is True

    def test_failure(self, tmp_repo: Path) -> None:
        """Return failure result for failing test."""
        tests_dir = tmp_repo / "tests"
        script = tests_dir / "test_fail.py"
        script.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
        script.chmod(0o755)
        result = ra.run_test_file(script)
        assert result.name == "test_fail"
        assert result.returncode == 1
        assert result.success is False

    def test_verbose_flag(self, tmp_repo: Path) -> None:
        """Pass --verbose when verbose=True."""
        tests_dir = tmp_repo / "tests"
        script = tests_dir / "test_args.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.exit(\n"
            "    0 if '--verbose' in sys.argv else 1\n"
            ")\n"
        )
        script.chmod(0o755)
        result = ra.run_test_file(script, verbose=True)
        assert result.success is True

    def test_coverage_flag(self, tmp_repo: Path) -> None:
        """Pass --coverage when coverage=True."""
        tests_dir = tmp_repo / "tests"
        script = tests_dir / "test_cov.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.exit(\n"
            "    0 if '--coverage' in sys.argv else 1\n"
            ")\n"
        )
        script.chmod(0o755)
        result = ra.run_test_file(script, coverage=True)
        assert result.success is True

    def test_e2e_flag(self, tmp_repo: Path) -> None:
        """Pass --e2e when e2e=True."""
        tests_dir = tmp_repo / "tests"
        script = tests_dir / "test_e2e.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.exit(\n"
            "    0 if '--e2e' in sys.argv else 1\n"
            ")\n"
        )
        script.chmod(0o755)
        result = ra.run_test_file(script, e2e=True)
        assert result.success is True

    def test_default_no_e2e_flag(self, tmp_repo: Path) -> None:
        """Do not pass --e2e by default."""
        tests_dir = tmp_repo / "tests"
        script = tests_dir / "test_no_e2e.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.exit(\n"
            "    1 if '--e2e' in sys.argv else 0\n"
            ")\n"
        )
        script.chmod(0o755)
        result = ra.run_test_file(script)
        assert result.success is True


# =============================================================
# Tests: conftest.run_tests (--e2e marker filter)
# =============================================================


class TestRunTestsE2eMarkerFilter:
    """conftest.run_tests' --e2e flag controls the marker filter.

    Without --e2e, the e2e suite is excluded via ``-m "not e2e"``.
    With --e2e, no marker filter is added (so e2e tests run too).
    """

    @staticmethod
    def _invoke(argv: list[str]) -> list[str]:
        """Run conftest.run_tests with the given argv, capture
        the pytest argv it would have invoked."""
        from conftest import run_tests

        captured: list[list[str]] = []

        def fake_main(args: list[str]) -> int:
            captured.append(list(args))
            return 0

        with (
            patch("sys.argv", ["test_file.py", *argv]),
            patch("pytest.main", fake_main),
            pytest.raises(SystemExit),
        ):
            run_tests("test_file.py", Path("/tmp/script"), Path("/tmp"))
        assert captured, "pytest.main was not called"
        return captured[0]

    def test_default_excludes_e2e_marker(self) -> None:
        argv = self._invoke([])
        assert "-m" in argv
        assert argv[argv.index("-m") + 1] == "not e2e"

    def test_e2e_flag_drops_marker_filter(self) -> None:
        argv = self._invoke(["--e2e"])
        # The marker filter is gone when --e2e is set, so the
        # suite collects both e2e and non-e2e tests.
        assert "not e2e" not in argv


# =============================================================
# Tests: run_repo_shared_phase
# =============================================================


class TestRunRepoSharedPhase:
    """Tests for the delivered-shared-tests phase.

    The phase shells out to ``uv run pytest <dir>``; the interesting
    shape is the argv it builds and the returncode mapping, not the
    pytest run itself, so ``subprocess.run`` is patched.
    """

    def _stub_run(self, returncode: int) -> tuple[list[list[str]], object]:
        captured: list[list[str]] = []

        def fake(cmd: list[str], **_kwargs: object) -> object:
            captured.append(list(cmd))
            return type("CP", (), {"returncode": returncode})()

        return captured, fake

    def test_invokes_uv_run_pytest_against_dir(self) -> None:
        captured, fake = self._stub_run(0)
        shared_dir = Path("/some/_repo_shared/tests")
        with patch.object(ra.subprocess, "run", fake):
            result = ra.run_repo_shared_phase(shared_dir)
        assert result.name == "repo-shared"
        assert result.returncode == 0
        assert result.success is True
        assert len(captured) == 1
        argv = captured[0]
        assert argv[:3] == ["uv", "run", "pytest"]
        assert argv[-1] == str(shared_dir)
        # No --confcutdir: the shared tests are a sibling of tests/,
        # not a descendant, so the conftest walk never reaches them.
        assert not any(a.startswith("--confcutdir") for a in argv)
        assert "-v" not in argv

    def test_verbose_flag_appends_dash_v(self) -> None:
        captured, fake = self._stub_run(0)
        with patch.object(ra.subprocess, "run", fake):
            ra.run_repo_shared_phase(
                Path("/some/_repo_shared/tests"), verbose=True
            )
        assert "-v" in captured[0]

    def test_nonzero_returncode_maps_to_failure(self) -> None:
        _captured, fake = self._stub_run(1)
        with patch.object(ra.subprocess, "run", fake):
            result = ra.run_repo_shared_phase(Path("/some/_repo_shared/tests"))
        assert result.returncode == 1
        assert result.success is False


# =============================================================
# Tests: print_summary
# =============================================================


class TestPrintSummary:
    """Tests for the summary printer."""

    def test_all_passed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Show 'All N phases passed' when all succeed."""
        results = [
            ra.TestResult(name="a", returncode=0),
            ra.TestResult(name="b", returncode=0),
        ]
        ra.print_summary(results)
        captured = capsys.readouterr()
        assert "All 2 phases passed." in captured.out
        assert "PASSED" in captured.out
        assert "FAILED" not in captured.out

    def test_some_failed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Show failure count when some phases fail."""
        results = [
            ra.TestResult(name="a", returncode=0),
            ra.TestResult(name="b", returncode=1),
        ]
        ra.print_summary(results)
        captured = capsys.readouterr()
        assert "FAILED: 1 of 2" in captured.out
        assert "exit code 1" in captured.out

    def test_singular_phase(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Use singular 'phase' when exactly 1 fails."""
        results = [
            ra.TestResult(name="only", returncode=1),
        ]
        ra.print_summary(results)
        captured = capsys.readouterr()
        assert "1 of 1 phase failed." in captured.out


# =============================================================
# Tests: TestResult dataclass
# =============================================================


class TestTestResult:
    """Tests for the TestResult dataclass."""

    def test_success_on_zero(self) -> None:
        r = ra.TestResult(name="t", returncode=0)
        assert r.success is True

    def test_failure_on_nonzero(self) -> None:
        r = ra.TestResult(name="t", returncode=1)
        assert r.success is False


# =============================================================
# Tests: build_parser / add_arguments
# =============================================================


class TestArgumentParser:
    """Tests for CLI argument parsing."""

    def test_parser_builds(self) -> None:
        parser = ra.build_parser()
        assert parser is not None

    def test_verbose_flag(self) -> None:
        parser = ra.build_parser()
        ra.add_arguments(parser)
        args = parser.parse_args(["-v"])
        assert args.verbose is True

    def test_default_no_verbose(self) -> None:
        parser = ra.build_parser()
        ra.add_arguments(parser)
        args = parser.parse_args([])
        assert args.verbose is False

    def test_coverage_flag(self) -> None:
        parser = ra.build_parser()
        ra.add_arguments(parser)
        args = parser.parse_args(["--coverage"])
        assert args.coverage is True

    def test_default_no_coverage(self) -> None:
        parser = ra.build_parser()
        ra.add_arguments(parser)
        args = parser.parse_args([])
        assert args.coverage is False

    def test_e2e_flag(self) -> None:
        parser = ra.build_parser()
        ra.add_arguments(parser)
        args = parser.parse_args(["--e2e"])
        assert args.e2e is True

    def test_default_no_e2e(self) -> None:
        parser = ra.build_parser()
        ra.add_arguments(parser)
        args = parser.parse_args([])
        assert args.e2e is False


# =============================================================
# Tests: main
# =============================================================


class TestMain:
    """Tests for the main entry point."""

    def test_returns_infra_error_no_test_files(self, tmp_repo: Path) -> None:
        """Exit 2 when no test files found."""
        with (
            patch.object(ra, "TESTS_DIR", tmp_repo / "tests"),
            patch("sys.argv", ["run_all.py"]),
        ):
            rc = ra.main()
        assert rc == ra.EXIT_INFRA_ERROR


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
