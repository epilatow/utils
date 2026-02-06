#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "lz4"]
# ///
# This is AI generated code

"""
Comprehensive unit tests for firefox-cookies
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import shutil
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import lz4.block  # type: ignore[import-untyped]

import pytest  # type: ignore[import-not-found]

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent

# Import firefox_cookies module from bin/
_script_path = REPO_ROOT / "bin" / "firefox-cookies"
if not _script_path.exists():
    _script_path = REPO_ROOT / "bin" / "firefox-cookies.py"
_loader = importlib.machinery.SourceFileLoader(
    "firefox_cookies", str(_script_path)
)
_spec = importlib.util.spec_from_loader(
    "firefox_cookies", _loader
)
assert _spec and _spec.loader
fc = importlib.util.module_from_spec(_spec)
sys.modules["firefox_cookies"] = fc
_spec.loader.exec_module(fc)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def firefox_dir(tmp_path: Path) -> Path:
    """Create a mock Firefox directory with profiles.ini."""
    ff_dir = tmp_path / "firefox"
    ff_dir.mkdir()

    profile_dir = ff_dir / "Profiles" / "abc123.default"
    profile_dir.mkdir(parents=True)

    ini_content = """\
[Install1234]
Default=Profiles/abc123.default

[Profile0]
Name=default
IsRelative=1
Path=Profiles/abc123.default
Default=1
"""
    (ff_dir / "profiles.ini").write_text(ini_content)
    return ff_dir


@pytest.fixture
def profile_dir(firefox_dir: Path) -> Path:
    """Return the mock profile directory."""
    return firefox_dir / "Profiles" / "abc123.default"


@pytest.fixture
def containers_json(profile_dir: Path) -> Path:
    """Create a mock containers.json."""
    data = {
        "version": 4,
        "lastUserContextId": 3,
        "identities": [
            {
                "userContextId": 1,
                "public": True,
                "icon": "fingerprint",
                "color": "blue",
                "l10nID": "userContextPersonal.label",
                "accessKey": (
                    "userContextPersonal.accesskey"
                ),
            },
            {
                "userContextId": 2,
                "public": True,
                "icon": "briefcase",
                "color": "orange",
                "l10nID": "userContextWork.label",
                "accessKey": (
                    "userContextWork.accesskey"
                ),
            },
            {
                "userContextId": 3,
                "public": True,
                "icon": "circle",
                "color": "green",
                "name": "My Custom",
            },
            {
                "userContextId": 4,
                "public": False,
                "icon": "circle",
                "color": "red",
                "name": "Hidden",
            },
        ],
    }
    path = profile_dir / "containers.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def cookies_db(profile_dir: Path) -> Path:
    """Create a mock cookies.sqlite database."""
    db_path = profile_dir / "cookies.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE moz_cookies (
            id INTEGER PRIMARY KEY,
            baseDomain TEXT,
            originAttributes TEXT NOT NULL
                DEFAULT '',
            name TEXT,
            value TEXT,
            host TEXT,
            path TEXT,
            expiry INTEGER,
            lastAccessed INTEGER,
            creationTime INTEGER,
            isSecure INTEGER,
            isHttpOnly INTEGER,
            inBrowserElement INTEGER DEFAULT 0,
            sameSite INTEGER DEFAULT 0,
            rawSameSite INTEGER DEFAULT 0,
            schemeMap INTEGER DEFAULT 0
        )
        """
    )
    # Insert test cookies
    test_cookies = [
        # (baseDomain, originAttributes, name, value,
        #  host, path, expiry, isSecure, isHttpOnly,
        #  sameSite)
        (
            "example.com", "", "session", "abc123",
            ".example.com", "/", 1700000000, 1, 0, 0,
        ),
        (
            "example.com", "", "pref", "dark",
            "example.com", "/", 1700000000, 0, 0, 0,
        ),
        (
            "other.org", "", "id", "xyz",
            ".other.org", "/", 1700000000, 1, 1, 2,
        ),
        (
            "example.com",
            "^userContextId=1",
            "container_cookie", "val1",
            ".example.com", "/", 1700000000, 0, 0, 0,
        ),
        (
            "test.net",
            "^userContextId=2",
            "work_cookie", "val2",
            ".test.net", "/app", 1700000000, 1, 0, 1,
        ),
        (
            "example.com",
            "^userContextId=2^privateBrowsingId=0",
            "multi_attr", "val3",
            ".example.com", "/", 1700000000, 0, 0, 0,
        ),
    ]
    for row in test_cookies:
        conn.execute(
            "INSERT INTO moz_cookies "
            "(baseDomain, originAttributes, name, value, "
            "host, path, expiry, isSecure, isHttpOnly, "
            "sameSite, lastAccessed, creationTime) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
            row,
        )
    conn.commit()
    conn.close()
    return db_path


def _make_mozlz4(data: dict[str, Any]) -> bytes:
    """Compress a dict as mozlz4 (for test fixtures)."""
    json_bytes = json.dumps(data).encode("utf-8")
    compressed = lz4.block.compress(
        json_bytes, store_size=False
    )
    magic = b"mozLz40\0"
    size_bytes = struct.pack("<I", len(json_bytes))
    return magic + size_bytes + compressed


def _make_cookie(
    host: str = ".example.com",
    name: str = "test",
    value: str = "val",
    path: str = "/",
    origin_attributes: str = "",
) -> fc.Cookie:
    """Create a Cookie with sensible defaults for tests."""
    return fc.Cookie(
        host=host,
        name=name,
        value=value,
        path=path,
        expiry=0,
        is_secure=False,
        is_http_only=False,
        same_site=0,
        origin_attributes=origin_attributes,
    )


@pytest.fixture
def recovery_jsonlz4(profile_dir: Path) -> Path:
    """Create a mock recovery.jsonlz4 file."""
    session_data: dict[str, Any] = {
        "version": ["sessionrestore", 1],
        "windows": [],
        "cookies": [
            {
                "host": ".wordpress.com",
                "name": "wp_session",
                "value": "sess123",
                "path": "/",
                "secure": True,
                "httponly": True,
                "expiry": 0,
                "originAttributes": {},
            },
            {
                "host": ".example.com",
                "name": "session_only",
                "value": "ephemeral",
                "path": "/",
                "secure": False,
                "httponly": False,
                "expiry": 0,
                "originAttributes": {},
            },
            {
                # Overlaps with sqlite cookie for dedup
                "host": ".example.com",
                "name": "session",
                "value": "SHOULD_BE_OVERRIDDEN",
                "path": "/",
                "secure": True,
                "httponly": False,
                "expiry": 0,
                "originAttributes": {},
            },
            {
                "host": ".example.com",
                "name": "container_sess",
                "value": "ctx5val",
                "path": "/",
                "secure": False,
                "httponly": False,
                "expiry": 0,
                "originAttributes": {
                    "userContextId": 5,
                },
            },
        ],
    }
    backups_dir = (
        profile_dir / "sessionstore-backups"
    )
    backups_dir.mkdir(parents=True, exist_ok=True)
    path = backups_dir / "recovery.jsonlz4"
    path.write_bytes(_make_mozlz4(session_data))
    return path


# =============================================================================
# Argument Parser Tests
# =============================================================================


