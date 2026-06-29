#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pytest",
#     "pytest-cov",
#     "tomlkit",
#     "pydantic>=2",
#     "mdformat",
#     "mdformat-gfm",
#     "mdformat-tables",
# ]
# ///
# This is AI generated code

"""Unit tests for the pure logic of `scripts/pandoc`.

Covers the host-to-release-asset mapping, the latest-release tag parse,
the release-asset digest parse, and the pin-file round-trip
(`_write_pin` / `_expected_sha`). The `update` subcommand's git /
worktree / push orchestration is a maintainer path -- exercised manually
and, for the fetch half, by CI's `pandoc install` -- so it is not
unit-tested here (mocking the whole of git would test the mock).
"""

import platform
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import pandoc  # noqa: E402  (import after the src/ path insert above)

_script_path = REPO_ROOT / "src" / "pandoc.py"


class TestAsset:
    """`_asset` maps the host (OS, arch) to a pinned release artifact."""

    def _patch(self, monkeypatch: Any, system: str, machine: str) -> None:
        # `pandoc` imports this same `platform` module, so patching it
        # here changes what `pandoc._asset` sees.
        monkeypatch.setattr(platform, "system", lambda: system)
        monkeypatch.setattr(platform, "machine", lambda: machine)

    def test_linux_amd64(self, monkeypatch: Any) -> None:
        self._patch(monkeypatch, "Linux", "x86_64")
        assert pandoc._asset("3.10") == (
            "pandoc-3.10-linux-amd64.tar.gz",
            "tar",
        )

    def test_linux_arm64(self, monkeypatch: Any) -> None:
        self._patch(monkeypatch, "Linux", "aarch64")
        assert pandoc._asset("3.10") == (
            "pandoc-3.10-linux-arm64.tar.gz",
            "tar",
        )

    def test_darwin_arm64(self, monkeypatch: Any) -> None:
        self._patch(monkeypatch, "Darwin", "arm64")
        assert pandoc._asset("3.10") == (
            "pandoc-3.10-arm64-macOS.zip",
            "zip",
        )

    def test_darwin_x86_64_unsupported(self, monkeypatch: Any) -> None:
        # Intel macOS isn't a target (GitHub's macos runner is arm64).
        self._patch(monkeypatch, "Darwin", "x86_64")
        with pytest.raises(SystemExit):
            pandoc._asset("3.10")

    def test_unsupported_os_raises(self, monkeypatch: Any) -> None:
        self._patch(monkeypatch, "Plan9", "x86_64")
        with pytest.raises(SystemExit):
            pandoc._asset("3.10")

    def test_unsupported_arch_raises(self, monkeypatch: Any) -> None:
        self._patch(monkeypatch, "Linux", "riscv64")
        with pytest.raises(SystemExit):
            pandoc._asset("3.10")

    def test_pinned_asset_names_cover_every_platform(self) -> None:
        # Every platform `_asset` can resolve to must have a pinned name,
        # so the pin file can carry a sha256 for it.
        names = set(pandoc._pinned_asset_names("3.10"))
        for system, machine in [
            ("Linux", "x86_64"),
            ("Linux", "aarch64"),
            ("Darwin", "arm64"),
        ]:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(platform, "system", lambda s=system: s)
                mp.setattr(platform, "machine", lambda m=machine: m)
                assert pandoc._asset("3.10")[0] in names


class TestParseLatestTag:
    """`_parse_latest_tag` reads the release version from the API JSON."""

    def test_reads_tag_name(self) -> None:
        assert pandoc._parse_latest_tag({"tag_name": "3.11"}) == "3.11"

    def test_missing_tag_raises(self) -> None:
        with pytest.raises(SystemExit):
            pandoc._parse_latest_tag({})

    def test_empty_tag_raises(self) -> None:
        with pytest.raises(SystemExit):
            pandoc._parse_latest_tag({"tag_name": ""})


class TestParseAssetDigests:
    """`_parse_asset_digests` extracts sha256 hexes from release JSON."""

    def test_maps_name_to_sha_dropping_prefix(self) -> None:
        payload = {
            "assets": [
                {"name": "a.tar.gz", "digest": "sha256:" + "a" * 64},
                {"name": "b.zip", "digest": "sha256:" + "b" * 64},
            ]
        }
        assert pandoc._parse_asset_digests(payload) == {
            "a.tar.gz": "a" * 64,
            "b.zip": "b" * 64,
        }

    def test_skips_assets_without_sha256_digest(self) -> None:
        payload = {
            "assets": [
                {"name": "a.tar.gz", "digest": "sha256:" + "a" * 64},
                {"name": "no-digest.zip"},
                {"name": "md5.zip", "digest": "md5:deadbeef"},
            ]
        }
        assert pandoc._parse_asset_digests(payload) == {"a.tar.gz": "a" * 64}

    def test_no_assets_is_empty(self) -> None:
        assert pandoc._parse_asset_digests({}) == {}


class TestPinFile:
    """`_write_pin` / `_expected_sha` round-trip the pin file."""

    def test_write_then_read_back(self, tmp_path: Path) -> None:
        (tmp_path / "scripts").mkdir()
        shas = {"pandoc-3.11-arm64-macOS.zip": "f" * 64}
        pandoc._write_pin(tmp_path, "3.11", shas)
        assert (
            pandoc._expected_sha(tmp_path, "pandoc-3.11-arm64-macOS.zip")
            == "f" * 64
        )

    def test_missing_sha_raises(self, tmp_path: Path) -> None:
        (tmp_path / "scripts").mkdir()
        pandoc._write_pin(tmp_path, "3.11", {})
        with pytest.raises(SystemExit):
            pandoc._expected_sha(tmp_path, "absent.zip")


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
