"""Pytest configuration - runs before test collection."""
# This is AI generated code

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

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