class TestArgumentParser:
    """Test argument parser structure."""

    def test_parser_builds_successfully(self) -> None:
        """Verify parser can be built without errors."""
        parser = fc.build_parser()
        assert parser is not None

    def test_all_subcommands_have_help(self) -> None:
        """Verify subcommands can show help."""
        parser = fc.build_parser()
        for cmd in [
            "list",
            "list-domains",
            "list-profiles",
            "list-containers",
            "self-test",
        ]:
            with pytest.raises(SystemExit):
                parser.parse_args([cmd, "--help"])

    def test_list_parses_all_options(self) -> None:
        """Test list subcommand parses all options."""
        parser = fc.build_parser()
        args = parser.parse_args(
            [
                "list",
                "-p", "myprofile",
                "-d", "example.com",
                "-c", "1",
                "--format", "json",
            ]
        )
        assert args.command == "list"
        assert args.profile == "myprofile"
        assert args.domains == ["example.com"]
        assert args.container == "1"
        assert args.fmt == "json"

    def test_list_defaults(self) -> None:
        """Test list subcommand defaults."""
        parser = fc.build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert args.profile is None
        assert args.domains is None
        assert args.container is None
        assert args.fmt == "netscape"

    def test_list_domains_parses_options(self) -> None:
        """Test list-domains subcommand."""
        parser = fc.build_parser()
        args = parser.parse_args(
            ["list-domains", "-p", "prof", "-c", "2"]
        )
        assert args.command == "list-domains"
        assert args.profile == "prof"
        assert args.container == "2"

    def test_list_profiles_no_extra_args(self) -> None:
        """Test list-profiles has no profile arg."""
        parser = fc.build_parser()
        args = parser.parse_args(["list-profiles"])
        assert args.command == "list-profiles"
        assert not hasattr(args, "profile")

    def test_list_containers_parses_profile(self) -> None:
        """Test list-containers accepts --profile."""
        parser = fc.build_parser()
        args = parser.parse_args(
            ["list-containers", "-p", "myprof"]
        )
        assert args.command == "list-containers"
        assert args.profile == "myprof"

    def test_self_test_parses_options(self) -> None:
        """Test self-test subcommand options."""
        parser = fc.build_parser()
        args = parser.parse_args(
            ["self-test", "-v", "--coverage"]
        )
        assert args.command == "self-test"
        assert args.verbose is True
        assert args.coverage is True

    def test_no_subcommand_errors(self) -> None:
        """Test that no subcommand produces an error."""
        parser = fc.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_invalid_format_errors(self) -> None:
        """Test that invalid format produces an error."""
        parser = fc.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["list", "--format", "xml"]
            )

    def test_multiple_domains(self) -> None:
        """Test -d can be repeated for multiple domains."""
        parser = fc.build_parser()
        args = parser.parse_args(
            [
                "list",
                "-d", "foo.com",
                "-d", "bar.com",
            ]
        )
        assert args.domains == ["foo.com", "bar.com"]

    def test_duplicate_profile_errors(self) -> None:
        """Test -p cannot be specified twice."""
        parser = fc.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["list", "-p", "a", "-p", "b"]
            )

    def test_duplicate_container_errors(self) -> None:
        """Test -c cannot be specified twice."""
        parser = fc.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["list", "-c", "1", "-c", "2"]
            )

    def test_duplicate_format_errors(self) -> None:
        """Test --format cannot be specified twice."""
        parser = fc.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "list",
                    "--format", "json",
                    "--format", "netscape",
                ]
            )


# =============================================================================
# Profile Tests
# =============================================================================


class TestParseProfiles:
    """Test profiles.ini parsing."""

    def test_parse_single_profile(
        self, firefox_dir: Path
    ) -> None:
        """Parse profiles.ini with a single profile."""
        profiles = fc.parse_profiles(firefox_dir)
        assert len(profiles) == 1
        assert profiles[0].name == "default"
        assert profiles[0].is_default is True
        assert profiles[0].path == (
            firefox_dir / "Profiles" / "abc123.default"
        )

    def test_parse_multiple_profiles(
        self, firefox_dir: Path
    ) -> None:
        """Parse profiles.ini with multiple profiles."""
        # Add a second profile
        second = (
            firefox_dir / "Profiles" / "def456.work"
        )
        second.mkdir(parents=True)
        ini = """\
[Install1234]
Default=Profiles/abc123.default

[Profile0]
Name=default
IsRelative=1
Path=Profiles/abc123.default

[Profile1]
Name=work
IsRelative=1
Path=Profiles/def456.work
"""
        (firefox_dir / "profiles.ini").write_text(ini)
        profiles = fc.parse_profiles(firefox_dir)
        assert len(profiles) == 2
        names = {p.name for p in profiles}
        assert names == {"default", "work"}

    def test_install_section_sets_default(
        self, firefox_dir: Path
    ) -> None:
        """Install section Default takes precedence."""
        second = (
            firefox_dir / "Profiles" / "def456.work"
        )
        second.mkdir(parents=True)
        ini = """\
[Install1234]
Default=Profiles/def456.work

[Profile0]
Name=default
IsRelative=1
Path=Profiles/abc123.default
Default=1

[Profile1]
Name=work
IsRelative=1
Path=Profiles/def456.work
"""
        (firefox_dir / "profiles.ini").write_text(ini)
        profiles = fc.parse_profiles(firefox_dir)
        defaults = [p for p in profiles if p.is_default]
        assert len(defaults) == 1
        assert defaults[0].name == "work"

    def test_absolute_path_profile(
        self, firefox_dir: Path, tmp_path: Path
    ) -> None:
        """Profile with IsRelative=0 uses absolute path."""
        abs_profile = tmp_path / "absolute_profile"
        abs_profile.mkdir()
        ini = f"""\
[Profile0]
Name=absolute
IsRelative=0
Path={abs_profile}
Default=1
"""
        (firefox_dir / "profiles.ini").write_text(ini)
        profiles = fc.parse_profiles(firefox_dir)
        assert profiles[0].path == abs_profile

    def test_missing_profiles_ini(
        self, tmp_path: Path
    ) -> None:
        """Raise NotFoundError for missing profiles.ini."""
        ff_dir = tmp_path / "firefox"
        ff_dir.mkdir()
        with pytest.raises(fc.NotFoundError):
            fc.parse_profiles(ff_dir)

    def test_empty_profiles_ini(
        self, firefox_dir: Path
    ) -> None:
        """Raise ConfigError when no profiles found."""
        (firefox_dir / "profiles.ini").write_text(
            "[General]\n"
        )
        with pytest.raises(fc.ConfigError):
            fc.parse_profiles(firefox_dir)


class TestResolveProfile:
    """Test profile resolution."""

    def test_auto_detect_default(
        self, firefox_dir: Path
    ) -> None:
        """Auto-detect the default profile."""
        profile = fc.resolve_profile(firefox_dir)
        assert profile.name == "default"
        assert profile.is_default is True

    def test_by_name(self, firefox_dir: Path) -> None:
        """Resolve profile by name."""
        profile = fc.resolve_profile(
            firefox_dir, "default"
        )
        assert profile.name == "default"

    def test_by_name_case_insensitive(
        self, firefox_dir: Path
    ) -> None:
        """Resolve profile by name case-insensitively."""
        profile = fc.resolve_profile(
            firefox_dir, "DEFAULT"
        )
        assert profile.name == "default"

    def test_by_path(
        self, firefox_dir: Path, profile_dir: Path
    ) -> None:
        """Resolve profile by directory path."""
        profile = fc.resolve_profile(
            firefox_dir, str(profile_dir)
        )
        assert profile.path == profile_dir

    def test_not_found(self, firefox_dir: Path) -> None:
        """Raise ConfigError for unknown profile name."""
        with pytest.raises(fc.ConfigError):
            fc.resolve_profile(
                firefox_dir, "nonexistent"
            )

    def test_no_default_uses_first(
        self, firefox_dir: Path
    ) -> None:
        """Use first profile when no default is marked."""
        ini = """\
[Profile0]
Name=only
IsRelative=1
Path=Profiles/abc123.default
"""
        (firefox_dir / "profiles.ini").write_text(ini)
        profile = fc.resolve_profile(firefox_dir)
        assert profile.name == "only"


# =============================================================================
# Container Tests
# =============================================================================


