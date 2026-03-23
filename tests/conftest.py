"""Pytest configuration - runs before test collection."""
# This is AI generated code

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import ClassVar

# Repository root
_REPO_ROOT = Path(__file__).parent.parent

# Create a temporary directory for __pycache__ and redirect all bytecode there
_pycache_tmpdir = tempfile.mkdtemp(prefix="pytest_pycache_")
sys.pycache_prefix = _pycache_tmpdir

# Also prevent bytecode writing for subsequent imports (belt and suspenders)
sys.dont_write_bytecode = True


def _cleanup_all_caches() -> None:
    """Remove temp dirs and any cache dirs in the repo."""
    # Clean up the temp pycache directory
    shutil.rmtree(_pycache_tmpdir, ignore_errors=True)
    # Clean up any __pycache__ created before sys.pycache_prefix was set
    # (e.g., conftest.py's own bytecode)
    for pycache in _REPO_ROOT.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)
    # Clean up any .mypy_cache directories
    for mypy_cache in _REPO_ROOT.rglob(".mypy_cache"):
        if mypy_cache.is_dir():
            shutil.rmtree(mypy_cache, ignore_errors=True)


# Register cleanup for when Python exits
atexit.register(_cleanup_all_caches)


def pytest_sessionfinish(
    session,  # type: ignore[no-untyped-def]
    exitstatus,  # type: ignore[no-untyped-def]
) -> None:
    """Clean up pycache directories after test session."""
    _cleanup_all_caches()


class CodeQualityBase:
    """Base class for code quality tests.

    Subclasses must define SCRIPT_PATH and TEST_PATH.
    """

    SCRIPT_PATH: ClassVar[Path]
    TEST_PATH: ClassVar[Path]

    def test_ruff_check_compliance(self) -> None:
        """Test that code passes ruff linting."""
        result = subprocess.run(
            ["uvx", "ruff", "check", str(self.SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff check failed:\n{result.stdout}"
        )

    def test_ruff_check_compliance_tests(self) -> None:
        """Test that tests pass ruff linting."""
        result = subprocess.run(
            ["uvx", "ruff", "check", str(self.TEST_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff check failed:\n{result.stdout}"
        )

    def test_ruff_format_compliance(self) -> None:
        """Test that code is formatted with ruff."""
        result = subprocess.run(
            [
                "uvx",
                "ruff",
                "format",
                "--check",
                str(self.SCRIPT_PATH),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff format check failed:\n{result.stderr}"
        )

    def test_ruff_format_compliance_tests(self) -> None:
        """Test that tests are formatted with ruff."""
        result = subprocess.run(
            [
                "uvx",
                "ruff",
                "format",
                "--check",
                str(self.TEST_PATH),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff format check failed:\n{result.stderr}"
        )

    def test_mypy_compliance(self, tmp_path: Path) -> None:
        """Test that code passes mypy."""
        cache_dir = tmp_path / "mypy_cache"
        result = subprocess.run(
            [
                "uvx",
                "mypy",
                "--cache-dir",
                str(cache_dir),
                str(self.SCRIPT_PATH),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"mypy check failed:\n{result.stdout}"
        )

    def test_mypy_compliance_tests(self, tmp_path: Path) -> None:
        """Test that tests pass mypy."""
        cache_dir = tmp_path / "mypy_cache"
        env = os.environ.copy()
        env["MYPYPATH"] = str(_REPO_ROOT / "bin")
        result = subprocess.run(
            [
                "uvx",
                "--with",
                "pytest",
                "mypy",
                "--cache-dir",
                str(cache_dir),
                str(self.TEST_PATH),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"mypy check failed:\n{result.stdout}"
        )


def run_tests(
    test_file: str,
    script_path: Path,
    repo_root: Path,
) -> None:
    """Entry point for running a test file directly.

    Handles ``--verbose`` and ``--coverage`` flags, then
    invokes ``pytest.main()``.  Called from each test
    file's ``__main__`` block.

    Args:
        test_file: The test file's ``__file__`` path.
        script_path: Path to the script under test
            (used to derive the coverage module name).
        repo_root: Repository root directory.
    """
    import argparse

    import pytest  # type: ignore[import-not-found]

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose test output",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run with coverage report",
    )
    args = parser.parse_args()

    pytest_args = [test_file, "-p", "no:cacheprovider"]
    if args.verbose:
        pytest_args.append("-v")
    if args.coverage:
        module = script_path.stem.replace("-", "_")
        cov_dir = Path(tempfile.gettempdir())
        pytest_args.extend(
            [
                f"--cov={module}",
                "--cov-report=term-missing",
                f"--cov-report=html:{cov_dir / (module + '_htmlcov')}",
            ]
        )
        os.environ["PYTHONPATH"] = str(repo_root / "bin")
        os.environ["COVERAGE_FILE"] = str(cov_dir / f"{module}.coverage")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(pytest.main(pytest_args))
