#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Unified test runner for the utils repository.

Discovers and runs all test files in tests/, reporting
a summary of results.

Exit codes:
    0 - All tests passed
    1 - One or more test phases failed
    2 - Script infrastructure error
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

EXIT_SUCCESS = 0
EXIT_TEST_FAILURE = 1
EXIT_INFRA_ERROR = 2

SEPARATOR = "=" * 60


# =============================================================
# Data Structures
# =============================================================


@dataclass
class TestResult:
    """Result of a single test phase."""

    name: str
    returncode: int

    @property
    def success(self) -> bool:
        return self.returncode == 0


# =============================================================
# Discovery
# =============================================================


def discover_test_files(tests_dir: Path) -> list[Path]:
    """Find all test files in the tests directory.

    Returns ``test_*.py`` paths sorted by name for
    deterministic ordering.  Returns an empty list if
    the directory does not exist.
    """
    if not tests_dir.is_dir():
        return []
    return sorted(tests_dir.glob("test_*.py"))


# =============================================================
# Execution
# =============================================================


def print_banner(title: str) -> None:
    """Print a visual separator with a title."""
    print(f"\n{SEPARATOR}")
    print(title)
    print(SEPARATOR, flush=True)


def run_test_file(
    path: Path,
    *,
    verbose: bool = False,
    coverage: bool = False,
) -> TestResult:
    """Run a single test file as a subprocess."""
    name = path.stem
    print_banner(f"Test: {name}")

    cmd: list[str] = [str(path)]
    if verbose:
        cmd.append("--verbose")
    if coverage:
        cmd.append("--coverage")

    cp = subprocess.run(cmd, cwd=REPO_ROOT)
    return TestResult(name=name, returncode=cp.returncode)


# =============================================================
# Reporting
# =============================================================


def print_summary(results: list[TestResult]) -> None:
    """Print a summary table of all test results."""
    print_banner("RESULTS SUMMARY")

    max_name = max(len(r.name) for r in results)
    for r in results:
        dots = "." * (max_name + 4 - len(r.name))
        if r.success:
            status = "PASSED"
        else:
            status = f"FAILED (exit code {r.returncode})"
        print(f"  {r.name} {dots} {status}")

    failed = [r for r in results if not r.success]
    total = len(results)
    print()
    if not failed:
        print(f"All {total} phases passed.")
    else:
        n = len(failed)
        label = "phase" if n == 1 else "phases"
        print(f"FAILED: {n} of {total} {label} failed.")


# =============================================================
# CLI
# =============================================================


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    return argparse.ArgumentParser(
        description="Run all tests for the utils repository.",
        epilog="""\
exit codes:
  0  All tests passed
  1  One or more test phases failed
  2  Script infrastructure error""",
        formatter_class=(argparse.RawDescriptionHelpFormatter),
    )


def add_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    """Add arguments to the parser."""
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output (passed through to tests)",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run with coverage report",
    )


def main() -> int:
    """Run all tests and report results.

    Returns:
        0 if all tests passed, 1 if any failed,
        2 on infrastructure error.
    """
    parser = build_parser()
    add_arguments(parser)
    args = parser.parse_args()

    test_files = discover_test_files(TESTS_DIR)
    if not test_files:
        print(
            "ERROR: No test files found " f"in {TESTS_DIR}",
            file=sys.stderr,
        )
        return EXIT_INFRA_ERROR

    results: list[TestResult] = []
    for test_file in test_files:
        result = run_test_file(
            test_file,
            verbose=args.verbose,
            coverage=args.coverage,
        )
        results.append(result)

    print_summary(results)

    if any(not r.success for r in results):
        return EXIT_TEST_FAILURE
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