class TestLoadContainers:
    """Test containers.json loading."""

    def test_load_containers(
        self,
        profile_dir: Path,
        containers_json: Path,
    ) -> None:
        """Load containers from containers.json."""
        containers = fc.load_containers(profile_dir)
        assert len(containers) == 3  # Hidden excluded
        assert containers[0].id == 1
        assert containers[0].name == "Personal"
        assert containers[1].id == 2
        assert containers[1].name == "Work"
        assert containers[2].id == 3
        assert containers[2].name == "My Custom"

    def test_no_containers_file(
        self, profile_dir: Path
    ) -> None:
        """Return empty list when no containers.json."""
        containers = fc.load_containers(profile_dir)
        assert containers == []

    def test_builtin_container_names(
        self,
        profile_dir: Path,
        containers_json: Path,
    ) -> None:
        """Built-in containers derive name from l10nID."""
        containers = fc.load_containers(profile_dir)
        names = {c.name for c in containers}
        assert "Personal" in names
        assert "Work" in names

    def test_custom_container_names(
        self,
        profile_dir: Path,
        containers_json: Path,
    ) -> None:
        """Custom containers use name field directly."""
        containers = fc.load_containers(profile_dir)
        custom = [c for c in containers if c.id == 3]
        assert len(custom) == 1
        assert custom[0].name == "My Custom"

    def test_hidden_containers_excluded(
        self,
        profile_dir: Path,
        containers_json: Path,
    ) -> None:
        """Containers with public=false are excluded."""
        containers = fc.load_containers(profile_dir)
        ids = {c.id for c in containers}
        assert 4 not in ids


class TestResolveContainer:
    """Test container resolution."""

    @pytest.fixture
    def containers(self) -> list[Any]:
        """Create test containers."""
        return [
            fc.Container(
                id=1,
                name="Personal",
                icon="fingerprint",
                color="blue",
            ),
            fc.Container(
                id=2,
                name="Work",
                icon="briefcase",
                color="orange",
            ),
            fc.Container(
                id=3,
                name="Work Extra",
                icon="circle",
                color="green",
            ),
        ]

    def test_by_id(
        self, containers: list[Any]
    ) -> None:
        """Resolve container by numeric ID."""
        result = fc.resolve_container(containers, "1")
        assert result.id == 1
        assert result.name == "Personal"

    def test_by_exact_name(
        self, containers: list[Any]
    ) -> None:
        """Resolve container by exact name."""
        result = fc.resolve_container(
            containers, "Personal"
        )
        assert result.id == 1

    def test_by_name_case_insensitive(
        self, containers: list[Any]
    ) -> None:
        """Resolve container by name, case-insensitive."""
        result = fc.resolve_container(
            containers, "personal"
        )
        assert result.id == 1

    def test_partial_match(
        self, containers: list[Any]
    ) -> None:
        """Resolve container by unique partial match."""
        result = fc.resolve_container(
            containers, "Person"
        )
        assert result.id == 1

    def test_ambiguous_name(
        self, containers: list[Any]
    ) -> None:
        """Error on ambiguous partial container name."""
        with pytest.raises(fc.ConfigError, match="Ambig"):
            fc.resolve_container(containers, "Wor")

    def test_id_not_found(
        self, containers: list[Any]
    ) -> None:
        """Error when container ID doesn't exist."""
        with pytest.raises(fc.ConfigError):
            fc.resolve_container(containers, "99")

    def test_name_not_found(
        self, containers: list[Any]
    ) -> None:
        """Error when container name doesn't match."""
        with pytest.raises(fc.ConfigError):
            fc.resolve_container(
                containers, "Nonexistent"
            )


# =============================================================================
# Cookie Query Tests
# =============================================================================


class TestGetUserContextId:
    """Test userContextId extraction."""

    def test_empty_attributes(self) -> None:
        """Empty string returns 0 (default context)."""
        assert fc.get_user_context_id("") == 0

    def test_single_attribute(self) -> None:
        """Parse single userContextId attribute."""
        assert (
            fc.get_user_context_id("^userContextId=5")
            == 5
        )

    def test_multiple_attributes(self) -> None:
        """Parse userContextId with other attributes."""
        attrs = "^userContextId=3^privateBrowsingId=0"
        assert fc.get_user_context_id(attrs) == 3

    def test_with_partition_key(self) -> None:
        """Parse userContextId with &partitionKey."""
        attrs = (
            "^userContextId=1324"
            "&partitionKey=%28https%2Cexample.com%29"
        )
        assert fc.get_user_context_id(attrs) == 1324

    def test_no_context_id(self) -> None:
        """Return 0 when userContextId not present."""
        assert (
            fc.get_user_context_id(
                "^privateBrowsingId=1"
            )
            == 0
        )


class TestQueryCookies:
    """Test cookie database querying."""

    def test_query_all(self, cookies_db: Path) -> None:
        """Query all cookies without filters."""
        cookies = fc.query_cookies(cookies_db)
        assert len(cookies) == 6

    def test_filter_by_domain(
        self, cookies_db: Path
    ) -> None:
        """Filter cookies by domain."""
        cookies = fc.query_cookies(
            cookies_db, domains=["example.com"]
        )
        # Should match .example.com and example.com
        hosts = {c.host for c in cookies}
        assert ".example.com" in hosts
        assert "example.com" in hosts
        assert ".other.org" not in hosts

    def test_filter_by_container(
        self, cookies_db: Path
    ) -> None:
        """Filter cookies by container ID."""
        cookies = fc.query_cookies(
            cookies_db, container_id=1
        )
        assert len(cookies) == 1
        assert cookies[0].name == "container_cookie"

    def test_filter_by_container_2(
        self, cookies_db: Path
    ) -> None:
        """Filter cookies by container 2."""
        cookies = fc.query_cookies(
            cookies_db, container_id=2
        )
        assert len(cookies) == 2
        names = {c.name for c in cookies}
        assert "work_cookie" in names
        assert "multi_attr" in names

    def test_filter_multiple_domains(
        self, cookies_db: Path
    ) -> None:
        """Filter cookies by multiple domains."""
        cookies = fc.query_cookies(
            cookies_db,
            domains=["example.com", "other.org"],
        )
        hosts = {c.host for c in cookies}
        assert ".example.com" in hosts
        assert ".other.org" in hosts
        assert ".test.net" not in hosts

    def test_filter_domain_and_container(
        self, cookies_db: Path
    ) -> None:
        """Filter by both domain and container."""
        cookies = fc.query_cookies(
            cookies_db,
            domains=["example.com"],
            container_id=2,
        )
        assert len(cookies) == 1
        assert cookies[0].name == "multi_attr"

    def test_default_context_cookies(
        self, cookies_db: Path
    ) -> None:
        """Filter to default context (no container)."""
        cookies = fc.query_cookies(
            cookies_db, container_id=0
        )
        assert len(cookies) == 3
        for c in cookies:
            assert (
                fc.get_user_context_id(
                    c.origin_attributes
                )
                == 0
            )

    def test_cookie_fields(
        self, cookies_db: Path
    ) -> None:
        """Verify cookie field mapping."""
        cookies = fc.query_cookies(
            cookies_db, domains=["other.org"]
        )
        assert len(cookies) == 1
        c = cookies[0]
        assert c.host == ".other.org"
        assert c.name == "id"
        assert c.value == "xyz"
        assert c.path == "/"
        assert c.expiry == 1700000000
        assert c.is_secure is True
        assert c.is_http_only is True
        assert c.same_site == 2

    def test_sorted_output(
        self, cookies_db: Path
    ) -> None:
        """Verify cookies are sorted by host, name."""
        cookies = fc.query_cookies(cookies_db)
        hosts_names = [
            (c.host, c.name) for c in cookies
        ]
        assert hosts_names == sorted(hosts_names)


# =============================================================================
# Format Tests
# =============================================================================


