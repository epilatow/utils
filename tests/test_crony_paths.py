#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Unit tests for crony.paths: the tool basename and the CRONY_<KEY>
env override helper the config / state directories resolve through."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony import BASENAME, paths  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
_script_path = REPO_ROOT / "src" / "crony" / "paths.py"


class TestIdentity:
    def test_basename_is_crony(self) -> None:
        assert BASENAME == "crony"

    def test_env_prefix_is_uppercased_basename(self) -> None:
        assert paths._ENV_PREFIX == "CRONY"


class TestEnvPath:
    def test_default_used_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CRONY_FOO_DIR", raising=False)
        assert paths._env_path("FOO_DIR", "/tmp/d") == Path("/tmp/d")

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRONY_FOO_DIR", "/tmp/override")
        assert paths._env_path("FOO_DIR", "/tmp/d") == Path("/tmp/override")

    def test_empty_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRONY_FOO_DIR", "")
        assert paths._env_path("FOO_DIR", "/tmp/d") == Path("/tmp/d")


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
