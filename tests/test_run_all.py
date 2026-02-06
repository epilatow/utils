#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov"]
# ///

"""
Unit tests for tests/run_all.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest  # type: ignore[import-not-found]

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent

# Import run_all module from tests/
_script_path = REPO_ROOT / "tests" / "run_all.py"
_loader = importlib.machinery.SourceFileLoader("run_all", str(_script_path))
_spec = importlib.util.spec_from_loader("run_all", _loader)
assert _spec and _spec.loader
ra = importlib.util.module_from_spec(_spec)
sys.modules["run_all"] = ra
_spec.loader.exec_module(ra)


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
        script.write_text(
            "#!/usr/bin/env python3\n" "import sys\n" "sys.exit(0)\n"
        )
        script.chmod(0o755)
        result = ra.run_test_file(script)
        assert result.name == "test_ok"
        assert result.returncode == 0
        assert result.success is True

    def test_failure(self, tmp_repo: Path) -> None:
        """Return failure result for failing test."""
        tests_dir = tmp_repo / "tests"
        script = tests_dir / "test_fail.py"
        script.write_text(
            "#!/usr/bin/env python3\n" "import sys\n" "sys.exit(1)\n"
        )
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


# =============================================================
# Tests: Code Quality
# =============================================================


class TestCodeQuality:
    """Code quality checks for run_all.py."""

    def test_black_compliance(self) -> None:
        """Test that run_all.py is formatted with black."""
        result = subprocess.run(
            [
                "uvx",
                "black",
                "-l80",
                "--check",
                str(_script_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"black check failed:\n{result.stderr}"

    def test_black_compliance_tests(self) -> None:
        """Test that tests are formatted with black."""
        result = subprocess.run(
            [
                "uvx",
                "black",
                "-l80",
                "--check",
                str(REPO_ROOT / "tests" / "test_run_all.py"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"black check failed:\n{result.stderr}"

    def test_flake8_compliance(self) -> None:
        """Test that run_all.py passes flake8."""
        result = subprocess.run(
            [
                "uvx",
                "flake8",
                "--max-line-length=80",
                "--extend-ignore=E203,W503",
                str(_script_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"flake8 check failed:\n{result.stdout}"

    def test_flake8_compliance_tests(self) -> None:
        """Test that tests pass flake8."""
        result = subprocess.run(
            [
                "uvx",
                "flake8",
                "--max-line-length=80",
                "--extend-ignore=E203,W503",
                str(REPO_ROOT / "tests" / "test_run_all.py"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"flake8 check failed:\n{result.stdout}"

    def test_mypy_compliance(self, tmp_path: Path) -> None:
        """Test that run_all.py passes mypy."""
        cache_dir = tmp_path / "mypy_cache"
        result = subprocess.run(
            [
                "uvx",
                "mypy",
                "--cache-dir",
                str(cache_dir),
                str(_script_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"mypy check failed:\n{result.stdout}"

    def test_mypy_compliance_tests(self, tmp_path: Path) -> None:
        """Test that tests pass mypy."""
        cache_dir = tmp_path / "mypy_cache"
        result = subprocess.run(
            [
                "uvx",
                "--with",
                "pytest",
                "mypy",
                "--cache-dir",
                str(cache_dir),
                str(REPO_ROOT / "tests" / "test_run_all.py"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"mypy check failed:\n{result.stdout}"


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