class TestFormatNetscape:
    """Test Netscape cookie format output."""

    def test_header(self) -> None:
        """Output includes Netscape header."""
        output = fc.format_netscape([])
        assert output.startswith(
            "# Netscape HTTP Cookie File"
        )

    def test_cookie_format(self) -> None:
        """Verify tab-separated Netscape format."""
        cookie = fc.Cookie(
            host=".example.com",
            name="session",
            value="abc",
            path="/",
            expiry=1700000000,
            is_secure=True,
            is_http_only=False,
            same_site=0,
            origin_attributes="",
        )
        output = fc.format_netscape([cookie])
        lines = output.strip().split("\n")
        # Last line should be the cookie
        parts = lines[-1].split("\t")
        assert parts[0] == ".example.com"
        assert parts[1] == "TRUE"  # subdomain flag
        assert parts[2] == "/"
        assert parts[3] == "TRUE"  # secure
        assert parts[4] == "1700000000"
        assert parts[5] == "session"
        assert parts[6] == "abc"

    def test_non_subdomain_cookie(self) -> None:
        """Non-dotted host has FALSE subdomain flag."""
        cookie = fc.Cookie(
            host="example.com",
            name="test",
            value="val",
            path="/",
            expiry=0,
            is_secure=False,
            is_http_only=False,
            same_site=0,
            origin_attributes="",
        )
        output = fc.format_netscape([cookie])
        lines = output.strip().split("\n")
        parts = lines[-1].split("\t")
        assert parts[0] == "example.com"
        assert parts[1] == "FALSE"
        assert parts[3] == "FALSE"


class TestFormatJson:
    """Test JSON cookie format output."""

    def test_empty_list(self) -> None:
        """Empty cookie list produces empty JSON array."""
        output = fc.format_json([])
        assert json.loads(output) == []

    def test_cookie_fields(self) -> None:
        """Verify JSON output contains expected fields."""
        cookie = fc.Cookie(
            host=".example.com",
            name="session",
            value="abc",
            path="/",
            expiry=1700000000,
            is_secure=True,
            is_http_only=True,
            same_site=2,
            origin_attributes="",
        )
        output = fc.format_json([cookie])
        data = json.loads(output)
        assert len(data) == 1
        c = data[0]
        assert c["host"] == ".example.com"
        assert c["name"] == "session"
        assert c["value"] == "abc"
        assert c["path"] == "/"
        assert c["expiry"] == 1700000000
        assert c["secure"] is True
        assert c["httpOnly"] is True
        assert c["sameSite"] == 2

    def test_no_origin_attributes_in_json(self) -> None:
        """origin_attributes should not appear in JSON."""
        cookie = fc.Cookie(
            host="x.com",
            name="n",
            value="v",
            path="/",
            expiry=0,
            is_secure=False,
            is_http_only=False,
            same_site=0,
            origin_attributes="^userContextId=1",
        )
        output = fc.format_json([cookie])
        data = json.loads(output)
        assert "origin_attributes" not in data[0]
        assert "originAttributes" not in data[0]


# =============================================================================
# Safe DB Copy Tests
# =============================================================================


class TestSafeCopyDb:
    """Test safe database copy."""

    def test_copies_db(
        self, cookies_db: Path, profile_dir: Path
    ) -> None:
        """Database file is copied to temp location."""
        tmp_db = fc.safe_copy_db(profile_dir)
        try:
            assert tmp_db.is_file()
            assert tmp_db.name == "cookies.sqlite"
            assert tmp_db.parent != profile_dir
        finally:
            shutil.rmtree(
                tmp_db.parent, ignore_errors=True
            )

    def test_copies_wal_files(
        self, cookies_db: Path, profile_dir: Path
    ) -> None:
        """WAL and SHM files are copied if present."""
        # Create mock WAL/SHM files
        (profile_dir / "cookies.sqlite-wal").write_text(
            "wal"
        )
        (profile_dir / "cookies.sqlite-shm").write_text(
            "shm"
        )
        tmp_db = fc.safe_copy_db(profile_dir)
        try:
            assert (
                tmp_db.parent / "cookies.sqlite-wal"
            ).is_file()
            assert (
                tmp_db.parent / "cookies.sqlite-shm"
            ).is_file()
        finally:
            shutil.rmtree(
                tmp_db.parent, ignore_errors=True
            )

    def test_missing_db(self, tmp_path: Path) -> None:
        """Raise NotFoundError when db doesn't exist."""
        with pytest.raises(fc.NotFoundError):
            fc.safe_copy_db(tmp_path)


# =============================================================================
# Cross-Platform Tests
# =============================================================================


class TestFindFirefoxDir:
    """Test platform-aware Firefox directory detection."""

    def test_darwin(self, tmp_path: Path) -> None:
        """Find Firefox dir on macOS."""
        ff_dir = (
            tmp_path
            / "Library"
            / "Application Support"
            / "Firefox"
        )
        ff_dir.mkdir(parents=True)
        with (
            patch.object(
                fc.platform,
                "system",
                autospec=True,
                return_value="Darwin",
            ),
            patch.object(
                fc.Path,
                "home",
                autospec=True,
                return_value=tmp_path,
            ),
        ):
            result = fc.find_firefox_dir()
            assert result == ff_dir

    def test_linux(self, tmp_path: Path) -> None:
        """Find Firefox dir on Linux."""
        ff_dir = tmp_path / ".mozilla" / "firefox"
        ff_dir.mkdir(parents=True)
        with (
            patch.object(
                fc.platform,
                "system",
                autospec=True,
                return_value="Linux",
            ),
            patch.object(
                fc.Path,
                "home",
                autospec=True,
                return_value=tmp_path,
            ),
        ):
            result = fc.find_firefox_dir()
            assert result == ff_dir

    def test_unsupported_platform(self) -> None:
        """Error on unsupported platform."""
        with (
            patch.object(
                fc.platform,
                "system",
                autospec=True,
                return_value="Windows",
            ),
            pytest.raises(fc.ConfigError),
        ):
            fc.find_firefox_dir()

    def test_missing_directory(
        self, tmp_path: Path
    ) -> None:
        """Error when Firefox directory doesn't exist."""
        with (
            patch.object(
                fc.platform,
                "system",
                autospec=True,
                return_value="Darwin",
            ),
            patch.object(
                fc.Path,
                "home",
                autospec=True,
                return_value=tmp_path,
            ),
            pytest.raises(fc.NotFoundError),
        ):
            fc.find_firefox_dir()


# =============================================================================
# Subcommand Integration Tests
# =============================================================================


class TestDoListProfiles:
    """Test list-profiles subcommand."""

    def test_lists_profiles(
        self,
        firefox_dir: Path,
        capsys: Any,
    ) -> None:
        """List profiles with default marker."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list_profiles()
        assert result == 0
        captured = capsys.readouterr()
        assert "default" in captured.out
        assert "(default)" in captured.out


class TestDoListContainers:
    """Test list-containers subcommand."""

    def test_lists_containers(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        containers_json: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """List containers with cookie counts."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list_containers(profile=None)
        assert result == 0
        captured = capsys.readouterr()
        assert "Personal" in captured.out
        assert "Work" in captured.out
        assert "My Custom" in captured.out

    def test_no_containers(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
    ) -> None:
        """Handle no containers gracefully."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list_containers(profile=None)
        assert result == 0


class TestDoListDomains:
    """Test list-domains subcommand."""

    def test_lists_domains(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """List domains with counts."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list_domains(
                profile=None, container=None
            )
        assert result == 0
        captured = capsys.readouterr()
        assert "example.com" in captured.out
        assert "other.org" in captured.out

    def test_includes_container_id_column(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """Output includes container ID column."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            fc.do_list_domains(
                profile=None, container=None
            )
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        # Each line should have 3 columns
        for line in lines:
            parts = line.split()
            assert len(parts) == 3, (
                f"Expected 3 columns: {line!r}"
            )
            # First col is count, second is container ID
            assert parts[0].isdigit()
            assert parts[1].isdigit()

    def test_different_containers_separate_rows(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """Same domain in different containers = separate rows."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            fc.do_list_domains(
                profile=None, container=None
            )
        captured = capsys.readouterr()
        # example.com appears in default (0), container
        # 1, and container 2
        example_lines = [
            line
            for line in captured.out.strip().split("\n")
            if "example.com" in line
        ]
        assert len(example_lines) == 3

    def test_filter_by_container(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        containers_json: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """List domains filtered by container."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list_domains(
                profile=None, container="1"
            )
        assert result == 0
        captured = capsys.readouterr()
        assert "example.com" in captured.out
        assert "other.org" not in captured.out


class TestDoList:
    """Test list subcommand."""

    def test_list_netscape(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """List cookies in Netscape format."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list(
                profile=None,
                domains=None,
                container=None,
                fmt="netscape",
            )
        assert result == 0
        captured = capsys.readouterr()
        assert "# Netscape HTTP Cookie File" in captured.out
        assert ".example.com" in captured.out

    def test_list_json(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """List cookies in JSON format."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list(
                profile=None,
                domains=None,
                container=None,
                fmt="json",
            )
        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 6

    def test_list_filter_domain(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """List cookies filtered by domain."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list(
                profile=None,
                domains=["other.org"],
                container=None,
                fmt="json",
            )
        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["host"] == ".other.org"

    def test_list_filter_container(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        containers_json: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """List cookies filtered by container."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list(
                profile=None,
                domains=None,
                container="Personal",
                fmt="json",
            )
        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["name"] == "container_cookie"


# =============================================================================
# CLI Tests
# =============================================================================


class TestMain:
    """Test main function and command dispatch."""

    @patch.object(fc, "do_list", autospec=True)
    def test_main_list_command(
        self, mock_do_list: MagicMock
    ) -> None:
        """Test main dispatches to do_list."""
        mock_do_list.return_value = 0
        args = argparse.Namespace(
            command="list",
            profile="myprofile",
            domains=["example.com"],
            container="1",
            fmt="json",
            sources=["db"],
        )

        result = fc.main(args)

        assert result == 0
        mock_do_list.assert_called_once_with(
            profile="myprofile",
            domains=["example.com"],
            container="1",
            fmt="json",
            sources=["db"],
        )

    @patch.object(fc, "do_list", autospec=True)
    def test_main_list_defaults(
        self, mock_do_list: MagicMock
    ) -> None:
        """Test main passes None defaults for list."""
        mock_do_list.return_value = 0
        args = argparse.Namespace(
            command="list",
            profile=None,
            domains=None,
            container=None,
            fmt="netscape",
            sources=None,
        )

        result = fc.main(args)

        assert result == 0
        mock_do_list.assert_called_once_with(
            profile=None,
            domains=None,
            container=None,
            fmt="netscape",
            sources=None,
        )

    @patch.object(
        fc, "do_list_domains", autospec=True
    )
    def test_main_list_domains_command(
        self, mock_do_list_domains: MagicMock
    ) -> None:
        """Test main dispatches to do_list_domains."""
        mock_do_list_domains.return_value = 0
        args = argparse.Namespace(
            command="list-domains",
            profile="prof",
            container="2",
            sources=["recovery"],
        )

        result = fc.main(args)

        assert result == 0
        mock_do_list_domains.assert_called_once_with(
            profile="prof",
            container="2",
            sources=["recovery"],
        )

    @patch.object(
        fc, "do_list_profiles", autospec=True
    )
    def test_main_list_profiles_command(
        self, mock_do_list_profiles: MagicMock
    ) -> None:
        """Test main dispatches to do_list_profiles."""
        mock_do_list_profiles.return_value = 0
        args = argparse.Namespace(
            command="list-profiles",
        )

        result = fc.main(args)

        assert result == 0
        mock_do_list_profiles.assert_called_once()

    @patch.object(
        fc, "do_list_containers", autospec=True
    )
    def test_main_list_containers_command(
        self, mock_do_list_containers: MagicMock
    ) -> None:
        """Test main dispatches to do_list_containers."""
        mock_do_list_containers.return_value = 0
        args = argparse.Namespace(
            command="list-containers",
            profile="myprof",
            sources=["db", "recovery"],
        )

        result = fc.main(args)

        assert result == 0
        mock_do_list_containers.assert_called_once_with(
            profile="myprof",
            sources=["db", "recovery"],
        )

    @patch.object(fc, "do_self_test", autospec=True)
    def test_main_self_test_command(
        self, mock_do_self_test: MagicMock
    ) -> None:
        """Test main dispatches to do_self_test."""
        args = argparse.Namespace(
            command="self-test",
            verbose=True,
            coverage=False,
        )

        result = fc.main(args)

        assert result == fc.EXIT_SUCCESS
        mock_do_self_test.assert_called_once_with(
            verbose=True,
            coverage=False,
        )

    @patch.object(fc, "do_self_test", autospec=True)
    def test_main_self_test_defaults(
        self, mock_do_self_test: MagicMock
    ) -> None:
        """Test main passes default self-test args."""
        args = argparse.Namespace(
            command="self-test",
            verbose=False,
            coverage=False,
        )

        result = fc.main(args)

        assert result == fc.EXIT_SUCCESS
        mock_do_self_test.assert_called_once_with(
            verbose=False,
            coverage=False,
        )

    def test_main_unknown_command(self) -> None:
        """Unknown command raises UsageError."""
        args = argparse.Namespace(command="bogus")
        with pytest.raises(
            fc.UsageError, match="Unknown command"
        ):
            fc.main(args)


class TestCli:
    """Test CLI entry point."""

    def test_no_args_returns_usage_error(self) -> None:
        """No arguments returns EXIT_USAGE."""
        with patch(
            "sys.argv", ["firefox-cookies"]
        ):
            result = fc.cli()
        assert result == fc.EXIT_USAGE

    def test_help_returns_success(self) -> None:
        """--help returns EXIT_SUCCESS."""
        with patch(
            "sys.argv", ["firefox-cookies", "--help"]
        ):
            result = fc.cli()
        assert result == fc.EXIT_SUCCESS

    def test_config_error_returns_config_code(
        self,
    ) -> None:
        """ConfigError maps to EXIT_CONFIG."""
        with (
            patch(
                "sys.argv",
                ["firefox-cookies", "list-profiles"],
            ),
            patch.object(
                fc,
                "find_firefox_dir",
                autospec=True,
                side_effect=fc.ConfigError("bad"),
            ),
        ):
            result = fc.cli()
        assert result == fc.EXIT_CONFIG

    def test_not_found_returns_not_found_code(
        self,
    ) -> None:
        """NotFoundError maps to EXIT_NOT_FOUND."""
        with (
            patch(
                "sys.argv",
                ["firefox-cookies", "list-profiles"],
            ),
            patch.object(
                fc,
                "find_firefox_dir",
                autospec=True,
                side_effect=fc.NotFoundError("missing"),
            ),
        ):
            result = fc.cli()
        assert result == fc.EXIT_NOT_FOUND

    def test_usage_error_returns_usage_code(
        self,
    ) -> None:
        """UsageError maps to EXIT_USAGE."""
        with (
            patch(
                "sys.argv",
                ["firefox-cookies", "list-profiles"],
            ),
            patch.object(
                fc,
                "main",
                autospec=True,
                side_effect=fc.UsageError("bad"),
            ),
        ):
            result = fc.cli()
        assert result == fc.EXIT_USAGE

    def test_test_error_returns_subprocess_code(
        self,
    ) -> None:
        """TestError maps to EXIT_SUBPROCESS."""
        with (
            patch(
                "sys.argv",
                ["firefox-cookies", "self-test"],
            ),
            patch.object(
                fc,
                "main",
                autospec=True,
                side_effect=fc.TestError("failed"),
            ),
        ):
            result = fc.cli()
        assert result == fc.EXIT_SUBPROCESS

    def test_generic_error_returns_other_code(
        self,
    ) -> None:
        """Generic Exception maps to EXIT_OTHER."""
        with (
            patch(
                "sys.argv",
                ["firefox-cookies", "list-profiles"],
            ),
            patch.object(
                fc,
                "main",
                autospec=True,
                side_effect=RuntimeError("unexpected"),
            ),
        ):
            result = fc.cli()
        assert result == fc.EXIT_OTHER

    @patch.object(fc, "main", autospec=True)
    def test_cli_list_passes_args(
        self, mock_main: MagicMock
    ) -> None:
        """CLI passes parsed list args to main."""
        mock_main.return_value = 0
        with patch(
            "sys.argv",
            [
                "firefox-cookies", "list",
                "-p", "myprof",
                "-d", "example.com",
                "-c", "1",
                "--format", "json",
                "--source", "db",
            ],
        ):
            result = fc.cli()
        assert result == 0
        args = mock_main.call_args[0][0]
        assert args.command == "list"
        assert args.profile == "myprof"
        assert args.domains == ["example.com"]
        assert args.container == "1"
        assert args.fmt == "json"
        assert args.sources == ["db"]

    @patch.object(fc, "main", autospec=True)
    def test_cli_list_domains_passes_args(
        self, mock_main: MagicMock
    ) -> None:
        """CLI passes parsed list-domains args."""
        mock_main.return_value = 0
        with patch(
            "sys.argv",
            [
                "firefox-cookies", "list-domains",
                "-p", "prof",
                "-c", "Work",
                "--source", "recovery",
            ],
        ):
            result = fc.cli()
        assert result == 0
        args = mock_main.call_args[0][0]
        assert args.command == "list-domains"
        assert args.profile == "prof"
        assert args.container == "Work"
        assert args.sources == ["recovery"]

    @patch.object(fc, "main", autospec=True)
    def test_cli_list_profiles_passes_args(
        self, mock_main: MagicMock
    ) -> None:
        """CLI passes parsed list-profiles args."""
        mock_main.return_value = 0
        with patch(
            "sys.argv",
            ["firefox-cookies", "list-profiles"],
        ):
            result = fc.cli()
        assert result == 0
        args = mock_main.call_args[0][0]
        assert args.command == "list-profiles"

    @patch.object(fc, "main", autospec=True)
    def test_cli_list_containers_passes_args(
        self, mock_main: MagicMock
    ) -> None:
        """CLI passes parsed list-containers args."""
        mock_main.return_value = 0
        with patch(
            "sys.argv",
            [
                "firefox-cookies", "list-containers",
                "-p", "myprof",
                "--source", "db",
            ],
        ):
            result = fc.cli()
        assert result == 0
        args = mock_main.call_args[0][0]
        assert args.command == "list-containers"
        assert args.profile == "myprof"
        assert args.sources == ["db"]

    @patch.object(fc, "main", autospec=True)
    def test_cli_self_test_passes_args(
        self, mock_main: MagicMock
    ) -> None:
        """CLI passes parsed self-test args."""
        mock_main.return_value = 0
        with patch(
            "sys.argv",
            [
                "firefox-cookies", "self-test",
                "-v", "--coverage",
            ],
        ):
            result = fc.cli()
        assert result == 0
        args = mock_main.call_args[0][0]
        assert args.command == "self-test"
        assert args.verbose is True
        assert args.coverage is True


# =============================================================================
# Origin Attributes Conversion Tests
# =============================================================================


class TestOriginAttributesFromDict:
    """Test dict-to-string origin attributes conversion."""

    def test_empty_dict(self) -> None:
        """Empty dict returns empty string."""
        assert fc.origin_attributes_from_dict({}) == ""

    def test_single_user_context_id(self) -> None:
        """Convert single userContextId."""
        result = fc.origin_attributes_from_dict(
            {"userContextId": 5}
        )
        assert result == "^userContextId=5"

    def test_zero_value_omitted(self) -> None:
        """Zero values are omitted (default context)."""
        result = fc.origin_attributes_from_dict(
            {"userContextId": 0}
        )
        assert result == ""

    def test_multiple_attributes(self) -> None:
        """Multiple attributes joined with ^."""
        result = fc.origin_attributes_from_dict(
            {"privateBrowsingId": 1, "userContextId": 3}
        )
        assert (
            result
            == "^privateBrowsingId=1^userContextId=3"
        )

    def test_empty_string_value_omitted(self) -> None:
        """Empty string values are omitted."""
        result = fc.origin_attributes_from_dict(
            {"userContextId": 5, "firstPartyDomain": ""}
        )
        assert result == "^userContextId=5"

    def test_roundtrip_with_get_user_context_id(
        self,
    ) -> None:
        """Converted string works with get_user_context_id."""
        s = fc.origin_attributes_from_dict(
            {"userContextId": 42}
        )
        assert fc.get_user_context_id(s) == 42


# =============================================================================
# Mozilla LZ4 Decompression Tests
# =============================================================================


class TestDecompressMozlz4:
    """Test Mozilla LZ4 decompression."""

    def test_decompress_valid(self) -> None:
        """Decompress a valid mozlz4 payload."""
        original = b'{"test": true}'
        data = _make_mozlz4({"test": True})
        result = fc.decompress_mozlz4(data)
        assert json.loads(result) == {"test": True}

    def test_invalid_magic(self) -> None:
        """Raise ValueError for wrong magic header."""
        with pytest.raises(ValueError, match="magic"):
            fc.decompress_mozlz4(b"notmozlz4data")

    def test_empty_after_magic(self) -> None:
        """Handle truncated data after magic."""
        with pytest.raises(Exception):
            fc.decompress_mozlz4(b"mozLz40\0")


# =============================================================================
# Session Cookie Loading Tests
# =============================================================================


class TestLoadSessionCookies:
    """Test session cookie loading from recovery.jsonlz4."""

    def test_load_all(
        self,
        profile_dir: Path,
        recovery_jsonlz4: Path,
    ) -> None:
        """Load all session cookies."""
        cookies = fc.load_session_cookies(profile_dir)
        assert len(cookies) == 4
        names = {c.name for c in cookies}
        assert "wp_session" in names
        assert "session_only" in names
        assert "container_sess" in names

    def test_filter_by_domain(
        self,
        profile_dir: Path,
        recovery_jsonlz4: Path,
    ) -> None:
        """Filter session cookies by domain."""
        cookies = fc.load_session_cookies(
            profile_dir, domains=["wordpress.com"]
        )
        assert len(cookies) == 1
        assert cookies[0].name == "wp_session"

    def test_filter_by_container(
        self,
        profile_dir: Path,
        recovery_jsonlz4: Path,
    ) -> None:
        """Filter session cookies by container ID."""
        cookies = fc.load_session_cookies(
            profile_dir, container_id=5
        )
        assert len(cookies) == 1
        assert cookies[0].name == "container_sess"
        assert (
            cookies[0].origin_attributes
            == "^userContextId=5"
        )

    def test_filter_default_container(
        self,
        profile_dir: Path,
        recovery_jsonlz4: Path,
    ) -> None:
        """Filter to default container (id=0)."""
        cookies = fc.load_session_cookies(
            profile_dir, container_id=0
        )
        assert len(cookies) == 3
        for c in cookies:
            assert (
                fc.get_user_context_id(
                    c.origin_attributes
                )
                == 0
            )

    def test_missing_recovery_file(
        self, profile_dir: Path
    ) -> None:
        """Return empty list if file doesn't exist."""
        cookies = fc.load_session_cookies(profile_dir)
        assert cookies == []

    def test_sorted_output(
        self,
        profile_dir: Path,
        recovery_jsonlz4: Path,
    ) -> None:
        """Session cookies are sorted by host, name."""
        cookies = fc.load_session_cookies(profile_dir)
        pairs = [(c.host, c.name) for c in cookies]
        assert pairs == sorted(pairs)

    def test_cookie_fields(
        self,
        profile_dir: Path,
        recovery_jsonlz4: Path,
    ) -> None:
        """Verify cookie field mapping from JSON."""
        cookies = fc.load_session_cookies(
            profile_dir, domains=["wordpress.com"]
        )
        c = cookies[0]
        assert c.host == ".wordpress.com"
        assert c.name == "wp_session"
        assert c.value == "sess123"
        assert c.path == "/"
        assert c.expiry == 0
        assert c.is_secure is True
        assert c.is_http_only is True
        assert c.same_site == 0
        assert c.origin_attributes == ""

    def test_origin_attributes_dict_converted(
        self,
        profile_dir: Path,
        recovery_jsonlz4: Path,
    ) -> None:
        """Dict originAttributes converted to string."""
        cookies = fc.load_session_cookies(
            profile_dir, container_id=5
        )
        assert (
            cookies[0].origin_attributes
            == "^userContextId=5"
        )

    def test_corrupt_file_returns_empty(
        self, profile_dir: Path
    ) -> None:
        """Return empty list for corrupt file."""
        backups = (
            profile_dir / "sessionstore-backups"
        )
        backups.mkdir(parents=True, exist_ok=True)
        (backups / "recovery.jsonlz4").write_bytes(
            b"garbage data not mozlz4"
        )
        cookies = fc.load_session_cookies(profile_dir)
        assert cookies == []


# =============================================================================
# Cookie Merge Tests
# =============================================================================


class TestMergeCookies:
    """Test cookie merging and deduplication."""

    def test_no_overlap(self) -> None:
        """Merge disjoint cookie sets."""
        sqlite = [_make_cookie(name="a")]
        session = [_make_cookie(name="b")]
        merged = fc.merge_cookies(sqlite, session)
        assert len(merged) == 2

    def test_sqlite_takes_precedence(self) -> None:
        """Sqlite cookie wins on duplicate key."""
        sqlite = [
            _make_cookie(
                name="x", value="from_sqlite"
            )
        ]
        session = [
            _make_cookie(
                name="x", value="from_session"
            )
        ]
        merged = fc.merge_cookies(sqlite, session)
        assert len(merged) == 1
        assert merged[0].value == "from_sqlite"

    def test_different_containers_not_deduped(
        self,
    ) -> None:
        """Same name/host in different containers kept."""
        sqlite = [
            _make_cookie(
                name="x", origin_attributes="",
            )
        ]
        session = [
            _make_cookie(
                name="x",
                origin_attributes="^userContextId=5",
            )
        ]
        merged = fc.merge_cookies(sqlite, session)
        assert len(merged) == 2

    def test_different_paths_not_deduped(self) -> None:
        """Same name/host with different paths kept."""
        sqlite = [
            _make_cookie(name="x", path="/a")
        ]
        session = [
            _make_cookie(name="x", path="/b")
        ]
        merged = fc.merge_cookies(sqlite, session)
        assert len(merged) == 2

    def test_empty_session_cookies(self) -> None:
        """Merging with empty session list."""
        sqlite = [_make_cookie(name="a")]
        merged = fc.merge_cookies(sqlite, [])
        assert len(merged) == 1

    def test_empty_sqlite_cookies(self) -> None:
        """Merging with empty sqlite list."""
        session = [_make_cookie(name="a")]
        merged = fc.merge_cookies([], session)
        assert len(merged) == 1

    def test_sorted_output(self) -> None:
        """Merged result is sorted by host, name."""
        sqlite = [_make_cookie(host=".z.com")]
        session = [_make_cookie(host=".a.com")]
        merged = fc.merge_cookies(sqlite, session)
        assert merged[0].host == ".a.com"
        assert merged[1].host == ".z.com"


# =============================================================================
# Container Conflict Dedup Tests
# =============================================================================


class TestDedupContainerConflicts:
    """Test container conflict deduplication."""

    def test_no_conflicts(self) -> None:
        """No conflicts when all cookies are unique."""
        cookies = [
            _make_cookie(name="a"),
            _make_cookie(name="b"),
        ]
        deduped, conflicts = (
            fc.dedup_container_conflicts(cookies)
        )
        assert len(deduped) == 2
        assert conflicts == []

    def test_conflict_picks_default_context(
        self,
    ) -> None:
        """Default context (id=0) wins conflicts."""
        cookies = [
            _make_cookie(
                name="x",
                origin_attributes="^userContextId=5",
                value="from_5",
            ),
            _make_cookie(
                name="x",
                origin_attributes="",
                value="from_default",
            ),
        ]
        deduped, conflicts = (
            fc.dedup_container_conflicts(cookies)
        )
        assert len(deduped) == 1
        assert deduped[0].value == "from_default"
        assert len(conflicts) == 1
        assert conflicts[0].kept_container_id == 0
        assert conflicts[0].omitted_container_ids == [5]

    def test_conflict_picks_lowest_id_no_default(
        self,
    ) -> None:
        """Lowest container ID wins when no default."""
        cookies = [
            _make_cookie(
                name="x",
                origin_attributes="^userContextId=10",
                value="from_10",
            ),
            _make_cookie(
                name="x",
                origin_attributes="^userContextId=3",
                value="from_3",
            ),
        ]
        deduped, conflicts = (
            fc.dedup_container_conflicts(cookies)
        )
        assert len(deduped) == 1
        assert deduped[0].value == "from_3"
        assert conflicts[0].kept_container_id == 3
        assert conflicts[0].omitted_container_ids == [10]

    def test_different_paths_not_conflicting(
        self,
    ) -> None:
        """Same name/host but different paths: no conflict."""
        cookies = [
            _make_cookie(name="x", path="/a"),
            _make_cookie(name="x", path="/b"),
        ]
        deduped, conflicts = (
            fc.dedup_container_conflicts(cookies)
        )
        assert len(deduped) == 2
        assert conflicts == []

    def test_same_container_different_partition_keys(
        self,
    ) -> None:
        """Same container with different partition keys: no conflict."""
        cookies = [
            _make_cookie(
                name="x",
                origin_attributes="^userContextId=5",
            ),
            _make_cookie(
                name="x",
                origin_attributes=(
                    "^userContextId=5"
                    "&partitionKey=example.com"
                ),
            ),
        ]
        deduped, conflicts = (
            fc.dedup_container_conflicts(cookies)
        )
        assert len(deduped) == 2
        assert conflicts == []

    def test_conflict_fields(self) -> None:
        """ContainerConflict has correct fields."""
        cookies = [
            _make_cookie(
                host=".test.com",
                name="sid",
                path="/app",
                origin_attributes="",
            ),
            _make_cookie(
                host=".test.com",
                name="sid",
                path="/app",
                origin_attributes="^userContextId=7",
            ),
        ]
        _, conflicts = (
            fc.dedup_container_conflicts(cookies)
        )
        assert len(conflicts) == 1
        cf = conflicts[0]
        assert cf.host == ".test.com"
        assert cf.name == "sid"
        assert cf.path == "/app"
        assert cf.kept_container_id == 0
        assert cf.omitted_container_ids == [7]


# =============================================================================
# Source Option Tests
# =============================================================================


class TestNormalizeSources:
    """Test --src normalization."""

    def test_none_defaults_to_both(self) -> None:
        """None returns both sources."""
        result = fc.normalize_sources(None)
        assert result == {"db", "recovery"}

    def test_single_db(self) -> None:
        """Single 'db' source."""
        result = fc.normalize_sources(["db"])
        assert result == {"db"}

    def test_single_recovery(self) -> None:
        """Single 'recovery' source."""
        result = fc.normalize_sources(["recovery"])
        assert result == {"recovery"}

    def test_both_explicit(self) -> None:
        """Both sources specified explicitly."""
        result = fc.normalize_sources(
            ["db", "recovery"]
        )
        assert result == {"db", "recovery"}

    def test_both_reversed(self) -> None:
        """Both sources in reverse order."""
        result = fc.normalize_sources(
            ["recovery", "db"]
        )
        assert result == {"db", "recovery"}


class TestSrcArgParser:
    """Test --src argument parsing."""

    def test_src_db(self) -> None:
        """Parse --src db."""
        parser = fc.build_parser()
        args = parser.parse_args(["list", "--source", "db"])
        assert args.sources == ["db"]

    def test_src_recovery(self) -> None:
        """Parse --src recovery."""
        parser = fc.build_parser()
        args = parser.parse_args(
            ["list", "--source", "recovery"]
        )
        assert args.sources == ["recovery"]

    def test_src_both(self) -> None:
        """Parse --src db --src recovery."""
        parser = fc.build_parser()
        args = parser.parse_args(
            ["list", "--source", "db", "--source", "recovery"]
        )
        assert set(args.sources) == {"db", "recovery"}

    def test_src_default_none(self) -> None:
        """No --src defaults to None."""
        parser = fc.build_parser()
        args = parser.parse_args(["list"])
        assert args.sources is None

    def test_src_invalid_rejected(self) -> None:
        """Invalid source value is rejected."""
        parser = fc.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["list", "--source", "invalid"]
            )

    def test_src_on_list_domains(self) -> None:
        """--src works on list-domains."""
        parser = fc.build_parser()
        args = parser.parse_args(
            ["list-domains", "--source", "recovery"]
        )
        assert args.sources == ["recovery"]

    def test_src_on_list_containers(self) -> None:
        """--src works on list-containers."""
        parser = fc.build_parser()
        args = parser.parse_args(
            ["list-containers", "--source", "db"]
        )
        assert args.sources == ["db"]


# =============================================================================
# Conflict Output Tests
# =============================================================================


class TestConflictOutput:
    """Test conflict annotations in output formats."""

    def test_netscape_conflict_comment(self) -> None:
        """Netscape output includes conflict comment."""
        cookie = _make_cookie(name="sid")
        conflict = fc.ContainerConflict(
            host=".example.com",
            name="sid",
            path="/",
            kept_container_id=0,
            omitted_container_ids=[5, 12],
        )
        output = fc.format_netscape(
            [cookie], [conflict]
        )
        assert "# Cookie 'sid'" in output
        assert "[5, 12]" in output
        assert "keeping container 0" in output

    def test_netscape_no_conflict_no_comment(
        self,
    ) -> None:
        """Netscape output has no comments without conflicts."""
        cookie = _make_cookie(name="safe")
        output = fc.format_netscape([cookie])
        lines = [
            l for l in output.split("\n")
            if l and not l.startswith("#")
        ]
        assert len(lines) == 1

    def test_json_conflict_field(self) -> None:
        """JSON output includes containerConflict."""
        cookie = _make_cookie(name="sid")
        conflict = fc.ContainerConflict(
            host=".example.com",
            name="sid",
            path="/",
            kept_container_id=0,
            omitted_container_ids=[5, 12],
        )
        output = fc.format_json([cookie], [conflict])
        data = json.loads(output)
        assert len(data) == 1
        assert "containerConflict" in data[0]
        cc = data[0]["containerConflict"]
        assert cc["keptContainerId"] == 0
        assert cc["omittedContainerIds"] == [5, 12]

    def test_json_no_conflict_no_field(self) -> None:
        """JSON output omits containerConflict when clean."""
        cookie = _make_cookie(name="safe")
        output = fc.format_json([cookie])
        data = json.loads(output)
        assert "containerConflict" not in data[0]


# =============================================================================
# Integration Tests: Session Cookies
# =============================================================================


class TestDoListWithSessionCookies:
    """Test list subcommand with session cookies."""

    def test_includes_session_cookies(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        recovery_jsonlz4: Path,
        capsys: Any,
    ) -> None:
        """Session cookies are included in output."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list(
                profile=None,
                domains=None,
                container=None,
                fmt="json",
            )
        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        names = {c["name"] for c in data}
        assert "wp_session" in names
        assert "session_only" in names

    def test_dedup_prefers_sqlite(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        recovery_jsonlz4: Path,
        capsys: Any,
    ) -> None:
        """Duplicate cookies use sqlite value."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            fc.do_list(
                profile=None,
                domains=["example.com"],
                container=None,
                fmt="json",
            )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        session_cookies = [
            c for c in data if c["name"] == "session"
        ]
        assert len(session_cookies) == 1
        assert session_cookies[0]["value"] == "abc123"

    def test_src_db_only(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        recovery_jsonlz4: Path,
        capsys: Any,
    ) -> None:
        """--src db excludes session cookies."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            fc.do_list(
                profile=None,
                domains=None,
                container=None,
                fmt="json",
                sources=["db"],
            )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        names = {c["name"] for c in data}
        assert "wp_session" not in names

    def test_src_recovery_only(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        recovery_jsonlz4: Path,
        capsys: Any,
    ) -> None:
        """--src recovery excludes sqlite cookies."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            fc.do_list(
                profile=None,
                domains=None,
                container=None,
                fmt="json",
                sources=["recovery"],
            )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        names = {c["name"] for c in data}
        assert "wp_session" in names
        # sqlite-only cookies should be absent
        assert "pref" not in names

    def test_without_recovery_file(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        capsys: Any,
    ) -> None:
        """Works fine without recovery.jsonlz4."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            result = fc.do_list(
                profile=None,
                domains=None,
                container=None,
                fmt="json",
            )
        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 6

    def test_container_filter_with_session(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        containers_json: Path,
        recovery_jsonlz4: Path,
        capsys: Any,
    ) -> None:
        """Container filter works with session cookies."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            fc.do_list(
                profile=None,
                domains=None,
                container="Personal",
                fmt="json",
            )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # Only container 1 cookies (from sqlite)
        assert len(data) == 1
        assert data[0]["name"] == "container_cookie"


class TestDoSelfTest:
    """Test do_self_test function."""

    @patch.object(
        fc.subprocess, "run", autospec=True
    )
    def test_basic(
        self, mock_run: MagicMock
    ) -> None:
        """Test do_self_test invokes the test file."""
        mock_run.return_value = MagicMock(
            returncode=0
        )

        fc.do_self_test(
            verbose=False, coverage=False
        )

        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert cmd[0].endswith(
            "test_firefox_cookies.py"
        )

    @patch.object(
        fc.subprocess, "run", autospec=True
    )
    def test_with_verbose(
        self, mock_run: MagicMock
    ) -> None:
        """Test do_self_test passes --verbose."""
        mock_run.return_value = MagicMock(
            returncode=0
        )

        fc.do_self_test(
            verbose=True, coverage=False
        )

        cmd = mock_run.call_args[0][0]
        assert "--verbose" in cmd

    @patch.object(
        fc.subprocess, "run", autospec=True
    )
    def test_without_verbose(
        self, mock_run: MagicMock
    ) -> None:
        """Test do_self_test omits --verbose."""
        mock_run.return_value = MagicMock(
            returncode=0
        )

        fc.do_self_test(
            verbose=False, coverage=False
        )

        cmd = mock_run.call_args[0][0]
        assert "--verbose" not in cmd

    @patch.object(
        fc.subprocess, "run", autospec=True
    )
    def test_with_coverage(
        self, mock_run: MagicMock
    ) -> None:
        """Test do_self_test passes --coverage."""
        mock_run.return_value = MagicMock(
            returncode=0
        )

        fc.do_self_test(
            verbose=False, coverage=True
        )

        cmd = mock_run.call_args[0][0]
        assert "--coverage" in cmd

    @patch.object(
        fc.subprocess, "run", autospec=True
    )
    def test_without_coverage(
        self, mock_run: MagicMock
    ) -> None:
        """Test do_self_test omits --coverage."""
        mock_run.return_value = MagicMock(
            returncode=0
        )

        fc.do_self_test(
            verbose=False, coverage=False
        )

        cmd = mock_run.call_args[0][0]
        assert "--coverage" not in cmd

    @patch.object(
        fc.subprocess, "run", autospec=True
    )
    def test_raises_on_failure(
        self, mock_run: MagicMock
    ) -> None:
        """Test do_self_test raises TestError."""
        mock_run.return_value = MagicMock(
            returncode=1
        )

        with pytest.raises(
            fc.TestError, match="Tests failed"
        ):
            fc.do_self_test(
                verbose=False, coverage=False
            )


class TestDoListDomainsWithSession:
    """Test list-domains with session cookies."""

    def test_includes_session_cookie_domains(
        self,
        firefox_dir: Path,
        profile_dir: Path,
        cookies_db: Path,
        recovery_jsonlz4: Path,
        capsys: Any,
    ) -> None:
        """Session cookie domains appear in domain list."""
        with patch.object(
            fc,
            "find_firefox_dir",
            autospec=True,
            return_value=firefox_dir,
        ):
            fc.do_list_domains(
                profile=None, container=None
            )
        captured = capsys.readouterr()
        assert "wordpress.com" in captured.out


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
