#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "tomlkit"]
# ///
# This is AI generated code

"""Comprehensive unit tests for crony."""

from __future__ import annotations

import argparse
import dataclasses
import importlib.machinery
import importlib.util
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import tomlkit
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from conftest import (
    CmdCallbacksBase,
    ExceptionHierarchyBase,
    SentinelHomeBase,
    UnknownArgRoutedToSubparserBase,
)

# Repository root directory (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent

# Import crony module from bin/ (works with or without .py extension)
_script_path = REPO_ROOT / "bin" / "crony"
if not _script_path.exists():
    _script_path = REPO_ROOT / "bin" / "crony.py"
_loader = importlib.machinery.SourceFileLoader("crony", str(_script_path))
_spec = importlib.util.spec_from_loader("crony", _loader)
assert _spec and _spec.loader
crony = importlib.util.module_from_spec(_spec)
sys.modules["crony"] = crony
_spec.loader.exec_module(crony)


def _apply(short: str, *, bundle: str = crony.DEFAULT_BUNDLE_NAME) -> str:
    """Apply one entry through the production path: build the
    `Config` model (one disk pass) and call `apply_one` with the
    resolved ref -- mirroring what `do_apply` does per entry, so
    tests exercise the model-based code rather than a standalone
    path. For tests not built on `_ApplyHarness` (which exposes the
    same thing as `h.apply`).
    """
    config = crony.load_config()
    ref = config.pending.by_full_name.get(f"{bundle}.{short}")
    assert ref is not None, f"{bundle}.{short} not selected on this host"
    result: str = crony.apply_one(config, ref)
    return result


def isolate_crony_home(
    module: Any,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Redirect every host-touching crony default to a non-existent
    sentinel under tmp_path.

    crony resolves its config/state/launchd/systemd paths from $HOME
    at import time. A test that forgets to monkeypatch the in-process
    module attributes (CONFIG_FILE, STATE_DIR, etc.) -- or that spawns
    a subprocess that re-reads the CRONY_* env vars -- would land on
    the user's real config and state dirs. This fixture wires every
    attribute and matching env var to a per-test sentinel; the
    sentinel itself is never created, so a stray write fails loudly
    rather than silently mutating the user's dotfiles.

    Tests that need to actually write files override these on top
    via monkeypatch.setattr / setenv; pytest's monkeypatch stack
    layers cleanly atop this fixture.
    """
    sentinel = tmp_path / "_home_sentinel_unwritten"
    monkeypatch.setattr(Path, "home", lambda: sentinel)
    layout = {
        "CONFIG_DIR": sentinel / ".config" / "crony",
        "CONFIG_FILE": sentinel / ".config" / "crony" / "config.toml",
        "CONFIG_DROPIN_DIR": sentinel / ".config" / "crony" / "config",
        "STATE_DIR": sentinel / ".local" / "state" / "crony",
        "LAUNCHAGENTS_DIR": sentinel / "Library" / "LaunchAgents",
        "SYSTEMD_USER_DIR": sentinel / ".config" / "systemd" / "user",
    }
    for attr, path in layout.items():
        monkeypatch.setattr(module, attr, path)
        monkeypatch.setenv(f"CRONY_{attr}", str(path))


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: Any) -> None:
    isolate_crony_home(crony, tmp_path, monkeypatch)


class TestIsolateCronyHomeFixture(SentinelHomeBase):
    """Pin the autouse `_isolate_home` fixture so a future refactor
    can't quietly remove the safety net. Inherits the generic
    Path.home() + sentinel-non-existence checks and adds the
    crony-specific attribute/env-var matrix assertions.
    """

    def test_all_attributes_under_sentinel(self) -> None:
        sentinel = Path.home()
        for attr in (
            "CONFIG_DIR",
            "CONFIG_FILE",
            "CONFIG_DROPIN_DIR",
            "STATE_DIR",
            "LAUNCHAGENTS_DIR",
            "SYSTEMD_USER_DIR",
        ):
            value = getattr(crony, attr)
            assert str(value).startswith(str(sentinel)), (
                f"crony.{attr}={value!r} escaped the sentinel"
            )

    def test_all_env_vars_under_sentinel(self) -> None:
        sentinel = Path.home()
        for attr in (
            "CONFIG_DIR",
            "CONFIG_FILE",
            "CONFIG_DROPIN_DIR",
            "STATE_DIR",
            "LAUNCHAGENTS_DIR",
            "SYSTEMD_USER_DIR",
        ):
            value = os.environ[f"CRONY_{attr}"]
            assert value.startswith(str(sentinel)), (
                f"CRONY_{attr}={value!r} escaped the sentinel"
            )


class TestExceptionHierarchy(ExceptionHierarchyBase):
    """Verify every non-excluded ExitCode has a matching exception."""

    BASE_ERROR = crony.CronyError
    EXIT_CODE = crony.ExitCode
    EXCLUDED_CODES = {
        crony.ExitCode.SUCCESS,
        crony.ExitCode.WARNING,
    }


class TestHelpOutput:
    """`crony --help` surfaces the design block appended to the epilog."""

    def test_help_includes_design_block(self) -> None:
        parser = crony.build_parser()
        text = parser.format_help()
        # Design block documents the default status columns.
        assert "CONFIG    synced" in text
        assert "SCHEDULE  the cron" in text
        assert "LAST      ok" in text
        # Exit codes still rendered.
        assert "exit codes:" in text
        # Design block is appended *after* the exit codes -- the
        # short tagline lives in description, design lives in
        # epilog after the exit-code list.
        assert text.index("exit codes:") < text.index("CONFIG    synced")


class TestUnknownArgRoutedToSubparser(UnknownArgRoutedToSubparserBase):
    """Unknown args print the subcommand's usage line."""

    PARSER_FUNC = staticmethod(crony.build_parser)
    CASES = [
        (["status", "--bogus"], "status"),
        (["logs", "--bogus"], "logs"),
        (["enable", "--bogus"], "enable"),
    ]


class TestCmdCallbacks(CmdCallbacksBase):
    """Test command callback dispatch table."""

    CALLBACKS = crony.COMMAND_CALLBACKS
    PARSER_FUNC = crony.build_parser
    CLI_FUNC = staticmethod(crony.cli)
    MODULE = crony
    EXIT_CODE_USAGE = crony.ExitCode.USAGE
    TEST_SUBCOMMAND = "status"
    EXCEPTION_EXIT_CODE_MAP = [
        (crony.UsageError("t"), crony.ExitCode.USAGE),
        (crony.ConfigError("t"), crony.ExitCode.CONFIG),
        (
            crony.SubprocessError(1, ["bogus"]),
            crony.ExitCode.SUBPROCESS,
        ),
        (crony.LockBusyError("t"), crony.ExitCode.LOCK_BUSY),
        (
            crony.PreconditionError("t"),
            crony.ExitCode.PRECONDITION,
        ),
        (
            crony.JobTimeoutError("t"),
            crony.ExitCode.TIMEOUT,
        ),
        (RuntimeError("t"), crony.ExitCode.ERROR),
    ]


class TestConfigSubcommandDispatch:
    """The `config` parent routes its nested actions through the
    "<command> <action>" key in COMMAND_CALLBACKS. These tests pin
    that the nested form actually reaches the right callback (a
    flat dispatch table without the join would silently do
    nothing) and that argparse's strict-subparsers error path
    fires for missing/unknown actions on the parent.
    """

    def test_config_init_dispatches_to_do_init(self) -> None:
        mock_cb = MagicMock()
        with (
            patch.dict(
                crony.COMMAND_CALLBACKS,
                {"config init": mock_cb},
            ),
            patch("sys.argv", ["prog", "config", "init", "--force"]),
        ):
            result = crony.cli()
        assert result == 0
        mock_cb.assert_called_once_with(force=True, bundle=None)

    def test_config_validate_dispatches_to_do_validate(self) -> None:
        mock_cb = MagicMock()
        with (
            patch.dict(
                crony.COMMAND_CALLBACKS,
                {"config validate": mock_cb},
            ),
            patch("sys.argv", ["prog", "config", "validate", "-b", "foo"]),
        ):
            result = crony.cli()
        assert result == 0
        mock_cb.assert_called_once_with(bundle="foo")

    def test_config_without_action_errors(self, capsys: Any) -> None:
        with (
            patch("sys.argv", ["prog", "config"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            crony.cli()
        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "config" in err

    def test_config_unknown_action_errors(self, capsys: Any) -> None:
        with (
            patch("sys.argv", ["prog", "config", "bogus"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            crony.cli()
        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "bogus" in err


# =============================================================================
# Helpers
# =============================================================================


def _job(**overrides: Any) -> dict[str, Any]:
    """Build a minimal job body with overrides."""
    base: dict[str, Any] = {"command": "true", "schedule": "daily"}
    base.update(overrides)
    return base


def _inject_uuids(raw: dict[str, Any]) -> dict[str, Any]:
    """Stamp a fresh UUID on every job/group in a test config that
    lacks one. Returns `raw` for chaining.

    UUIDs are required on every parsed entry, but most tests
    exercise unrelated parser behavior and would otherwise have
    to repeat boilerplate `"uuid": str(uuid.uuid4())` lines on
    each fixture. Tests that specifically exercise missing or
    duplicate UUID validation bypass this helper.
    """
    for section in ("job", "job-group"):
        entries = raw.get(section)
        if not isinstance(entries, dict):
            continue
        for body in entries.values():
            if isinstance(body, dict) and "uuid" not in body:
                body["uuid"] = str(uuid.uuid4())
    return raw


def _parse(raw: dict[str, Any]) -> Any:
    """Auto-stamp missing uuids and parse. The dominant test path."""
    return crony.parse_config(_inject_uuids(raw))


def _uuid_toml(text: str) -> str:
    """Stamp missing uuids on every `[job.*]` / `[job-group.*]`
    table in a TOML string. Mirrors what `crony config update`
    does on a real bundle file; lets fixtures that write raw TOML
    to disk stay focused on the surface they exercise rather than
    repeating `uuid = "..."` lines.
    """
    doc = tomlkit.parse(text)
    crony._insert_missing_uuids_in_section(doc, "job")
    crony._insert_missing_uuids_in_section(doc, "job-group")
    return tomlkit.dumps(doc)


def _assert_errored_job(raw: dict[str, Any], short: str, match: str) -> None:
    """Assert parse_config records a per-entity error for job `short`.

    Per-entity parse failures land in `TomlBundleConfig.errored_jobs` instead
    of raising, so tests of bad-shape inputs check the recorded
    message rather than wrapping the call in `pytest.raises`.
    """
    cfg = _parse(raw)
    assert short in cfg.errored_jobs, (
        f"expected {short!r} in errored_jobs, got {list(cfg.errored_jobs)}"
    )
    assert re.search(match, cfg.errored_jobs[short]), (
        f"errored_jobs[{short!r}]={cfg.errored_jobs[short]!r} "
        f"did not match {match!r}"
    )
    assert short not in cfg.jobs


def _assert_errored_job_group(
    raw: dict[str, Any], short: str, match: str
) -> None:
    """As `_assert_errored_job` but for `[job-group.*]` entries."""
    cfg = _parse(raw)
    assert short in cfg.errored_job_groups, (
        f"expected {short!r} in errored_job_groups, got "
        f"{list(cfg.errored_job_groups)}"
    )
    assert re.search(match, cfg.errored_job_groups[short]), (
        f"errored_job_groups[{short!r}]="
        f"{cfg.errored_job_groups[short]!r} "
        f"did not match {match!r}"
    )
    assert short not in cfg.job_groups


def _assert_errored_platform_target(
    raw: dict[str, Any], platform: str, match: str
) -> None:
    """As `_assert_errored_job` but for `[target.<platform>]` entries."""
    cfg = _parse(raw)
    assert platform in cfg.errored_platform_targets, (
        f"expected {platform!r} in errored_platform_targets, got "
        f"{list(cfg.errored_platform_targets)}"
    )
    assert re.search(match, cfg.errored_platform_targets[platform]), (
        f"errored_platform_targets[{platform!r}]="
        f"{cfg.errored_platform_targets[platform]!r} "
        f"did not match {match!r}"
    )
    assert platform not in cfg.platform_targets


def _assert_errored_host_target(
    raw: dict[str, Any], host: str, match: str
) -> None:
    """As `_assert_errored_job` but for `[target.host.<name>]`."""
    cfg = _parse(raw)
    assert host in cfg.errored_host_targets, (
        f"expected {host!r} in errored_host_targets, got "
        f"{list(cfg.errored_host_targets)}"
    )
    assert re.search(match, cfg.errored_host_targets[host]), (
        f"errored_host_targets[{host!r}]="
        f"{cfg.errored_host_targets[host]!r} "
        f"did not match {match!r}"
    )
    assert host not in cfg.host_targets


# =============================================================================
# Schedule format
# =============================================================================


class TestSchedule:
    """validate_schedule and parse_interval_seconds."""

    @pytest.mark.parametrize(
        "kw",
        ["hourly", "daily", "weekly", "monthly", "yearly", "annually"],
    )
    def test_keyword_accepted(self, kw: str) -> None:
        crony.validate_schedule(kw)  # no raise

    @pytest.mark.parametrize(
        "expr",
        [
            "*-*-* 03:15",
            "03:15",
            "Mon..Fri *-*-* 09:00",
            "Sun *-*-* 04:00",
            "*-*-01 03:00",
            "*:0/15",
            "*:00,30",
        ],
    )
    def test_oncalendar_accepted(self, expr: str) -> None:
        crony.validate_schedule(expr)  # no raise

    @pytest.mark.parametrize("bad", ["", "   ", "soon", "Mon", "garbage"])
    def test_garbage_rejected(self, bad: str) -> None:
        with pytest.raises(crony.ConfigError):
            crony.validate_schedule(bad)

    def test_multiline_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="one line"):
            crony.validate_schedule("daily\nfoo")

    @pytest.mark.parametrize(
        "spec, expected",
        [
            ("30s", 30),
            ("2m", 120),
            ("1h", 3600),
            ("30min", 1800),
            ("1d", 86400),
            ("1h30min", 5400),
            ("90 seconds", 90),
            ("2h 15m", 8100),
            ("1M", 2592000),
            ("1month", 2592000),
            ("2months", 5184000),
            ("1year", 31536000),
        ],
    )
    def test_interval_valid(self, spec: str, expected: int) -> None:
        assert crony.parse_interval_seconds(spec) == expected

    def test_interval_capital_m_is_months_lowercase_is_minutes(
        self,
    ) -> None:
        # Sanity check: 'm' != 'M'.
        assert crony.parse_interval_seconds(
            "1m"
        ) != crony.parse_interval_seconds("1M")

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "soon", "0s", "30 lightyears", "30"],
    )
    def test_interval_invalid(self, bad: str) -> None:
        with pytest.raises(crony.ConfigError):
            crony.parse_interval_seconds(bad)


# =============================================================================
# TomlBundleConfig parsing - structural
# =============================================================================


def _email_block(**overrides: Any) -> dict[str, Any]:
    """Minimal valid [defaults.notify.email] body, with overrides."""
    body: dict[str, Any] = {
        "to": "you@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_user": "you",
    }
    body.update(overrides)
    return body


def _ntfy_block(**overrides: Any) -> dict[str, Any]:
    """Minimal valid [defaults.notify.ntfy] body, with overrides."""
    body: dict[str, Any] = {"url": "https://ntfy.example.com/x"}
    body.update(overrides)
    return body


class TestParseDefaults:
    def test_empty_config_uses_defaults(self) -> None:
        cfg = _parse({})
        assert cfg.defaults.notify_channels == []
        assert cfg.defaults.job_timeout_sec == 1800
        assert cfg.defaults.notify_attach_log is True
        assert cfg.defaults.notify_channel_defs == {}

    def test_override_defaults(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify_attach_log": False,
                    "notify_attach_max_kb": 512,
                    "job_timeout_sec": 3600,
                    "log_keep_runs": 50,
                    "notify": {"ntfy": _ntfy_block()},
                }
            }
        )
        assert cfg.defaults.notify_channels == ["ntfy"]
        assert cfg.defaults.notify_attach_log is False
        assert cfg.defaults.notify_attach_max_kb == 512
        assert cfg.defaults.job_timeout_sec == 3600
        assert cfg.defaults.log_keep_runs == 50
        assert "ntfy" in cfg.defaults.notify_channel_defs

    def test_listed_channel_must_be_defined(self) -> None:
        # Listing a channel that has no [defaults.notify.<name>]
        # block is a config error -- the dispatcher would have
        # nothing to send through.
        with pytest.raises(crony.ConfigError, match="not defined"):
            _parse({"defaults": {"notify_channels": ["carrier-pigeon"]}})

    def test_duplicate_notify_channels_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="listed twice"):
            _parse({"defaults": {"notify_channels": ["ntfy", "ntfy"]}})

    def test_multi_channel_defaults(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["email", "ntfy"],
                    "notify": {
                        "email": _email_block(),
                        "ntfy": _ntfy_block(),
                    },
                }
            }
        )
        assert cfg.defaults.notify_channels == ["email", "ntfy"]
        assert set(cfg.defaults.notify_channel_defs) == {"email", "ntfy"}

    def test_legacy_singular_notify_channel_rejected(self) -> None:
        # The pre-multi-channel singular `notify_channel` field is
        # gone; ensure it surfaces as an unknown key rather than
        # being silently ignored.
        with pytest.raises(crony.ConfigError, match="unknown key"):
            _parse({"defaults": {"notify_channel": "ntfy"}})

    def test_notify_email_subsection(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify": {
                        "email": _email_block(
                            to="edp@example.com",
                            smtp_user="edp",
                            smtp_port=465,
                            smtp_starttls=False,
                            smtp_pass_keychain_service="crony-smtp",
                            smtp_pass_keychain_account="edp",
                        )
                    }
                }
            }
        )
        ch = cfg.defaults.notify_channel_defs["email"]
        assert ch.transport == "email"
        assert ch.email is not None
        assert ch.email.to == "edp@example.com"
        assert ch.email.smtp_port == 465
        assert ch.email.smtp_starttls is False
        assert ch.email.smtp_pass_keychain_service == "crony-smtp"
        assert ch.email.smtp_pass_keychain_account == "edp"

    def test_notify_email_missing_required(self) -> None:
        with pytest.raises(crony.ConfigError, match="required"):
            _parse({"defaults": {"notify": {"email": {"to": "x@y.com"}}}})

    def test_notify_ntfy_subsection(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify": {
                        "ntfy": _ntfy_block(
                            token_keychain_service="ntfy-token",
                        )
                    }
                }
            }
        )
        ch = cfg.defaults.notify_channel_defs["ntfy"]
        assert ch.transport == "ntfy"
        assert ch.ntfy is not None
        assert ch.ntfy.url == "https://ntfy.example.com/x"

    def test_arbitrary_channel_name_requires_transport(self) -> None:
        # `notify.foo` doesn't match a built-in transport; the user
        # must declare `transport=`.
        with pytest.raises(crony.ConfigError, match="transport"):
            _parse({"defaults": {"notify": {"foo": _ntfy_block()}}})

    def test_arbitrary_channel_with_transport(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify": {
                        "ntfy-loud": dict(
                            _ntfy_block(),
                            transport="ntfy",
                            headers={"Priority": "urgent"},
                        )
                    }
                }
            }
        )
        ch = cfg.defaults.notify_channel_defs["ntfy-loud"]
        assert ch.transport == "ntfy"
        assert ch.headers == {"Priority": "urgent"}
        assert ch.ntfy is not None
        assert ch.ntfy.url == "https://ntfy.example.com/x"

    def test_unknown_transport_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="transport"):
            _parse(
                {
                    "defaults": {
                        "notify": {"carrier-pigeon": {"transport": "carrier"}}
                    }
                }
            )

    def test_reserved_email_header_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="cannot be overridden"):
            _parse(
                {
                    "defaults": {
                        "notify": {
                            "email": dict(
                                _email_block(),
                                headers={"Subject": "override"},
                            )
                        }
                    }
                }
            )

    def test_reserved_ntfy_header_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="cannot be overridden"):
            _parse(
                {
                    "defaults": {
                        "notify": {
                            "ntfy": dict(
                                _ntfy_block(),
                                headers={"Tags": "override"},
                            )
                        }
                    }
                }
            )

    def test_reserved_ntfy_filename_header_rejected(self) -> None:
        # `Filename` would make ntfy render the body as a downloadable
        # file (publicly addressable by URL guessing). Reserved so a
        # config can't accidentally turn the inline-body design into
        # the very attachment behavior it was designed to avoid.
        with pytest.raises(crony.ConfigError, match="cannot be overridden"):
            _parse(
                {
                    "defaults": {
                        "notify": {
                            "ntfy": dict(
                                _ntfy_block(),
                                headers={"Filename": "custom.log"},
                            )
                        }
                    }
                }
            )

    def test_email_headers_pass_through(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify": {
                        "email": dict(
                            _email_block(),
                            headers={"Reply-To": "you@example.com"},
                        )
                    }
                }
            }
        )
        ch = cfg.defaults.notify_channel_defs["email"]
        assert ch.headers == {"Reply-To": "you@example.com"}


class TestParseJob:
    """Per-job structural validation."""

    @staticmethod
    def _cfg(body: dict[str, Any]) -> dict[str, Any]:
        return {"job": {"j": body}}

    def test_command_form_minimal(self) -> None:
        cfg = _parse(self._cfg(_job()))
        assert cfg.jobs["j"].command == "true"
        assert cfg.jobs["j"].script is None

    def test_script_with_args(self) -> None:
        cfg = _parse(
            self._cfg(
                {
                    "script": "scripts/foo.sh",
                    "args": ["--flag", "value"],
                    "schedule": "daily",
                }
            )
        )
        assert cfg.jobs["j"].script == "scripts/foo.sh"
        assert cfg.jobs["j"].args == ["--flag", "value"]

    def test_command_xor_script_both(self) -> None:
        _assert_errored_job(
            self._cfg(
                {
                    "command": "x",
                    "script": "y",
                    "schedule": "daily",
                }
            ),
            "j",
            "exactly one of",
        )

    def test_command_xor_script_neither(self) -> None:
        _assert_errored_job(
            self._cfg({"schedule": "daily"}), "j", "exactly one of"
        )

    def test_args_with_command_rejected(self) -> None:
        _assert_errored_job(self._cfg(_job(args=["a"])), "j", "only valid with")

    def test_gate_xor_gate_script(self) -> None:
        _assert_errored_job(
            self._cfg(_job(gate="x", gate_script="y.sh")),
            "j",
            "mutually exclusive",
        )

    def test_gate_args_without_gate_script(self) -> None:
        _assert_errored_job(
            self._cfg(_job(gate="x", gate_args=["a"])),
            "j",
            "only valid with 'gate_script'",
        )

    def test_schedule_xor_interval(self) -> None:
        _assert_errored_job(
            self._cfg(
                {
                    "command": "x",
                    "schedule": "daily",
                    "interval": "30min",
                }
            ),
            "j",
            "mutually exclusive",
        )

    def test_interval_form(self) -> None:
        cfg = _parse(self._cfg({"command": "x", "interval": "1h30min"}))
        assert cfg.jobs["j"].interval == "1h30min"
        assert cfg.jobs["j"].schedule is None

    def test_invalid_platforms_value(self) -> None:
        _assert_errored_job(
            self._cfg(_job(platforms=["windows"])), "j", "not in"
        )

    def test_hosts_mixed_negation_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(hosts=["alpha", "!beta"])),
            "j",
            "must all be negated",
        )

    def test_hosts_empty_negation_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(hosts=["!"])),
            "j",
            "empty after the negation prefix",
        )

    def test_invalid_notify_channel(self) -> None:
        # notify_channels validation runs in _validate_config, but
        # per-job references promote the job into errored_jobs
        # (same tolerance shape as parse-time per-entity errors).
        _assert_errored_job(
            self._cfg(_job(notify_channels=["carrier-pigeon"])),
            "j",
            "notify_channels",
        )

    def test_negative_timeout(self) -> None:
        _assert_errored_job(
            self._cfg(_job(job_timeout_sec=-1)), "j", "positive"
        )

    def test_zero_timeout(self) -> None:
        _assert_errored_job(self._cfg(_job(job_timeout_sec=0)), "j", "positive")

    def test_env_must_be_string_dict(self) -> None:
        _assert_errored_job(
            self._cfg(_job(env={"FOO": 42})), "j", "string -> string"
        )

    def test_unknown_job_key(self) -> None:
        _assert_errored_job(
            self._cfg(_job(surprise="boom")), "j", "unknown key"
        )

    def test_group_only_job_no_schedule(self) -> None:
        # Valid only when referenced by a group.
        cfg = _parse(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
            }
        )
        assert cfg.jobs["a"].schedule is None
        assert cfg.jobs["a"].interval is None

    def test_interactive_auto_tags_platform_darwin(self) -> None:
        cfg = _parse(self._cfg(_job(interactive=True)))
        assert cfg.jobs["j"].interactive is True
        assert cfg.jobs["j"].platforms == ["darwin"]

    def test_interactive_explicit_darwin_platform_ok(self) -> None:
        cfg = _parse(self._cfg(_job(interactive=True, platforms=["darwin"])))
        assert cfg.jobs["j"].interactive is True

    def test_interactive_with_linux_platform_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(interactive=True, platforms=["linux"])),
            "j",
            "implies platforms",
        )

    def test_interactive_with_multi_platform_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(interactive=True, platforms=["darwin", "linux"])),
            "j",
            "implies platforms",
        )

    def test_interactive_active_resolves_to_seconds(self) -> None:
        cfg = _parse(
            self._cfg(_job(interactive=True, interactive_active="5min"))
        )
        assert cfg.jobs["j"].interactive_active_sec == 300

    def test_interactive_delay_resolves_to_seconds(self) -> None:
        cfg = _parse(self._cfg(_job(interactive=True, interactive_delay="2h")))
        assert cfg.jobs["j"].interactive_delay_sec == 7200

    def test_interactive_active_without_flag_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(interactive_active="10min")),
            "j",
            "interactive = true",
        )

    def test_interactive_delay_without_flag_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(interactive_delay="1h")),
            "j",
            "interactive = true",
        )

    def test_interactive_active_zero_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(interactive=True, interactive_active="0s")),
            "j",
            "must be positive",
        )


class TestParseJobGroup:
    def test_valid_group(self) -> None:
        cfg = _parse(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            }
        )
        assert cfg.job_groups["g"].jobs == ["a"]
        assert cfg.job_groups["g"].schedule == "*-*-* 03:00"

    def test_empty_jobs_list(self) -> None:
        _assert_errored_job_group(
            {"job-group": {"g": {"jobs": [], "schedule": "daily"}}},
            "g",
            "non-empty list",
        )

    def test_schedule_optional(self) -> None:
        # A group with no schedule / no interval is a transit group:
        # it parses fine, but its chains are checked at validate
        # time (a target referencing it through a path with no
        # schedule errors).
        cfg = _parse(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"]}},
            }
        )
        assert cfg.job_groups["g"].schedule is None
        assert cfg.job_groups["g"].interval is None

    def test_both_schedule_and_interval(self) -> None:
        _assert_errored_job_group(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "interval": "1h",
                    }
                },
            },
            "g",
            "mutually exclusive",
        )

    def test_unknown_group_key(self) -> None:
        _assert_errored_job_group(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "surprise": True,
                    }
                },
            },
            "g",
            "unknown key",
        )

    def test_group_hosts_mixed_negation_rejected(self) -> None:
        _assert_errored_job_group(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "hosts": ["alpha", "!beta"],
                    }
                },
            },
            "g",
            "must all be negated",
        )

    def test_group_hosts_empty_negation_rejected(self) -> None:
        _assert_errored_job_group(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "hosts": ["!"],
                    }
                },
            },
            "g",
            "empty after the negation prefix",
        )

    def test_group_rejects_notify_channels(self) -> None:
        # Groups don't carry notify settings: per-child cascade
        # resolves notify via job/target/defaults instead.
        _assert_errored_job_group(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "notify_channels": ["ntfy"],
                    }
                },
            },
            "g",
            "unknown key",
        )


class TestUuidField:
    """Required `uuid` on jobs and groups is parsed when present,
    validated for canonical lowercase 8-4-4-4-12 form, and demotes
    to errored-entity when missing or malformed so other entries
    in the same bundle still apply.
    """

    GOOD = "aabbccdd-1234-5678-9abc-aabbccddeeff"

    def test_job_uuid_round_trip(self) -> None:
        cfg = _parse({"job": {"j": {"command": "true", "uuid": self.GOOD}}})
        assert cfg.jobs["j"].uuid == self.GOOD

    def test_job_missing_uuid_is_errored(self) -> None:
        # Bypass `_parse` so `_inject_uuids` doesn't paper over the
        # condition we're verifying.
        cfg = crony.parse_config({"job": {"j": _job()}})
        assert "j" in cfg.errored_jobs
        assert "uuid" in cfg.errored_jobs["j"]
        assert "crony config update" in cfg.errored_jobs["j"]
        assert "j" not in cfg.jobs

    def test_group_missing_uuid_is_errored(self) -> None:
        cfg = crony.parse_config(
            {
                "job": {
                    "a": {"command": "true", "uuid": self.GOOD},
                },
                "job-group": {
                    "g": {"jobs": ["a"], "schedule": "daily"},
                },
            }
        )
        assert "g" in cfg.errored_job_groups
        assert "uuid" in cfg.errored_job_groups["g"]
        assert "crony config update" in cfg.errored_job_groups["g"]
        assert "g" not in cfg.job_groups

    def test_job_uuid_rejects_non_string(self) -> None:
        _assert_errored_job(
            {"job": {"j": {"command": "true", "uuid": 42}}},
            "j",
            "must be str",
        )

    def test_job_uuid_rejects_garbage(self) -> None:
        _assert_errored_job(
            {"job": {"j": {"command": "true", "uuid": "not-a-uuid"}}},
            "j",
            "not a valid UUID",
        )

    def test_job_uuid_rejects_missing_dashes(self) -> None:
        _assert_errored_job(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "uuid": "12345678123456781234567812345678",
                    }
                }
            },
            "j",
            "canonical",
        )

    def test_job_uuid_rejects_uppercase(self) -> None:
        _assert_errored_job(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "uuid": self.GOOD.upper(),
                    }
                }
            },
            "j",
            "canonical",
        )

    def test_group_uuid_round_trip(self) -> None:
        cfg = _parse(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "uuid": self.GOOD,
                    }
                },
            }
        )
        assert cfg.job_groups["g"].uuid == self.GOOD

    def test_group_uuid_rejects_garbage(self) -> None:
        _assert_errored_job_group(
            {
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "uuid": "nope",
                    }
                }
            },
            "g",
            "not a valid UUID",
        )


class TestDuplicateUuidInBundle:
    """UUIDs are bundle-scoped, so this check runs after each
    bundle's parse_config. Both entries sharing the duplicate
    UUID are demoted into the errored maps with the same message
    -- the user sees the conflict on every side, not just one,
    and other bundles/entries remain operational.
    """

    GOOD = "aabbccdd-1234-5678-9abc-aabbccddeeff"

    def _write_and_load(self, tmp_path: Path, body: str) -> Any:
        path = tmp_path / "bundle.toml"
        path.write_text(body, encoding="utf-8")
        return crony._load_one_bundle("default", path)

    def test_duplicate_uuid_on_two_jobs_demotes_both(
        self, tmp_path: Path
    ) -> None:
        bundle = self._write_and_load(
            tmp_path,
            f'[job.a]\nuuid = "{self.GOOD}"\n'
            'command = "true"\nschedule = "daily"\n\n'
            f'[job.b]\nuuid = "{self.GOOD}"\n'
            'command = "true"\nschedule = "daily"\n',
        )
        cfg = bundle.config
        assert "a" not in cfg.jobs
        assert "b" not in cfg.jobs
        for short in ("a", "b"):
            msg = cfg.errored_jobs[short]
            assert "duplicate uuid" in msg
            assert self.GOOD in msg
            assert "'default.a'" in msg
            assert "'default.b'" in msg
            assert "crony config generate-uuid" in msg

    def test_duplicate_uuid_across_job_and_group_demotes_both(
        self, tmp_path: Path
    ) -> None:
        bundle = self._write_and_load(
            tmp_path,
            f'[job.a]\nuuid = "{self.GOOD}"\n'
            'command = "true"\nschedule = "daily"\n\n'
            f'[job-group.g]\nuuid = "{self.GOOD}"\n'
            'jobs = ["a"]\nschedule = "daily"\n',
        )
        cfg = bundle.config
        assert "a" not in cfg.jobs
        assert "g" not in cfg.job_groups
        assert "duplicate uuid" in cfg.errored_jobs["a"]
        assert "duplicate uuid" in cfg.errored_job_groups["g"]

    def test_three_way_duplicate_names_all_sites(self, tmp_path: Path) -> None:
        # When 3+ entries share a UUID, the message names every
        # site so the user sees every place that needs fixing on
        # the first reload, not just two.
        bundle = self._write_and_load(
            tmp_path,
            f'[job.a]\nuuid = "{self.GOOD}"\n'
            'command = "true"\nschedule = "daily"\n\n'
            f'[job.b]\nuuid = "{self.GOOD}"\n'
            'command = "true"\nschedule = "daily"\n\n'
            f'[job.c]\nuuid = "{self.GOOD}"\n'
            'command = "true"\nschedule = "daily"\n',
        )
        cfg = bundle.config
        for short in ("a", "b", "c"):
            msg = cfg.errored_jobs[short]
            assert "'default.a'" in msg
            assert "'default.b'" in msg
            assert "'default.c'" in msg

    def test_distinct_uuids_load_normally(self, tmp_path: Path) -> None:
        bundle = self._write_and_load(
            tmp_path,
            f'[job.a]\nuuid = "{self.GOOD}"\n'
            'command = "true"\nschedule = "daily"\n\n'
            '[job.b]\nuuid = "11223344-5566-7788-99aa-bbccddeeff00"\n'
            'command = "true"\nschedule = "daily"\n',
        )
        assert set(bundle.config.jobs) == {"a", "b"}
        assert not bundle.config.errored_jobs


class TestParseTarget:
    def test_platform_target(self) -> None:
        cfg = _parse(
            {
                "job": {"a": _job()},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        assert "darwin" in cfg.platform_targets
        t = cfg.platform_targets["darwin"]
        assert t.jobs == ["a"]
        assert t.kind == "platform"

    def test_host_target(self) -> None:
        cfg = _parse(
            {
                "job": {"a": _job()},
                "target": {"host": {"my-host": {"jobs": ["a"]}}},
            }
        )
        assert "my-host" in cfg.host_targets
        assert cfg.host_targets["my-host"].kind == "host"

    def test_invalid_platform_name(self) -> None:
        _assert_errored_platform_target(
            {
                "job": {"a": _job()},
                "target": {"windows": {"jobs": ["a"]}},
            },
            "windows",
            "platform must be one of",
        )

    def test_target_unknown_key(self) -> None:
        with pytest.raises(crony.ConfigError, match="unknown key"):
            _parse(
                {
                    "job": {"a": _job()},
                    "target": {"darwin": {"jobs": ["a"], "surprise": "x"}},
                }
            )


# =============================================================================
# Cross-cutting validation
# =============================================================================


class TestValidateConfig:
    def test_name_collision(self) -> None:
        with pytest.raises(crony.ConfigError, match="name collision"):
            _parse(
                {
                    "job": {"foo": _job()},
                    "job-group": {
                        "foo": {"jobs": ["foo"], "schedule": "daily"}
                    },
                }
            )

    def test_group_references_undefined_name(self) -> None:
        _assert_errored_job_group(
            {"job-group": {"g": {"jobs": ["nope"], "schedule": "daily"}}},
            "g",
            "undefined name",
        )

    def test_group_undefined_ref_does_not_drop_siblings(self) -> None:
        # A single bad group must not take other groups or the
        # bundle with it -- a typo in one entry leaves the rest of
        # the bundle resolvable.
        cfg = _parse(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "bad": {"jobs": ["nope"], "schedule": "daily"},
                    "good": {"jobs": ["a"], "schedule": "daily"},
                },
                "target": {"darwin": {"jobs": ["good"]}},
            }
        )
        assert "bad" in cfg.errored_job_groups
        assert "good" in cfg.job_groups
        assert "darwin" in cfg.platform_targets

    def test_interactive_as_direct_target_child_ok(self) -> None:
        cfg = _parse(
            {
                "job": {
                    "iv": {
                        "command": "true",
                        "schedule": "daily",
                        "interactive": True,
                    }
                },
                "target": {"darwin": {"jobs": ["iv"]}},
            }
        )
        assert "iv" in cfg.jobs
        assert cfg.jobs["iv"].interactive is True

    def test_interactive_inside_group_is_allowed(self) -> None:
        # The group dispatches the interactive child async, so it
        # is allowed as a [job-group.*] member -- no demotion.
        cfg = _parse(
            {
                "job": {
                    "iv": {
                        "command": "true",
                        "schedule": "daily",
                        "interactive": True,
                    }
                },
                "job-group": {
                    "g": {"jobs": ["iv"], "schedule": "daily"},
                },
                "target": {"darwin": {"jobs": ["g"]}},
            }
        )
        assert "g" in cfg.job_groups
        assert cfg.job_groups["g"].jobs == ["iv"]
        assert "iv" in cfg.jobs

    def test_nested_groups_supported(self) -> None:
        # A group can reference another group; only the chain to a
        # target needs to contain a schedule somewhere.
        cfg = _parse(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "leaf": {"jobs": ["a"]},
                    "root": {"jobs": ["leaf"], "schedule": "daily"},
                },
                "target": {"darwin": {"jobs": ["root"]}},
            }
        )
        assert "leaf" in cfg.job_groups
        assert "root" in cfg.job_groups
        assert cfg.job_groups["leaf"].schedule is None
        assert cfg.job_groups["root"].schedule == "daily"

    def test_chain_without_schedule_rejected(self) -> None:
        # A target reaches `a` via a chain with no schedule anywhere:
        # `a` would never fire. The target carries the per-entity
        # error since the chain belongs to it.
        _assert_errored_platform_target(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "leaf": {"jobs": ["a"]},
                    "root": {"jobs": ["leaf"]},
                },
                "target": {"darwin": {"jobs": ["root"]}},
            },
            "darwin",
            "no schedule anywhere",
        )

    def test_chain_cycle_rejected(self) -> None:
        _assert_errored_platform_target(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g1": {"jobs": ["g2"], "schedule": "daily"},
                    "g2": {"jobs": ["g1"]},
                },
                "target": {"darwin": {"jobs": ["g1"]}},
            },
            "darwin",
            "cycle in group chain",
        )

    def test_multi_parent_target_and_group_rejected(self) -> None:
        # Target lists both group G and job A directly; G also lists
        # A. Within this target's subtree A has two parents, so the
        # platform schedulers would dispatch A twice per fire.
        _assert_errored_platform_target(
            {
                "job": {
                    "a": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                },
                "job-group": {
                    "g": {"jobs": ["a"], "schedule": "daily"},
                },
                "target": {"darwin": {"jobs": ["g", "a"]}},
            },
            "darwin",
            "multiple parents",
        )

    def test_multi_parent_two_groups_rejected(self) -> None:
        # Two groups under the same scheduled root both list job A.
        # Walked from the target, A has two parent groups.
        _assert_errored_platform_target(
            {
                "job": {
                    "a": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                },
                "job-group": {
                    "g1": {"jobs": ["a"]},
                    "g2": {"jobs": ["a"]},
                    "root": {
                        "jobs": ["g1", "g2"],
                        "schedule": "daily",
                    },
                },
                "target": {"darwin": {"jobs": ["root"]}},
            },
            "darwin",
            "multiple parents",
        )

    def test_multi_parent_duplicate_in_list_rejected(self) -> None:
        # Same parent referencing the same child twice in its `jobs`
        # list still doubles the dispatch on every fire, so it's
        # also flagged.
        _assert_errored_platform_target(
            {
                "job": {
                    "a": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                },
                "target": {"darwin": {"jobs": ["a", "a"]}},
            },
            "darwin",
            "multiple parents",
        )

    def test_multi_parent_cross_target_allowed(self) -> None:
        # Two targets each listing the same group is fine: only one
        # target activates on a given host, so the dispatch graphs
        # are disjoint at runtime.
        cfg = _parse(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
                "target": {
                    "darwin": {"jobs": ["g"]},
                    "linux": {"jobs": ["g"]},
                    "host": {"squee": {"jobs": ["g"]}},
                },
            }
        )
        assert "g" in cfg.job_groups

    def test_target_references_undefined_name(self) -> None:
        _assert_errored_platform_target(
            {"target": {"darwin": {"jobs": ["nope"]}}},
            "darwin",
            "undefined name",
        )

    def test_host_target_references_undefined_name(self) -> None:
        _assert_errored_host_target(
            {"target": {"host": {"my-host": {"jobs": ["nope"]}}}},
            "my-host",
            "undefined name",
        )

    def test_one_bad_target_does_not_drop_siblings(self) -> None:
        # A typo'd target.linux must not take target.darwin with
        # it -- per-target failures stay scoped.
        cfg = _parse(
            {
                "job": {"a": _job()},
                "target": {
                    "darwin": {"jobs": ["a"]},
                    "linux": {"jobs": ["nope"]},
                },
            }
        )
        assert "linux" in cfg.errored_platform_targets
        assert "darwin" in cfg.platform_targets
        assert "linux" not in cfg.platform_targets

    def test_target_references_group_ok(self) -> None:
        cfg = _parse(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
                "target": {"darwin": {"jobs": ["g"]}},
            }
        )
        assert "g" in cfg.job_groups

    def test_unreferenced_schedule_less_job_is_dead_weight(self) -> None:
        # A schedule-less job not reachable from any target is dead
        # weight but harmless -- the user might be staging. Validation
        # only fires when a target reaches a chain without a schedule;
        # this config has no target, so it parses fine.
        cfg = _parse({"job": {"a": {"command": "true"}}})
        assert "a" in cfg.jobs

    def test_target_reaching_schedule_less_job_directly_rejected(
        self,
    ) -> None:
        # A target referencing a job with no schedule and no chain
        # to a schedule is the canonical "this would never fire"
        # case. The target carries the per-entity error.
        _assert_errored_platform_target(
            {
                "job": {"a": {"command": "true"}},
                "target": {"darwin": {"jobs": ["a"]}},
            },
            "darwin",
            "no schedule anywhere",
        )

    def test_referenced_group_only_job_ok(self) -> None:
        cfg = _parse(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
            }
        )
        assert "a" in cfg.jobs

    def test_platform_filter_is_silent_skip_not_validate_error(
        self, monkeypatch: Any
    ) -> None:
        # Parsing a config that targets a darwin-only job from a
        # linux target is allowed: filtering happens at selection
        # time so a single bundle can describe entries for
        # multiple platforms and each host picks up only its
        # applicable subset.
        monkeypatch.setattr(crony, "current_platform", lambda: "linux")
        monkeypatch.setattr(crony, "current_host", lambda: "host-l")
        cfg = _parse(
            {
                "job": {
                    "a": _job(platforms=["darwin"]),
                },
                "target": {"linux": {"jobs": ["a"]}},
            }
        )
        # Parse succeeds; selection on linux excludes `a`.
        target = crony.resolve_target(cfg, "host-l", "linux")
        sel_jobs, _ = crony.selected_jobs_and_groups(cfg, target)
        assert "a" not in sel_jobs


class TestUnknownTopLevel:
    def test_unknown_toplevel_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="unknown"):
            _parse({"surprise": {}})


# =============================================================================
# Loading from file
# =============================================================================


class TestLoadConfigFromFile:
    def test_loads_valid_config(self, tmp_path: Path) -> None:
        cfg_text = _uuid_toml(
            "[defaults]\n"
            "notify_channels = []\n"
            "\n"
            "[job.brew-update]\n"
            'command = "brew update && brew upgrade"\n'
            'schedule = "*-*-* 03:15"\n'
        )
        f = tmp_path / "config.toml"
        f.write_text(cfg_text)
        cfg = crony.load_toml_bundle_config(f)
        assert "brew-update" in cfg.jobs
        assert cfg.jobs["brew-update"].schedule == "*-*-* 03:15"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(crony.ConfigError, match="not found"):
            crony.load_toml_bundle_config(tmp_path / "absent.toml")

    def test_bad_toml(self, tmp_path: Path) -> None:
        f = tmp_path / "config.toml"
        f.write_text("this is not [toml")
        with pytest.raises(crony.ConfigError, match="TOML parse error"):
            crony.load_toml_bundle_config(f)


# =============================================================================
# Resolution
# =============================================================================


class TestResolution:
    def test_host_target_wins(self) -> None:
        cfg = _parse(
            {
                "job": {"a": _job(), "b": _job()},
                "target": {
                    "darwin": {"jobs": ["a"]},
                    "host": {"my-host": {"jobs": ["b"]}},
                },
            }
        )
        target = crony.resolve_target(cfg, "my-host", "darwin")
        assert target is not None
        assert target.jobs == ["b"]
        assert target.kind == "host"

    def test_falls_back_to_platform(self) -> None:
        cfg = _parse(
            {
                "job": {"a": _job()},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "other-host", "darwin")
        assert target is not None
        assert target.jobs == ["a"]
        assert target.kind == "platform"

    def test_no_target_returns_none(self) -> None:
        cfg = _parse({})
        assert crony.resolve_target(cfg, "h", "darwin") is None

    def test_selected_includes_group_children(self) -> None:
        cfg = _parse(
            {
                "job": {
                    "a": {"command": "true"},
                    "b": {"command": "true"},
                    "c": _job(),
                },
                "job-group": {
                    "g": {
                        "jobs": ["a", "b"],
                        "schedule": "daily",
                    }
                },
                "target": {"darwin": {"jobs": ["g", "c"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        jobs, groups = crony.selected_jobs_and_groups(cfg, target)
        assert jobs == {"a", "b", "c"}
        assert groups == {"g"}

    def test_selected_for_no_target_is_empty(self) -> None:
        cfg = _parse({})
        jobs, groups = crony.selected_jobs_and_groups(cfg, None)
        assert jobs == set()
        assert groups == set()

    def test_notify_target_wins(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": [],
                    "notify": {
                        "email": _email_block(),
                        "ntfy": _ntfy_block(),
                    },
                },
                "job": {"a": _job(notify_channels=["email"])},
                "target": {
                    "darwin": {
                        "jobs": ["a"],
                        "notify_channels": ["ntfy"],
                    }
                },
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_notify_channels(cfg, target, cfg.jobs["a"]) == [
            "ntfy"
        ]

    def test_notify_job_overrides_defaults(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": [],
                    "notify": {"email": _email_block()},
                },
                "job": {"a": _job(notify_channels=["email"])},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_notify_channels(cfg, target, cfg.jobs["a"]) == [
            "email"
        ]

    def test_notify_default_fallback(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {"ntfy": _ntfy_block()},
                },
                "job": {"a": _job()},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_notify_channels(cfg, target, cfg.jobs["a"]) == [
            "ntfy"
        ]

    def test_notify_target_empty_list_overrides_job(self) -> None:
        # An explicit empty list at the target layer suppresses
        # job-level channels (no inheritance from defaults).
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {
                        "email": _email_block(),
                        "ntfy": _ntfy_block(),
                    },
                },
                "job": {"a": _job(notify_channels=["email"])},
                "target": {
                    "darwin": {
                        "jobs": ["a"],
                        "notify_channels": [],
                    }
                },
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_notify_channels(cfg, target, cfg.jobs["a"]) == []

    def test_notify_multi_channel_resolution(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": [],
                    "notify": {
                        "email": _email_block(),
                        "ntfy": _ntfy_block(),
                    },
                },
                "job": {
                    "a": _job(notify_channels=["email", "ntfy"]),
                },
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_notify_channels(cfg, target, cfg.jobs["a"]) == [
            "email",
            "ntfy",
        ]

    def test_timeout_cascade_job_overrides_defaults(self) -> None:
        cfg = _parse(
            {
                "defaults": {"job_timeout_sec": 100},
                "job": {"a": _job(job_timeout_sec=200)},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_job_timeout_sec(cfg, target, cfg.jobs["a"]) == 200

    def test_target_rejects_job_timeout_sec(self) -> None:
        # Targets deliberately have no timeout knob: timeouts are a
        # per-leaf-job concern. An attempt to set one in the target
        # block must surface as a config error, not silently no-op.
        with pytest.raises(crony.ConfigError, match="unknown key"):
            _parse(
                {
                    "job": {"a": _job()},
                    "target": {
                        "darwin": {"jobs": ["a"], "job_timeout_sec": 300}
                    },
                }
            )

    def test_timeout_default_fallback(self) -> None:
        cfg = _parse(
            {
                "defaults": {"job_timeout_sec": 100},
                "job": {"a": _job()},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_job_timeout_sec(cfg, target, cfg.jobs["a"]) == 100


def _bundle_set(*pairs: tuple[str, Any]) -> Any:
    """Wrap (name, TomlBundleConfig) pairs into a TomlConfig."""
    tc = crony.TomlConfig()
    for name, cfg in pairs:
        tc.bundles.append(
            crony.TomlBundle(
                name=name, source=Path(f"/test/{name}.toml"), config=cfg
            )
        )
    return tc


class TestNotifyInherit:
    """`notify_channels = ["default"]` inherit sentinel: a non-default
    bundle notifies as the default bundle would, and inherits it
    implicitly when it omits notify config.
    """

    def test_implicit_default_for_nondefault_bundle(self) -> None:
        cfg = crony.parse_config(
            _inject_uuids({"job": {"a": _job()}}), bundle_name="borgadm"
        )
        assert cfg.defaults.notify_channels == [crony.NOTIFY_INHERIT_TOKEN]

    def test_implicit_default_with_defaults_but_no_notify(self) -> None:
        cfg = crony.parse_config(
            _inject_uuids(
                {"defaults": {"job_timeout_sec": 60}, "job": {"a": _job()}}
            ),
            bundle_name="borgadm",
        )
        assert cfg.defaults.notify_channels == [crony.NOTIFY_INHERIT_TOKEN]

    def test_default_bundle_stays_empty(self) -> None:
        cfg = crony.parse_config(_inject_uuids({"job": {"a": _job()}}))
        assert cfg.defaults.notify_channels == []

    def test_explicit_empty_opts_out(self) -> None:
        cfg = crony.parse_config(
            _inject_uuids(
                {"defaults": {"notify_channels": []}, "job": {"a": _job()}}
            ),
            bundle_name="borgadm",
        )
        assert cfg.defaults.notify_channels == []

    def test_explicit_token_in_nondefault_ok(self) -> None:
        cfg = crony.parse_config(
            _inject_uuids(
                {
                    "defaults": {"notify_channels": ["default"]},
                    "job": {"a": _job()},
                }
            ),
            bundle_name="borgadm",
        )
        assert cfg.defaults.notify_channels == ["default"]

    def test_token_rejected_in_default_bundle(self) -> None:
        with pytest.raises(crony.ConfigError, match="cannot inherit its own"):
            crony.parse_config(
                _inject_uuids({"defaults": {"notify_channels": ["default"]}})
            )

    def test_token_must_be_sole_entry(self) -> None:
        with pytest.raises(crony.ConfigError, match="must be the only"):
            crony.parse_config(
                _inject_uuids(
                    {
                        "defaults": {
                            "notify_channels": ["default", "email"],
                            "notify": {"email": _email_block()},
                        }
                    }
                ),
                bundle_name="borgadm",
            )

    def test_reserved_channel_name_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="reserved channel name"):
            crony.parse_config(
                _inject_uuids(
                    {"defaults": {"notify": {"default": _ntfy_block()}}}
                )
            )

    def test_job_level_token_ok_in_nondefault(self) -> None:
        cfg = crony.parse_config(
            _inject_uuids({"job": {"a": _job(notify_channels=["default"])}}),
            bundle_name="borgadm",
        )
        assert "a" in cfg.jobs
        assert cfg.jobs["a"].notify_channels == ["default"]

    def test_job_level_token_demoted_in_default(self) -> None:
        cfg = crony.parse_config(
            _inject_uuids({"job": {"a": _job(notify_channels=["default"])}})
        )
        assert "a" in cfg.errored_jobs
        assert "cannot inherit its own" in cfg.errored_jobs["a"]

    def _two_bundles(self, default_notify: list[str]) -> tuple[Any, Any]:
        default_cfg = crony.parse_config(
            _inject_uuids(
                {
                    "defaults": {
                        "notify_channels": default_notify,
                        "notify": {
                            "ntfy": _ntfy_block(),
                            "email": _email_block(),
                        },
                    }
                }
            )
        )
        borgadm_cfg = crony.parse_config(
            _inject_uuids({"job": {"a": _job()}}), bundle_name="borgadm"
        )
        return default_cfg, borgadm_cfg

    def test_expand_pulls_default_bundle(self) -> None:
        default_cfg, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(
            ("default", default_cfg), ("borgadm", borgadm_cfg)
        )
        channels, defaults = crony._expand_notify_inherit(
            ["default"], "borgadm", bundles, borgadm_cfg.defaults
        )
        assert channels == ["ntfy"]
        # Dispatch sources channel defs + attach settings from the
        # default bundle, not the inheriting one.
        assert defaults is default_cfg.defaults

    def test_expand_noop_for_non_sentinel(self) -> None:
        default_cfg, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(
            ("default", default_cfg), ("borgadm", borgadm_cfg)
        )
        channels, defaults = crony._expand_notify_inherit(
            ["email"], "borgadm", bundles, borgadm_cfg.defaults
        )
        assert channels == ["email"]
        assert defaults is borgadm_cfg.defaults

    def test_expand_default_self_inherit_guarded(self) -> None:
        default_cfg, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(
            ("default", default_cfg), ("borgadm", borgadm_cfg)
        )
        channels, defaults = crony._expand_notify_inherit(
            ["default"], "default", bundles, default_cfg.defaults
        )
        assert channels == []
        assert defaults is default_cfg.defaults

    def test_expand_missing_default_bundle(self) -> None:
        _, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(("borgadm", borgadm_cfg))
        channels, defaults = crony._expand_notify_inherit(
            ["default"], "borgadm", bundles, borgadm_cfg.defaults
        )
        assert channels == []
        assert defaults is borgadm_cfg.defaults

    def test_expand_drops_recursive_token(self) -> None:
        default_cfg, borgadm_cfg = self._two_bundles(["ntfy"])
        # A (malformed) default bundle that itself carries the token:
        # expansion drops it rather than recursing.
        default_cfg.defaults.notify_channels = ["default", "ntfy"]
        bundles = _bundle_set(
            ("default", default_cfg), ("borgadm", borgadm_cfg)
        )
        channels, _ = crony._expand_notify_inherit(
            ["default"], "borgadm", bundles, borgadm_cfg.defaults
        )
        assert channels == ["ntfy"]

    def test_runtime_inherit_and_per_job_disable(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The borgadm shape: a non-default bundle inherits the default
        # bundle's channels, while one noisy job opts out with [].
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {"ntfy": _ntfy_block()},
                }
            },
            default_target_jobs=[],
        )
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.check]\ncommand = "true"\n'
                '[job.create]\ncommand = "true"\n'
                "notify_channels = []\n"
            ),
            encoding="utf-8",
        )
        channels, defaults = crony._resolve_notify_at_runtime("borgadm.check")
        assert channels == ["ntfy"]
        assert "ntfy" in defaults.notify_channel_defs
        disabled, _ = crony._resolve_notify_at_runtime("borgadm.create")
        assert disabled == []


class TestSelectionFilters:
    """Per-entry `platforms` / `hosts` filters silently filter
    entries out of the selection on incompatible (host, platform).
    Both TomlJob and TomlJobGroup carry the same fields with the same
    semantics; a filtered group does not recurse into its
    children.
    """

    def _cfg(self, raw: dict[str, Any]) -> Any:
        return _parse(raw)

    def test_job_with_matching_platform_selected(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        cfg = self._cfg(
            {
                "job": {"a": _job(platforms=["darwin"])},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        sel_jobs, _ = crony.selected_jobs_and_groups(cfg, target)
        assert "a" in sel_jobs

    def test_job_with_excluding_platform_filtered_out(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        monkeypatch.setattr(crony, "current_platform", lambda: "linux")
        cfg = self._cfg(
            {
                "job": {"a": _job(platforms=["darwin"])},
                "target": {"linux": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "linux")
        sel_jobs, _ = crony.selected_jobs_and_groups(cfg, target)
        assert "a" not in sel_jobs

    def test_job_hosts_filter(self, monkeypatch: Any) -> None:
        cfg = self._cfg(
            {
                "job": {"a": _job(hosts=["alpha", "beta"])},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        # On a listed host: selected.
        monkeypatch.setattr(crony, "current_host", lambda: "alpha")
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        target = crony.resolve_target(cfg, "alpha", "darwin")
        sel_jobs, _ = crony.selected_jobs_and_groups(cfg, target)
        assert "a" in sel_jobs
        # On a non-listed host: filtered out.
        monkeypatch.setattr(crony, "current_host", lambda: "gamma")
        target = crony.resolve_target(cfg, "gamma", "darwin")
        sel_jobs, _ = crony.selected_jobs_and_groups(cfg, target)
        assert "a" not in sel_jobs

    def test_group_filter_skips_recursion(self, monkeypatch: Any) -> None:
        # When a group's filter excludes the current host /
        # platform, the walk does not recurse into its children
        # via that group. A child reachable only through the
        # filtered group is therefore not selected.
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        monkeypatch.setattr(crony, "current_platform", lambda: "linux")
        cfg = self._cfg(
            {
                "job": {"a": _job()},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "platforms": ["darwin"],
                    },
                },
                "target": {"linux": {"jobs": ["g"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "linux")
        sel_jobs, sel_groups = crony.selected_jobs_and_groups(cfg, target)
        assert "g" not in sel_groups
        assert "a" not in sel_jobs

    def test_group_hosts_filter(self, monkeypatch: Any) -> None:
        cfg = self._cfg(
            {
                "job": {"a": _job()},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "hosts": ["alpha"],
                    },
                },
                "target": {"darwin": {"jobs": ["g"]}},
            }
        )
        # On the listed host: group + child selected.
        monkeypatch.setattr(crony, "current_host", lambda: "alpha")
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        target = crony.resolve_target(cfg, "alpha", "darwin")
        sel_jobs, sel_groups = crony.selected_jobs_and_groups(cfg, target)
        assert "g" in sel_groups
        assert "a" in sel_jobs
        # On a different host: group filtered, child not reached.
        monkeypatch.setattr(crony, "current_host", lambda: "beta")
        target = crony.resolve_target(cfg, "beta", "darwin")
        sel_jobs, sel_groups = crony.selected_jobs_and_groups(cfg, target)
        assert "g" not in sel_groups
        assert "a" not in sel_jobs

    def test_job_hosts_filter_negated(self, monkeypatch: Any) -> None:
        # A `hosts = ["!squee"]` filter is a denylist: the job
        # applies on every host except the listed ones.
        cfg = self._cfg(
            {
                "job": {"a": _job(hosts=["!squee"])},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        # On a non-listed host: selected.
        monkeypatch.setattr(crony, "current_host", lambda: "alpha")
        target = crony.resolve_target(cfg, "alpha", "darwin")
        sel_jobs, _ = crony.selected_jobs_and_groups(cfg, target)
        assert "a" in sel_jobs
        # On the listed (denied) host: filtered out.
        monkeypatch.setattr(crony, "current_host", lambda: "squee")
        target = crony.resolve_target(cfg, "squee", "darwin")
        sel_jobs, _ = crony.selected_jobs_and_groups(cfg, target)
        assert "a" not in sel_jobs

    def test_group_hosts_filter_negated(self, monkeypatch: Any) -> None:
        cfg = self._cfg(
            {
                "job": {"a": _job()},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "hosts": ["!squee"],
                    },
                },
                "target": {"darwin": {"jobs": ["g"]}},
            }
        )
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        # On a non-denied host: group + child selected.
        monkeypatch.setattr(crony, "current_host", lambda: "alpha")
        target = crony.resolve_target(cfg, "alpha", "darwin")
        sel_jobs, sel_groups = crony.selected_jobs_and_groups(cfg, target)
        assert "g" in sel_groups
        assert "a" in sel_jobs
        # On the denied host: group filtered, child not reached.
        monkeypatch.setattr(crony, "current_host", lambda: "squee")
        target = crony.resolve_target(cfg, "squee", "darwin")
        sel_jobs, sel_groups = crony.selected_jobs_and_groups(cfg, target)
        assert "g" not in sel_groups
        assert "a" not in sel_jobs

    def test_group_with_all_masked_children_is_masked_empty(
        self, monkeypatch: Any
    ) -> None:
        # A group whose every direct child is itself masked on
        # this host has nothing to dispatch -- the reference is a
        # no-op and the group joins masked with reason "empty".
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        monkeypatch.setattr(crony, "current_platform", lambda: "linux")
        cfg = self._cfg(
            {
                "job": {
                    "a": _job(hosts=["other"]),
                    "b": _job(platforms=["darwin"]),
                },
                "job-group": {
                    "g": {"jobs": ["a", "b"], "schedule": "daily"},
                },
                "target": {"linux": {"jobs": ["g"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "linux")
        _sel_jobs, sel_groups, masked = (
            crony.selected_and_masked_jobs_and_groups(cfg, target)
        )
        assert "g" not in sel_groups
        assert masked.get("g") == "empty"
        assert masked.get("a") == "host"
        assert masked.get("b") == "platform"

    def test_empty_group_cascade_propagates_to_parents(
        self, monkeypatch: Any
    ) -> None:
        # P references only G, and G's only child is masked.
        # G becomes "empty"; the cascade then demotes P, whose
        # only remaining child is now masked.
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        monkeypatch.setattr(crony, "current_platform", lambda: "linux")
        cfg = self._cfg(
            {
                "job": {"a": _job(hosts=["other"])},
                "job-group": {
                    "g": {"jobs": ["a"], "schedule": "daily"},
                    "p": {"jobs": ["g"], "schedule": "daily"},
                },
                "target": {"linux": {"jobs": ["p"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "linux")
        _sel_jobs, sel_groups, masked = (
            crony.selected_and_masked_jobs_and_groups(cfg, target)
        )
        assert "g" not in sel_groups
        assert "p" not in sel_groups
        assert masked.get("g") == "empty"
        assert masked.get("p") == "empty"

    def test_group_with_partial_unmasked_child_stays_selected(
        self, monkeypatch: Any
    ) -> None:
        # If at least one direct child is unmasked, the group is
        # NOT empty -- it remains selected and its snapshot will
        # reference that one child.
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        monkeypatch.setattr(crony, "current_platform", lambda: "linux")
        cfg = self._cfg(
            {
                "job": {
                    "a": _job(),
                    "b": _job(hosts=["other"]),
                },
                "job-group": {
                    "g": {"jobs": ["a", "b"], "schedule": "daily"},
                },
                "target": {"linux": {"jobs": ["g"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "linux")
        sel_jobs, sel_groups, masked = (
            crony.selected_and_masked_jobs_and_groups(cfg, target)
        )
        assert "g" in sel_groups
        assert "a" in sel_jobs
        assert masked.get("b") == "host"
        assert "g" not in masked


# =============================================================================
# crony config init
# =============================================================================


class TestInit:
    """do_init writes the default config template, refuses to clobber."""

    def _redirect_config(self, monkeypatch: Any, tmp_path: Path) -> Path:
        """Point CONFIG_DIR / CONFIG_FILE at a tmp dir so do_init
        doesn't touch the user's real ~/.config/crony.
        """
        cfg_dir = tmp_path / "crony"
        cfg_file = cfg_dir / "config.toml"
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        return cfg_file

    def test_creates_file_when_absent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        assert not cfg_file.exists()
        crony.do_init(force=False, bundle=None)
        assert cfg_file.exists()
        body = cfg_file.read_text(encoding="utf-8")
        assert "[defaults]" in body
        assert "[job." in body
        assert "[job-group." in body
        assert "[target." in body

    def test_creates_parent_dir(self, tmp_path: Path, monkeypatch: Any) -> None:
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        # Parent doesn't exist yet.
        assert not cfg_file.parent.exists()
        crony.do_init(force=False, bundle=None)
        assert cfg_file.parent.is_dir()

    def test_refuses_to_overwrite_without_force(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text("user content", encoding="utf-8")
        with pytest.raises(crony.UsageError, match="already exists"):
            crony.do_init(force=False, bundle=None)
        # File untouched.
        assert cfg_file.read_text() == "user content"

    def test_overwrites_with_force(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text("user content", encoding="utf-8")
        crony.do_init(force=True, bundle=None)
        body = cfg_file.read_text(encoding="utf-8")
        assert "user content" not in body
        assert "[defaults]" in body

    def test_template_is_ascii_only(self) -> None:
        """All persistent files in this repo are ASCII; the template
        we ship as a starting point must be too.
        """
        crony._DEFAULT_CONFIG_TEMPLATE.encode("ascii")  # raises if not

    def test_bundle_writes_to_dropin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_dir = tmp_path / "crony"
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = cfg_dir / "config"
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        crony.do_init(force=False, bundle="borgadm")
        target = cfg_dropin / "borgadm.toml"
        assert target.is_file()
        assert "[defaults]" in target.read_text(encoding="utf-8")
        # config.toml is untouched.
        assert not cfg_file.exists()

    def test_bundle_default_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_dir = tmp_path / "crony"
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = cfg_dir / "config"
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        with pytest.raises(crony.UsageError, match="default"):
            crony.do_init(force=False, bundle="default")

    def test_bundle_invalid_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_dir = tmp_path / "crony"
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = cfg_dir / "config"
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        with pytest.raises(crony.UsageError, match="bundle name"):
            crony.do_init(force=False, bundle="has.dot")

    def test_template_parses_when_uncommented(self) -> None:
        """The example schema in the template must be valid TOML.

        Extract section headers (`# [foo]`) and simple key = value
        lines (`# foo = ...`), strip the leading `# `, and feed the
        result to parse_config. Prose comments, dividers, and
        double-commented variants (`# # foo`) don't match the
        strict patterns and are skipped.
        """
        extracted: list[str] = []
        section_re = re.compile(r"^# \[[\w.\-]+\]\s*$")
        kv_re = re.compile(r"^# [A-Za-z_][\w.]*\s*=")
        for line in crony._DEFAULT_CONFIG_TEMPLATE.splitlines():
            if section_re.match(line) or kv_re.match(line):
                extracted.append(line[2:])
        text = "\n".join(extracted)
        _parse(tomlkit.loads(text))


class TestGenerateUuidAction:
    """`crony config generate-uuid` prints a single canonical UUID
    on stdout. Used by users hand-editing a config before the file
    is otherwise valid (the `config update` path requires a parsable
    file).
    """

    def test_emits_one_canonical_uuid(self, capsys: Any) -> None:
        crony.do_generate_uuid()
        out = capsys.readouterr().out.strip()
        parsed = uuid.UUID(out)
        assert str(parsed) == out


class TestConfigUpdateAction:
    """`crony config update` assigns UUIDs to jobs and groups that
    lack one, rewriting the bundle file in place via tomlkit so
    comments and key order survive. Files where every job and
    group already has a UUID are skipped (no rewrite).
    """

    def _redirect(self, monkeypatch: Any, tmp_path: Path) -> tuple[Path, Path]:
        cfg_dir = tmp_path / "crony"
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = cfg_dir / "config"
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        return cfg_file, cfg_dropin

    def test_fills_missing_uuids_in_default_bundle(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file, _ = self._redirect(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(
            "[job.a]\n"
            'command = "true"\n'
            'schedule = "daily"\n'
            "\n"
            "[job-group.g]\n"
            'jobs = ["a"]\n'
            'schedule = "daily"\n',
            encoding="utf-8",
        )
        crony.do_config_update(bundle=None)
        doc = tomlkit.loads(cfg_file.read_text(encoding="utf-8"))
        a_uuid = doc["job"]["a"]["uuid"]
        g_uuid = doc["job-group"]["g"]["uuid"]
        assert isinstance(a_uuid, str)
        assert isinstance(g_uuid, str)
        # Distinct, valid uuid4 form.
        assert a_uuid != g_uuid
        assert str(uuid.UUID(a_uuid)) == a_uuid
        assert str(uuid.UUID(g_uuid)) == g_uuid

    def test_idempotent_when_all_present(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file, _ = self._redirect(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        body = (
            "[job.a]\n"
            'uuid = "12345678-1234-5678-1234-567812345678"\n'
            'command = "true"\n'
            'schedule = "daily"\n'
        )
        cfg_file.write_text(body, encoding="utf-8")
        mtime_before = cfg_file.stat().st_mtime_ns
        crony.do_config_update(bundle=None)
        # Body must be byte-for-byte identical AND mtime untouched:
        # the no-change path skips the write_text entirely.
        assert cfg_file.read_text(encoding="utf-8") == body
        assert cfg_file.stat().st_mtime_ns == mtime_before

    def test_preserves_comments_and_key_order(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file, _ = self._redirect(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        body = (
            "# top-level comment\n"
            "[job.a]\n"
            "# inline comment for command\n"
            'command = "true"\n'
            'schedule = "daily"\n'
        )
        cfg_file.write_text(body, encoding="utf-8")
        crony.do_config_update(bundle=None)
        new_body = cfg_file.read_text(encoding="utf-8")
        # Comments survive the round-trip.
        assert "# top-level comment" in new_body
        assert "# inline comment for command" in new_body
        # Pre-existing keys keep their relative order.
        cmd_pos = new_body.index("command")
        sched_pos = new_body.index("schedule")
        assert cmd_pos < sched_pos

    def test_scoped_to_one_bundle(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file, cfg_dropin = self._redirect(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_dropin.mkdir(parents=True)
        cfg_file.write_text(
            '[job.a]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        other = cfg_dropin / "borgadm.toml"
        other.write_text(
            '[job.a]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        crony.do_config_update(bundle="borgadm")
        # The borgadm bundle got a uuid; the default bundle did not.
        other_doc = tomlkit.loads(other.read_text(encoding="utf-8"))
        default_doc = tomlkit.loads(cfg_file.read_text(encoding="utf-8"))
        assert "uuid" in other_doc["job"]["a"]
        assert "uuid" not in default_doc["job"]["a"]

    def test_unknown_bundle_errors(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file, _ = self._redirect(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(
            '[job.a]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        with pytest.raises(crony.UsageError, match="bundle 'ghost'"):
            crony.do_config_update(bundle="ghost")

    def test_no_config_at_all_errors(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._redirect(monkeypatch, tmp_path)
        with pytest.raises(crony.ConfigError, match="no config"):
            crony.do_config_update(bundle=None)

    def test_parse_error_in_one_file_does_not_block_others(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        cfg_file, cfg_dropin = self._redirect(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_dropin.mkdir(parents=True)
        cfg_file.write_text(
            '[job.a]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        broken = cfg_dropin / "broken.toml"
        broken.write_text("not valid toml [[[", encoding="utf-8")
        with caplog.at_level("ERROR"):
            crony.do_config_update(bundle=None)
        # Default bundle's job got a uuid even though broken.toml failed.
        default_doc = tomlkit.loads(cfg_file.read_text(encoding="utf-8"))
        assert "uuid" in default_doc["job"]["a"]
        # The broken file was reported.
        assert any("broken.toml" in r.message for r in caplog.records)


# =============================================================================
# Runner shim
# =============================================================================


class _RunnerHarness:
    """Isolated state + config so runner tests don't touch the real
    ~/.local/state/crony. Sets the in-process module attributes
    (for direct calls into run_job/run_group) and the matching
    CRONY_*_DIR / CRONY_CONFIG_FILE env vars (so subprocess
    re-invocations from group dispatch see the same paths).
    """

    def __init__(self, tmp_path: Path, monkeypatch: Any) -> None:
        state = tmp_path / "state"
        state.mkdir()
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        # Empty dropin dir so multi-bundle discovery doesn't pick up
        # anything outside the test's CRONY_CONFIG_FILE.
        cfg_dropin = tmp_path / "config_dropin"
        cfg_dropin.mkdir()
        monkeypatch.setenv("CRONY_STATE_DIR", str(state))
        monkeypatch.setenv("CRONY_CONFIG_DIR", str(cfg_dir))
        monkeypatch.setenv("CRONY_CONFIG_FILE", str(cfg_file))
        monkeypatch.setenv("CRONY_CONFIG_DROPIN_DIR", str(cfg_dropin))
        monkeypatch.setattr(crony, "STATE_DIR", state)
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony, "current_host", lambda: "test-host")
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        self.state = state
        self.cfg_file = cfg_file
        self.cfg_dropin = cfg_dropin
        self._last_cfg: Any | None = None
        # Stable short -> uuid mapping across successive `config()`
        # calls so a test that builds a fresh config to simulate a
        # drift / edit keeps the same identity for an entry, not a
        # fresh one (which would look like a delete + add to apply).
        self._uuid_pins: dict[tuple[str, str], dict[str, str]] = {}

    def full(self, short: str) -> str:
        """The full namespaced name for a short job/group name in the
        default bundle, used for unit-label assertions and CLI
        argument construction.
        """
        return f"{crony.DEFAULT_BUNDLE_NAME}.{short}"

    def fabricate_orphan(
        self,
        short: str,
        *,
        bundle: str = crony.DEFAULT_BUNDLE_NAME,
        kind: str = "job",
    ) -> Path:
        """Plant a state dir whose snapshot records a name that no
        live config selects -- i.e. a remnant of a previously-applied
        entry that's since been removed. Used by orphan-detection
        tests that need to verify the apply / destroy paths see and
        clean up such remnants.
        """
        # Use a deterministic uuid so re-stamps in the same test
        # collide on the same dir rather than fanning out. The uuid
        # format here is arbitrary -- the directory name itself is
        # the storage key (no canonical-uuid4 validation), and
        # production lookups go through `Config.current.by_full_name`
        # built by `_build_current_graph`'s tree walk.
        orphan_uuid = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"orphan/{bundle}.{short}")
        )
        sd = self.state / bundle / orphan_uuid
        sd.mkdir(parents=True, exist_ok=True)
        # Fully populated snapshot matching what apply would have
        # written. load_config skips snapshots missing required
        # fields (TypeError from Job(**raw)); incomplete fixtures
        # would silently fall out of the current graph.
        snapshot: dict[str, Any] = {
            "schema": crony._SNAPSHOT_SCHEMA,
            "kind": kind,
            "name": f"{bundle}.{short}",
            "bundle": bundle,
            "uuid": orphan_uuid,
        }
        if kind == "job":
            snapshot.update(
                {
                    "command": "true",
                    "script": None,
                    "args": [],
                    "gate": None,
                    "gate_script": None,
                    "gate_args": [],
                    "env": {},
                    "job_timeout_sec": 600,
                    "schedule": "daily",
                    "interval": None,
                    "interactive": False,
                    "interactive_active_sec": 600,
                    "interactive_delay_sec": 3600,
                }
            )
        else:
            snapshot.update(
                {
                    "children": [],
                    "group_budget_sec": 600,
                    "trigger_timeout_sec": 15,
                    "schedule": "daily",
                    "interval": None,
                }
            )
        (sd / "snapshot.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        return sd

    def last_run(self, short: str, cfg: Any | None = None) -> dict[str, Any]:
        """Read last-run.json for `<default>.<short>` and return its
        parsed dict.
        """
        return _cast_dict(
            (self.state_dir(short, cfg=cfg) / "last-run.json").read_text()
        )

    def state_dir(
        self,
        short: str,
        cfg: Any | None = None,
        *,
        bundle: str = crony.DEFAULT_BUNDLE_NAME,
        ensure_snapshot: bool = True,
    ) -> Path:
        """uuid-keyed state directory for `<bundle>.<short>`.

        `cfg` is the parsed TomlBundleConfig for the bundle, used to look up the
        entry's uuid. Pass it explicitly when the harness's `_last_cfg`
        is stale (e.g. mid-test config rewrite that doesn't go through
        the harness). Most callers omit it and rely on the most-recent
        `h.config(...)` result.

        `ensure_snapshot=True` materializes a minimal snapshot.json so
        the runtime helpers that resolve a state dir by walking
        `snapshot.json` (logs, trigger, status) can find it. Useful
        for tests that fabricate a state dir without actually running
        apply.
        """
        cfg = cfg or self._last_cfg
        assert cfg is not None, "no config built yet for state_dir lookup"
        entity_uuid: str = (
            cfg.jobs[short].uuid
            if short in cfg.jobs
            else cfg.job_groups[short].uuid
        )
        sd = self.state / bundle / entity_uuid
        if ensure_snapshot:
            sd.mkdir(parents=True, exist_ok=True)
            snap_p = sd / "snapshot.json"
            if not snap_p.exists():
                kind = "job" if short in cfg.jobs else "group"
                # Fully populated snapshot so load_config can build
                # the entity's runtime state; a partial dict would
                # be skipped by Job(**raw) / JobGroup(**raw).
                payload: dict[str, Any] = {
                    "schema": crony._SNAPSHOT_SCHEMA,
                    "kind": kind,
                    "name": f"{bundle}.{short}",
                    "bundle": bundle,
                    "uuid": entity_uuid,
                }
                if kind == "job":
                    payload.update(
                        {
                            "command": "true",
                            "script": None,
                            "args": [],
                            "gate": None,
                            "gate_script": None,
                            "gate_args": [],
                            "env": {},
                            "job_timeout_sec": 600,
                            "schedule": "daily",
                            "interval": None,
                            "interactive": False,
                            "interactive_active_sec": 600,
                            "interactive_delay_sec": 3600,
                        }
                    )
                else:
                    payload.update(
                        {
                            "children": [],
                            "group_budget_sec": 600,
                            "trigger_timeout_sec": 15,
                            "schedule": "daily",
                            "interval": None,
                        }
                    )
                snap_p.write_text(json.dumps(payload), encoding="utf-8")
        return sd

    def snap(self, cfg: Any, short: str) -> Any:
        """Resolve a snapshot for a default-bundle entry. Convenience
        for runner tests that build a TomlBundleConfig and call run_job /
        run_group directly without going through full apply.
        """
        return crony._resolve_snapshot_for(cfg, short)

    def write_snap(self, cfg: Any, short: str) -> None:
        """Write a snapshot to disk so `_load_snapshot` finds it.
        Used by group runner tests where children are loaded from
        their own snapshot files (not from the parent's config)."""
        snap = self.snap(cfg, short)
        sd = self.state_dir(short, cfg=cfg)
        p = sd / "snapshot.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        import dataclasses as _dc
        import json as _json

        p.write_text(
            _json.dumps(_dc.asdict(snap), sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def config(
        self, raw: dict[str, Any], *, default_target_jobs: list[str]
    ) -> Any:
        """Build a TomlBundleConfig with a target (for the platform
        this harness simulates) selecting these jobs.

        Persists the raw config to the on-disk file so subprocess
        re-invocations of `crony run <child>` (group dispatch) load
        the same config we hand to run_group. The target keys on the
        simulated platform so the entries are actually selected when
        a later `load_config()` resolves the host's target.
        """
        plat = crony.current_platform()
        full = dict(raw)
        full.setdefault("target", {})
        target_section = full["target"]
        if isinstance(target_section, dict):
            target_section.setdefault(plat, {})
            assert isinstance(target_section[plat], dict)
            target_section[plat].setdefault("jobs", default_target_jobs)
        # Re-use uuids previously assigned to the same short name
        # so a successive `h.config(...)` call simulates an edit
        # to the same entity rather than a delete + replace.
        for section in ("job", "job-group"):
            entries = full.get(section)
            if not isinstance(entries, dict):
                continue
            for short, body in entries.items():
                if not isinstance(body, dict) or "uuid" in body:
                    continue
                pinned = self._uuid_pins.get((section, short))
                if pinned is not None:
                    body["uuid"] = pinned
        _inject_uuids(full)
        # Record the (section, short) -> uuid pins for next time.
        for section in ("job", "job-group"):
            entries = full.get(section)
            if not isinstance(entries, dict):
                continue
            for short, body in entries.items():
                if isinstance(body, dict) and isinstance(body.get("uuid"), str):
                    self._uuid_pins[(section, short)] = body["uuid"]
        self.cfg_file.write_text(tomlkit.dumps(full), encoding="utf-8")
        cfg = crony.parse_config(full)
        self._last_cfg = cfg
        return cfg


def _last_run(state: Path, name: str) -> dict[str, Any]:
    """Read last-run.json by job name.

    A bare short name resolves against the default bundle so call
    sites stay terse. A full namespaced name (containing a dot)
    looks up that exact path.
    """
    if "." not in name:
        name = f"{crony.DEFAULT_BUNDLE_NAME}.{name}"
    # State dirs are uuid-keyed under the bundle subdir; resolve
    # the path via the snapshot file rather than reconstructing
    # from the full name (which is no longer the on-disk key).
    bundle, _, _ = name.partition(".")
    bundle_dir = state / bundle
    last_run_path: Path | None = None
    if bundle_dir.is_dir():
        for uuid_dir in bundle_dir.iterdir():
            snap = uuid_dir / "snapshot.json"
            if not snap.is_file():
                continue
            try:
                raw = json.loads(snap.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if raw.get("name") == name:
                last_run_path = uuid_dir / "last-run.json"
                break
    assert last_run_path is not None, (
        f"no state dir for {name!r} under {bundle_dir}"
    )
    text = last_run_path.read_text()
    return _cast_dict(text)


def _cast_dict(text: str) -> dict[str, Any]:
    """Read JSON into a typed dict for test assertions."""
    import json as _json

    out = _json.loads(text)
    assert isinstance(out, dict)
    return out


class TestPathFieldExpansion:
    """`script`, `args`, `gate_script`, and `gate_args` accept `~` and
    `$VAR` / `${VAR}`, mirroring how shell-string `command` fields are
    expanded by `/bin/sh`. Without this, configs that use `$HOME` in
    a script path fail with a misleading "script not found" error
    (the literal `$HOME` gets concatenated under CONFIG_DIR).
    """

    def test_resolve_script_expands_tilde(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("HOME", "/home/user")
        p = crony._resolve_script("~/bin/foo.sh")
        assert str(p) == "/home/user/bin/foo.sh"

    def test_resolve_script_expands_dollar_var(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("HOME", "/home/user")
        p = crony._resolve_script("$HOME/bin/foo.sh")
        assert str(p) == "/home/user/bin/foo.sh"

    def test_resolve_script_expands_braced_var(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("HOME", "/home/user")
        p = crony._resolve_script("${HOME}/bin/foo.sh")
        assert str(p) == "/home/user/bin/foo.sh"

    def test_resolve_script_unresolved_var_stays_literal(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("CRONY_NO_SUCH_VAR", raising=False)
        # When no expansion applies, the value falls under CONFIG_DIR
        # as a relative path. The literal `$VAR` is preserved.
        p = crony._resolve_script("$CRONY_NO_SUCH_VAR/foo.sh")
        assert "$CRONY_NO_SUCH_VAR" in str(p)

    def test_snapshot_resolves_expanded_args(self, monkeypatch: Any) -> None:
        # Path-field expansion is applied at snapshot-resolve time
        # (apply); the runner then pulls already-expanded argv from
        # the snapshot.
        monkeypatch.setenv("HOME", "/home/user")
        job = crony.TomlJob(
            name="j",
            uuid=str(uuid.uuid4()),
            script="/abs/path.sh",
            args=["~/data", "$HOME/cache", "--flag"],
        )
        snap = crony._resolve_job_snapshot(
            crony.TomlBundleConfig(), None, job, "default.j"
        )
        assert snap.script == "/abs/path.sh"
        assert snap.args == [
            "/home/user/data",
            "/home/user/cache",
            "--flag",
        ]
        assert crony._command_argv(snap) == [
            "/abs/path.sh",
            "/home/user/data",
            "/home/user/cache",
            "--flag",
        ]

    def test_snapshot_resolves_expanded_gate_args(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("HOME", "/home/user")
        job = crony.TomlJob(
            name="j",
            uuid=str(uuid.uuid4()),
            command="true",
            gate_script="/abs/gate.sh",
            gate_args=["$HOME/state"],
        )
        snap = crony._resolve_job_snapshot(
            crony.TomlBundleConfig(), None, job, "default.j"
        )
        assert snap.gate_script == "/abs/gate.sh"
        assert snap.gate_args == ["/home/user/state"]
        assert crony._gate_argv(snap) == ["/abs/gate.sh", "/home/user/state"]


class TestRuntimeEnvExpansion:
    """`_runtime_env` is called at fire time with the snapshot's
    user_env dict. It passes the inherited (scheduler-provided) env
    through and overlays user_env, expanding `$VAR` / `${VAR}`
    references in user_env values against the merged env. Called at
    runtime, not apply time, so the inherited env stays current per
    fire.
    """

    def test_inherits_path_when_no_env_override(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        env = crony._runtime_env({})
        assert env["PATH"] == "/usr/bin:/bin"

    def test_session_bus_vars_forwarded(self, monkeypatch: Any) -> None:
        # The linux session-bus locators must reach the job so a
        # command like `crony apply` can drive `systemctl --user`.
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        monkeypatch.setenv(
            "DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus"
        )
        env = crony._runtime_env({})
        assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"
        assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"

    def test_arbitrary_inherited_var_forwarded(self, monkeypatch: Any) -> None:
        # The inherited env passes through wholesale: any var in the
        # runner's environment reaches the job. SSH_AUTH_SOCK rides
        # this path so jobs can reach the user's ssh-agent.
        monkeypatch.setenv("CRONY_INHERIT_PROBE", "passed-through")
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
        env = crony._runtime_env({})
        assert env["CRONY_INHERIT_PROBE"] == "passed-through"
        assert env["SSH_AUTH_SOCK"] == "/tmp/agent.sock"

    def test_unset_var_absent(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("CRONY_INHERIT_PROBE", raising=False)
        env = crony._runtime_env({})
        assert "CRONY_INHERIT_PROBE" not in env

    def test_dollar_var_resolves_against_inherited(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        env = crony._runtime_env({"PATH": "/extra:$PATH"})
        assert env["PATH"] == "/extra:/usr/bin:/bin"

    def test_brace_form_resolves(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("HOME", "/Users/edp")
        env = crony._runtime_env({"TMPDIR": "${HOME}/.local/tmp"})
        assert env["TMPDIR"] == "/Users/edp/.local/tmp"

    def test_expansion_resolves_against_any_inherited_var(
        self, monkeypatch: Any
    ) -> None:
        # Expansion sees the whole inherited env, so a value can
        # reference any inherited var (here the session runtime dir).
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        env = crony._runtime_env({"BUS": "$XDG_RUNTIME_DIR/bus"})
        assert env["BUS"] == "/run/user/1000/bus"

    def test_unknown_var_stays_literal(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("CRONY_NOPE", raising=False)
        env = crony._runtime_env({"FOO": "$CRONY_NOPE"})
        assert env["FOO"] == "$CRONY_NOPE"

    def test_double_dollar_escapes_to_literal(self, monkeypatch: Any) -> None:
        env = crony._runtime_env({"MSG": "cost: $$5"})
        assert env["MSG"] == "cost: $5"

    def test_iteration_order_lets_later_keys_see_earlier(
        self, monkeypatch: Any
    ) -> None:
        # Python dicts preserve insertion order; toml parsers do too.
        # An earlier job.env key should be visible to a later one.
        monkeypatch.setenv("PATH", "/usr/bin")
        env = crony._runtime_env(
            {
                "PATH": "/extra:$PATH",
                "LD_LIBRARY_PATH": "$PATH/../lib",
            }
        )
        assert env["PATH"] == "/extra:/usr/bin"
        assert env["LD_LIBRARY_PATH"] == "/extra:/usr/bin/../lib"

    def test_inherited_keys_not_overridden_by_unset_job_env(
        self, monkeypatch: Any
    ) -> None:
        # Job env is overlay; absent keys inherit unchanged.
        monkeypatch.setenv("HOME", "/Users/edp")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        env = crony._runtime_env({"FOO": "bar"})
        assert env["HOME"] == "/Users/edp"
        assert env["LANG"] == "en_US.UTF-8"
        assert env["FOO"] == "bar"

    def test_malformed_references_stay_literal(self, monkeypatch: Any) -> None:
        # safe_substitute leaves bad-shape references untouched
        # rather than raising. $1 isn't a valid identifier; a
        # trailing bare $ has nothing to consume; ${UNCLOSED has
        # no closing brace.
        env = crony._runtime_env(
            {
                "DIGIT": "$1 is not an identifier",
                "TRAILING": "ends with $",
                "BRACE_NO_CLOSE": "starts ${UNCLOSED",
            }
        )
        assert env["DIGIT"] == "$1 is not an identifier"
        assert env["TRAILING"] == "ends with $"
        assert env["BRACE_NO_CLOSE"] == "starts ${UNCLOSED"


class TestRunJobBasics:
    def test_simple_command_succeeds(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"ok": {"command": "true", "schedule": "daily"}}},
            default_target_jobs=["ok"],
        )
        rc = crony.run_job(h.snap(cfg, "ok"))
        assert rc == 0
        rec = h.last_run("ok")
        assert rec["exit_class"] == "ok"
        assert rec["exit_code"] == 0
        assert rec["gate"] == "none"

    def test_command_failure_propagates_rc(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "fail": {
                        "command": "exit 17",
                        "schedule": "daily",
                    }
                }
            },
            default_target_jobs=["fail"],
        )
        rc = crony.run_job(h.snap(cfg, "fail"))
        assert rc == 17
        rec = h.last_run("fail")
        assert rec["exit_class"] == "fail"
        assert rec["exit_code"] == 17

    def test_unknown_name_raises_precondition_at_resolve(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config({}, default_target_jobs=[])
        with pytest.raises(crony.PreconditionError, match="unknown"):
            crony._resolve_snapshot_for(cfg, "ghost")

    def test_run_without_snapshot_raises_precondition(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.PreconditionError, match="no snapshot"):
            crony.do_run(
                ref="default:11111111-2222-3333-4444-999999999999",
                dry_run=False,
                skip_gate=False,
            )

    def test_run_records_last_run_on_schema_mismatch(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A schema bump makes every entity's pinned snapshot
        # unloadable until re-apply. Without this record, the
        # scheduled fire silently fails -- `crony status` shows
        # the entry as `stale` (which the user reads as
        # "edit-not-yet-applied") and the same outcome repeats
        # every subsequent fire. The canceled row makes the
        # failure visible.
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        uuid_value = "11112222-3333-4444-5555-666677778888"
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            '{"schema": 999, "kind": "job", "name": "default.j"}',
            encoding="utf-8",
        )
        with pytest.raises(crony.PreconditionError, match="schema 999"):
            crony.do_run(
                ref=f"default:{uuid_value}",
                dry_run=False,
                skip_gate=False,
            )
        rec = json.loads((sd / "last-run.json").read_text(encoding="utf-8"))
        assert rec["exit_class"] == "canceled"
        assert rec["exit_code"] == int(crony.ExitCode.PRECONDITION)
        assert "schema 999" in rec["reason"]
        # run.log gained the canceled entry too.
        assert "CANCELED" in (sd / "run.log").read_text(encoding="utf-8")

    def test_run_skips_last_run_write_when_state_dir_missing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # No state dir on disk: don't create one just to hold the
        # error -- that would leave an orphan dir the operator has
        # to clean up. The PreconditionError still propagates so
        # the platform sees a non-zero exit.
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        uuid_value = "11112222-3333-4444-5555-eeeeffff0000"
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / uuid_value
        assert not sd.exists()
        with pytest.raises(crony.PreconditionError, match="no snapshot"):
            crony.do_run(
                ref=f"default:{uuid_value}",
                dry_run=False,
                skip_gate=False,
            )
        assert not sd.exists()

    def test_run_records_last_run_on_unreadable_snapshot(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        uuid_value = "11112222-3333-4444-5555-aaaabbbbcccc"
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        # Corrupt JSON: parser bails before schema / kind checks.
        (sd / "snapshot.json").write_text("{not valid json", encoding="utf-8")
        with pytest.raises(crony.PreconditionError, match="unreadable"):
            crony.do_run(
                ref=f"default:{uuid_value}",
                dry_run=False,
                skip_gate=False,
            )
        rec = json.loads((sd / "last-run.json").read_text(encoding="utf-8"))
        assert rec["exit_class"] == "canceled"

    def test_run_records_last_run_on_unknown_kind(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        uuid_value = "11112222-3333-4444-5555-bbbbccccdddd"
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        # Schema matches, but `kind` is neither "job" nor "group".
        (sd / "snapshot.json").write_text(
            f'{{"schema": {crony._SNAPSHOT_SCHEMA}, '
            f'"kind": "banana", "name": "default.j"}}',
            encoding="utf-8",
        )
        with pytest.raises(crony.PreconditionError, match="unknown kind"):
            crony.do_run(
                ref=f"default:{uuid_value}",
                dry_run=False,
                skip_gate=False,
            )
        rec = json.loads((sd / "last-run.json").read_text(encoding="utf-8"))
        assert rec["exit_class"] == "canceled"

    def test_canceled_surfaces_in_status_last_column(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # The whole point of writing last-run.json: `crony status`
        # shows the canceled label in the LAST column on the next
        # refresh, so a scheduled fire that bailed on a schema
        # mismatch becomes visible.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j", cfg=cfg)
        # Stash a canceled record directly to skip the runner
        # plumbing for the assertion-of-display.
        (sd / "last-run.json").write_text(
            '{"exit_class": "canceled", "started_at": '
            '"2026-01-01T00:00:00-08:00", "exit_code": 64, '
            '"reason": "snapshot has schema 3 expected 4"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # LAST column carries the canceled label; not silently
        # turned into "unknown" by the legacy mapping.
        assert "canceled" in out

    def test_canceled_appears_in_exclude_healthy(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `status --exclude-healthy` shows the canceled row (synced
        # + canceled is not in the healthy set) so an external
        # monitoring script can count unhealthy rows.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j", cfg=cfg)
        (sd / "last-run.json").write_text(
            '{"exit_class": "canceled", "started_at": '
            '"2026-01-01T00:00:00-08:00", "exit_code": 64}',
            encoding="utf-8",
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=True,
        )
        out = capsys.readouterr().out
        assert h.full("j") in out
        assert "canceled" in out

    def test_dry_run_does_not_exec(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "ok": {
                        "command": "exit 5",
                        "schedule": "daily",
                    }
                }
            },
            default_target_jobs=["ok"],
        )
        rc = crony.run_job(h.snap(cfg, "ok"), dry_run=True)
        assert rc == 0
        # No last-run.json written on dry-run
        assert not (h.state_dir("ok") / "last-run.json").exists()


class TestRunJobGate:
    def test_gate_pass_runs_command(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "g": {
                        "command": "true",
                        "gate": "true",
                        "schedule": "daily",
                    }
                }
            },
            default_target_jobs=["g"],
        )
        rc = crony.run_job(h.snap(cfg, "g"))
        assert rc == 0
        rec = h.last_run("g")
        assert rec["exit_class"] == "ok"
        assert rec["gate"] == "passed"

    def test_gate_fail_marks_gated_no_notify(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "g": {
                        "command": "exit 99",  # would fail if reached
                        "gate": "false",
                        "schedule": "daily",
                    }
                }
            },
            default_target_jobs=["g"],
        )
        rc = crony.run_job(h.snap(cfg, "g"))
        assert rc == 0  # gated exits 0
        rec = h.last_run("g")
        assert rec["exit_class"] == "gated"
        assert rec["gate"] == "failed"
        # Main command never ran -> exit_code recorded as 0 placeholder
        assert rec["exit_code"] == 0
        log = (h.state_dir("g") / "run.log").read_text()
        assert "skipping job" in log

    def test_skip_gate_runs_command_anyway(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "g": {
                        "command": "true",
                        "gate": "false",  # would normally skip
                        "schedule": "daily",
                    }
                }
            },
            default_target_jobs=["g"],
        )
        rc = crony.run_job(h.snap(cfg, "g"), skip_gate=True)
        assert rc == 0
        rec = h.last_run("g")
        assert rec["exit_class"] == "ok"
        assert rec["gate"] == "none"


class TestRunJobLockContention:
    def test_lock_busy_returns_lock_busy_no_notify(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "daily"}}},
            default_target_jobs=["j"],
        )
        # Pre-acquire the lock from another file descriptor. The
        # state dir is created with a snapshot stub via state_dir's
        # ensure_snapshot helper so the runner has a snapshot to
        # load before reaching the lock acquisition.
        sd = h.state_dir("j")
        lock = sd / "run.lock"
        import fcntl as _fcntl

        held = open(lock, "w")
        _fcntl.flock(held, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        try:
            rc = crony.run_job(h.snap(cfg, "j"))
        finally:
            _fcntl.flock(held, _fcntl.LOCK_UN)
            held.close()
        assert rc == int(crony.ExitCode.LOCK_BUSY)
        # No last-run.json on contention; the previous holder owns
        # that record.
        assert not (sd / "last-run.json").exists()


class TestRunJobNotify:
    def test_no_channels_is_noop(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "fail": {
                        "command": "exit 1",
                        "schedule": "daily",
                    }
                }
            },
            default_target_jobs=["fail"],
        )
        crony.run_job(h.snap(cfg, "fail"))
        rec = h.last_run("fail")
        assert rec["notifications"] == {}

    def test_listing_undefined_channel_rejected_at_parse(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Listing a channel without a [defaults.notify.<name>] block
        # is a config error -- cross-cutting validation refuses to
        # construct a config that would silently drop notifications
        # at runtime.
        h = _RunnerHarness(tmp_path, monkeypatch)
        with pytest.raises(crony.ConfigError, match="not defined"):
            h.config(
                {
                    "defaults": {"notify_channels": ["ntfy"]},
                    "job": {
                        "fail": {"command": "exit 1", "schedule": "daily"},
                    },
                },
                default_target_jobs=["fail"],
            )


def _stub_trigger_sync(
    monkeypatch: Any, results: dict[str, dict[str, Any]]
) -> None:
    """Replace `_trigger_unit_sync` with a deterministic stub.

    `results` maps full child names -> the dict each call should
    return (mimicking last-run.json). The stub records each call's
    args (job_timeout, trigger_timeout) on a ledger we can assert
    against.
    """
    ledger: list[dict[str, Any]] = []

    def _stub(
        full_name: str,
        *,
        state_dir: Path,
        job_timeout: float,
        trigger_timeout: float,
    ) -> dict[str, Any]:
        ledger.append(
            {
                "full_name": full_name,
                "state_dir": state_dir,
                "job_timeout": job_timeout,
                "trigger_timeout": trigger_timeout,
            }
        )
        return results.get(full_name, {"exit_code": 0, "exit_class": "ok"})

    monkeypatch.setattr(crony, "_trigger_unit_sync", _stub)
    monkeypatch.setattr(crony, "_ledger", ledger, raising=False)


class TestRunGroup:
    def test_group_dispatches_each_child_via_platform(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "a": {"command": "true"},
                    "b": {"command": "true"},
                },
                "job-group": {
                    "g": {
                        "jobs": ["a", "b"],
                        "schedule": "daily",
                    }
                },
            },
            default_target_jobs=["g"],
        )
        h.write_snap(cfg, "a")
        h.write_snap(cfg, "b")
        _stub_trigger_sync(
            monkeypatch,
            {
                h.full("a"): {"exit_code": 0, "exit_class": "ok"},
                h.full("b"): {"exit_code": 0, "exit_class": "ok"},
            },
        )
        rc = crony.run_group(h.snap(cfg, "g"))
        assert rc == 0
        rec = h.last_run("g")
        names = [c["name"] for c in rec["jobs_run"]]
        assert names == [h.full("a"), h.full("b")]
        # Children fire in declared order through the platform stub.
        led = crony._ledger
        assert [e["full_name"] for e in led] == [h.full("a"), h.full("b")]

    def test_group_continues_on_child_failure(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "good": {"command": "true"},
                    "bad": {"command": "exit 3"},
                },
                "job-group": {
                    "g": {
                        "jobs": ["bad", "good"],
                        "schedule": "daily",
                    }
                },
            },
            default_target_jobs=["g"],
        )
        h.write_snap(cfg, "good")
        h.write_snap(cfg, "bad")
        _stub_trigger_sync(
            monkeypatch,
            {
                h.full("bad"): {"exit_code": 3, "exit_class": "fail"},
                h.full("good"): {"exit_code": 0, "exit_class": "ok"},
            },
        )
        rc = crony.run_group(h.snap(cfg, "g"))
        # Group orchestration succeeds even if a child failed.
        assert rc == 0
        rec = h.last_run("g")
        assert rec["jobs_run"][0]["name"] == h.full("bad")
        assert rec["jobs_run"][0]["exit_class"] == "fail"
        assert rec["jobs_run"][0]["exit_code"] == 3
        assert rec["jobs_run"][1]["name"] == h.full("good")
        assert rec["jobs_run"][1]["exit_class"] == "ok"
        # Group-level rollup: any child failure -> "fail" at the
        # group level (so status reflects the failure
        # without re-deriving the rollup on every read).
        assert rec["exit_class"] == "fail"

    def test_group_rollup_is_ok_when_all_children_ok(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "a": {"command": "true"},
                    "b": {"command": "true"},
                },
                "job-group": {
                    "g": {"jobs": ["a", "b"], "schedule": "daily"},
                },
            },
            default_target_jobs=["g"],
        )
        _stub_trigger_sync(
            monkeypatch,
            {
                h.full("a"): {"exit_code": 0, "exit_class": "ok"},
                h.full("b"): {"exit_code": 0, "exit_class": "ok"},
            },
        )
        h.write_snap(cfg, "a")
        h.write_snap(cfg, "b")
        crony.run_group(h.snap(cfg, "g"))
        rec = h.last_run("g")
        assert rec["exit_class"] == "ok"

    def test_group_dry_run_skips_children(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {"a": {"command": "exit 1"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
            },
            default_target_jobs=["g"],
        )
        _stub_trigger_sync(monkeypatch, {})
        rc = crony.run_group(h.snap(cfg, "g"), dry_run=True)
        assert rc == 0
        # No last-run.json for either group or child on dry-run.
        # The stub was never called.
        assert not (h.state_dir("g") / "last-run.json").exists()
        assert not (h.state_dir("a") / "last-run.json").exists()
        assert crony._ledger == []

    def test_group_budget_exhausted_skips_remaining(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Group budget = 1.05 * (5 + 5) = ~10s. First child
        # consumes nearly all of it; second child sees no budget
        # remaining and is recorded as timed-out without dispatch.
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "a": {
                        "command": "true",
                        "schedule": "daily",
                        "job_timeout_sec": 5,
                    },
                    "b": {
                        "command": "true",
                        "schedule": "daily",
                        "job_timeout_sec": 5,
                    },
                },
                "job-group": {"g": {"jobs": ["a", "b"], "schedule": "daily"}},
            },
            default_target_jobs=["g"],
        )

        def _slow(
            full_name: str,
            *,
            state_dir: Path,
            job_timeout: float,
            trigger_timeout: float,
        ) -> dict[str, Any]:
            # Burn 11 seconds of monotonic time using a fake clock;
            # we monkeypatch time.monotonic to make this fast.
            return {"exit_code": 0, "exit_class": "ok"}

        # Simulate elapsed time by returning a moving monotonic value.
        clock = {"now": 0.0}
        real_monotonic = crony.time.monotonic

        def fake_monotonic() -> float:
            return float(real_monotonic()) + clock["now"]

        monkeypatch.setattr(crony.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(crony, "_trigger_unit_sync", _slow)

        # Advance the fake clock forward in the stub so the second
        # iteration sees no remaining budget.
        called: list[str] = []

        def _stub_advance(
            full_name: str,
            *,
            state_dir: Path,
            job_timeout: float,
            trigger_timeout: float,
        ) -> dict[str, Any]:
            called.append(full_name)
            clock["now"] += 11.0  # past 1.05*(5+5) budget
            return {"exit_code": 0, "exit_class": "ok"}

        monkeypatch.setattr(crony, "_trigger_unit_sync", _stub_advance)

        h.write_snap(cfg, "a")
        h.write_snap(cfg, "b")
        rc = crony.run_group(h.snap(cfg, "g"))
        assert rc == 0
        rec = h.last_run("g")
        # Only `a` actually fired; `b` was budget-skipped.
        assert called == [h.full("a")]
        assert rec["jobs_run"][0]["name"] == h.full("a")
        assert rec["jobs_run"][0]["exit_class"] == "ok"
        assert rec["jobs_run"][1]["name"] == h.full("b")
        assert rec["jobs_run"][1]["exit_class"] == "timeout"

    def test_group_soft_fails_missing_child_unit(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # If a parent group's snapshot references a child whose
        # platform unit isn't installed (snapshot pre-dates a
        # destroy, or the host was never apply'd post-refilter),
        # the dispatcher raises `UnitNotInstalledError`. The
        # group must catch it, record the child as a precondition
        # fail, and continue with siblings -- otherwise a single
        # stale reference takes the whole nightly run down and
        # the notification path is bypassed.
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "missing": {"command": "true"},
                    "ok": {"command": "true"},
                },
                "job-group": {
                    "g": {
                        "jobs": ["missing", "ok"],
                        "schedule": "daily",
                    }
                },
            },
            default_target_jobs=["g"],
        )

        def _stub(
            full_name: str,
            *,
            state_dir: Path,
            job_timeout: float,
            trigger_timeout: float,
        ) -> dict[str, Any]:
            if full_name == h.full("missing"):
                raise crony.UnitNotInstalledError(
                    f"unit for {full_name!r} is not installed on this host"
                )
            return {"exit_code": 0, "exit_class": "ok"}

        monkeypatch.setattr(crony, "_trigger_unit_sync", _stub)
        h.write_snap(cfg, "missing")
        h.write_snap(cfg, "ok")
        rc = crony.run_group(h.snap(cfg, "g"))
        # Group orchestration succeeds (rc 0); the child failure
        # surfaces in the rolled-up exit_class and per-child
        # records so the runner's notification path fires.
        assert rc == 0
        rec = h.last_run("g")
        missing_rec = rec["jobs_run"][0]
        assert missing_rec["name"] == h.full("missing")
        assert missing_rec["exit_class"] == "fail"
        assert missing_rec["exit_code"] == int(crony.ExitCode.PRECONDITION)
        # Sibling still ran.
        assert rec["jobs_run"][1]["name"] == h.full("ok")
        assert rec["jobs_run"][1]["exit_class"] == "ok"
        # Group rollup: a fail child surfaces at the group level.
        assert rec["exit_class"] == "fail"

    def test_group_fails_child_with_no_snapshot_on_host(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A child uuid the parent's snapshot references can be
        # unresolvable on this host: rename mid-flight, a partial
        # state wipe, or a stale snapshot referencing an uuid that
        # was never applied here. The runner records a synthetic
        # `<bundle>:<uuid>` fail row so the rollup catches it
        # and the dispatch loop continues for siblings whose
        # snapshot does resolve.
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "gone": {"command": "true"},
                    "ok": {"command": "true"},
                },
                "job-group": {
                    "g": {
                        "jobs": ["gone", "ok"],
                        "schedule": "daily",
                    }
                },
            },
            default_target_jobs=["g"],
        )

        def _stub(
            full_name: str,
            *,
            state_dir: Path,
            job_timeout: float,
            trigger_timeout: float,
        ) -> dict[str, Any]:
            return {"exit_code": 0, "exit_class": "ok"}

        monkeypatch.setattr(crony, "_trigger_unit_sync", _stub)
        # Only "ok" gets a snapshot; "gone" stays unresolvable.
        h.write_snap(cfg, "ok")
        rc = crony.run_group(h.snap(cfg, "g"))
        assert rc == 0
        rec = h.last_run("g")
        # Resolved child ran first; the synthetic fail row trails.
        assert rec["jobs_run"][0]["name"] == h.full("ok")
        assert rec["jobs_run"][0]["exit_class"] == "ok"
        gone_uuid = cfg.jobs["gone"].uuid
        synthetic = rec["jobs_run"][1]
        assert synthetic["name"] == f"default:{gone_uuid}"
        assert synthetic["exit_class"] == "fail"
        assert synthetic["exit_code"] == int(crony.ExitCode.PRECONDITION)
        assert rec["exit_class"] == "fail"


class TestRunGroupInteractive:
    """A group that contains an interactive child fires that child
    async (via `_trigger_unit`, not `_trigger_unit_sync`) and moves
    on without waiting. The interactive child's own runner does its
    wait + dialog independently, and the parent group's deadline
    excludes the child's job_timeout_sec.
    """

    def test_interactive_child_dispatched_async(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "iv": {
                        "command": "true",
                        "schedule": "daily",
                        "interactive": True,
                    },
                    "regular": {"command": "true"},
                },
                "job-group": {
                    "g": {
                        "jobs": ["iv", "regular"],
                        "schedule": "daily",
                    },
                },
            },
            default_target_jobs=["g"],
        )
        # Snapshots so `_child_is_interactive` can read them.
        h.write_snap(cfg, "iv")
        h.write_snap(cfg, "regular")

        sync_calls: list[str] = []
        async_calls: list[str] = []

        def _stub_sync(
            full_name: str,
            *,
            state_dir: Path,
            job_timeout: float,
            trigger_timeout: float,
            triggered_by_user: bool = False,
        ) -> dict[str, Any]:
            sync_calls.append(full_name)
            return {"exit_code": 0, "exit_class": "ok"}

        def _stub_async(name: str, platform: str, **kw: Any) -> None:
            async_calls.append(name)

        monkeypatch.setattr(crony, "_trigger_unit_sync", _stub_sync)
        monkeypatch.setattr(crony, "_trigger_unit", _stub_async)

        rc = crony.run_group(h.snap(cfg, "g"))
        assert rc == 0

        # Interactive child went through the async path; the
        # regular sibling kept the sync path.
        assert async_calls == [h.full("iv")]
        assert sync_calls == [h.full("regular")]

        # The group's last-run.json records the dispatch as a
        # child with exit_class="dispatched"; rollup stays "ok"
        # because dispatched has precedence 0.
        rec = h.last_run("g")
        iv_rec = next(c for c in rec["jobs_run"] if c["name"] == h.full("iv"))
        assert iv_rec["exit_class"] == "dispatched"
        assert iv_rec["exit_code"] == 0
        assert rec["exit_class"] == "ok"

    def test_group_budget_excludes_interactive_children(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "iv": {
                        "command": "true",
                        "schedule": "daily",
                        "interactive": True,
                        # Big timeout that would inflate the
                        # group's budget if not skipped.
                        "job_timeout_sec": 10_000,
                    },
                    "regular": {
                        "command": "true",
                        "job_timeout_sec": 100,
                    },
                },
                "job-group": {
                    "g": {
                        "jobs": ["iv", "regular"],
                        "schedule": "daily",
                    },
                },
            },
            default_target_jobs=["g"],
        )
        target = crony.resolve_target(cfg, "test-host", "darwin")
        budget = crony.resolved_group_timeout_sec(cfg, target, "g")
        # Only the non-interactive child contributes:
        # 1.05 * 100 == 105.
        assert budget == 105

    def test_dispatched_does_not_poison_rollup(self) -> None:
        # `dispatched` has precedence 0 so it ties with ok / gated
        # in the rollup; a group with only dispatched children
        # rolls up as "ok".
        rollup = crony._rollup_group_exit_class(
            [
                crony.GroupChildResult(
                    name="a", exit_class="dispatched", exit_code=0
                ),
                crony.GroupChildResult(name="b", exit_class="ok", exit_code=0),
            ]
        )
        assert rollup == "ok"

    def test_dispatched_rolls_up_under_fail(self) -> None:
        rollup = crony._rollup_group_exit_class(
            [
                crony.GroupChildResult(
                    name="a", exit_class="dispatched", exit_code=0
                ),
                crony.GroupChildResult(
                    name="b", exit_class="fail", exit_code=1
                ),
            ]
        )
        assert rollup == "fail"


class TestTriggerUnitNotInstalled:
    """`_trigger_unit` refuses early when the platform unit file
    doesn't exist, and `_trigger_unit_sync` doesn't side-effect a
    state dir for a never-installed name.
    """

    def _isolate_unit_dirs(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Redirect LAUNCHAGENTS_DIR / SYSTEMD_USER_DIR at empty
        tmp dirs so a missing `org.crony.<name>.plist` /
        `crony-<name>.service` lookup gives a deterministic answer
        regardless of what's in the test host's real ~/Library or
        ~/.config/systemd/user.
        """
        agents = tmp_path / "LaunchAgents"
        agents.mkdir()
        sysd = tmp_path / "systemd-user"
        sysd.mkdir()
        monkeypatch.setattr(crony, "LAUNCHAGENTS_DIR", agents)
        monkeypatch.setattr(crony, "SYSTEMD_USER_DIR", sysd)

    def test_trigger_unit_raises_when_unit_file_missing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        self._isolate_unit_dirs(tmp_path, monkeypatch)
        full = h.full("ghost")
        platform = crony.current_platform()
        with pytest.raises(crony.UnitNotInstalledError, match="not installed"):
            crony._trigger_unit(full, platform)
        # No state dir leaked: the bundle subdir for default
        # should not have any uuid-keyed entries.
        bundle_dir = h.state / crony.DEFAULT_BUNDLE_NAME
        assert not bundle_dir.exists() or not any(bundle_dir.iterdir())

    def test_trigger_unit_sync_does_not_create_state_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The waiter takes a read-only stance on the state dir
        # until the runner actually starts. Refusing a missing
        # unit must not leave a phantom state-dir-only remnant
        # behind (which `crony status` would then surface).
        h = _RunnerHarness(tmp_path, monkeypatch)
        self._isolate_unit_dirs(tmp_path, monkeypatch)
        full = h.full("ghost")
        ghost_sd = h.state / crony.DEFAULT_BUNDLE_NAME / "u-ghost"
        with pytest.raises(crony.UnitNotInstalledError):
            crony._trigger_unit_sync(
                full,
                state_dir=ghost_sd,
                job_timeout=5.0,
                trigger_timeout=5.0,
            )
        # The waiter took read-only stance; refusing didn't create
        # the state dir we pointed it at.
        assert not ghost_sd.exists()


# =============================================================================
# Notify channels
# =============================================================================


class TestSecretRetrieval:
    """_retrieve_secret reads from Keychain (mac) or 0600 file."""

    def test_returns_none_when_no_source(self) -> None:
        assert (
            crony._retrieve_secret(keychain_service=None, file_path=None)
            is None
        )

    def test_reads_from_file(self, tmp_path: Path) -> None:
        f = tmp_path / "secret"
        f.write_text("supersecret\n")
        f.chmod(0o600)
        assert (
            crony._retrieve_secret(keychain_service=None, file_path=str(f))
            == "supersecret"
        )

    def test_rejects_loose_mode(self, tmp_path: Path) -> None:
        f = tmp_path / "secret"
        f.write_text("supersecret")
        f.chmod(0o644)  # group/world readable
        with pytest.raises(crony.PreconditionError, match="0600"):
            crony._retrieve_secret(keychain_service=None, file_path=str(f))

    def test_rejects_loose_parent_dir(self, tmp_path: Path) -> None:
        # File mode is fine but the directory is group/world
        # accessible; reject so file names / mtimes don't leak.
        d = tmp_path / "secrets"
        d.mkdir(mode=0o755)
        f = d / "smtp-pw"
        f.write_text("hunter2")
        f.chmod(0o600)
        with pytest.raises(crony.PreconditionError, match="secret directory"):
            crony._retrieve_secret(keychain_service=None, file_path=str(f))

    def test_keychain_falls_back_to_file_on_failure(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # When the keychain lookup fails (non-darwin or item missing),
        # the function should try the file_path as a fallback.
        f = tmp_path / "secret"
        f.write_text("from-file")
        f.chmod(0o600)
        # Pretend we're on linux so the keychain branch is skipped
        # entirely.
        monkeypatch.setattr(crony.sys, "platform", "linux")
        assert (
            crony._retrieve_secret(
                keychain_service="missing-item", file_path=str(f)
            )
            == "from-file"
        )

    def test_account_passed_as_dash_a(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # When keychain_account is set, `security` is invoked with
        # `-a <account>` after `-s <service>` so the lookup picks the
        # right item among multiple sharing a service name.
        captured: dict[str, Any] = {}

        def _fake_run(argv: list[str], **kwargs: Any) -> Any:
            captured["argv"] = argv
            import subprocess as _sp

            return _sp.CompletedProcess(
                args=argv, returncode=0, stdout="thesecret\n", stderr=""
            )

        monkeypatch.setattr(crony.sys, "platform", "darwin")
        monkeypatch.setattr(crony.subprocess, "run", _fake_run)
        secret = crony._retrieve_secret(
            keychain_service="svc",
            keychain_account="acct",
            file_path=None,
        )
        assert secret == "thesecret"
        # `-s svc` precedes `-a acct`, and `-w` is the trailing flag.
        argv = captured["argv"]
        assert "-s" in argv and argv[argv.index("-s") + 1] == "svc"
        assert "-a" in argv and argv[argv.index("-a") + 1] == "acct"
        assert argv[-1] == "-w"

    def test_no_account_omits_dash_a(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Without keychain_account, no `-a` is passed -- preserves
        # the prior behavior for users who don't need to disambiguate.
        captured: dict[str, Any] = {}

        def _fake_run(argv: list[str], **kwargs: Any) -> Any:
            captured["argv"] = argv
            import subprocess as _sp

            return _sp.CompletedProcess(
                args=argv, returncode=0, stdout="x\n", stderr=""
            )

        monkeypatch.setattr(crony.sys, "platform", "darwin")
        monkeypatch.setattr(crony.subprocess, "run", _fake_run)
        crony._retrieve_secret(keychain_service="svc", file_path=None)
        assert "-a" not in captured["argv"]


class TestEmailNotify:
    """Email channel routing via smtplib (mocked)."""

    def _common_config(self, tmp_path: Path) -> Any:
        secret = tmp_path / "smtp-pw"
        secret.write_text("hunter2")
        secret.chmod(0o600)
        return _parse(
            {
                "defaults": {
                    "notify_channels": ["email"],
                    "notify_attach_log": True,
                    "notify_attach_max_kb": 1,
                    "notify": {
                        "email": {
                            "to": "you@example.com",
                            "from": "crony@example.com",
                            "smtp_host": "smtp.example.com",
                            "smtp_port": 587,
                            "smtp_user": "u@example.com",
                            "smtp_starttls": True,
                            "smtp_pass_file": str(secret),
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["email"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )

    def _make_failed_result(self, channels: list[str]) -> Any:
        return crony.JobRunResult(
            host="h",
            platform="darwin",
            started_at="2026-05-02T10:00:00-07:00",
            ended_at="2026-05-02T10:00:01-07:00",
            duration_sec=1.0,
            exit_class="fail",
            exit_code=2,
            signal=None,
            gate="none",
            log_path="/tmp/run.log",
            log_bytes_this_run=42,
            notifications={
                ch: crony.NotificationResult(sent=False) for ch in channels
            },
        )

    def test_sends_via_smtp(self, tmp_path: Path, monkeypatch: Any) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["email"])

        # autospec exercises the real SMTP signature; the resulting
        # mock instance plays the context-manager role with the same
        # return-value contract.
        smtp_cls = create_autospec(crony.smtplib.SMTP)
        smtp_inst = smtp_cls.return_value
        smtp_inst.__enter__.return_value = smtp_inst
        smtp_inst.__exit__.return_value = None
        monkeypatch.setattr(crony.smtplib, "SMTP", smtp_cls)

        crony._dispatch_notify(
            result, "default.j", "log content here", cfg.defaults
        )

        assert result.notifications["email"].sent is True
        assert result.notifications["email"].error is None
        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=15)
        smtp_inst.starttls.assert_called_once()
        smtp_inst.login.assert_called_once_with("u@example.com", "hunter2")
        assert smtp_inst.send_message.call_count == 1
        sent = smtp_inst.send_message.call_args[0][0]
        assert sent["To"] == "you@example.com"
        assert sent["From"] == "crony@example.com"
        body = sent.get_content()
        assert "Job:        default.j" in body
        assert "fail" in body
        assert "--- log (latest run) ---" in body
        assert "log content here" in body

    def test_email_body_is_latest_run_entry_only(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Multi-run log: email and ntfy both include only the most
        # recent run's entry. Earlier history would be noise the
        # recipient already saw in prior notifications.
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["email"])

        smtp_cls = create_autospec(crony.smtplib.SMTP)
        smtp_inst = smtp_cls.return_value
        smtp_inst.__enter__.return_value = smtp_inst
        smtp_inst.__exit__.return_value = None
        monkeypatch.setattr(crony.smtplib, "SMTP", smtp_cls)

        log_text = (
            "=== 2026-05-01T03:00:00-08:00 j pid=1 ===\n"
            "older-run-detail\n"
            "=== 2026-05-02T03:00:00-08:00 j pid=2 ===\n"
            "newest-run-detail\n"
        )
        crony._dispatch_notify(result, "default.j", log_text, cfg.defaults)
        sent = smtp_inst.send_message.call_args[0][0]
        body = sent.get_content()
        assert "newest-run-detail" in body
        assert "older-run-detail" not in body

    def test_records_smtp_failure_no_propagate(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["email"])

        smtp_cls = create_autospec(
            crony.smtplib.SMTP, side_effect=ConnectionRefusedError("no")
        )
        monkeypatch.setattr(crony.smtplib, "SMTP", smtp_cls)

        crony._dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["email"].sent is False
        assert "ConnectionRefusedError" in (
            result.notifications["email"].error or ""
        )

    def test_missing_smtp_password_records_error(self, tmp_path: Path) -> None:
        # Build a config that omits smtp_pass_*.
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["email"],
                    "notify": {
                        "email": {
                            "to": "y@e.com",
                            "smtp_host": "x",
                            "smtp_user": "u",
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["email"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )
        result = self._make_failed_result(["email"])
        crony._dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["email"].sent is False
        assert "no SMTP password" in (result.notifications["email"].error or "")

    def test_user_headers_attached(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A `headers = { Reply-To = ... }` block on an email channel
        # should land as headers on the rendered EmailMessage.
        secret = tmp_path / "smtp-pw"
        secret.write_text("hunter2")
        secret.chmod(0o600)
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["email"],
                    "notify": {
                        "email": {
                            "to": "you@example.com",
                            "smtp_host": "smtp.example.com",
                            "smtp_user": "u@example.com",
                            "smtp_pass_file": str(secret),
                            "headers": {
                                "Reply-To": "support@example.com",
                                "X-Crony-Source": "automation",
                            },
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["email"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )
        result = self._make_failed_result(["email"])
        smtp_cls = create_autospec(crony.smtplib.SMTP)
        smtp_inst = smtp_cls.return_value
        smtp_inst.__enter__.return_value = smtp_inst
        smtp_inst.__exit__.return_value = None
        monkeypatch.setattr(crony.smtplib, "SMTP", smtp_cls)

        crony._dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["email"].sent is True
        sent = smtp_inst.send_message.call_args[0][0]
        assert sent["Reply-To"] == "support@example.com"
        assert sent["X-Crony-Source"] == "automation"
        # crony-controlled headers still in place.
        assert sent["To"] == "you@example.com"


class TestNtfyNotify:
    """ntfy channel routing via urllib (mocked)."""

    def _common_config(self, tmp_path: Path) -> Any:
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        return _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify_attach_log": True,
                    "notify_attach_max_kb": 1,
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["ntfy"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )

    def _make_failed_result(self, channels: list[str]) -> Any:
        return crony.JobRunResult(
            host="h",
            platform="darwin",
            started_at="2026-05-02T10:00:00-07:00",
            ended_at="2026-05-02T10:00:01-07:00",
            duration_sec=1.0,
            exit_class="fail",
            exit_code=2,
            signal=None,
            gate="none",
            log_path="/tmp/run.log",
            log_bytes_this_run=42,
            notifications={
                ch: crony.NotificationResult(sent=False) for ch in channels
            },
        )

    def test_sends_via_urllib(self, tmp_path: Path, monkeypatch: Any) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["ntfy"])

        captured: dict[str, Any] = {}

        class _Resp:
            status = 200

            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake_urlopen(req: Any, timeout: Any = None) -> Any:
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["data"] = req.data
            captured["method"] = req.get_method()
            return _Resp()

        monkeypatch.setattr(crony.urllib.request, "urlopen", _fake_urlopen)
        crony._dispatch_notify(
            result, "default.j", "log content here", cfg.defaults
        )

        assert result.notifications["ntfy"].sent is True
        assert result.notifications["ntfy"].error is None
        assert captured["url"] == "https://ntfy.example.com/x"
        assert captured["method"] == "POST"
        # urllib.request.Request normalises header keys via
        # capitalize(); accept either form defensively.
        auth = captured["headers"].get("Authorization") or captured[
            "headers"
        ].get("authorization")
        assert auth == "Bearer tk_test"
        tags = captured["headers"].get("Tags") or captured["headers"].get(
            "tags"
        )
        assert tags == "warning,fail"
        # Body mirrors the email layout: human summary block,
        # separator, then the latest log entry. (No run-header in
        # this fixture, so latest-entry extraction passes the
        # text through unchanged.)
        body = captured["data"].decode("utf-8")
        assert "Job:" in body
        assert "Exit class:" in body
        assert "--- log (latest run) ---" in body
        assert "log content here" in body
        # No Filename header: the body is inline content, not an
        # ntfy attachment (which would be publicly addressable).
        for k in captured["headers"]:
            assert k.lower() != "filename", (
                f"Filename header leaked: {captured['headers']!r}"
            )

    def test_ntfy_body_is_latest_run_entry_only(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Multi-run log: the body should contain only the most
        # recent run's entry, not earlier history. ntfy's 4 KB
        # message ceiling means we can't ship the whole log.
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["ntfy"])
        captured: dict[str, Any] = {}

        class _Resp:
            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake(req: Any, timeout: Any = None) -> Any:
            captured["data"] = req.data
            return _Resp()

        monkeypatch.setattr(crony.urllib.request, "urlopen", _fake)
        log_text = (
            "=== 2026-05-01T03:00:00-08:00 j pid=1 ===\n"
            "older-run-detail\n"
            "=== 2026-05-02T03:00:00-08:00 j pid=2 ===\n"
            "newest-run-detail\n"
        )
        crony._dispatch_notify(result, "default.j", log_text, cfg.defaults)
        body = captured["data"].decode("utf-8")
        assert "newest-run-detail" in body
        assert "older-run-detail" not in body

    def test_ntfy_body_head_truncated_to_3kb(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Body must fit ntfy's per-message limit. The summary stays
        # intact at the top (its structured fields are more useful
        # than a truncated stub); the log section is head-truncated
        # so the most recent failure output stays visible at the
        # bottom.
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["ntfy"])
        captured: dict[str, Any] = {}

        class _Resp:
            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake(req: Any, timeout: Any = None) -> Any:
            captured["data"] = req.data
            return _Resp()

        monkeypatch.setattr(crony.urllib.request, "urlopen", _fake)
        log_text = (
            "=== 2026-05-02T03:00:00-08:00 j pid=2 ===\n"
            + ("X" * 5000)
            + "MARKER-AT-TAIL\n"
        )
        crony._dispatch_notify(result, "default.j", log_text, cfg.defaults)
        body_bytes = captured["data"]
        assert len(body_bytes) <= 3 * 1024
        body = body_bytes.decode("utf-8", errors="replace")
        # Summary block intact at the top.
        assert body.startswith("Job:")
        assert "Exit class:" in body
        # Log section follows the separator and shows the tail.
        assert "--- log (latest run) ---" in body
        assert "MARKER-AT-TAIL" in body
        # Truncation marker appears within the log section.
        assert "bytes truncated" in body

    def test_ntfy_body_is_summary_only_when_attach_log_disabled(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `notify_attach_log = false` means "no log content in
        # notifications"; the body is the structured summary
        # without the trailing log section.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify_attach_log": False,
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["ntfy"])},
            }
        )
        result = self._make_failed_result(["ntfy"])
        captured: dict[str, Any] = {}

        class _Resp:
            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake(req: Any, timeout: Any = None) -> Any:
            captured["data"] = req.data
            return _Resp()

        monkeypatch.setattr(crony.urllib.request, "urlopen", _fake)
        crony._dispatch_notify(
            result, "default.j", "log content not in body", cfg.defaults
        )
        body = captured["data"].decode("utf-8")
        # Human summary keys are present; log content is not.
        assert "Job:" in body
        assert "Exit class:" in body
        assert "log content not in body" not in body

    def test_http_error_recorded(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["ntfy"])

        # urllib raises HTTPError for 4xx/5xx responses; mirror that
        # so the test reflects real-world failure.
        def _raise(req: Any, timeout: Any = None) -> Any:
            raise crony.urllib.error.HTTPError(
                req.full_url, 503, "service unavailable", {}, None
            )

        monkeypatch.setattr(crony.urllib.request, "urlopen", _raise)
        crony._dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["ntfy"].sent is False
        assert "503" in (result.notifications["ntfy"].error or "")

    def test_user_headers_attached(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A custom-named ntfy channel with `headers = { Email = ... }`
        # should reach the HTTP POST.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy-email"],
                    "notify": {
                        "ntfy-email": {
                            "transport": "ntfy",
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                            "headers": {
                                "Email": "you@example.com",
                                "Priority": "urgent",
                            },
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["ntfy-email"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )
        result = self._make_failed_result(["ntfy-email"])
        captured: dict[str, Any] = {}

        class _Resp:
            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake_urlopen(req: Any, timeout: Any = None) -> Any:
            captured["headers"] = dict(req.header_items())
            return _Resp()

        monkeypatch.setattr(crony.urllib.request, "urlopen", _fake_urlopen)
        crony._dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["ntfy-email"].sent is True
        # User headers reached the request. urllib normalizes header
        # keys via .capitalize().
        h = captured["headers"]
        email_h = h.get("Email") or h.get("email")
        prio_h = h.get("Priority") or h.get("priority")
        assert email_h == "you@example.com"
        assert prio_h == "urgent"
        # crony's controlled headers still set.
        assert h.get("Authorization") or h.get("authorization")
        assert h.get("Tags") or h.get("tags")


class TestMultiChannelDispatch:
    """`_dispatch_notify` fans out across all configured channels and
    one channel's failure must not suppress the others. The
    single-channel tests in TestEmailNotify / TestNtfyNotify don't
    exercise this; this class pins the headline contract.
    """

    def _config(self, tmp_path: Path) -> Any:
        smtp_secret = tmp_path / "smtp-pw"
        smtp_secret.write_text("hunter2")
        smtp_secret.chmod(0o600)
        ntfy_secret = tmp_path / "ntfy-token"
        ntfy_secret.write_text("tk_test")
        ntfy_secret.chmod(0o600)
        return _parse(
            {
                "defaults": {
                    "notify_channels": ["email", "ntfy"],
                    "notify_attach_log": True,
                    "notify_attach_max_kb": 1,
                    "notify": {
                        "email": {
                            "to": "you@example.com",
                            "smtp_host": "smtp.example.com",
                            "smtp_user": "u@example.com",
                            "smtp_pass_file": str(smtp_secret),
                        },
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(ntfy_secret),
                        },
                    },
                },
                "job": {"j": _job(notify_channels=["email", "ntfy"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )

    def test_email_succeeds_ntfy_fails(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg = self._config(tmp_path)
        result = crony.JobRunResult(
            host="h",
            platform="darwin",
            started_at="2026-05-02T10:00:00-07:00",
            ended_at="2026-05-02T10:00:01-07:00",
            duration_sec=1.0,
            exit_class="fail",
            exit_code=2,
            signal=None,
            gate="none",
            log_path="/tmp/run.log",
            log_bytes_this_run=42,
            notifications={
                "email": crony.NotificationResult(sent=False),
                "ntfy": crony.NotificationResult(sent=False),
            },
        )
        smtp_cls = create_autospec(crony.smtplib.SMTP)
        smtp_inst = smtp_cls.return_value
        smtp_inst.__enter__.return_value = smtp_inst
        smtp_inst.__exit__.return_value = None
        monkeypatch.setattr(crony.smtplib, "SMTP", smtp_cls)

        def _fail_post(req: Any, timeout: Any = None) -> Any:
            raise crony.urllib.error.HTTPError(
                req.full_url, 503, "service unavailable", {}, None
            )

        monkeypatch.setattr(crony.urllib.request, "urlopen", _fail_post)
        crony._dispatch_notify(result, "default.j", "log content", cfg.defaults)

        # email succeeded
        assert result.notifications["email"].sent is True
        assert result.notifications["email"].error is None
        # ntfy failed independently
        assert result.notifications["ntfy"].sent is False
        assert "503" in (result.notifications["ntfy"].error or "")
        # And both still appear (one transport failure didn't suppress
        # the other channel).
        assert set(result.notifications.keys()) == {"email", "ntfy"}


class TestNotifyChannelOrderPreserved:
    """The runner pre-populates result.notifications in configured
    channel order so the JSON record reflects the ordering the user
    set; pin that contract end-to-end.
    """

    @pytest.mark.parametrize("order", [["email", "ntfy"], ["ntfy", "email"]])
    def test_run_job_preserves_channel_order(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        order: list[str],
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "defaults": {
                    "notify_channels": order,
                    "notify": {
                        "email": _email_block(),
                        "ntfy": _ntfy_block(),
                    },
                },
                "job": {
                    "fail": {
                        "command": "exit 1",
                        "schedule": "daily",
                    }
                },
            },
            default_target_jobs=["fail"],
        )
        crony.run_job(h.snap(cfg, "fail"))
        rec = h.last_run("fail")
        assert list(rec["notifications"].keys()) == order


class TestNotifyTestSubcommand:
    """`crony notify-test` synth event invocation."""

    def test_no_channels_is_quiet(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        # No channels configured: should not raise.
        crony.do_notify_test(channel=None, bundle=None)

    def test_unresolvable_secret_raises_config_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # email channel is fully defined but the SMTP password
        # source can't be resolved -- this is a config-shaped
        # failure (the user can fix it), so notify-test surfaces
        # it as CONFIG (3) rather than ERROR (4).
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["email"],
                    "notify": {"email": _email_block()},
                },
            },
            default_target_jobs=[],
        )
        with pytest.raises(crony.ConfigError, match="notify-test failed"):
            crony.do_notify_test(channel=None, bundle=None)

    def test_transport_failure_raises_crony_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Properly configured ntfy but the transport itself fails
        # (HTTP 503). The classifier should surface CronyError ->
        # ERROR (4), not ConfigError -- this is not a config issue
        # the user can fix in the toml.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                },
            },
            default_target_jobs=[],
        )

        def _raise(req: Any, timeout: Any = None) -> Any:
            raise crony.urllib.error.HTTPError(
                req.full_url, 503, "service unavailable", {}, None
            )

        monkeypatch.setattr(crony.urllib.request, "urlopen", _raise)
        with pytest.raises(crony.CronyError) as exc:
            crony.do_notify_test(channel="ntfy", bundle=None)
        # Distinguishing from ConfigError matters: CronyError exits
        # with ERROR (4), ConfigError with CONFIG (3).
        assert not isinstance(exc.value, crony.ConfigError)
        assert "notify-test failed" in str(exc.value)

    def test_no_bundle_means_default_only(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # With multiple bundles present and --bundle omitted,
        # notify-test exercises only the default bundle (matches
        # crony's bare-input rule). The borgadm bundle's broken
        # ntfy config must not be touched.
        h = _RunnerHarness(tmp_path, monkeypatch)
        # default bundle: no channels, so no attempt -> quiet exit.
        h.config({}, default_target_jobs=[])
        # second bundle: lists ntfy but has no [defaults.notify.ntfy]
        # block -- would raise ConfigError if reached.
        (h.cfg_dropin / "borgadm.toml").write_text(
            '[defaults]\nnotify_channels = ["ntfy"]\n',
            encoding="utf-8",
        )
        # Should not raise: only default is exercised.
        crony.do_notify_test(channel=None, bundle=None)

    def test_namespaced_channel_picks_named_bundle(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `--channel borgadm.ntfy` should target borgadm's ntfy
        # config, not the default bundle's.
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        # borgadm has no ntfy block. Asking for borgadm.ntfy should
        # fail because no channel of that name is defined there,
        # which proves we routed into borgadm and not default.
        (h.cfg_dropin / "borgadm.toml").write_text(
            "[defaults]\nnotify_channels = []\n",
            encoding="utf-8",
        )
        with pytest.raises(crony.ConfigError, match="unknown notify channel"):
            crony.do_notify_test(channel="borgadm.ntfy", bundle=None)

    def test_bundle_and_channel_mismatch_errors(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="contradicts"):
            crony.do_notify_test(channel="borgadm.ntfy", bundle="other")

    def test_unknown_bundle_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_notify_test(channel=None, bundle="ghost")

    def test_inheriting_bundle_dispatches_default_channels(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An inheriting bundle's notify-test sends through the default
        # bundle's channels. A 503 from the (only, inherited) ntfy
        # channel proves it was resolved and attempted.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                }
            },
            default_target_jobs=[],
        )
        (h.cfg_dropin / "borgadm.toml").write_text(
            "[defaults]\n", encoding="utf-8"
        )
        calls: list[str] = []

        def _raise(req: Any, timeout: Any = None) -> Any:
            calls.append(req.full_url)
            raise crony.urllib.error.HTTPError(
                req.full_url, 503, "service unavailable", {}, None
            )

        monkeypatch.setattr(crony.urllib.request, "urlopen", _raise)
        with pytest.raises(crony.CronyError, match="notify-test failed"):
            crony.do_notify_test(channel=None, bundle="borgadm")
        assert calls, "inherited ntfy channel was not attempted"


# =============================================================================
# Apply / destroy + platform unit rendering
# =============================================================================


class _ApplyHarness(_RunnerHarness):
    """RunnerHarness extension that also redirects platform unit dirs
    and stubs subprocess so launchctl/systemctl never run for real.
    """

    def __init__(
        self, tmp_path: Path, monkeypatch: Any, *, platform: str = "darwin"
    ) -> None:
        super().__init__(tmp_path, monkeypatch)
        agents = tmp_path / "LaunchAgents"
        agents.mkdir()
        sysd = tmp_path / "systemd-user"
        sysd.mkdir()
        monkeypatch.setattr(crony, "LAUNCHAGENTS_DIR", agents)
        monkeypatch.setattr(crony, "SYSTEMD_USER_DIR", sysd)
        monkeypatch.setattr(crony, "current_platform", lambda: platform)
        # Capture subprocess.run calls so apply/destroy don't actually
        # invoke launchctl or systemctl.
        self.calls: list[list[str]] = []

        def fake_run(*args: Any, **kwargs: Any) -> Any:
            argv: list[str] = list(args[0] if args else kwargs.get("args", []))
            self.calls.append(argv)
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        # The default empty-subprocess path resolves
        # `_unit_state` to "none", which the unit-drift check
        # treats as "scheduler unloaded the unit." Stub the
        # underlying primitives so a freshly-applied unit reads
        # back as `enabled`. Tests that assert a specific
        # scheduler state override these at the same level (e.g.
        # `_systemd_is_enabled` -> "disabled").
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "enabled")
        monkeypatch.setattr(crony, "_is_launchd_loaded", lambda label: True)
        monkeypatch.setattr(crony, "_is_launchd_disabled", lambda label: False)
        self.platform = platform
        self.agents = agents
        self.sysd = sysd

    def apply(
        self, short: str, *, bundle: str = crony.DEFAULT_BUNDLE_NAME
    ) -> str:
        """Apply one entry through the production path (see the
        module-level `_apply`)."""
        return _apply(short, bundle=bundle)


_REF = "default:u-test"


class TestPlistRendering:
    """_render_plist produces well-formed launchd plists."""

    def test_keyword_daily(self) -> None:
        plist = crony._render_plist(
            "brew", crony.EntityRef("default", "u-test"), "daily", None
        )
        assert "<key>Label</key>" in plist
        assert "<string>org.crony.brew</string>" in plist
        assert "<key>StartCalendarInterval</key>" in plist
        # daily -> 00:00
        assert "<key>Hour</key>" in plist
        assert "<integer>0</integer>" in plist

    def test_oncalendar_simple_time(self) -> None:
        plist = crony._render_plist(
            "j", crony.EntityRef("default", "u-test"), "*-*-* 03:15", None
        )
        assert "<key>Hour</key>" in plist
        assert "<integer>3</integer>" in plist
        assert "<integer>15</integer>" in plist

    def test_oncalendar_dow_with_time(self) -> None:
        plist = crony._render_plist(
            "j", crony.EntityRef("default", "u-test"), "Mon *-*-* 09:00", None
        )
        assert "<key>Weekday</key>" in plist
        assert "<integer>1</integer>" in plist  # Mon=1

    def test_oncalendar_first_of_month(self) -> None:
        plist = crony._render_plist(
            "j", crony.EntityRef("default", "u-test"), "*-*-01 03:00", None
        )
        assert "<key>Day</key>" in plist
        assert "<integer>1</integer>" in plist

    def test_interval(self) -> None:
        plist = crony._render_plist(
            "j", crony.EntityRef("default", "u-test"), None, "30min"
        )
        assert "<key>StartInterval</key>" in plist
        assert "<integer>1800</integer>" in plist

    def test_step_pattern_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="step / range / list"):
            crony._render_plist(
                "j", crony.EntityRef("default", "u-test"), "*:0/15", None
            )

    def test_range_pattern_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="step / range / list"):
            crony._render_plist(
                "j",
                crony.EntityRef("default", "u-test"),
                "Mon..Fri *-*-* 09:00",
                None,
            )

    def test_program_args_invoke_uv_with_absolute_path(
        self, monkeypatch: Any
    ) -> None:
        # launchd's per-agent PATH is /usr/bin:/bin:/usr/sbin:/sbin
        # which doesn't contain ~/.local/bin or homebrew's bin dir,
        # so the script's `env -S uv run --script` shebang fails to
        # find uv (exit 127). Render the absolute uv path into
        # ProgramArguments so the unit doesn't depend on PATH.
        monkeypatch.setattr(crony, "_uv_executable", lambda: Path("/abs/uv"))
        monkeypatch.setattr(
            crony, "_crony_executable", lambda: Path("/abs/crony")
        )
        plist = crony._render_plist(
            "j", crony.EntityRef("default", "u-test"), "daily", None
        )
        assert "<string>/abs/uv</string>" in plist
        assert "<string>run</string>" in plist
        assert "<string>--script</string>" in plist
        assert "<string>/abs/crony</string>" in plist
        # The runner argv carries the bundle:uuid ref, not the name,
        # so it can locate the state dir directly without scanning.
        assert "<string>default:u-test</string>" in plist


class TestSystemdRendering:
    def test_service_unit(self) -> None:
        svc = crony._render_systemd_service(
            "brew", crony.EntityRef("default", "u-test")
        )
        assert "[Unit]" in svc
        assert "[Service]" in svc
        assert "Type=oneshot" in svc
        assert "ExecStart=" in svc
        assert " run default:u-test" in svc
        assert "WorkingDirectory=%h" in svc

    def test_timer_oncalendar(self) -> None:
        timer = crony._render_systemd_timer("j", "*-*-* 03:00", None)
        assert "OnCalendar=*-*-* 03:00" in timer
        assert "Persistent=true" in timer
        assert "WantedBy=timers.target" in timer

    def test_timer_interval(self) -> None:
        timer = crony._render_systemd_timer("j", None, "1h")
        assert "OnUnitActiveSec=1h" in timer

    def test_service_invokes_uv_with_absolute_path(
        self, monkeypatch: Any
    ) -> None:
        # systemd user services run with a minimal default PATH;
        # render uv's absolute path so the unit doesn't depend on
        # whoever's PATH happens to contain it (same reason as the
        # launchd plist case).
        monkeypatch.setattr(crony, "_uv_executable", lambda: Path("/abs/uv"))
        monkeypatch.setattr(
            crony, "_crony_executable", lambda: Path("/abs/crony")
        )
        svc = crony._render_systemd_service(
            "j", crony.EntityRef("default", "u-test")
        )
        assert (
            "ExecStart=/abs/uv run --script /abs/crony run default:u-test"
            in svc
        )

    def test_uv_executable_errors_when_uv_not_on_path(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(crony.shutil, "which", lambda name: None)
        with pytest.raises(crony.PreconditionError, match="uv not found"):
            crony._uv_executable()


class TestJobPriority:
    """`priority` enum rendered into the platform unit (and tracked
    by the snapshot + unit-drift check)."""

    def test_parse_valid(self) -> None:
        cfg = _parse({"job": {"a": _job(priority="high")}})
        assert cfg.jobs["a"].priority == "high"

    def test_parse_omitted_is_none(self) -> None:
        cfg = _parse({"job": {"a": _job()}})
        assert cfg.jobs["a"].priority is None

    def test_parse_invalid_rejected(self) -> None:
        _assert_errored_job(
            {"job": {"a": _job(priority="turbo")}},
            "a",
            "priority must be one of",
        )

    def test_snapshot_carries_priority(self) -> None:
        cfg = _parse({"job": {"a": _job(priority="high")}})
        target = crony.resolve_target(cfg, "h", "darwin")
        snap = crony._resolve_job_snapshot(
            cfg, target, cfg.jobs["a"], "default.a"
        )
        assert snap.priority == "high"

    def test_plist_high(self) -> None:
        plist = crony._render_plist(
            "j", crony.EntityRef("default", "u-test"), "daily", None, "high"
        )
        assert crony._plist_priority_block("high") in plist
        assert "<string>Interactive</string>" in plist

    def test_plist_low(self) -> None:
        plist = crony._render_plist(
            "j", crony.EntityRef("default", "u-test"), "daily", None, "low"
        )
        assert crony._plist_priority_block("low") in plist
        assert "<string>Background</string>" in plist

    def test_plist_normal_and_none_emit_nothing(self) -> None:
        for p in ("normal", None):
            plist = crony._render_plist(
                "j", crony.EntityRef("default", "u-test"), "daily", None, p
            )
            assert "ProcessType" not in plist
        assert crony._plist_priority_block("normal") == ""
        assert crony._plist_priority_block(None) == ""

    def test_systemd_high_records_intent(self) -> None:
        svc = crony._render_systemd_service(
            "j", crony.EntityRef("default", "u-test"), "high"
        )
        assert "# crony priority=high" in svc
        # high leaves CPU/IO at the Linux defaults.
        assert "Nice=" not in svc
        assert "IOSchedulingClass" not in svc

    def test_systemd_low_sets_scheduling(self) -> None:
        svc = crony._render_systemd_service(
            "j", crony.EntityRef("default", "u-test"), "low"
        )
        assert "Nice=10" in svc
        assert "IOSchedulingClass=idle" in svc

    def test_systemd_normal_emits_nothing(self) -> None:
        svc = crony._render_systemd_service(
            "j", crony.EntityRef("default", "u-test"), "normal"
        )
        assert "Nice=" not in svc
        assert "IOSchedulingClass" not in svc

    def test_apply_writes_priority_into_plist(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "priority": "high",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert h.apply("j") == "added"
        plist = (h.agents / f"org.crony.{h.full('j')}.plist").read_text()
        assert "<string>Interactive</string>" in plist

    def test_priority_change_re_renders(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "priority": "high",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "priority": "low",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert h.apply("j") == "updated"
        plist = (h.agents / f"org.crony.{h.full('j')}.plist").read_text()
        assert "<string>Background</string>" in plist

    def test_hand_edited_priority_key_flags_stale(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "priority": "high",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=[h.full("j")], verbose=False, bundle=None)
        unit = h.agents / f"org.crony.{h.full('j')}.plist"
        content = unit.read_text()
        munged = content.replace(
            "<string>Interactive</string>", "<string>Standard</string>"
        )
        assert munged != content
        unit.write_text(munged)
        config = crony.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.runtime[ref].unit_is_stale is True


class TestApplyDarwin:
    def test_writes_plist_and_activates(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        result = h.apply("j")
        assert result == "added"
        plist_path = h.agents / f"org.crony.{h.full('j')}.plist"
        assert plist_path.exists()
        # Activated via launchctl (plus plutil validation)
        commands = [c[0] for c in h.calls]
        assert "plutil" in commands
        assert "launchctl" in commands
        # Hash stamp written
        assert (h.state_dir("j") / "snapshot.json").exists()

    def test_idempotent_when_unchanged(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.calls.clear()
        result = h.apply("j")
        assert result == "unchanged"
        # No further launchctl invocations on no-op apply
        assert all(c[0] != "launchctl" for c in h.calls)

    def test_drift_triggers_update(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        h.calls.clear()
        result = h.apply("j")
        assert result == "updated"
        plist = (h.agents / f"org.crony.{h.full('j')}.plist").read_text()
        assert "<integer>4</integer>" in plist


class TestApplyLinux:
    def test_writes_service_and_timer(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        assert (h.sysd / f"crony-{h.full('j')}.service").exists()
        assert (h.sysd / f"crony-{h.full('j')}.timer").exists()
        commands = [c[0] for c in h.calls]
        assert "systemctl" in commands


class TestApplyFullSync:
    def test_removes_orphans_on_no_arg_apply(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        # Pre-stamp an orphan: an entry's state dir with a
        # `snapshot.json` but no corresponding config entry.
        # `crony apply` with no args treats it as an orphan and
        # destroys it.
        orphan_dir = h.fabricate_orphan("old")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        assert (h.state_dir("j") / "snapshot.json").exists()
        assert not (orphan_dir / "snapshot.json").exists()

    def test_surgical_apply_leaves_orphans(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        orphan_dir = h.fabricate_orphan("old")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=["j"], verbose=False, bundle=None)
        assert (orphan_dir / "snapshot.json").exists()  # left alone

    def test_no_arg_apply_fully_wipes_orphan_state_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Apply's orphan removal goes through destroy_one with
        # default semantics, which fully wipes the entry's state
        # dir -- runtime artifacts included. This matches the
        # default destroy behavior so a renamed entry's residue
        # doesn't keep surfacing in status after the next apply.
        h = _ApplyHarness(tmp_path, monkeypatch)
        orphan_dir = h.fabricate_orphan("old")
        (orphan_dir / "run.log").write_text("old run\n")
        (orphan_dir / "last-run.json").write_text("{}")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        assert not orphan_dir.exists()

    def test_no_arg_apply_removes_broken_snapshot_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A state dir whose snapshot can't be loaded (wrong schema)
        # has no recoverable identity in the name-based world, so
        # the old sweep missed it. The ref-based reconcile removes
        # it: its ref is on disk but not in the live config.
        h = _ApplyHarness(tmp_path, monkeypatch)
        broken_dir = h.state / "default" / "dead-beef-uuid"
        broken_dir.mkdir(parents=True)
        (broken_dir / "snapshot.json").write_text(
            json.dumps({"schema": 999, "kind": "job", "name": "default.x"}),
            encoding="utf-8",
        )
        h.config({}, default_target_jobs=[])
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        assert not broken_dir.exists()

    def test_no_arg_apply_removes_unit_only_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A platform unit with no state dir (state wiped without a
        # destroy) is a unit-only orphan. The ref-based reconcile
        # removes the lingering unit file.
        h = _ApplyHarness(tmp_path, monkeypatch)
        plist = h.agents / "org.crony.default.ghost.plist"
        plist.write_text("", encoding="utf-8")
        h.config({}, default_target_jobs=[])
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        assert not plist.exists()

    def test_no_arg_apply_leaves_running_orphan_in_place(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An orphan with a run in progress is skipped (not wiped
        # out from under the running shim); a later apply / destroy
        # reclaims it.
        import fcntl as _fcntl

        h = _ApplyHarness(tmp_path, monkeypatch)
        orphan_dir = h.fabricate_orphan("busy")
        h.config({}, default_target_jobs=[])
        held = open(orphan_dir / "run.lock", "w")
        try:
            _fcntl.flock(held, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            crony.do_apply(jobs=[], verbose=False, bundle=None)
            assert orphan_dir.exists()
        finally:
            _fcntl.flock(held, _fcntl.LOCK_UN)
            held.close()

    def test_uuid_edit_surgical_apply_removes_old_residue(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Change an entry's uuid and apply just that job. The
        # name-keyed unit is re-pointed at the new uuid and the old
        # uuid's state dir -- unreachable history -- is reclaimed,
        # so a surgical apply is self-healing without a full sync.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg1 = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        old_uuid = cfg1.jobs["j"].uuid
        h.apply("j")
        old_dir = h.state / "default" / old_uuid
        assert old_dir.is_dir()
        new_uuid = "abcdabcd-1111-2222-3333-444455556666"
        cfg2 = h.config(
            {
                "job": {
                    "j": {
                        "uuid": new_uuid,
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert cfg2.jobs["j"].uuid == new_uuid
        crony.do_apply(jobs=["j"], verbose=False, bundle=None)
        assert (h.state / "default" / new_uuid / "snapshot.json").is_file()
        assert not old_dir.exists()

    def test_surgical_apply_leaves_unrelated_broken_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The surgical same-name cleanup only touches the applied
        # entry's own superseded residue -- an unrelated broken
        # orphan stays put (no full-sync side effects).
        h = _ApplyHarness(tmp_path, monkeypatch)
        broken_dir = h.state / "default" / "dead-beef-uuid"
        broken_dir.mkdir(parents=True)
        (broken_dir / "snapshot.json").write_text(
            json.dumps({"schema": 999, "kind": "job", "name": "default.x"}),
            encoding="utf-8",
        )
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=["j"], verbose=False, bundle=None)
        assert broken_dir.exists()

    def test_unchanged_suppressed_by_default(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        caplog: Any,
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        # Re-apply with no changes: nothing to print.
        with caplog.at_level(logging.INFO, logger="crony"):
            crony.do_apply(jobs=[], verbose=False, bundle=None)
        messages = [r.getMessage() for r in caplog.records]
        assert not any("unchanged" in m for m in messages), messages

    def test_masked_name_rejected_by_apply(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A masked entry is "reached via target" but excluded by
        # the host's filters; apply must reject it rather than
        # install a unit the host's selection would have skipped.
        # `_selected_full_names_per_bundle`'s `by_full` keyset is
        # the gate `do_apply` consults to distinguish "known to
        # this host" from "elsewhere"; the test pins that the
        # gate honors the masked-vs-selected distinction.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "platforms": ["linux"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        with pytest.raises(crony.UsageError, match="unselected on this host"):
            crony.do_apply(jobs=["j"], verbose=False, bundle=None)
        assert not (h.agents / f"org.crony.{h.full('j')}.plist").exists()

    def test_unchanged_shown_with_verbose(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        caplog: Any,
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        with caplog.at_level(logging.INFO, logger="crony"):
            crony.do_apply(jobs=[], verbose=True, bundle=None)
        messages = [r.getMessage() for r in caplog.records]
        assert any("unchanged" in m for m in messages), messages

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_apply(jobs=[], verbose=False, bundle="ghost")

    def test_bundle_scopes_orphan_removal(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Pre-stamp orphans in two namespaces. `apply -b borgadm`
        # must only prune orphans inside the borgadm namespace;
        # default's orphan stays put.
        h = _ApplyHarness(tmp_path, monkeypatch)
        default_orphan = h.fabricate_orphan("gone", bundle="default")
        borgadm_orphan = h.fabricate_orphan("gone", bundle="borgadm")
        h.config({}, default_target_jobs=[])
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
                "\n"
                '[target.darwin]\njobs = ["k"]\n',
            ),
            encoding="utf-8",
        )
        crony.do_apply(jobs=[], verbose=False, bundle="borgadm")
        assert (default_orphan / "snapshot.json").exists()
        assert not (borgadm_orphan / "snapshot.json").exists()

    def test_bundle_resolves_bare_name_in_scope(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `crony apply -b borgadm k` must resolve to `borgadm.k`,
        # not `default.k` (which doesn't exist on this host).
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
                "\n"
                '[target.darwin]\njobs = ["k"]\n',
            ),
            encoding="utf-8",
        )
        crony.do_apply(jobs=["k"], verbose=False, bundle="borgadm")
        bundles = crony.load_all_bundles()
        borgadm_cfg = bundles.by_name("borgadm").config
        k_uuid = borgadm_cfg.jobs["k"].uuid
        assert (h.state / "borgadm" / k_uuid / "snapshot.json").exists()

    def test_no_arg_apply_refuses_when_a_bundle_is_errored(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A bundle that failed to parse has no pending-side data;
        # the no-args sweep would mark every stamped entry of
        # that bundle as un-selected and wipe it. Refuse the
        # sweep so the operator either fixes the config or
        # passes explicit names / `--bundle` to scope the wipe
        # intentionally. Surgical apply still works.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.cfg_dropin.mkdir(parents=True, exist_ok=True)
        (h.cfg_dropin / "broken.toml").write_text(
            "this is not [valid toml", encoding="utf-8"
        )
        with pytest.raises(crony.UsageError, match="refusing the full-sync"):
            crony.do_apply(jobs=[], verbose=False, bundle=None)

    def test_bundle_scoped_apply_proceeds_when_sibling_bundle_errored(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The full-sync refusal is for the unscoped (bundle=None)
        # sweep, whose orphan removal spans the broken bundle. A
        # `--bundle` sweep is scoped to that one bundle (confirmed
        # parsed by require_known_bundle), so a broken *sibling*
        # bundle is out of scope and must not block it.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.cfg_dropin.mkdir(parents=True, exist_ok=True)
        (h.cfg_dropin / "broken.toml").write_text(
            "this is not [valid toml", encoding="utf-8"
        )
        sd = h.state_dir("j", cfg=cfg, ensure_snapshot=False)
        crony.do_apply(jobs=[], verbose=False, bundle=crony.DEFAULT_BUNDLE_NAME)
        assert (sd / "snapshot.json").exists()


class TestApplyCreatesRunLog:
    """Apply materializes an empty `run.log` in each entity's state
    dir so an operator can `tail -f` it before the first run, rather
    than racing the runner to create it.
    """

    def test_apply_creates_empty_run_log(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        sd = h.state_dir("j", cfg=cfg, ensure_snapshot=False)
        h.apply("j")
        log = sd / "run.log"
        assert log.is_file()
        assert log.read_text(encoding="utf-8") == ""

    def test_apply_creates_run_log_for_group(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A grouped entry (no own schedule) gets its run.log too.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {"jobs": ["a"], "schedule": "*-*-* 03:00"},
                },
            },
            default_target_jobs=["g"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        assert (
            h.state_dir("g", cfg=cfg, ensure_snapshot=False) / "run.log"
        ).is_file()
        assert (
            h.state_dir("a", cfg=cfg, ensure_snapshot=False) / "run.log"
        ).is_file()

    def test_reapply_does_not_truncate_existing_run_log(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j", cfg=cfg, ensure_snapshot=False)
        (sd / "run.log").write_text("prior run output\n", encoding="utf-8")
        h.apply("j")
        assert (sd / "run.log").read_text(
            encoding="utf-8"
        ) == "prior run output\n"


class TestApplyRenamePreservesHistory:
    """The whole point of keying state by uuid: a TOML edit that
    only changes a job's short name keeps the same state dir, so
    run.log and last-run.json carry over to the new name. The old
    name's platform unit becomes a stale label; apply_one removes
    it when re-applying the entry under its new name (unless the
    old name was handed to a live sibling in a name-swap edit).
    """

    def test_rename_keeps_state_destroys_old_unit(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "foo": {"command": "true", "schedule": "*-*-* 03:00"},
                },
            },
            default_target_jobs=["foo"],
        )
        h.apply("foo")
        # Plant a run.log artifact so we can prove it survives the
        # rename via the state dir's uuid-keyed continuity.
        foo_dir = h.state_dir("foo", cfg=cfg)
        log = foo_dir / "run.log"
        log.write_text("from the foo era\n", encoding="utf-8")
        # Rename the entry from `foo` to `bar`, keeping the same
        # uuid (the harness pins shorts across successive config
        # calls). Full sync apply: re-renders the unit at the new
        # label and orphan-sweeps the stale `foo` plist.
        cfg2_raw = {
            "job": {
                "bar": {"command": "true", "schedule": "*-*-* 03:00"},
            },
        }
        # The pin keys on (section, short); the rename short is
        # different, so seed the new short with foo's uuid by hand.
        foo_uuid = cfg.jobs["foo"].uuid
        cfg2_raw["job"]["bar"]["uuid"] = foo_uuid
        h.config(cfg2_raw, default_target_jobs=["bar"])
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        # State dir didn't move (same uuid -> same path); log
        # survives untouched.
        assert (foo_dir / "run.log").read_text() == "from the foo era\n"
        # New label is wired up; old label is gone.
        assert (h.agents / f"org.crony.{h.full('bar')}.plist").exists()
        assert not (h.agents / f"org.crony.{h.full('foo')}.plist").exists()

    def test_name_swap_preserves_renamed_entry_history(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # One edit renames `aaa` -> `zzz` (keeping its uuid) AND
        # adds a fresh `aaa` (new uuid) that grabs the freed name.
        # The freed name `aaa` sorts first, so the new claimant
        # applies BEFORE the renamed entry: it must not wipe the
        # renamed entry's state dir as "residue" (that dir is the
        # live `zzz` now, history and all), and the renamed entry
        # must not later unlink the new claimant's `aaa` unit.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"aaa": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["aaa"],
        )
        h.apply("aaa")
        renamed_uuid = cfg.jobs["aaa"].uuid
        renamed_dir = h.state / "default" / renamed_uuid
        (renamed_dir / "run.log").write_text("history\n", encoding="utf-8")
        # zzz inherits aaa's uuid (the rename); the new aaa gets a
        # fresh uuid (set explicitly so the harness pin doesn't
        # re-use the old one).
        new_aaa_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        h.config(
            {
                "job": {
                    "zzz": {
                        "uuid": renamed_uuid,
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                    "aaa": {
                        "uuid": new_aaa_uuid,
                        "command": "true",
                        "schedule": "*-*-* 04:00",
                    },
                }
            },
            default_target_jobs=["zzz", "aaa"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        # The renamed entry's history survived (not wiped by the
        # new aaa's residue sweep).
        assert (renamed_dir / "run.log").read_text() == "history\n"
        assert (renamed_dir / "snapshot.json").is_file()
        assert (h.state / "default" / new_aaa_uuid / "snapshot.json").is_file()
        # Both names have live units: the new aaa re-points the
        # `aaa` plist at its own uuid; zzz gets its own plist. The
        # renamed entry must not have unlinked the new aaa's unit.
        assert (h.agents / f"org.crony.{h.full('zzz')}.plist").exists()
        assert (h.agents / f"org.crony.{h.full('aaa')}.plist").exists()


class TestDestroy:
    def test_factory_reset(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j", ensure_snapshot=False)
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        assert (sd / "snapshot.json").exists()
        crony.do_destroy(jobs=[], bundle=None, orphans=False)
        assert not (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        assert not sd.exists()

    def test_surgical_destroy(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "a": {"command": "true", "schedule": "*-*-* 03:00"},
                    "b": {"command": "true", "schedule": "*-*-* 04:00"},
                }
            },
            default_target_jobs=["a", "b"],
        )
        h.apply("a")
        h.apply("b")
        sd_a = h.state_dir("a", ensure_snapshot=False)
        sd_b = h.state_dir("b", ensure_snapshot=False)
        crony.do_destroy(jobs=["a"], bundle=None, orphans=False)
        assert not sd_a.exists()
        assert (sd_b / "snapshot.json").exists()

    def test_default_destroy_wipes_state_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Destroy fully wipes the state dir, including runtime
        # artifacts like run.log / last-run.json / run.lock.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j")
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "run.log").write_text("...")
        crony.do_destroy(jobs=["j"], bundle=None, orphans=False)
        assert not sd.exists()

    def test_unknown_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown"):
            crony.do_destroy(
                jobs=["ghost"],
                bundle=None,
                orphans=False,
            )

    def test_destroy_refuses_with_run_in_progress_message(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A run-lock that's held when destroy runs must surface the
        # "run in progress; will not destroy" message and the
        # LOCK_BUSY exit class -- not a generic SubprocessError
        # whose user-visible string is "Command 'destroy X' returned
        # non-zero exit status 1."
        import fcntl as _fcntl

        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j")
        sd.mkdir(parents=True, exist_ok=True)
        lock_path = sd / "run.lock"
        held = open(lock_path, "w")
        _fcntl.flock(held, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        try:
            with pytest.raises(crony.LockBusyError) as exc:
                crony.do_destroy(
                    jobs=["j"],
                    bundle=None,
                    orphans=False,
                )
            assert "run in progress; will not destroy" in str(exc.value)
            assert exc.value.exit_code == crony.ExitCode.LOCK_BUSY
        finally:
            _fcntl.flock(held, _fcntl.LOCK_UN)
            held.close()

    def test_destroy_finds_units_with_no_state(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # If state was wiped (rm -rf ~/.local/state/crony) but
        # platform unit files linger, `crony destroy` (no args =
        # factory reset) must still find and clean them up via
        # the platform-unit discovery pass.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        assert plist.exists()
        # Wipe state but leave the plist behind.
        shutil.rmtree(h.state)
        assert plist.exists()
        # Factory reset still finds and removes the orphan plist.
        crony.do_destroy(jobs=[], bundle=None, orphans=False)
        assert not plist.exists()

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_destroy(
                jobs=[],
                bundle="ghost",
                orphans=False,
            )

    def test_bundle_scoped_destroy_leaves_other_bundles(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Two bundles, both stamped. `destroy -b borgadm` removes
        # only borgadm's remnants; default's survive.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
                "\n"
                '[target.darwin]\njobs = ["k"]\n',
            ),
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        h.apply("k", bundle="borgadm")
        k_dir = h.state / "borgadm" / borgadm.config.jobs["k"].uuid
        assert k_dir.exists()
        crony.do_destroy(
            jobs=[],
            bundle="borgadm",
            orphans=False,
        )
        assert (h.state_dir("j") / "snapshot.json").exists()
        assert not k_dir.exists()

    def test_bundle_scoped_destroy_works_when_bundle_config_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # destroy is a read-side / on-disk operation: scoping it to
        # a bundle whose config has since broken must still tear
        # down that bundle's on-disk remnants. The moment you most
        # want to scope-destroy a bundle is right after its config
        # broke, so `require_addressable_bundle` must accept a
        # bundle present only as on-disk state.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
                "\n"
                '[target.darwin]\njobs = ["k"]\n',
            ),
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        h.apply("k", bundle="borgadm")
        k_dir = h.state / "borgadm" / borgadm.config.jobs["k"].uuid
        assert k_dir.exists()
        # Break borgadm's config after it applied. Its snapshot /
        # unit linger on disk but its pending config no longer
        # parses, so it's an errored bundle, not a loaded one.
        (h.cfg_dropin / "borgadm.toml").write_text(
            "this is not [valid toml", encoding="utf-8"
        )
        crony.do_destroy(jobs=[], bundle="borgadm", orphans=False)
        assert not k_dir.exists()
        # default bundle's remnants are untouched.
        assert (h.state_dir("j") / "snapshot.json").exists()

    def test_bundle_qualified_other_bundle_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
                "\n"
                '[target.darwin]\njobs = ["k"]\n',
            ),
            encoding="utf-8",
        )
        with pytest.raises(crony.UsageError, match="bundle 'default'"):
            crony.do_destroy(
                jobs=["default.j"],
                bundle="borgadm",
                orphans=False,
            )

    def test_orphans_flag_destroys_unselected_remnants(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Apply a config, drop the entry from config (a rename
        # leaves the old name's remnants behind), then run
        # `destroy --orphans`. Only the orphaned remnants are
        # removed; entries that remain selected stay put.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "live": {"command": "true", "schedule": "*-*-* 03:00"},
                    "renamed": {"command": "true", "schedule": "*-*-* 04:00"},
                }
            },
            default_target_jobs=["live", "renamed"],
        )
        h.apply("live")
        h.apply("renamed")
        renamed_dir = h.state_dir("renamed", cfg=cfg)
        # Drop `renamed` from config without applying -- now
        # selected={live} but discovered={live,renamed}.
        h.config(
            {
                "job": {
                    "live": {"command": "true", "schedule": "*-*-* 03:00"},
                }
            },
            default_target_jobs=["live"],
        )
        crony.do_destroy(jobs=[], bundle=None, orphans=True)
        assert (h.state_dir("live") / "snapshot.json").exists()
        assert not renamed_dir.exists()
        assert not (h.agents / f"org.crony.{h.full('renamed')}.plist").exists()

    def test_orphans_flag_under_bundle_scopes_to_bundle(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Two bundles each have one orphan. `--orphans -b borgadm`
        # touches only borgadm's orphan; default's orphan stays.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"old_d": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["old_d"],
        )
        h.apply("old_d")
        default_old_d_dir = h.state_dir("old_d")
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.old_b]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
                "\n"
                '[target.darwin]\njobs = ["old_b"]\n',
            ),
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        h.apply("old_b", bundle="borgadm")
        borgadm_old_b_dir = (
            h.state / "borgadm" / borgadm.config.jobs["old_b"].uuid
        )
        # Strip both entries from their configs, leaving them
        # as orphan remnants on disk.
        h.config({}, default_target_jobs=[])
        (h.cfg_dropin / "borgadm.toml").write_text(
            "[target.darwin]\njobs = []\n",
            encoding="utf-8",
        )
        crony.do_destroy(
            jobs=[],
            bundle="borgadm",
            orphans=True,
        )
        assert (default_old_d_dir / "snapshot.json").exists()
        assert not borgadm_old_b_dir.exists()

    def test_orphans_flag_with_positional_names_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="mutually exclusive"):
            crony.do_destroy(
                jobs=["foo"],
                bundle=None,
                orphans=True,
            )

    def test_orphans_flag_leaves_active_entries_alone(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # No orphans on disk: `--orphans` is a no-op and active
        # entries are untouched.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony.do_destroy(jobs=[], bundle=None, orphans=True)
        assert (h.state_dir("j") / "snapshot.json").exists()
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()


# =============================================================================
# Platform/host detection
# =============================================================================


class TestPlatformDetection:
    def test_current_platform(self) -> None:
        p = crony.current_platform()
        assert p in ("darwin", "linux")

    def test_current_host(self) -> None:
        h = crony.current_host()
        assert isinstance(h, str)
        assert len(h) > 0
        assert "." not in h


# =============================================================================
# Type strictness & bound checks
# =============================================================================


class TestTypeStrictness:
    """Booleans must not silently pass for int-typed fields, and
    int-typed defaults must be positive.
    """

    def test_bool_rejected_for_int_field(self) -> None:
        with pytest.raises(crony.ConfigError, match="bool"):
            _parse({"defaults": {"job_timeout_sec": True}})

    def test_negative_default_timeout_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            _parse({"defaults": {"job_timeout_sec": -5}})

    def test_zero_default_timeout_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            _parse({"defaults": {"job_timeout_sec": 0}})

    def test_negative_default_attach_max_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            _parse({"defaults": {"notify_attach_max_kb": -1}})

    def test_negative_default_log_keep_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            _parse({"defaults": {"log_keep_runs": 0}})


# =============================================================================
# Name shape validation
# =============================================================================


class TestNameShape:
    """Job/group/host names map onto filesystem paths and unit labels.

    They must be safe filename characters; reject empty, leading
    punctuation, slashes, and spaces at parse time so later code
    that builds filesystem paths and unit labels doesn't have to
    defend against pathological inputs.
    """

    @pytest.mark.parametrize(
        "bad_name",
        ["", ".", "..", ".hidden", "a/b", "has space", "-leading"],
    )
    def test_invalid_job_name(self, bad_name: str) -> None:
        _assert_errored_job({"job": {bad_name: _job()}}, bad_name, "must match")

    @pytest.mark.parametrize(
        "good_name",
        ["a", "brew-update", "rust_update", "Job1", "x.y.z"],
    )
    def test_valid_job_name(self, good_name: str) -> None:
        cfg = _parse({"job": {good_name: _job()}})
        assert good_name in cfg.jobs

    def test_invalid_group_name(self) -> None:
        _assert_errored_job_group(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "bad/name": {
                        "jobs": ["a"],
                        "schedule": "daily",
                    }
                },
            },
            "bad/name",
            "must match",
        )

    def test_invalid_host_name(self) -> None:
        with pytest.raises(crony.ConfigError, match="must match"):
            _parse(
                {
                    "job": {"a": _job()},
                    "target": {"host": {"bad name": {"jobs": ["a"]}}},
                }
            )


class TestResolveCliName:
    """`-b/--bundle` reshapes how bare and qualified CLI args resolve.

    Without `-b`, bare 'foo' resolves to 'default.foo' (legacy
    behavior covered elsewhere). Under `-b <name>`, bare resolves
    in `<name>`, qualified `<name>.short` round-trips, and any
    other qualified prefix is rejected so a bulk operation can't
    sneak in a cross-bundle name.
    """

    def test_bare_resolves_to_default_without_scope(self) -> None:
        assert crony.resolve_cli_name("foo", None) == "default.foo"

    def test_qualified_round_trips_without_scope(self) -> None:
        assert crony.resolve_cli_name("borgadm.k", None) == "borgadm.k"

    def test_bare_resolves_in_scope_bundle(self) -> None:
        assert crony.resolve_cli_name("foo", "borgadm") == "borgadm.foo"

    def test_qualified_in_scope_round_trips(self) -> None:
        assert crony.resolve_cli_name("borgadm.k", "borgadm") == "borgadm.k"

    def test_qualified_other_bundle_rejected(self) -> None:
        with pytest.raises(crony.UsageError, match="default"):
            crony.resolve_cli_name("default.k", "borgadm")


# =============================================================================
# Tightened schedule validation
# =============================================================================


class TestScheduleTightened:
    """validate_schedule rejects strings that contain permitted chars
    but lack a real time component. Catches typos like '*' or '1234'
    before they reach the platform translator.
    """

    @pytest.mark.parametrize(
        "bad",
        ["*", "1234", "-not-a-real-schedule-", "*-*-*", "***"],
    )
    def test_no_time_component_rejected(self, bad: str) -> None:
        with pytest.raises(crony.ConfigError):
            crony.validate_schedule(bad)


# =============================================================================
# Broken-pipe-aware logging
# =============================================================================


class TestBrokenPipeHandler:
    """Smoke check that BrokenPipeAwareStreamHandler swallows
    BrokenPipeError without raising and swaps to /dev/null so the
    next emit doesn't blow up either.
    """

    def test_handler_swaps_stream_on_broken_pipe(self, tmp_path: Path) -> None:
        # Create the handler attached to a regular file we can verify.
        log_path = tmp_path / "out"
        stream = open(log_path, "w")
        handler = crony.BrokenPipeAwareStreamHandler(stream)
        # Synthesize a "BrokenPipeError caught while emitting" by
        # stuffing one into sys.exc_info via a dummy raise.
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        try:
            raise BrokenPipeError("simulated")
        except BrokenPipeError:
            handler.handleError(record)
        # Stream should be swapped (and not the original anymore).
        assert handler.stream is not stream
        # And future emits should not raise.
        handler.emit(record)


# =============================================================================
# Status / enable / disable / linger
# =============================================================================


class TestLingerDetection:
    def test_returns_none_no_user(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("LOGNAME", raising=False)
        monkeypatch.setattr(crony.os, "getuid", lambda: -1)
        assert crony.linger_enabled(user=None) is None

    def test_sentinel_file_present(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        sentinel = tmp_path / "edp"
        sentinel.touch()
        real_path = crony.Path

        def fake_path(p: Any) -> Path:
            if str(p) == "/var/lib/systemd/linger/edp":
                return sentinel
            return Path(real_path(p))

        monkeypatch.setattr(crony, "Path", fake_path)
        assert crony.linger_enabled(user="edp") is True


class TestUnitStateDarwin:
    def test_loaded_label_is_enabled(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_launchctl_print_disabled", lambda: "")
        monkeypatch.setattr(
            crony, "_launchctl_list", lambda: "-\t0\torg.crony.j\n"
        )
        assert crony._unit_state("j", "darwin") == "enabled"

    def test_disabled_record_takes_precedence(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            crony,
            "_launchctl_print_disabled",
            lambda: '"org.crony.j" => disabled',
        )
        monkeypatch.setattr(
            crony, "_launchctl_list", lambda: "-\t0\torg.crony.j\n"
        )
        assert crony._unit_state("j", "darwin") == "disabled"

    def test_none_when_neither_loaded_nor_disabled(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(crony, "_launchctl_print_disabled", lambda: "")
        monkeypatch.setattr(crony, "_launchctl_list", lambda: "")
        assert crony._unit_state("j", "darwin") == "none"


class TestUnitStateLinux:
    def test_enabled(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "enabled")
        assert crony._unit_state("j", "linux") == "enabled"

    def test_disabled(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "disabled")
        assert crony._unit_state("j", "linux") == "disabled"

    def test_none_on_empty(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "")
        assert crony._unit_state("j", "linux") == "none"


class TestConfigState:
    """`Config.config_state` classification driven through the real
    apply -> load_config path (vs `TestConfigStateInMemory`, which
    plants snapshots by hand). Confirms apply_one writes a snapshot
    that load_config scores as synced, and that a config edit
    without re-apply flips it to stale.
    """

    def _ref(self, config: Any, full: str) -> Any:
        return config.pending.by_full_name.get(
            full
        ) or config.current.by_full_name.get(full)

    def test_missing_when_in_config_no_stamp(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        config = crony.load_config()
        assert config.config_state(self._ref(config, "default.j")) == "missing"

    def test_synced_after_apply(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        config = crony.load_config()
        assert config.config_state(self._ref(config, "default.j")) == "synced"

    def test_stale_when_config_changes(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # Edit the config (new schedule) without re-applying.
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        config = crony.load_config()
        assert config.config_state(self._ref(config, "default.j")) == "stale"

    def test_orphan_stamped_not_in_config(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        # Stamp the entry on disk, then drop it from the config.
        h.fabricate_orphan("old")
        h.config({}, default_target_jobs=[])
        config = crony.load_config()
        ref = config.current.by_full_name["default.old"]
        assert config.config_state(ref) == "orphan"


class TestEnableDisable:
    def test_enable_invokes_systemctl_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.calls.clear()
        crony.do_enable(jobs=["j"], bundle=None)
        cmd = next(c for c in h.calls if c[0] == "systemctl")
        assert cmd == [
            "systemctl",
            "--user",
            "--quiet",
            "enable",
            "--now",
            f"crony-{h.full('j')}.timer",
        ]

    def test_disable_invokes_launchctl_on_darwin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.calls.clear()
        crony.do_disable(jobs=["j"], bundle=None)
        verbs = [c[1] if len(c) > 1 else "" for c in h.calls]
        assert "unload" in verbs
        assert "disable" in verbs

    def test_unknown_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="not stamped"):
            crony.do_enable(jobs=["ghost"], bundle=None)

    def test_unknown_name_rejected_for_disable(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="not stamped"):
            crony.do_disable(jobs=["ghost"], bundle=None)

    def test_unscheduled_entry_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        with pytest.raises(crony.UsageError, match="grouped entries"):
            crony.do_enable(jobs=["a"], bundle=None)
        with pytest.raises(crony.UsageError, match="grouped entries"):
            crony.do_disable(jobs=["a"], bundle=None)

    def test_enable_keys_off_applied_schedule_not_pending(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `a` is applied as a grouped (schedule-less) entry, so its
        # installed unit has no timer. A later config edit gives
        # `a` its own schedule but is NOT applied. enable must
        # still refuse: it arms the *installed* unit, and the
        # applied snapshot -- not the pending edit -- decides
        # whether there's a timer to arm.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        # Pending edit: `a` gains its own schedule (still grouped
        # under g too), not applied.
        h.config(
            {
                "job": {"a": {"command": "true", "schedule": "*-*-* 05:00"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["g", "a"],
        )
        with pytest.raises(crony.UsageError, match="grouped entries"):
            crony.do_enable(jobs=["a"], bundle=None)

    def test_trigger_invokes_launchctl_kickstart_on_darwin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.calls.clear()
        crony.do_trigger(
            jobs=["j"], wait=False, trigger_timeout=None, bundle=None
        )
        cmd = next(c for c in h.calls if c[0] == "launchctl")
        assert cmd == [
            "launchctl",
            "kickstart",
            f"gui/{os.getuid()}/org.crony.{h.full('j')}",
        ]

    def test_trigger_invokes_systemctl_start_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.calls.clear()
        crony.do_trigger(
            jobs=["j"], wait=False, trigger_timeout=None, bundle=None
        )
        cmd = next(c for c in h.calls if c[0] == "systemctl")
        assert cmd == [
            "systemctl",
            "--user",
            "start",
            f"crony-{h.full('j')}.service",
        ]

    def test_trigger_unknown_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="not runnable here"):
            crony.do_trigger(
                jobs=["ghost"], wait=False, trigger_timeout=None, bundle=None
            )

    def test_trigger_wait_refuses_config_removed_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Apply a job, then drop it from the config so it's an
        # installed orphan. `trigger --wait` resolves timeouts from
        # the config, which no longer describes it -- it must raise
        # a clean UsageError, not a raw KeyError.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="not in the current config"):
            crony.do_trigger(
                jobs=[h.full("j")],
                wait=True,
                trigger_timeout=None,
                bundle=None,
            )

    def test_trigger_wait_maps_timeout_to_nonzero_exit(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `crony trigger --wait` must surface a non-zero exit code
        # when the job times out (exit_code is None for that class).
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(
            crony,
            "_trigger_unit_sync",
            lambda *a, **kw: {
                "exit_class": "timeout",
                "exit_code": None,
                "signal": None,
            },
        )
        with pytest.raises(SystemExit) as exc:
            crony.do_trigger(
                jobs=["j"], wait=True, trigger_timeout=None, bundle=None
            )
        assert exc.value.code == int(crony.ExitCode.TIMEOUT)

    def test_trigger_wait_maps_signal_to_128_plus_n(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(
            crony,
            "_trigger_unit_sync",
            lambda *a, **kw: {
                "exit_class": "signal",
                "exit_code": None,
                "signal": 9,
            },
        )
        with pytest.raises(SystemExit) as exc:
            crony.do_trigger(
                jobs=["j"], wait=True, trigger_timeout=None, bundle=None
            )
        assert exc.value.code == 137

    def test_trigger_wait_passes_through_command_exit_code(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "false", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(
            crony,
            "_trigger_unit_sync",
            lambda *a, **kw: {
                "exit_class": "fail",
                "exit_code": 7,
                "signal": None,
            },
        )
        with pytest.raises(SystemExit) as exc:
            crony.do_trigger(
                jobs=["j"], wait=True, trigger_timeout=None, bundle=None
            )
        assert exc.value.code == 7

    def test_trigger_timeout_requires_wait(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        with pytest.raises(crony.UsageError, match="--trigger-timeout"):
            crony.do_trigger(
                jobs=["j"], wait=False, trigger_timeout=10, bundle=None
            )

    def test_trigger_rejects_ambiguous_uuid_swap(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A uuid edit is a new entity, not a rename: the name `j` now
        # addresses one uuid on disk (the applied unit) and a different
        # uuid in config. That's ambiguous, so `do_trigger` refuses and
        # points at `crony apply` rather than guessing a target.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "uuid": "11111111-1111-1111-1111-111111111111",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        current_sd = h.state_dir("j", cfg=cfg)
        assert current_sd.name == "11111111-1111-1111-1111-111111111111"
        # Edit the uuid in config without re-applying. Pending now
        # references a different ref than current.
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "uuid": "22222222-2222-2222-2222-222222222222",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        with pytest.raises(crony.UsageError, match="crony apply"):
            crony.do_trigger(
                jobs=["j"], wait=False, trigger_timeout=None, bundle=None
            )

    def _rename_keeping_uuid(
        self, h: "_ApplyHarness", old: str, new: str
    ) -> str:
        """Apply a scheduled job `old`, then rewrite config renaming it
        to `new` (same uuid) without re-applying. Returns the uuid.
        """
        cfg = h.config(
            {"job": {old: {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=[old],
        )
        h.apply(old)
        job_uuid: str = cfg.jobs[old].uuid
        h.config(
            {
                "job": {
                    new: {
                        "uuid": job_uuid,
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    }
                }
            },
            default_target_jobs=[new],
        )
        return job_uuid

    def test_trigger_renamed_entry_by_new_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A rename keeps the uuid; triggering by the new config name
        # before re-apply fires the installed unit (still under the old
        # name) and flags the uuid-keyed state dir.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        job_uuid = self._rename_keeping_uuid(h, "j", "k")
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / job_uuid
        h.calls.clear()
        crony.do_trigger(
            jobs=["k"], wait=False, trigger_timeout=None, bundle=None
        )
        assert (sd / "user-trigger.flag").exists()
        kick = next(
            c for c in h.calls if c[0] == "launchctl" and c[1] == "kickstart"
        )
        assert any("org.crony.default.j" in part for part in kick)
        assert not any("org.crony.default.k" in part for part in kick)

    def test_enable_renamed_entry_by_new_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # enable by the new name acts on the installed (old-name) unit.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        self._rename_keeping_uuid(h, "j", "k")
        h.calls.clear()
        crony.do_enable(jobs=["k"], bundle=None)
        enable = next(
            c for c in h.calls if c[0] == "launchctl" and c[1] == "enable"
        )
        assert any("org.crony.default.j" in part for part in enable)

    def test_disable_renamed_entry_by_new_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # disable by the new name acts on the installed (old-name) unit.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        self._rename_keeping_uuid(h, "j", "k")
        h.calls.clear()
        crony.do_disable(jobs=["k"], bundle=None)
        disable = next(
            c for c in h.calls if c[0] == "launchctl" and c[1] == "disable"
        )
        assert any("org.crony.default.j" in part for part in disable)

    def test_destroy_renamed_entry_by_new_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # destroy by the new name removes the installed (old-name) unit
        # and wipes the shared uuid state dir.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        job_uuid = self._rename_keeping_uuid(h, "j", "k")
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / job_uuid
        old_plist = h.agents / f"org.crony.{h.full('j')}.plist"
        assert old_plist.exists()
        assert sd.exists()
        crony.do_destroy(jobs=["k"], bundle=None, orphans=False)
        assert not old_plist.exists()
        assert not sd.exists()

    def test_destroy_rejects_ambiguous_uuid_swap(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A uuid edit (same name, new uuid) is an identity change, not
        # a rename: `j` addresses one uuid on disk and another in
        # config, so destroy refuses rather than guessing which to wipe.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "uuid": "11111111-1111-1111-1111-111111111111",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "uuid": "22222222-2222-2222-2222-222222222222",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        with pytest.raises(crony.UsageError, match="crony apply"):
            crony.do_destroy(jobs=["j"], bundle=None, orphans=False)

    def test_trigger_refuses_unit_only_entry(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A unit-only remnant (platform unit on disk, no parseable
        # snapshot) is not runnable: trigger must refuse rather than
        # fire a unit whose snapshot can't load.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # Drop the snapshot, leaving only the platform unit -> unit_only.
        sd = h.state_dir("j", ensure_snapshot=False)
        (sd / "snapshot.json").unlink()
        with pytest.raises(crony.UsageError, match="crony apply"):
            crony.do_trigger(
                jobs=["j"], wait=False, trigger_timeout=None, bundle=None
            )

    def test_trigger_works_on_schedule_less_job(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Every entry installs a platform unit, including schedule-
        # less group-only jobs. trigger fires that unit directly.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        h.calls.clear()
        crony.do_trigger(
            jobs=["a"], wait=False, trigger_timeout=None, bundle=None
        )
        cmd = next(c for c in h.calls if c[0] == "launchctl")
        assert cmd[1] == "kickstart"
        assert cmd[2].endswith(f"org.crony.{h.full('a')}")

    def test_apply_preserves_disabled_state_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "disabled")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        h.calls.clear()
        h.apply("j")
        # Strip leading flags (`--user`, `--quiet`, etc.) and pull
        # the systemctl subcommand verb so the test isn't tied to
        # flag ordering.
        verbs = [
            next((a for a in c[1:] if not a.startswith("-")), "")
            for c in h.calls
        ]
        assert "daemon-reload" in verbs
        assert "enable" not in verbs

    def test_apply_after_state_wipe_preserves_disabled_unit(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # State-dir wipe + surviving platform unit + scheduler
        # reporting `disabled`: re-apply must consult the live
        # scheduler state, not the absent snapshot, to decide
        # whether to preserve the disable. The unit can outlive
        # its state dir, so the disable signal lives only in the
        # scheduler view.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # Wipe state; the timer file under sysd survives.
        shutil.rmtree(h.state)
        timer = h.sysd / f"crony-{h.full('j')}.timer"
        assert timer.exists()
        # Scheduler reports the unit as disabled (user disabled it
        # by hand before the state wipe).
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "disabled")
        h.calls.clear()
        h.apply("j")
        verbs = [
            next((a for a in c[1:] if not a.startswith("-")), "")
            for c in h.calls
        ]
        assert "enable" not in verbs

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_enable(jobs=[], bundle="ghost")
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_disable(jobs=[], bundle="ghost")
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_trigger(
                jobs=[], wait=False, trigger_timeout=None, bundle="ghost"
            )

    def test_bundle_or_jobs_required(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="specify job names"):
            crony.do_enable(jobs=[], bundle=None)
        with pytest.raises(crony.UsageError, match="specify job names"):
            crony.do_disable(jobs=[], bundle=None)
        with pytest.raises(crony.UsageError, match="specify job names"):
            crony.do_trigger(
                jobs=[], wait=False, trigger_timeout=None, bundle=None
            )

    def test_enable_bulk_skips_unscheduled_in_bundle(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # TomlBundle has one scheduled job and one schedule-less group
        # member. `enable -b foo` enables the scheduled one and
        # silently skips the unscheduled one rather than aborting.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {
                "job": {
                    "a": {"command": "true"},
                    "b": {"command": "true", "schedule": "*-*-* 03:00"},
                },
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 04:00"}},
            },
            default_target_jobs=["b", "g"],
        )
        h.apply("a")
        h.apply("b")
        h.apply("g")
        h.calls.clear()
        crony.do_enable(jobs=[], bundle="default")
        # Only b and g (scheduled) get enable invocations.
        timers = [
            c[-1]
            for c in h.calls
            if c and c[0] == "systemctl" and "enable" in c
        ]
        assert f"crony-{h.full('b')}.timer" in timers
        assert f"crony-{h.full('g')}.timer" in timers
        assert f"crony-{h.full('a')}.timer" not in timers

    def test_trigger_bulk_includes_unscheduled_in_bundle(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Trigger fires every stamped entry, including schedule-less
        # ones (their dormant units kickstart fine).
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 04:00"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        h.apply("g")
        h.calls.clear()
        crony.do_trigger(
            jobs=[], wait=False, trigger_timeout=None, bundle="default"
        )
        labels = [
            c[-1]
            for c in h.calls
            if c and c[0] == "launchctl" and c[1] == "kickstart"
        ]
        assert any(h.full("a") in lbl for lbl in labels)
        assert any(h.full("g") in lbl for lbl in labels)

    def test_enable_disable_trigger_scoped_when_bundle_config_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # enable / disable / trigger address installed units, not
        # the pending config, so scoping them to a bundle whose
        # config has since broken must still act on that bundle's
        # installed entries rather than be refused as an unknown
        # bundle.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config({}, default_target_jobs=[])
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
                "\n"
                '[target.darwin]\njobs = ["k"]\n',
            ),
            encoding="utf-8",
        )
        h.apply("k", bundle="borgadm")
        # Break borgadm's config after it applied; its snapshot /
        # unit linger but its pending config no longer parses.
        (h.cfg_dropin / "borgadm.toml").write_text(
            "this is not [valid toml", encoding="utf-8"
        )

        h.calls.clear()
        crony.do_enable(jobs=[], bundle="borgadm")
        assert any("borgadm.k" in str(c) for c in h.calls)

        h.calls.clear()
        crony.do_disable(jobs=[], bundle="borgadm")
        assert any("borgadm.k" in str(c) for c in h.calls)

        h.calls.clear()
        crony.do_trigger(
            jobs=[], wait=False, trigger_timeout=None, bundle="borgadm"
        )
        assert any("borgadm.k" in str(c) for c in h.calls)


class TestStatusUuidColumn:
    """`uuid` is an opt-in column rendering the `<bundle>:<UUID>`
    ref form. Default `cols=None` hides it (the default identity
    column is `job-or-uuid`, which shows the plain name for an
    unambiguous entry); `cols="job,uuid"` surfaces the stable
    identity for scripts correlating status with disk state or the
    config file's `uuid =` keys.
    """

    def test_opt_in_uuid_column_renders(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,uuid",
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "UUID" in out
        assert cfg.jobs["j"].uuid in out

    def test_uuid_column_omitted_by_default(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # The default identity column (`job-or-uuid`) shows the
        # plain name for an unambiguous entry, so the bare uuid
        # value never appears unless the opt-in `uuid` column is
        # requested. (The "UUID" substring does appear in the
        # default "JOB / UUID" header, so assert on the value.)
        assert cfg.jobs["j"].uuid not in out
        assert "default.j" in out

    def test_uuid_column_for_orphan_row_comes_from_snapshot(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        sd = h.fabricate_orphan("ghost")
        ghost_uuid = sd.name
        h.config({}, default_target_jobs=[])
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,uuid,config",
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert ghost_uuid in out
        assert "orphan" in out


class TestStatusReport:
    def test_prints_table(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "JOB" in out
        assert "CONFIG" in out
        assert "SCHEDULE" in out
        assert "LAST" in out
        assert "j" in out
        assert "synced" in out

    def test_orphan_appears(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.fabricate_orphan("ghost")
        h.config({}, default_target_jobs=[])
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "ghost" in out
        assert "orphan" in out

    def test_orphan_appears_when_only_unit_file_remains(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # State wiped but the platform unit lingers: status
        # discovers it via the platform-unit scan and reports
        # it as orphan so the user can `crony destroy` it.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # Drop the entry from the config and wipe state, leaving
        # only the plist behind.
        h.config({}, default_target_jobs=[])
        shutil.rmtree(h.state)
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert h.full("j") in out
        assert "orphan" in out

    def test_cols_replaces_default_column_set(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,last,last-ran",
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        header = out.splitlines()[0]
        assert "JOB" in header
        assert "LAST" in header
        assert "LAST RAN" in header
        # Columns omitted from --cols are absent.
        assert "CONFIG" not in header
        assert "SCHEDULE" not in header
        assert "UNIT" not in header

    def test_cols_unknown_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown status column"):
            crony.do_status(
                jobs=[],
                cols="job,bogus",
                show_masked=False,
                bundle=None,
                config_current=False,
                config_pending=False,
                exclude_healthy=False,
            )

    def test_last_ran_column_shows_relative_time(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Write a last-run.json with a timestamp ~5 minutes back
        # and confirm the LAST RAN column renders "5m ago".
        import datetime as _dt

        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j")
        sd.mkdir(parents=True, exist_ok=True)
        five_min_ago = (
            _dt.datetime.now(_dt.timezone.utc).astimezone()
            - _dt.timedelta(minutes=5)
        ).isoformat(timespec="seconds")
        (sd / "last-run.json").write_text(
            f'{{"started_at": "{five_min_ago}",'
            f' "ended_at": "{five_min_ago}",'
            ' "exit_code": 0, "exit_class": "ok"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,last-ran",
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # Allow a small wallclock drift between writing the file and
        # the status read -- it should still land in the 4-6m range.
        assert any(f"{m}m ago" in out for m in (4, 5, 6)), (
            f"expected ~5m ago in:\n{out}"
        )

    def test_last_ran_column_shows_never_when_no_run(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,last-ran",
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "never" in out

    def test_long_names_keep_columns_aligned(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Names longer than the historical 30-char JOB column width
        # used to push later columns out of alignment. The width
        # should now adapt to the longest name actually printed.
        h = _ApplyHarness(tmp_path, monkeypatch)
        long_name = "this-is-a-deliberately-long-job-name-for-alignment"
        h.config(
            {
                "job": {
                    long_name: {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    }
                }
            },
            default_target_jobs=[long_name],
        )
        h.apply(long_name)
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        rows = [r for r in capsys.readouterr().out.splitlines() if r.strip()]
        # The header's CONFIG label and every row's state token
        # (synced / missing / orphan / etc.) should start at the
        # same column.
        config_col = rows[0].index("CONFIG")
        valid_states = {"synced", "stale", "missing", "orphan"}
        for r in rows[1:]:
            token = r[config_col:].split(" ", 1)[0]
            assert token in valid_states, (
                f"row {r!r} not aligned: col {config_col} -> {token!r}"
            )

    def test_all_flag_lists_platform_masked_jobs(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A linux-only job under a darwin target is reachable via
        # the target but excluded by its own platforms filter; with
        # --all it should surface as config=masked.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "platforms": ["linux"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert h.full("j") not in out
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert h.full("j") in out
        assert "masked" in out

    def test_masked_entry_shows_uuid_column(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A masked entry is in neither the pending nor current
        # graph, but it still has a config-declared uuid: the UUID
        # column must show its `<bundle>:<uuid>` ref, not a blank
        # cell.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "platforms": ["linux"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        crony.do_status(
            jobs=[],
            cols="job,uuid",
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        masked_line = next(
            line for line in out.splitlines() if h.full("j") in line
        )
        assert f"default:{cfg.jobs['j'].uuid}" in masked_line

    def test_masked_entry_shows_group_membership(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A masked entry is absent from the pending / current graphs
        # that GROUPS is normally indexed from, but the status tree
        # still nests it under its parent -- so GROUPS must show that
        # config-declared parent, without a false `^` divergence flag.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        h.config(
            {
                "job": {
                    # `j` is masked on darwin; `k` keeps the group
                    # selected so it isn't an empty-cascade mask.
                    "j": {"command": "true", "platforms": ["linux"]},
                    "k": {"command": "true"},
                },
                "job-group": {
                    "g": {"jobs": ["j", "k"], "schedule": "*-*-* 03:00"},
                },
            },
            default_target_jobs=["g"],
        )
        crony.do_status(
            jobs=[],
            cols="job-or-uuid,config,groups",
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        j_line = next(
            line
            for line in out.splitlines()
            if line.split() and line.split()[0] == h.full("j")
        )
        assert "masked" in j_line
        assert h.full("g") in j_line
        # Config-only membership must not read as drift.
        assert "^" not in j_line

    def test_unused_offtree_entry_has_no_group_membership(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # An entry defined but not reached by this host's target is
        # `unused` and renders flat (off-tree), not nested. The
        # config-membership fallback is gated on tree presence, so
        # an off-tree entry's GROUPS cell must stay empty rather than
        # claim a parent the row isn't shown under.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        h.config(
            {
                "job": {
                    "sel": {"command": "true", "schedule": "*-*-* 03:00"},
                    # `gu` (and its child `ju`) are defined but absent
                    # from the target -> `unused`, off-tree.
                    "ju": {"command": "true"},
                },
                "job-group": {
                    "gu": {"jobs": ["ju"], "schedule": "*-*-* 04:00"},
                },
            },
            default_target_jobs=["sel"],
        )
        crony.do_status(
            jobs=[],
            cols="job-or-uuid,config,groups",
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        ju_line = next(
            line
            for line in out.splitlines()
            if line.split() and line.split()[0] == h.full("ju")
        )
        assert h.full("gu") not in ju_line

    def test_masked_entry_shows_schedule(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Masked entries shown nested in the tree surface their
        # config-declared SCHEDULE: a grouped one as "grouped", a
        # scheduled one as its cron, neither with a false `^` divergence
        # marker.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        h.config(
            {
                "job": {
                    # masked grouped child (platform); `k` keeps `g`
                    # from being an empty-cascade mask.
                    "j": {"command": "true", "platforms": ["linux"]},
                    "k": {"command": "true"},
                    # masked top-level job that carries its own cron.
                    "sched": {
                        "command": "true",
                        "schedule": "*-*-* 05:00",
                        "hosts": ["other"],
                    },
                },
                "job-group": {
                    "g": {"jobs": ["j", "k"], "schedule": "*-*-* 03:00"},
                },
            },
            default_target_jobs=["g", "sched"],
        )
        crony.do_status(
            jobs=[],
            cols="job-or-uuid,config,schedule",
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out: str = capsys.readouterr().out

        def _row(name: str) -> str:
            return next(
                line
                for line in out.splitlines()
                if line.split() and line.split()[0] == name
            )

        j_row = _row(h.full("j"))
        assert "masked" in j_row
        assert "grouped" in j_row
        assert not j_row.rstrip().endswith("^")
        sched_row = _row(h.full("sched"))
        assert "masked" in sched_row
        assert "*-*-* 05:00" in sched_row
        assert not sched_row.rstrip().endswith("^")

    def test_all_flag_with_masked_by_column_shows_reason(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Three jobs: host-only-other, platform-other, both-other.
        # Each should report its masking axis in MASKED BY.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        h.config(
            {
                "job": {
                    "host_only": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "hosts": ["other"],
                    },
                    "plat_only": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "platforms": ["linux"],
                    },
                    "both": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "hosts": ["other"],
                        "platforms": ["linux"],
                    },
                }
            },
            default_target_jobs=["host_only", "plat_only", "both"],
        )
        crony.do_status(
            jobs=[],
            cols="default,masked-by",
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "MASKED BY" in out
        lines = out.splitlines()
        by_name = {
            line.split()[0]: line for line in lines if line and " " in line
        }
        host_row = by_name[h.full("host_only")]
        plat_row = by_name[h.full("plat_only")]
        both_row = by_name[h.full("both")]
        assert host_row.split()[-1] == "host"
        assert plat_row.split()[-1] == "platform"
        assert both_row.split()[-1] == "host,platform"

    def test_masked_by_column_hidden_by_default(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        monkeypatch.setattr(crony, "current_host", lambda: "h")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "platforms": ["linux"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "MASKED BY" not in out
        # The masked row still surfaces -- only the reason column
        # is hidden in the default column set.
        assert "masked" in out

    def test_filter_masked_entry_with_remnants_reports_orphan(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Install a job, then tighten the config so the same entry
        # is masked here (hosts=["other"]). The on-disk unit /
        # state-dir become orphaned: `crony destroy --orphans`
        # is the cleanup, and status must surface it as `orphan`
        # in the default view so the cleanup is discoverable. The
        # masked-by column still carries the reason (`host`).
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "hosts": ["other"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="default,masked-by",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        row = next(
            line for line in out.splitlines() if line.startswith(h.full("j"))
        )
        assert "orphan" in row
        assert "masked" not in row
        assert row.split()[-1] == "host"

    def test_filter_masked_entry_without_remnants_stays_masked(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Counterpart to the orphan case: never applied here, so
        # no remnants. The row stays `masked` and is hidden from
        # the default view; --all surfaces it.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "hosts": ["other"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        crony.do_status(
            jobs=[],
            cols="default,masked-by",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert h.full("j") not in out
        crony.do_status(
            jobs=[],
            cols="default,masked-by",
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        row = next(
            line for line in out.splitlines() if line.startswith(h.full("j"))
        )
        assert "masked" in row
        assert "orphan" not in row

    def test_empty_cascade_group_with_remnant_reports_orphan(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A group whose only child is host-only on `other`: under
        # the empty-mask cascade the group itself is masked with
        # reason `empty`. If the group was previously applied
        # here (when the child wasn't yet restricted), the unit
        # remnant should now report as `orphan` -- same axis as
        # direct host-mask + remnant, just via the cascade.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {"jobs": ["a"], "schedule": "*-*-* 03:00"},
                },
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        h.apply("g")
        h.config(
            {
                "job": {"a": {"command": "true", "hosts": ["other"]}},
                "job-group": {
                    "g": {"jobs": ["a"], "schedule": "*-*-* 03:00"},
                },
            },
            default_target_jobs=["g"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="default,masked-by",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        row = next(
            line for line in out.splitlines() if line.startswith(h.full("g"))
        )
        assert "orphan" in row
        assert row.split()[-1] == "empty"

    def test_unused_entry_with_remnants_reports_orphan(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `unused` is the other axis that puts a name into masked-by
        # without an own filter: it's defined in the bundle but no
        # target lists it. If a prior config listed it and apply
        # installed it, removing the target reference leaves an
        # orphan -- same as the host-mask case.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=[],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="default,masked-by",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        row = next(
            line for line in out.splitlines() if line.startswith(h.full("j"))
        )
        assert "orphan" in row
        assert row.split()[-1] == "unused"

    def test_cols_all_alias_expands_to_every_column(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="all",
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        header = capsys.readouterr().out.splitlines()[0]
        assert "JOB" in header
        assert "KIND" in header
        assert "CONFIG" in header
        assert "SCHEDULE" in header
        assert "UNIT" in header
        assert "LAST" in header
        assert "LAST RAN" in header
        assert "MASKED BY" in header
        assert "UUID" in header

    def test_cols_default_alias_matches_no_cols(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        baseline = capsys.readouterr().out
        crony.do_status(
            jobs=[],
            cols="default",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        aliased = capsys.readouterr().out
        assert baseline == aliased

    def test_cols_default_combined_with_extra_column(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `default,masked-by` keeps the default columns and appends
        # the extra one (deduped, with `job` first).
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="default,masked-by",
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        header = capsys.readouterr().out.splitlines()[0]
        # JOB first, MASKED BY last; default columns preserved in
        # between. The default set covers config, schedule, runtime,
        # and last-run signals.
        labels = [
            "JOB",
            "CONFIG",
            "SCHEDULE",
            "LAST",
            "LAST RAN",
            "MASKED BY",
        ]
        positions = [header.index(label) for label in labels]
        assert positions == sorted(positions)

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_status(
                jobs=[],
                cols=None,
                show_masked=False,
                bundle="ghost",
                config_current=False,
                config_pending=False,
                exclude_healthy=False,
            )

    def test_bundle_scopes_table(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Two bundles, both selected. `status -b borgadm` prints
        # only borgadm.k -- default.j is out of scope.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
                "\n"
                '[target.darwin]\njobs = ["k"]\n',
            ),
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        h.apply("k", bundle="borgadm")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle="borgadm",
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "borgadm.k" in out
        assert "default.j" not in out

    def test_rows_ordered_by_target_tree_execution_order(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Tree DFS order, not alphabetical: target.jobs and
        # group.jobs list order is preserved end-to-end, and
        # children are indented two spaces per depth level.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "zzz-first": {"command": "true"},
                    "aaa-second": {"command": "true"},
                    "mmm-third": {"command": "true"},
                },
                "job-group": {
                    "inner": {
                        "jobs": ["zzz-first", "aaa-second", "mmm-third"],
                    },
                    "root": {"jobs": ["inner"], "schedule": "*-*-* 02:30"},
                },
            },
            default_target_jobs=["root"],
        )
        for short in ("zzz-first", "aaa-second", "mmm-third", "inner", "root"):
            h.apply(short)
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job-or-uuid",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out_lines = capsys.readouterr().out.splitlines()
        # Drop the header line; remaining lines are the rows in
        # rendering order.
        rendered = [line.rstrip() for line in out_lines[1:] if line.strip()]
        assert rendered == [
            "default.root",
            "  default.inner",
            "    default.zzz-first",
            "    default.aaa-second",
            "    default.mmm-third",
        ]

    def test_off_tree_rows_render_below_unindented_and_sorted(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # An on-disk remnant (no longer in any active target) renders
        # below the tree without indentation, alphabetically ordered
        # alongside other off-tree rows.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "tree-job": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                    "orphan-job": {
                        "command": "true",
                        "schedule": "*-*-* 04:00",
                    },
                },
            },
            default_target_jobs=["tree-job", "orphan-job"],
        )
        h.apply("tree-job")
        h.apply("orphan-job")
        # Rewrite config so orphan-job is no longer in any target;
        # its on-disk state remains, surfacing as a remnant.
        h.config(
            {
                "job": {
                    "tree-job": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                    "orphan-job": {
                        "command": "true",
                        "schedule": "*-*-* 04:00",
                    },
                },
            },
            default_target_jobs=["tree-job"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job-or-uuid",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out_lines = capsys.readouterr().out.splitlines()
        rendered = [line.rstrip() for line in out_lines[1:] if line.strip()]
        # tree-job comes first (root of the active target's tree);
        # orphan-job follows below, unindented.
        assert rendered == ["default.tree-job", "default.orphan-job"]

    def test_masked_in_tree_row_renders_at_config_depth(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A child filtered out by `hosts` is masked on this host
        # but still in the active target's config tree. With --all,
        # its row appears at its configured depth (indented), not
        # banished to the off-tree section below.
        h = _ApplyHarness(tmp_path, monkeypatch)
        monkeypatch.setattr(crony, "current_host", lambda: "this-host")
        h.config(
            {
                "job": {
                    "leaf-here": {"command": "true"},
                    "leaf-elsewhere": {
                        "command": "true",
                        "hosts": ["other-host"],
                    },
                },
                "job-group": {
                    "root": {
                        "jobs": ["leaf-here", "leaf-elsewhere"],
                        "schedule": "*-*-* 02:30",
                    },
                },
            },
            default_target_jobs=["root"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job-or-uuid",
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out_lines = capsys.readouterr().out.splitlines()
        rendered = [line.rstrip() for line in out_lines[1:] if line.strip()]
        assert rendered == [
            "default.root",
            "  default.leaf-here",
            "  default.leaf-elsewhere",
        ]

    def test_bundle_filter_scopes_tree_rendering(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `--bundle <name>` restricts the table to that bundle's
        # tree -- rows from other bundles' active trees are
        # excluded and don't perturb the indentation widths of the
        # filtered view.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "leaf": {"command": "true"},
                },
                "job-group": {
                    "root": {"jobs": ["leaf"], "schedule": "*-*-* 02:30"},
                },
            },
            default_target_jobs=["root"],
        )
        h.apply("leaf")
        h.apply("root")
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
                "\n"
                '[target.darwin]\njobs = ["k"]\n',
            ),
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        h.apply("k", bundle="borgadm")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job-or-uuid",
            show_masked=False,
            bundle="default",
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out_lines = capsys.readouterr().out.splitlines()
        rendered = [line.rstrip() for line in out_lines[1:] if line.strip()]
        assert rendered == [
            "default.root",
            "  default.leaf",
        ]

    def test_kind_column_shows_job_or_group(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}},
                "job-group": {"g": {"jobs": ["j"], "schedule": "*-*-* 04:00"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("j")
        h.apply("g")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,kind",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # default.j is a job; default.g is a group
        for line in out.splitlines():
            if "default.j " in line:
                assert "job" in line
            if "default.g " in line:
                assert "group" in line

    def test_kind_column_uses_snapshot_for_orphan(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Apply, then drop the entry from config so the row turns
        # orphan. KIND falls back to the snapshot's recorded kind.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config({}, default_target_jobs=[])
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,kind,config",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        for line in out.splitlines():
            if "default.j " in line:
                assert "job" in line
                assert "orphan" in line

    def test_unit_name_column_darwin_uses_launchd_label(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,unit-name",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "UNIT NAME" in out
        for line in out.splitlines():
            if "default.j " in line:
                assert f"org.crony.{h.full('j')}" in line

    def test_unit_name_column_linux_picks_timer_or_service(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Scheduled job -> .timer; grouped job -> .service.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {
                "job": {
                    "sched": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                    "gm": {"command": "true"},
                },
                "job-group": {"g": {"jobs": ["gm"], "schedule": "*-*-* 04:00"}},
            },
            default_target_jobs=["sched", "g"],
        )
        h.apply("sched")
        h.apply("gm")
        h.apply("g")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,unit-name",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        for line in out.splitlines():
            if "default.sched " in line:
                assert f"crony-{h.full('sched')}.timer" in line
            if "default.gm " in line:
                assert f"crony-{h.full('gm')}.service" in line

    def test_groups_column_shows_membership(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Job `a` belongs to group `g`. The groups column lists `g`.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        h.apply("g")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,groups",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        for line in out.splitlines():
            if "default.a " in line:
                assert "default.g" in line

    def test_groups_column_shows_active_membership_not_dead_group(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Job `a` is listed by two groups in config, but the
        # single-parent invariant means only one can dispatch it on
        # this host: `g1` is in the target, `g2` is defined but
        # unused (no target reaches it). The GROUPS column reflects
        # active dispatch membership (pending / current graphs), so
        # `a` shows g1 only -- the dead g2 doesn't surface.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g1": {"jobs": ["a"], "schedule": "*-*-* 03:00"},
                    "g2": {"jobs": ["a"], "schedule": "*-*-* 04:00"},
                },
            },
            default_target_jobs=["g1"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,groups",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        for line in out.splitlines():
            if "default.a " in line:
                assert "default.g1" in line
                assert "default.g2" not in line

    def test_groups_default_marks_stale_when_membership_changes(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Apply with a in g1; rewrite config so a moves to g2 (without
        # re-applying), keeping a selected in both graphs so the
        # divergence is a genuine pending-vs-current disagreement.
        # Default mode is pending-first: a's cell shows the pending
        # membership (g2) starred to flag the applied side (g1)
        # differs, plus the shared stale footer.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "a": {"command": "true"},
                    "b": {"command": "true"},
                },
                "job-group": {
                    "g1": {"jobs": ["a"], "schedule": "*-*-* 03:00"},
                    "g2": {"jobs": ["b"], "schedule": "*-*-* 04:00"},
                },
            },
            default_target_jobs=["g1", "g2"],
        )
        h.apply("a")
        h.apply("b")
        h.apply("g1")
        h.apply("g2")
        # Swap a and b between the two groups in pending config.
        h.config(
            {
                "job": {
                    "a": {"command": "true"},
                    "b": {"command": "true"},
                },
                "job-group": {
                    "g1": {"jobs": ["b"], "schedule": "*-*-* 03:00"},
                    "g2": {"jobs": ["a"], "schedule": "*-*-* 04:00"},
                },
            },
            default_target_jobs=["g1", "g2"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,groups",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # `a`'s row shows its pending membership (g2), starred to flag
        # that the applied side still records g1.
        for line in out.splitlines():
            if "default.a " in line:
                assert "default.g2^" in line
                assert "default.g1" not in line
        assert "stale" in out
        assert "crony apply" in out

    def test_groups_config_pending_overrides_applied(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "a": {"command": "true"},
                    "b": {"command": "true"},
                },
                "job-group": {
                    "g1": {"jobs": ["a"], "schedule": "*-*-* 03:00"},
                    "g2": {"jobs": ["b"], "schedule": "*-*-* 04:00"},
                },
            },
            default_target_jobs=["g1", "g2"],
        )
        h.apply("a")
        h.apply("b")
        h.apply("g1")
        h.apply("g2")
        # Pending: swap a and b between the groups (a stays selected in
        # both graphs, so the divergence is genuine).
        h.config(
            {
                "job": {
                    "a": {"command": "true"},
                    "b": {"command": "true"},
                },
                "job-group": {
                    "g1": {"jobs": ["b"], "schedule": "*-*-* 03:00"},
                    "g2": {"jobs": ["a"], "schedule": "*-*-* 04:00"},
                },
            },
            default_target_jobs=["g1", "g2"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,groups",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=True,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # --config-pending shows the pending membership (g2); the `^`
        # divergence indicator fires because the applied current still
        # records a under g1.
        for line in out.splitlines():
            if "default.a " in line:
                assert "default.g2" in line
                assert "default.g1" not in line
                assert "^" in line

    def test_opt_in_columns_not_in_default_set(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        header = capsys.readouterr().out.splitlines()[0]
        assert "UNIT NAME" not in header
        # KIND and UNIT moved to opt-in -- the schedule column
        # surfaces "disabled" inline when the unit is off, so
        # the standalone runtime axis isn't load-bearing for
        # day-to-day reading.
        assert "KIND" not in header
        # `UNIT` is a substring of `UNIT NAME`; check the bare header
        # label with surrounding whitespace.
        assert " UNIT " not in header
        assert not header.rstrip().endswith("UNIT")

    def test_status_help_epilog_lists_columns(self) -> None:
        parser = crony.build_parser()
        # Locate the status subparser and pull its epilog text.
        subparsers_action = next(
            a
            for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        status_parser = subparsers_action.choices["status"]
        text = status_parser.format_help()
        # Two sections: every column documented in Columns; aliases
        # documented as their expansions in Aliases.
        assert "Columns\n-------" in text
        assert "Aliases\n-------" in text
        for col in [
            "job",
            "kind",
            "config",
            "schedule",
            "unit",
            "last",
            "last-ran",
            "masked-by",
            "unit-name",
            "uuid",
        ]:
            assert col in text
        # `default` alias enumerates its expansion verbatim so the
        # block doubles as the documentation of the default set.
        for col in crony._DEFAULT_STATUS_COLS:
            assert col in text
        # `all` is rendered as a label rather than the full list.
        assert "  all       all" in text

    def test_schedule_column_renders_cron_interval_and_grouped(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "cron-job": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                    "iv-job": {"command": "true", "interval": "1h"},
                    "child": {"command": "true"},
                },
                "job-group": {
                    "g": {"jobs": ["child"], "schedule": "*-*-* 04:00"}
                },
            },
            default_target_jobs=["cron-job", "iv-job", "g"],
        )
        h.apply("cron-job")
        h.apply("iv-job")
        h.apply("child")
        h.apply("g")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,schedule",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        for line in out.splitlines():
            if "default.cron-job" in line:
                assert "*-*-* 03:00" in line
            if "default.iv-job" in line:
                assert "interval=1h" in line
            if "default.child" in line:
                assert "grouped" in line

    def test_schedule_renders_disabled_when_unit_disabled(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Default mode: a disabled platform unit replaces the cron
        # cell with `disabled`. --config-pending suppresses the
        # override (the pending value is a config fact).
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "disabled")
        crony.do_status(
            jobs=[],
            cols="job,schedule",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        for line in out.splitlines():
            if "default.j " in line:
                assert "disabled" in line
                assert "*-*-* 03:00" not in line
        # --config-pending still shows the cron expression.
        capsys.readouterr()
        crony.do_status(
            jobs=[],
            cols="job,schedule",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=True,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        for line in out.splitlines():
            if "default.j " in line:
                assert "*-*-* 03:00" in line
                assert "disabled" not in line

    def test_schedule_disabled_override_drops_stale_marker(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Apply with one schedule, mutate the config (so schedule
        # would normally be stale), and disable the unit. The
        # cell renders `disabled` with no marker and no footer
        # since the cell is no longer the schedule that would
        # have been compared against pending.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 09:00"}}},
            default_target_jobs=["j"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "disabled")
        crony.do_status(
            jobs=[],
            cols="job,schedule",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "disabled" in out
        assert "^" not in out
        assert "stale" not in out

    def test_schedule_default_marks_stale_with_marker_and_footer(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Apply with one schedule, then mutate config to a new
        # schedule. Default mode is pending-first, so the cell shows
        # the pending (config) value with `^` flagging the applied
        # value differs; footer prints.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # Rewrite config with a new schedule (no re-apply).
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 09:00"}}},
            default_target_jobs=["j"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,schedule",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "*-*-* 09:00^" in out
        assert "stale" in out
        assert "crony apply" in out

    def test_config_current_shows_applied_with_marker_on_diverge(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `*` is a pure divergence indicator -- it shows whenever
        # pending and current disagree on the field, regardless of
        # which side `--config-current` / `--config-pending` is
        # displaying. The user reads it as "the other view says
        # something different here", not as "the displayed value
        # is stale relative to pending".
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 09:00"}}},
            default_target_jobs=["j"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,schedule",
            show_masked=False,
            bundle=None,
            config_current=True,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "*-*-* 03:00^" in out
        assert "*-*-* 09:00" not in out

    def test_config_pending_shows_config_with_marker_on_diverge(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 09:00"}}},
            default_target_jobs=["j"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,schedule",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=True,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "*-*-* 09:00^" in out
        assert "*-*-* 03:00" not in out

    def test_config_current_and_pending_mutually_exclusive(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="mutually exclusive"):
            crony.do_status(
                jobs=[],
                cols=None,
                show_masked=False,
                bundle=None,
                config_current=True,
                config_pending=True,
                exclude_healthy=False,
            )

    def test_unused_mask_reason_surfaces_under_all(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `extra` is defined in config but not in target.jobs.
        # Default `crony status` hides it; `-a` surfaces it as
        # config=masked, masked-by=unused.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {"command": "true", "schedule": "*-*-* 03:00"},
                    "extra": {
                        "command": "true",
                        "schedule": "*-*-* 04:00",
                    },
                }
            },
            default_target_jobs=["j"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,config,masked-by",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "default.extra" not in out
        capsys.readouterr()
        crony.do_status(
            jobs=[],
            cols="job,config,masked-by",
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        for line in out.splitlines():
            if "default.extra" in line:
                assert "masked" in line
                assert "unused" in line

    def test_unit_state_axis_uses_none_for_uninstantiated(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Stub _unit_state to "none" -- simulating a unit the
        # platform scheduler doesn't see. The cell renders `none`.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "none")
        crony.do_status(
            jobs=[],
            cols="job,unit",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "UNIT" in out
        for line in out.splitlines():
            if "default.j" in line:
                assert "none" in line


# =============================================================================
# config validate / logs
# =============================================================================


class TestValidate:
    def test_clean_config(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_validate(bundle=None)
        out = capsys.readouterr().out
        assert "ok" in out

    def test_does_not_report_orphans(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # validate is config-only: an installed orphan (on-disk
        # state no config selects) must NOT surface here. `crony
        # status` / `crony destroy --orphans` own that picture.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.fabricate_orphan("ghost")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_validate(bundle=None)
        out = capsys.readouterr().out
        assert "orphans on this host" not in out
        assert "ghost" not in out

    def test_warns_when_referenced_channel_secret_unresolvable(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # The channel is fully defined, but its SMTP password has no
        # source crony can reach. validate should surface that as a
        # warning so the user knows the channel won't actually fire.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "defaults": {
                    "notify_channels": ["email"],
                    "notify": {"email": _email_block()},
                },
                "job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["j"],
        )
        with pytest.raises(SystemExit) as exc:
            crony.do_validate(bundle=None)
        assert exc.value.code == int(crony.ExitCode.WARNING)
        out = capsys.readouterr().out
        assert "channel 'email'" in out
        assert "SMTP password" in out

    def test_warns_when_channel_defined_but_never_referenced(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A channel defined in [defaults.notify.<name>] that no
        # notify_channels list ever names is dead weight. Warn so
        # the user knows it's a no-op.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "defaults": {
                    "notify_channels": [],
                    "notify": {"ntfy": _ntfy_block()},
                },
                "job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["j"],
        )
        with pytest.raises(SystemExit) as exc:
            crony.do_validate(bundle=None)
        assert exc.value.code == int(crony.ExitCode.WARNING)
        out = capsys.readouterr().out
        assert "channel 'ntfy'" in out
        assert "never referenced" in out

    def test_bundle_filter_skips_orphan_check(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Orphans live in no bundle, so a bundle-scoped validate
        # ignores them. The orphan stamp here would normally
        # trigger a WARNING; --bundle borgadm should exit clean.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.fabricate_orphan("ghost")
        h.config({}, default_target_jobs=[])
        (h.cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.foo]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        crony.do_validate(bundle="borgadm")
        out = capsys.readouterr().out
        assert "ok" in out
        assert "orphans" not in out

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_validate(bundle="ghost")

    def test_warns_on_errored_job_group(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A demoted entry (here a job-group with an undefined-name
        # ref) must flip validate's exit code -- a CI gate that
        # runs `crony config validate` shouldn't pass on a config that
        # has a broken entry.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.cfg_file.write_text(
            _uuid_toml(
                '[job.good]\ncommand = "true"\nschedule = "daily"\n'
                '[job-group.bad]\njobs = ["nope"]\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            crony.do_validate(bundle=None)
        assert exc.value.code == int(crony.ExitCode.WARNING)
        out = capsys.readouterr().out
        assert "undefined name" in out
        assert "errored=1" in out

    def test_warns_on_errored_target(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.cfg_file.write_text(
            _uuid_toml(
                '[job.a]\ncommand = "true"\nschedule = "daily"\n'
                '[target.darwin]\njobs = ["nope"]\n',
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            crony.do_validate(bundle=None)
        assert exc.value.code == int(crony.ExitCode.WARNING)
        out = capsys.readouterr().out
        assert "[target.darwin]" in out
        assert "undefined name" in out


class TestResolveStateAxes:
    """Direct unit tests for `_resolve_state_axes`. `do_status` is
    the only consumer; the helper is unit-tested separately so
    a future refactor can rely on its branch semantics being
    pinned without re-deriving them from the renderer.
    """

    def test_orphan_when_stamp_present_without_bundle(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        # Stamped on disk but not in any bundle -> orphan; no
        # entry to consult so sched falls through to _unit_state
        # (stubbed to "enabled" to surface the branch).
        ghost = h.full("ghost")
        h.fabricate_orphan("ghost")
        h.config({}, default_target_jobs=[])
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        config = crony.load_config()
        cfg, sched, last = crony._resolve_state_axes(
            config, ghost, "darwin", config.installed_full_names()
        )
        assert cfg == "orphan"
        assert sched == "enabled"
        assert last == "never"

    def test_missing_short_circuits_unit_state_to_none(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # No stamp, no bundle entry -> missing; unit short-
        # circuits to "none" without consulting _unit_state.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        called: list[str] = []

        def _stub_sched(n: str, p: str) -> str:
            called.append(n)
            return "enabled"

        monkeypatch.setattr(crony, "_unit_state", _stub_sched)
        config = crony.load_config()
        cfg, unit_state, last = crony._resolve_state_axes(
            config, h.full("ghost"), "darwin", set()
        )
        assert cfg == "missing"
        assert unit_state == "none"
        assert last == "never"
        assert not called  # short-circuit honored

    def test_grouped_when_entry_has_no_schedule_or_interval(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Group-only job (no schedule / interval, fires only via
        # parent group) -> sched = "grouped" without consulting
        # _unit_state.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        h.apply("g")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        config = crony.load_config()
        _, sched, _ = crony._resolve_state_axes(
            config, h.full("a"), "darwin", config.installed_full_names()
        )
        assert sched == "grouped"

    def test_leaf_with_schedule_consults_unit_state(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Scheduled leaf -> sched read from _unit_state.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "disabled")
        config = crony.load_config()
        cfg_state, sched, _ = crony._resolve_state_axes(
            config, h.full("j"), "darwin", config.installed_full_names()
        )
        assert cfg_state == "synced"
        assert sched == "disabled"


class TestPerEntityConfigErrors:
    """A parse-time ConfigError on one entity records itself on the
    TomlBundleConfig's errored_* maps instead of aborting the whole bundle.
    Siblings still parse, status renders the errored entity with
    `config=error`, and lifecycle commands leave its installed unit
    alone.
    """

    def test_sibling_jobs_survive_bad_job(self) -> None:
        cfg = _parse(
            {
                "job": {
                    "good": _job(),
                    "bad": _job(surprise="boom"),
                },
            }
        )
        assert "good" in cfg.jobs
        assert "bad" not in cfg.jobs
        assert "bad" in cfg.errored_jobs
        assert "unknown key" in cfg.errored_jobs["bad"]

    def test_sibling_groups_survive_bad_group(self) -> None:
        cfg = _parse(
            {
                "job": {"a": _job(), "b": _job()},
                "job-group": {
                    "ok": {"jobs": ["a"], "schedule": "daily"},
                    "bad": {
                        "jobs": ["b"],
                        "schedule": "daily",
                        "surprise": True,
                    },
                },
            }
        )
        assert "ok" in cfg.job_groups
        assert "bad" not in cfg.job_groups
        assert "bad" in cfg.errored_job_groups

    def test_group_references_errored_leaf_does_not_raise(self) -> None:
        # The group is well-formed; only its referenced leaf has a
        # parse error. The parent group is treated as valid; chain
        # validation stops at the errored leaf without raising
        # "would never fire", since the errored leaf might have had
        # a schedule if it had parsed.
        cfg = _parse(
            {
                "job": {"bad": _job(surprise="boom")},
                "job-group": {
                    "g": {"jobs": ["bad"], "schedule": "daily"},
                },
                "target": {"darwin": {"jobs": ["g"]}},
            }
        )
        assert "g" in cfg.job_groups
        assert "bad" in cfg.errored_jobs

    def test_target_references_errored_root_does_not_raise(self) -> None:
        cfg = _parse(
            {
                "job": {"bad": _job(surprise="boom")},
                "target": {"darwin": {"jobs": ["bad"]}},
            }
        )
        assert "bad" in cfg.errored_jobs
        assert "darwin" in cfg.platform_targets

    def test_collision_with_errored_still_raises(self) -> None:
        # Errored entries participate in the collision check so a
        # typo'd `[job.x]` plus a valid `[job-group.x]` still
        # surfaces the structural problem.
        with pytest.raises(crony.ConfigError, match="name collision"):
            _parse(
                {
                    "job": {"x": _job(surprise="boom")},
                    "job-group": {"x": {"jobs": ["x"], "schedule": "daily"}},
                }
            )

    def test_notify_channels_promotes_job_to_errored(self) -> None:
        cfg = _parse(
            {
                "job": {
                    "ok": _job(),
                    "bad": _job(notify_channels=["nope"]),
                },
            }
        )
        assert "ok" in cfg.jobs
        assert "bad" not in cfg.jobs
        assert "bad" in cfg.errored_jobs
        assert "notify_channels" in cfg.errored_jobs["bad"]

    def test_load_one_bundle_logs_per_entity_errors(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "good": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                    "bad": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "surprise": True,
                    },
                },
            },
            default_target_jobs=["good"],
        )
        import logging

        with caplog.at_level(logging.ERROR, logger=crony.logger.name):
            bundles = crony.load_all_bundles()
        # `path: [job.bad]: unknown key(s) ['surprise']`
        assert any(
            "[job.bad]" in r.message and "unknown key" in r.message
            for r in caplog.records
        )
        # The good sibling parsed successfully.
        bundle = bundles.by_name("default")
        assert bundle is not None
        assert "good" in bundle.config.jobs
        assert "bad" in bundle.config.errored_jobs

    def test_status_renders_error_for_errored_entry(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "good": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                    "bad": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "surprise": True,
                    },
                },
            },
            default_target_jobs=["good"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # Both names appear; bad gets "error", good gets a normal
        # status word (missing here -- never applied).
        assert h.full("good") in out
        assert h.full("bad") in out
        # The full("bad") row carries "error" somewhere on it.
        bad_row = next(
            line for line in out.splitlines() if h.full("bad") in line
        )
        assert "error" in bad_row

    def test_apply_explicit_errored_name_raises_config_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "bad": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "surprise": True,
                    },
                },
            },
            default_target_jobs=[],
        )
        with pytest.raises(crony.UsageError, match="config error"):
            crony.do_apply(jobs=[h.full("bad")], verbose=False, bundle=None)

    def test_apply_no_args_skips_errored_silently(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # With siblings: the good job applies, the errored sibling
        # is never selected (no parsed job to install).
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "good": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    },
                    "bad": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "surprise": True,
                    },
                },
            },
            default_target_jobs=["good"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        # The good plist landed; the bad one didn't.
        assert (h.agents / f"org.crony.{h.full('good')}.plist").exists()
        assert not (h.agents / f"org.crony.{h.full('bad')}.plist").exists()

    def test_destroy_accepts_errored_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The user previously applied an entry, then later edited
        # the config and introduced a typo. The errored state
        # shouldn't block them from cleaning up the prior install.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        # Now break the config -- same name, bad body.
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "surprise": True,
                    },
                },
            },
            default_target_jobs=["j"],
        )
        crony.do_destroy(
            jobs=[h.full("j")],
            bundle=None,
            orphans=False,
        )
        assert not (h.agents / f"org.crony.{h.full('j')}.plist").exists()

    def test_errored_entry_appears_in_status_exclude_healthy(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "bad": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "surprise": True,
                    },
                },
            },
            default_target_jobs=[],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=True,
        )
        out = capsys.readouterr().out
        assert h.full("bad") in out
        assert "error" in out

    def test_resolve_state_axes_returns_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "bad": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "surprise": True,
                    },
                },
            },
            default_target_jobs=[],
        )
        config = crony.load_config()
        cfg_state, _unit_state, _last_state = crony._resolve_state_axes(
            config, h.full("bad"), "darwin", config.installed_full_names()
        )
        assert cfg_state == "error"


class TestLogs:
    def test_n_lines(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state_dir("j") / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "\n".join(f"line {i}" for i in range(20)) + "\n",
            encoding="utf-8",
        )
        crony.do_logs(
            name="j", n=5, since=None, tail=False, path=False, latest=False
        )
        out = capsys.readouterr().out
        assert "line 19" in out
        assert "line 15" in out
        assert "line 14" not in out

    def test_default_n_non_tail_is_200(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `n=None` -> 200 for one-shot reads: the wider window keeps
        # parity with `tail -n 200` and the historical default.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state_dir("j") / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "\n".join(f"line {i}" for i in range(300)) + "\n",
            encoding="utf-8",
        )
        crony.do_logs(
            name="j",
            n=None,
            since=None,
            tail=False,
            path=False,
            latest=False,
        )
        out = capsys.readouterr().out
        assert "line 299" in out
        assert "line 100" in out
        assert "line 99" not in out

    def test_default_n_tail_is_10(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `crony logs -t` with no `-n` prints only the last 10 history
        # lines before entering the follow loop, so the interactive
        # tail doesn't dump the full retained log first.
        log = tmp_path / "run.log"
        lines = [f"line-{i}\n" for i in range(50)]
        log.write_text("".join(lines))

        def _interrupt(*args: Any, **kwargs: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(crony.time, "sleep", _interrupt)
        # Mirrors the resolution `do_logs` performs when `n is None`
        # and `tail` is True.
        crony._follow_log(log, n=10)
        out = capsys.readouterr().out
        printed = out.splitlines()
        assert printed == [f"line-{i}" for i in range(40, 50)]

    def test_missing_log_raises(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="no log"):
            crony.do_logs(
                name="ghost",
                n=10,
                since=None,
                tail=False,
                path=False,
                latest=False,
            )

    def test_path_prints_file_path(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state_dir("j") / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("hello\n", encoding="utf-8")
        crony.do_logs(
            name="j", n=0, since=None, tail=False, path=True, latest=False
        )
        out = capsys.readouterr().out.strip()
        assert out == str(log)

    def test_path_works_without_existing_log(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # --path is purely structural: it prints the resolved path
        # without requiring the file to exist. Useful for tooling
        # like `mkdir -p $(dirname $(crony logs j -p))` or
        # `tail -F "$(crony logs j -p)"` in advance of any run.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        # No log file written; --path should still succeed.
        crony.do_logs(
            name="j", n=0, since=None, tail=False, path=True, latest=False
        )
        out = capsys.readouterr().out.strip()
        expected = h.state_dir("j") / "run.log"
        assert out == str(expected)

    def test_since_filters_old_runs(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state_dir("j") / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        old_iso = "2026-01-01T03:15:00-08:00"
        import datetime as _dt

        now_iso = (
            _dt.datetime.now(_dt.timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds")
        )
        log.write_text(
            f"=== {old_iso} j pid=1 ===\nold-line\n"
            f"=== {now_iso} j pid=2 ===\nnew-line\n",
            encoding="utf-8",
        )
        crony.do_logs(
            name="j", n=0, since="1h", tail=False, path=False, latest=False
        )
        out = capsys.readouterr().out
        assert "new-line" in out
        assert "old-line" not in out

    def test_parse_since_unparseable(self) -> None:
        with pytest.raises(crony.UsageError, match="unparseable"):
            crony._parse_since("eventually")

    def test_parse_since_naive_iso_rejected(self) -> None:
        # Naive ISO would crash later when compared with tz-aware
        # run-header timestamps; surface at parse time instead.
        with pytest.raises(crony.UsageError, match="timezone offset"):
            crony._parse_since("2026-04-01T12:00:00")

    def test_follow_log_returns_cleanly_on_keyboard_interrupt(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Ctrl-C during `crony logs -t` should exit without a stack
        # trace. The follow loop sleeps on time.sleep(); raising
        # KeyboardInterrupt from there mimics the live signal.
        log = tmp_path / "run.log"
        log.write_text("existing line\n")

        def _interrupt(*args: Any, **kwargs: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(crony.time, "sleep", _interrupt)
        # Should return None, not propagate the exception.
        assert crony._follow_log(log) is None

    def test_follow_log_prints_history_before_following(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        capsys: Any,
    ) -> None:
        # `tail -f` style: print the last N lines, then live-tail.
        # The KeyboardInterrupt stub causes the follow loop to bail
        # immediately, so what's captured is the history alone.
        log = tmp_path / "run.log"
        lines = [f"line-{i}\n" for i in range(20)]
        log.write_text("".join(lines))

        def _interrupt(*args: Any, **kwargs: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(crony.time, "sleep", _interrupt)
        crony._follow_log(log, n=5)
        out = capsys.readouterr().out
        # Last 5 lines (15..19) present, earlier lines suppressed.
        assert "line-19" in out
        assert "line-15" in out
        assert "line-14" not in out
        assert "line-0" not in out

    def test_follow_log_skips_history_when_n_zero(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        capsys: Any,
    ) -> None:
        log = tmp_path / "run.log"
        log.write_text("existing line\n")

        def _interrupt(*args: Any, **kwargs: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(crony.time, "sleep", _interrupt)
        crony._follow_log(log, n=0)
        out = capsys.readouterr().out
        assert out == ""

    def test_latest_prints_only_the_last_run_entry(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state_dir("j") / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "=== 2026-05-01T03:00:00-08:00 j pid=1 ===\n"
            "first-run-output\n"
            "=== 2026-05-02T03:00:00-08:00 j pid=2 ===\n"
            "second-run-output\n",
            encoding="utf-8",
        )
        crony.do_logs(
            name="j",
            n=0,
            since=None,
            tail=False,
            path=False,
            latest=True,
        )
        out = capsys.readouterr().out
        assert "second-run-output" in out
        assert "first-run-output" not in out

    def test_latest_falls_back_when_no_headers(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Pre-header content (e.g. a hand-edited or partially-
        # truncated log) returns whole-file unchanged rather than
        # an empty stream.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state_dir("j") / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("orphan content with no === header\n", encoding="utf-8")
        crony.do_logs(
            name="j",
            n=0,
            since=None,
            tail=False,
            path=False,
            latest=True,
        )
        out = capsys.readouterr().out
        assert "orphan content" in out

    def test_latest_and_tail_mutually_exclusive(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state_dir("j") / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("=== 2026-05-01T03:00:00-08:00 j pid=1 ===\n")
        with pytest.raises(crony.UsageError, match="mutually exclusive"):
            crony.do_logs(
                name="j",
                n=0,
                since=None,
                tail=True,
                path=False,
                latest=True,
            )


class TestGroupExitClassRollup:
    """Direct unit tests for `_rollup_group_exit_class`. Status
    and the LAST axis read this rolled-up value from the
    group's last-run.json instead of re-deriving it; coverage
    here keeps the precedence ladder honest as new exit_class
    values get introduced.
    """

    def _children(self, *classes: str) -> list[Any]:
        return [
            crony.GroupChildResult(
                name=f"default.c{i}", exit_class=cls, exit_code=0
            )
            for i, cls in enumerate(classes)
        ]

    def test_empty_rolls_up_to_ok(self) -> None:
        assert crony._rollup_group_exit_class([]) == "ok"

    def test_all_ok_rolls_up_to_ok(self) -> None:
        assert (
            crony._rollup_group_exit_class(self._children("ok", "ok")) == "ok"
        )

    def test_gated_treated_as_success(self) -> None:
        # Gating is per-child intent ("don't run today"), not a
        # group-level outcome.
        assert (
            crony._rollup_group_exit_class(self._children("ok", "gated"))
            == "ok"
        )
        assert (
            crony._rollup_group_exit_class(self._children("gated", "gated"))
            == "ok"
        )

    def test_any_fail_rolls_up_to_fail(self) -> None:
        assert (
            crony._rollup_group_exit_class(self._children("ok", "fail"))
            == "fail"
        )

    def test_signal_at_fail_grade(self) -> None:
        # A signaled child surfaces its own exit_class so a
        # downstream reader can distinguish abort signals from
        # plain non-zero exits if it cares.
        assert (
            crony._rollup_group_exit_class(self._children("ok", "signal"))
            == "signal"
        )

    def test_timeout_outranks_fail(self) -> None:
        # Group with both a fail and a timeout: timeout wins so
        # the LAST axis surfaces the more severe condition.
        assert (
            crony._rollup_group_exit_class(
                self._children("fail", "timeout", "ok")
            )
            == "timeout"
        )

    def test_gated_does_not_mask_fail(self) -> None:
        # gated ties with ok at the bottom; a fail child must
        # still surface, not be masked by sibling gating.
        assert (
            crony._rollup_group_exit_class(self._children("gated", "fail"))
            == "fail"
        )
        assert (
            crony._rollup_group_exit_class(self._children("fail", "gated"))
            == "fail"
        )

    def test_signal_and_fail_are_equally_severe(self) -> None:
        # signal and fail share severity 1; the first child of
        # that tier wins, so the readout reflects the
        # encountered-order outcome rather than swapping based on
        # iteration. This pins the tie-break for either case.
        assert (
            crony._rollup_group_exit_class(self._children("signal", "fail"))
            == "signal"
        )
        assert (
            crony._rollup_group_exit_class(self._children("fail", "signal"))
            == "fail"
        )


class TestLogHelpers:
    """Direct unit tests for `_extract_latest_log_entry` and
    `_head_truncate_to_kb`. Exercised end-to-end via TestLogs and
    TestNtfyNotify; this class isolates the boundary conditions
    so a regression in either helper surfaces here first.
    """

    def test_extract_returns_from_last_header(self) -> None:
        text = (
            "=== 2026-05-01T03:00:00-08:00 j pid=1 ===\n"
            "older\n"
            "=== 2026-05-02T03:00:00-08:00 j pid=2 ===\n"
            "newest\n"
        )
        out = crony._extract_latest_log_entry(text)
        assert out.startswith("=== 2026-05-02T03:00:00-08:00")
        assert "newest" in out
        assert "older" not in out

    def test_extract_returns_full_text_when_no_header(self) -> None:
        text = "no header here, just content\n"
        assert crony._extract_latest_log_entry(text) == text

    def test_extract_returns_empty_for_empty_input(self) -> None:
        assert crony._extract_latest_log_entry("") == ""

    def test_head_truncate_under_cap_passes_through(self) -> None:
        text = "small body\n"
        out, truncated = crony._head_truncate_to_kb(text, 1)
        assert out == text
        assert truncated is False

    def test_head_truncate_over_cap_keeps_tail_with_marker(self) -> None:
        # 1 KB cap; build a text that's ~3KB so head-truncation drops
        # the start. The output must be <= 1024 bytes and start with
        # the truncation marker.
        text = "X" * 3000 + "TAIL"
        out, truncated = crony._head_truncate_to_kb(text, 1)
        assert truncated is True
        assert len(out.encode("utf-8")) <= 1024
        assert out.startswith("[... ")
        assert "bytes truncated" in out
        assert out.endswith("TAIL")


# =============================================================================
# End-to-end lifecycle
# =============================================================================


class TestParseFullName:
    """`parse_full_name` turns CLI input into (bundle, short)."""

    def test_bare_name_is_default_bundle(self) -> None:
        assert crony.parse_full_name("foo") == (
            crony.DEFAULT_BUNDLE_NAME,
            "foo",
        )

    def test_namespaced_form(self) -> None:
        assert crony.parse_full_name("borgadm.foo") == ("borgadm", "foo")

    def test_multi_dot_short_name(self) -> None:
        # Splits on the FIRST dot; remaining dots stay in the short.
        assert crony.parse_full_name("default.foo.bar") == (
            "default",
            "foo.bar",
        )

    def test_empty_bundle_rejected(self) -> None:
        with pytest.raises(crony.UsageError):
            crony.parse_full_name(".foo")

    def test_empty_short_rejected(self) -> None:
        with pytest.raises(crony.UsageError):
            crony.parse_full_name("default.")


class TestEntityRefInput:
    """The `<bundle>:<UUID>` input form lets an operator address
    an entity that has no recoverable config-side name (corrupt
    snapshot.json, broken entity, unit-only orphan) by pasting the
    JOB cell from a status row back into a subcommand. The same
    form is what platform units pass to `crony run`. The parser
    validates both pieces so the resulting `EntityRef` is safe
    to compose into a state-dir path.
    """

    _CANONICAL_UUID = "11111111-2222-3333-4444-555555555555"

    def test_parse_round_trips(self) -> None:
        ref = crony.EntityRef("default", self._CANONICAL_UUID)
        rendered = str(ref)
        assert rendered == f"default:{self._CANONICAL_UUID}"
        assert crony.EntityRef.from_str(rendered) == ref

    def test_parse_with_non_default_bundle(self) -> None:
        ref = crony.EntityRef("borgadm", self._CANONICAL_UUID)
        rendered = str(ref)
        assert crony.EntityRef.from_str(rendered) == ref

    def test_parse_non_ref_returns_none(self) -> None:
        # Dot-separated names aren't entity refs.
        assert crony.EntityRef.from_str("default.foo") is None
        # Bare names aren't entity refs either.
        assert crony.EntityRef.from_str("foo") is None
        # Bundle-only (no uuid body).
        assert crony.EntityRef.from_str("default:") is None
        # No bundle.
        assert crony.EntityRef.from_str(f":{self._CANONICAL_UUID}") is None

    def test_parse_rejects_non_canonical_uuid(self) -> None:
        # Validation runs because the parsed ref flows into a
        # path that `shutil.rmtree` later trusts -- a malformed
        # uuid must fail at parse time, not at filesystem time.
        assert crony.EntityRef.from_str("default:not-a-uuid") is None
        assert crony.EntityRef.from_str("default:abc123") is None
        # Path-traversal-shaped uuid bodies must be rejected so
        # `crony destroy` can't be tricked into `rmtree`-ing
        # `STATE_DIR/default/../../etc`.
        assert crony.EntityRef.from_str("default:../../etc") is None

    def test_parse_rejects_invalid_bundle_name(self) -> None:
        # Bundle names are constrained by `_BUNDLE_NAME_RE`; an
        # invalid bundle prevents the path composition from
        # walking outside `STATE_DIR`.
        bad = f"../etc:{self._CANONICAL_UUID}"
        assert crony.EntityRef.from_str(bad) is None


class TestConfigResolveEntityRef:
    """`Config.resolve` accepts the `<bundle>:<UUID>` input form
    and returns the parsed ref directly -- the entity doesn't
    have to be in `current.by_full_name` or `pending.by_full_name`
    for the lookup to succeed.
    """

    def test_resolve_returns_ref_for_entity_ref_form(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        cfg = crony.load_config()
        ref_input = "default:11111111-2222-3333-4444-555555555555"
        # The ref-form is recognized by all three resolve methods.
        # When the entity isn't in any side (this test has no
        # pending or current entry), the methods all return None
        # for the ref since the ref doesn't appear in their
        # backing source. The ref-form parser still gives the
        # caller a way to construct an EntityRef explicitly:
        assert crony.EntityRef.from_str(ref_input) == crony.EntityRef(
            "default", "11111111-2222-3333-4444-555555555555"
        )
        assert cfg.resolve_runnable(ref_input) is None
        assert cfg.resolve_current(ref_input) is None
        assert cfg.resolve_pending(ref_input) is None


class TestDestroyByEntityRef:
    """`do_destroy` accepts a `<bundle>:<UUID>` input and wipes
    the state dir at the addressed ref even when no config /
    snapshot / unit covers it.
    """

    def test_destroy_by_ref_wipes_state_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        # Fabricate a state dir with no parseable snapshot at all,
        # only the directory and a stray run.log file -- the
        # state-dir presence is what makes destroy accept the
        # ref input.
        ghost_uuid = "deadbeef-0000-0000-0000-deadbeef0000"
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / ghost_uuid
        sd.mkdir(parents=True)
        (sd / "run.log").write_text("stale\n", encoding="utf-8")
        h.config({}, default_target_jobs=[])
        ref_input = f"{crony.DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
        crony.do_destroy(jobs=[ref_input], bundle=None, orphans=False)
        assert not sd.exists()

    def test_destroy_by_ref_rejects_unknown_state_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        # Even a canonical-shaped uuid whose state dir doesn't
        # exist is rejected -- destroy refuses to act on a ref
        # input that addresses nothing.
        ghost_uuid = "99999999-aaaa-bbbb-cccc-dddddddddddd"
        ref_input = f"{crony.DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
        with pytest.raises(crony.UsageError, match="unknown name"):
            crony.do_destroy(jobs=[ref_input], bundle=None, orphans=False)

    def test_destroy_by_ref_rejects_path_traversal(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The ref parser rejects non-canonical uuid bodies;
        # destroy then treats the input as a normal full name and
        # rejects it as unknown. The would-be
        # `STATE_DIR/default/../../etc` target is never composed.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        attack = f"{crony.DEFAULT_BUNDLE_NAME}:../../etc"
        with pytest.raises(crony.UsageError, match="unknown name"):
            crony.do_destroy(jobs=[attack], bundle=None, orphans=False)

    def test_destroy_by_ref_recovers_name_for_unit_cleanup(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # When the addressed state dir has a parseable snapshot
        # the ref-form destroy must use the snapshot's `name`
        # field for platform unit cleanup -- otherwise the unit
        # file would leak (the ref input has no
        # `<bundle>.<short>` shape to match the installed
        # `org.crony.<name>.plist`).
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        assert plist.exists()
        sd = h.state_dir("j", cfg=cfg)
        ref_input = f"{crony.DEFAULT_BUNDLE_NAME}:{cfg.jobs['j'].uuid}"
        crony.do_destroy(jobs=[ref_input], bundle=None, orphans=False)
        # Both the state dir AND the platform unit are gone.
        assert not sd.exists()
        assert not plist.exists()

    def test_destroy_by_ref_refuses_during_run_lock_held(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The run-lock guard applies on the ref-form path too:
        # a destroy mid-run would leave the running shim with
        # deleted state under it.
        import fcntl as _fcntl

        h = _ApplyHarness(tmp_path, monkeypatch)
        ghost_uuid = "deadbeef-0000-0000-0000-deadbeef0000"
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / ghost_uuid
        sd.mkdir(parents=True)
        lock = sd / "run.lock"
        held = open(lock, "w")
        _fcntl.flock(held, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        try:
            h.config({}, default_target_jobs=[])
            ref_input = f"{crony.DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
            with pytest.raises(crony.LockBusyError, match="run in progress"):
                crony.do_destroy(jobs=[ref_input], bundle=None, orphans=False)
        finally:
            _fcntl.flock(held, _fcntl.LOCK_UN)
            held.close()
        # State dir survived because the destroy refused.
        assert sd.exists()


class TestLogsByEntityRef:
    """`do_logs` accepts a `<bundle>:<UUID>` input and reads the
    state dir's run.log directly via the parsed ref -- the entity
    doesn't have to appear in `Config.runtime` for the lookup to
    succeed.
    """

    def test_logs_by_ref_reads_run_log(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        ghost_uuid = "deadbeef-0000-0000-0000-deadbeef0000"
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / ghost_uuid
        sd.mkdir(parents=True)
        (sd / "run.log").write_text("hello\n", encoding="utf-8")
        ref_input = f"{crony.DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
        crony.do_logs(
            name=ref_input,
            n=200,
            since=None,
            tail=False,
            path=False,
            latest=False,
        )
        out = capsys.readouterr().out
        assert "hello" in out

    def test_logs_by_ref_path_mode(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        ghost_uuid = "deadbeef-1111-1111-1111-deadbeef1111"
        ref_input = f"{crony.DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
        crony.do_logs(
            name=ref_input,
            n=None,
            since=None,
            tail=False,
            path=True,
            latest=False,
        )
        out = capsys.readouterr().out.strip()
        assert out.endswith(f"{ghost_uuid}/run.log")


class TestBundleLoading:
    """`load_all_bundles` discovers config.toml + config/*.toml,
    isolates per-bundle failures, and rejects collisions."""

    def _setup(self, tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path]:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = tmp_path / "config_dropin"
        cfg_dropin.mkdir()
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        return cfg_file, cfg_dropin

    def test_default_only(self, tmp_path: Path, monkeypatch: Any) -> None:
        cfg_file, _ = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text(
            _uuid_toml(
                '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        assert [b.name for b in bundles.bundles] == ["default"]
        assert "j" in bundles.bundles[0].config.jobs

    def test_dropin_alongside_default(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file, dropin = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text(
            _uuid_toml(
                '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        (dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.prune]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        names = sorted(b.name for b in bundles.bundles)
        assert names == ["borgadm", "default"]
        assert bundles.by_name("borgadm") is not None
        assert "prune" in bundles.by_name("borgadm").config.jobs

    def test_dropin_only_no_default(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # config.toml is absent; only config/*.toml exists. Should
        # still load successfully (no requirement that default exists).
        _, dropin = self._setup(tmp_path, monkeypatch)
        (dropin / "private.toml").write_text(
            _uuid_toml(
                '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        assert [b.name for b in bundles.bundles] == ["private"]

    def test_no_configs_at_all_returns_empty(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `load_all_bundles` tolerates the no-config case so the
        # runner / destroy / status keep working off on-disk
        # state alone. `apply` is the only caller that enforces
        # "must have a config" -- it needs pending data.
        self._setup(tmp_path, monkeypatch)
        bundles = crony.load_all_bundles()
        assert bundles.bundles == []
        assert bundles.errored_bundles == {}

    def test_lex_sorted_dropin_order(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file, dropin = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text("", encoding="utf-8")
        for name in ("zulu", "alpha", "mike"):
            (dropin / f"{name}.toml").write_text(
                _uuid_toml(
                    f'[job.j_{name}]\ncommand = "true"\nschedule = "daily"\n',
                ),
                encoding="utf-8",
            )
        bundles = crony.load_all_bundles()
        # default first, then config/*.toml lex-sorted
        assert [b.name for b in bundles.bundles] == [
            "default",
            "alpha",
            "mike",
            "zulu",
        ]

    def test_broken_bundle_isolation(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # A syntactically broken bundle is dropped; siblings still load.
        cfg_file, dropin = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text(
            _uuid_toml(
                '[job.good]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        (dropin / "broken.toml").write_text(
            "this is not [valid toml",
            encoding="utf-8",
        )
        (dropin / "ok.toml").write_text(
            _uuid_toml(
                '[job.fine]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        with caplog.at_level(logging.ERROR, logger=crony.logger.name):
            bundles = crony.load_all_bundles()
        names = sorted(b.name for b in bundles.bundles)
        assert names == ["default", "ok"]
        # The broken bundle's path is in errored_bundles and in
        # the error log output.
        assert any("broken.toml" in src for src in bundles.errored_bundles)
        assert any("broken.toml" in r.message for r in caplog.records)

    def test_all_bundles_broken_returns_empty(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # The runner is config-independent so a fully-broken
        # config shouldn't take down `crony status` / `destroy` /
        # `logs`; load_all_bundles returns an empty TomlConfig
        # with the parse failures captured.
        cfg_file, dropin = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text("this is not [valid toml", encoding="utf-8")
        (dropin / "alpha.toml").write_text("also broken (", encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger=crony.logger.name):
            bundles = crony.load_all_bundles()
        assert bundles.bundles == []
        assert len(bundles.errored_bundles) == 2

    def test_bundle_loads_despite_undefined_group_ref(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # A `[job-group.X]` whose `jobs` list references a name
        # that doesn't exist must not take the whole bundle down:
        # the bundle stays loadable, the bad group sits in
        # errored_job_groups, and the error is logged with the
        # source path.
        cfg_file, _ = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text(
            _uuid_toml(
                '[job.good]\ncommand = "true"\nschedule = "daily"\n'
                '[job-group.bad]\njobs = ["nope"]\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        with caplog.at_level(logging.ERROR, logger=crony.logger.name):
            bundles = crony.load_all_bundles()
        assert [b.name for b in bundles.bundles] == ["default"]
        config = bundles.by_name("default").config
        assert "good" in config.jobs
        assert "bad" in config.errored_job_groups
        assert any(
            "undefined name" in r.message and "bad" in r.message
            for r in caplog.records
        )

    def test_bundle_loads_despite_errored_target(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # A `[target.<platform>]` with a bad ref demotes just
        # that target -- siblings (here, a host target) remain
        # live and the bundle keeps loading.
        cfg_file, _ = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text(
            _uuid_toml(
                '[job.a]\ncommand = "true"\nschedule = "daily"\n'
                '[target.darwin]\njobs = ["nope"]\n'
                '[target.host.squee]\njobs = ["a"]\n',
            ),
            encoding="utf-8",
        )
        with caplog.at_level(logging.ERROR, logger=crony.logger.name):
            bundles = crony.load_all_bundles()
        assert [b.name for b in bundles.bundles] == ["default"]
        config = bundles.by_name("default").config
        assert "darwin" in config.errored_platform_targets
        assert "squee" in config.host_targets
        assert any(
            "undefined name" in r.message and "[target.darwin]" in r.message
            for r in caplog.records
        )

    def test_invalid_filename_rejected(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # `config/has.dot.toml` -> stem "has.dot" -> not a valid
        # bundle name (contains the namespace separator).
        cfg_file, dropin = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text(
            _uuid_toml(
                '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        (dropin / "has.dot.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        with caplog.at_level(logging.ERROR, logger=crony.logger.name):
            bundles = crony.load_all_bundles()
        names = [b.name for b in bundles.bundles]
        assert names == ["default"]
        assert any(
            "has.dot.toml" in r.message and "bundle name" in r.message
            for r in caplog.records
        )

    def test_default_dropin_filename_collides_with_config_toml(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # A user creates `config/default.toml`; that bundle name is
        # already claimed by `config.toml`. The dropin is dropped.
        cfg_file, dropin = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text(
            _uuid_toml(
                '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        (dropin / "default.toml").write_text(
            _uuid_toml(
                '[job.k]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        with caplog.at_level(logging.ERROR, logger=crony.logger.name):
            bundles = crony.load_all_bundles()
        names = [b.name for b in bundles.bundles]
        assert names == ["default"]
        # The colliding dropin is referenced in the error.
        assert any("default.toml" in r.message for r in caplog.records)


class TestLoadConfig:
    """`load_config()` builds the whole-process Config: parsed TOML +
    pending graph (from cascade resolution) + current graph (from
    on-disk snapshots) + runtime state + orphan-unit detection. It's
    the one-shot scan that every later read path consults instead of
    re-walking the disk.
    """

    def _setup(self, tmp_path: Path, monkeypatch: Any) -> Path:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = tmp_path / "config_dropin"
        cfg_dropin.mkdir()
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony, "current_host", lambda: "test-host")
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        return cfg_file

    def test_pending_graph_built_from_toml(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._setup(tmp_path, monkeypatch)
        uuid_a = "11111111-2222-3333-4444-555555555555"
        cfg_file.write_text(
            f'[job.a]\nuuid = "{uuid_a}"\n'
            'command = "true"\nschedule = "daily"\n'
            '[target.darwin]\njobs = ["a"]\n',
            encoding="utf-8",
        )
        config = crony.load_config()
        ref = crony.EntityRef("default", uuid_a)
        assert ref in config.pending.jobs
        assert config.pending.jobs[ref].name == "default.a"
        # No on-disk snapshot yet -> nothing in current.
        assert ref not in config.current.jobs
        assert config.config_state(ref) == "missing"

    def test_current_only_entry_is_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        uuid_g = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        # Plant a state dir + snapshot for an entry that no TOML
        # defines. (Empty config file = no jobs.)
        crony.CONFIG_FILE.write_text("", encoding="utf-8")
        sd = crony.STATE_DIR / "default" / uuid_g
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps(
                {
                    "schema": crony._SNAPSHOT_SCHEMA,
                    "kind": "job",
                    "name": "default.gone",
                    "bundle": "default",
                    "uuid": uuid_g,
                    "command": "true",
                    "script": None,
                    "args": [],
                    "gate": None,
                    "gate_script": None,
                    "gate_args": [],
                    "env": {},
                    "job_timeout_sec": 600,
                    "schedule": "daily",
                    "interval": None,
                    "interactive": False,
                    "interactive_active_sec": 600,
                    "interactive_delay_sec": 3600,
                }
            ),
            encoding="utf-8",
        )
        config = crony.load_config()
        ref = crony.EntityRef("default", uuid_g)
        assert config.config_state(ref) == "orphan"
        assert ref in config.runtime

    def test_synced_when_pending_matches_current(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._setup(tmp_path, monkeypatch)
        uuid_a = "33333333-4444-5555-6666-777777777777"
        cfg_file.write_text(
            f'[job.a]\nuuid = "{uuid_a}"\n'
            'command = "true"\nschedule = "daily"\n'
            '[target.darwin]\njobs = ["a"]\n',
            encoding="utf-8",
        )
        # Build a snapshot that matches what apply would write.
        bundles = crony.load_all_bundles()
        target = crony.resolve_target(
            bundles.bundles[0].config, "test-host", "darwin"
        )
        snap = crony._resolve_job_snapshot(
            bundles.bundles[0].config,
            target,
            bundles.bundles[0].config.jobs["a"],
            "default.a",
            "default",
        )
        sd = crony.STATE_DIR / "default" / uuid_a
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps(dataclasses.asdict(snap)), encoding="utf-8"
        )
        config = crony.load_config()
        ref = crony.EntityRef("default", uuid_a)
        assert config.config_state(ref) == "synced"

    def test_stale_when_pending_differs(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._setup(tmp_path, monkeypatch)
        uuid_a = "44444444-5555-6666-7777-888888888888"
        cfg_file.write_text(
            f'[job.a]\nuuid = "{uuid_a}"\n'
            'command = "true"\nschedule = "daily"\n'
            '[target.darwin]\njobs = ["a"]\n',
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        target = crony.resolve_target(
            bundles.bundles[0].config, "test-host", "darwin"
        )
        snap = crony._resolve_job_snapshot(
            bundles.bundles[0].config,
            target,
            bundles.bundles[0].config.jobs["a"],
            "default.a",
            "default",
        )
        sd = crony.STATE_DIR / "default" / uuid_a
        sd.mkdir(parents=True)
        # Persist a snapshot with a divergent command vs what TOML
        # currently says ("true" vs "stale-command").
        diverged = dataclasses.asdict(snap)
        diverged["command"] = "stale-command"
        (sd / "snapshot.json").write_text(
            json.dumps(diverged), encoding="utf-8"
        )
        config = crony.load_config()
        ref = crony.EntityRef("default", uuid_a)
        assert config.config_state(ref) == "stale"

    def test_resolve_finds_by_pending_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._setup(tmp_path, monkeypatch)
        uuid_a = "55555555-6666-7777-8888-999999999999"
        cfg_file.write_text(
            f'[job.a]\nuuid = "{uuid_a}"\n'
            'command = "true"\nschedule = "daily"\n'
            '[target.darwin]\njobs = ["a"]\n',
            encoding="utf-8",
        )
        config = crony.load_config()
        ref = config.resolve_pending("default.a")
        assert ref is not None
        assert ref.uuid == uuid_a


class TestConfigBroken:
    """`Config.broken` carries the entities whose on-disk
    snapshot can't be loaded by this crony binary. `_build_current_graph`
    populates it instead of silently dropping the state dir;
    `Config.config_state` returns `"broken"` for refs that land
    there, beating the synced / stale / orphan / missing axes.
    """

    def _setup(self, tmp_path: Path, monkeypatch: Any) -> Path:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = tmp_path / "config_dropin"
        cfg_dropin.mkdir()
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony, "current_host", lambda: "test-host")
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        cfg_file.write_text("", encoding="utf-8")
        return cfg_file

    def _plant_state(self, uuid_value: str, contents: str) -> tuple[Any, Path]:
        sd = crony.STATE_DIR / "default" / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(contents, encoding="utf-8")
        return crony.EntityRef("default", uuid_value), sd

    def test_wrong_schema_recorded_as_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        ref, _ = self._plant_state(
            "11111111-1111-1111-1111-111111111111",
            json.dumps(
                {"schema": 999, "kind": "job", "name": "default.legacy"}
            ),
        )
        config = crony.load_config()
        assert ref in config.broken
        assert ref not in config.current.jobs
        # Broken entries get a runtime entry so the unit-config /
        # last / last-ran columns can read the same state-dir
        # files normal current entries read.
        assert ref in config.runtime
        assert config.broken[ref].name == "default.legacy"
        assert "schema 999" in config.broken[ref].reason
        assert config.config_state(ref) == "broken"
        # Name-recovery let it land in broken_by_full_name.
        assert config.broken_by_full_name.get("default.legacy") == ref

    def test_unrecognized_kind_recorded_as_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        ref, _ = self._plant_state(
            "22222222-2222-2222-2222-222222222222",
            json.dumps(
                {
                    "schema": crony._SNAPSHOT_SCHEMA,
                    "kind": "banana",
                    "name": "default.j",
                }
            ),
        )
        config = crony.load_config()
        assert ref in config.broken
        assert "banana" in config.broken[ref].reason
        assert config.config_state(ref) == "broken"

    def test_dataclass_type_error_recorded_as_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        # Right schema + kind but missing required fields -> Job(**raw) raises.
        ref, _ = self._plant_state(
            "33333333-3333-3333-3333-333333333333",
            json.dumps(
                {
                    "schema": crony._SNAPSHOT_SCHEMA,
                    "kind": "job",
                    "name": "default.partial",
                }
            ),
        )
        config = crony.load_config()
        assert ref in config.broken
        assert config.broken[ref].name == "default.partial"
        assert "dataclass conversion" in config.broken[ref].reason

    def test_corrupt_json_recorded_as_broken_without_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        ref, _ = self._plant_state(
            "44444444-4444-4444-4444-444444444444",
            "{not valid json",
        )
        config = crony.load_config()
        assert ref in config.broken
        # No recoverable name from corrupt JSON; the entry is
        # reachable only by ref (or the synthetic input form).
        assert config.broken[ref].name is None
        assert "unreadable" in config.broken[ref].reason
        assert ref not in config.broken_by_full_name.values()

    def test_broken_beats_orphan_when_only_current_side_exists(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A broken state dir with no pending entry would otherwise
        # report as `orphan`; broken wins so the operator sees the
        # specific "snapshot can't load" reason.
        self._setup(tmp_path, monkeypatch)
        ref, _ = self._plant_state(
            "55555555-5555-5555-5555-555555555555",
            json.dumps(
                {"schema": 999, "kind": "job", "name": "default.unowned"}
            ),
        )
        config = crony.load_config()
        assert config.config_state(ref) == "broken"


class TestStatusBrokenSurface:
    """`crony status` surfaces broken entries with `CONFIG=broken`
    so a schema-bump or corrupt-snapshot failure becomes visible
    instead of looking like ordinary stale drift. Recovered-name
    broken entries render under their normal name; entries with
    no recoverable name (corrupt JSON) render as
    `<bundle>:<UUID>` in the JOB cell.
    """

    def _setup(self, tmp_path: Path, monkeypatch: Any) -> Path:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = tmp_path / "config_dropin"
        cfg_dropin.mkdir()
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony, "current_host", lambda: "test-host")
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        return cfg_file

    def test_status_renders_broken_for_schema_mismatch(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        cfg_file = self._setup(tmp_path, monkeypatch)
        uuid_value = "11111111-1111-1111-1111-111111111111"
        cfg_file.write_text(
            f'[job.j]\nuuid = "{uuid_value}"\n'
            'command = "true"\nschedule = "daily"\n'
            '[target.darwin]\njobs = ["j"]\n',
            encoding="utf-8",
        )
        sd = crony.STATE_DIR / "default" / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps({"schema": 999, "kind": "job", "name": "default.j"}),
            encoding="utf-8",
        )
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "default.j" in out
        assert "broken" in out

    def test_status_renders_synthetic_form_for_unrecoverable_name(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        cfg_file = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text("", encoding="utf-8")
        uuid_value = "22222222-2222-2222-2222-222222222222"
        sd = crony.STATE_DIR / "default" / uuid_value
        sd.mkdir(parents=True)
        # Corrupt JSON: the `name` field can't be recovered, so the
        # broken entry is addressable only by ref.
        (sd / "snapshot.json").write_text("{not valid json", encoding="utf-8")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert f"default:{uuid_value}" in out
        assert "broken" in out

    def test_status_bundle_scoped_works_when_bundle_config_broken(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # status is read-side: scoping to a bundle whose config has
        # since broken must surface that bundle's on-disk remnants,
        # not be refused as an unknown bundle.
        cfg_file = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text("", encoding="utf-8")
        uuid_value = "33333333-3333-3333-3333-333333333333"
        sd = crony.STATE_DIR / "borgadm" / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps({"schema": 999, "kind": "job", "name": "borgadm.k"}),
            encoding="utf-8",
        )
        # borgadm's config file fails to parse: an errored bundle,
        # not a loaded one, yet it has on-disk state.
        (crony.CONFIG_DROPIN_DIR / "borgadm.toml").write_text(
            "this is not [valid toml", encoding="utf-8"
        )
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle="borgadm",
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "borgadm.k" in out
        assert "broken" in out


class TestNameCollision:
    """When two current state dirs recover the same full name (uuid
    residue that escaped apply's cleanup, or hand-mucked state), the
    config-matching ref keeps the plain name and the other is
    `shadowed` -- surfaced by `<bundle>:<UUID>` in the JOB / UUID
    column so it stays addressable for `crony destroy`.
    """

    def _plant_residue(
        self, h: _ApplyHarness, full: str, uuid_value: str
    ) -> Path:
        bundle, _, _ = full.partition(".")
        sd = h.state / bundle / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps(
                {
                    "schema": crony._SNAPSHOT_SCHEMA,
                    "kind": "job",
                    "name": full,
                    "bundle": bundle,
                    "uuid": uuid_value,
                    "command": "true",
                    "script": None,
                    "args": [],
                    "gate": None,
                    "gate_script": None,
                    "gate_args": [],
                    "env": {},
                    "job_timeout_sec": 600,
                    "schedule": "*-*-* 03:00",
                    "interval": None,
                    "interactive": False,
                    "interactive_active_sec": 600,
                    "interactive_delay_sec": 3600,
                }
            ),
            encoding="utf-8",
        )
        return sd

    def test_config_matching_ref_keeps_name_other_is_shadowed(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        live_uuid = cfg.jobs["j"].uuid
        h.apply("j")
        # Plant a stray dir recovering the same name under a
        # different uuid (residue that bypassed apply's cleanup).
        stray_uuid = "99999999-8888-7777-6666-555544443333"
        self._plant_residue(h, h.full("j"), stray_uuid)
        config = crony.load_config()
        live_ref = crony.EntityRef("default", live_uuid)
        stray_ref = crony.EntityRef("default", stray_uuid)
        # The live (config-matching) ref keeps the name; the stray
        # is shadowed.
        assert config.current.by_full_name[h.full("j")] == live_ref
        assert config.shadowed == {stray_ref}

    def test_shadowed_row_renders_by_ref_in_status(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        stray_uuid = "99999999-8888-7777-6666-555544443333"
        self._plant_residue(h, h.full("j"), stray_uuid)
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # The live entry shows by name; the shadowed residue shows
        # by ref form (and as an orphan) so it's addressable.
        assert h.full("j") in out
        assert f"default:{stray_uuid}" in out
        assert "orphan" in out

    def test_full_apply_clears_shadow_but_keeps_live_unit(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A no-arg apply reconciles the shadowed residue away, but
        # the shadow shares the live entry's name-keyed unit -- the
        # sweep must reclaim only the stray state dir, never unlink
        # the unit the live entry is still firing from.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        live_uuid = cfg.jobs["j"].uuid
        h.apply("j")
        stray_uuid = "99999999-8888-7777-6666-555544443333"
        stray_dir = self._plant_residue(h, h.full("j"), stray_uuid)
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        assert plist.exists()
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        # Stray residue gone; live state dir and shared unit intact.
        assert not stray_dir.exists()
        assert (h.state / "default" / live_uuid / "snapshot.json").is_file()
        assert plist.exists()

    def test_destroy_orphans_reclaims_shadowed_residue(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A shadowed residue dir shares the live entry's name, so
        # the name-keyed discovery set never lists it. `destroy
        # --orphans` must still reclaim it by ref (state dir only;
        # the shared unit belongs to the live winner).
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        live_uuid = cfg.jobs["j"].uuid
        h.apply("j")
        stray_uuid = "99999999-8888-7777-6666-555544443333"
        stray_dir = self._plant_residue(h, h.full("j"), stray_uuid)
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        crony.do_destroy(jobs=[], bundle=None, orphans=True)
        # Shadowed residue reclaimed; the live entry's dir + shared
        # unit are untouched (it's selected, not an orphan).
        assert not stray_dir.exists()
        assert (h.state / "default" / live_uuid / "snapshot.json").is_file()
        assert plist.exists()


class TestUnitOnlyOrphan:
    """A platform unit file with no corresponding state dir
    becomes a `Config.unit_only` entry with a deterministic
    synthetic uuid. Destroy reaches it through `resolve_current`,
    same as the broken-state surface. The synthetic uuid is
    `uuid5(NAMESPACE_DNS, "crony.unit-only/<full_name>")` so
    repeat loads address the same entity.
    """

    def _setup(
        self, tmp_path: Path, monkeypatch: Any, platform: str = "darwin"
    ) -> _ApplyHarness:
        return _ApplyHarness(tmp_path, monkeypatch, platform=platform)

    def test_unit_file_with_no_state_dir_lands_in_unit_only(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = self._setup(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        plist = h.agents / "org.crony.default.ghost.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("", encoding="utf-8")
        config = crony.load_config()
        ref = config.unit_only_by_full_name.get("default.ghost")
        assert ref is not None
        # The synthetic uuid is deterministic: repeat loads
        # produce the same ref.
        config2 = crony.load_config()
        assert config2.unit_only_by_full_name["default.ghost"] == ref
        # The platform unit path is captured in RuntimeState.
        rt = config.runtime[ref]
        assert rt.unit_config == plist
        # `config_state` reports orphan, not broken / synced /
        # stale.
        assert config.config_state(ref) == "orphan"

    def test_destroy_wipes_unit_only_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = self._setup(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        plist = h.agents / "org.crony.default.ghost.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("", encoding="utf-8")
        crony.do_destroy(jobs=["default.ghost"], bundle=None, orphans=False)
        assert not plist.exists()


class TestStatusUnitConfigColumn:
    """`crony status --cols ...,unit-config` shows the on-disk
    path of the platform unit file. The cell value comes from
    `RuntimeState.unit_config` so subcommands don't re-walk the
    unit dirs themselves.
    """

    def test_unit_config_renders_plist_path(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols="job,unit-config",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "UNIT CONFIG" in out
        assert "org.crony.default.j.plist" in out


class TestStatusExcludeHealthy:
    """`crony status --exclude-healthy` drops synced + ok / never /
    gated rows and renders flat (no tree indent). Always exits 0 --
    this is a filter on the display, not a gate.
    """

    def test_healthy_row_filtered_out(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"healthy": {"command": "true", "schedule": "daily"}}},
            default_target_jobs=["healthy"],
        )
        # Apply so it's synced; never run -> LAST=never (healthy).
        h.apply("healthy")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=True,
        )
        out = capsys.readouterr().out
        assert "healthy" not in out

    def test_unhealthy_row_kept(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "daily"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        (h.state_dir("j") / "last-run.json").write_text(
            '{"exit_class": "fail", "exit_code": 1, '
            '"started_at": "2026-01-01T00:00:00-08:00"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=True,
        )
        out = capsys.readouterr().out
        assert "default.j" in out
        assert "fail" in out

    def test_exclude_healthy_renders_flat(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Tree indent (two spaces per depth level) is dropped
        # under --exclude-healthy. The unhealthy row lands flat
        # against the left margin.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"leaf": {"command": "true"}},
                "job-group": {"g": {"jobs": ["leaf"], "schedule": "daily"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("leaf")
        h.apply("g")
        (h.state_dir("leaf") / "last-run.json").write_text(
            '{"exit_class": "fail", "exit_code": 1, '
            '"started_at": "2026-01-01T00:00:00-08:00"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=True,
        )
        out = capsys.readouterr().out
        # The leaf row appears flat -- no leading whitespace
        # before the name -- since tree indent is dropped.
        lines = [ln for ln in out.splitlines() if "default.leaf" in ln]
        assert lines and lines[0].lstrip() == lines[0]

    def test_disabled_unit_survives_exclude_healthy(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A disabled unit isn't firing, so it's unhealthy and must
        # survive the --exclude-healthy filter even though its
        # snapshot is synced and it never failed a run.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"paused": {"command": "true", "schedule": "daily"}}},
            default_target_jobs=["paused"],
        )
        h.apply("paused")
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "disabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=True,
        )
        out = capsys.readouterr().out
        assert "default.paused" in out


class TestResolveMethods:
    """The three named resolvers encode the operation's intent at
    the call site: `resolve_runnable` for the current-only snapshot
    lookup behind the UNIT NAME guess, `resolve_current` for destroy
    (current + broken), `resolve_pending` for apply / pending-side
    displays. Callers that want a broader lookup compose explicitly
    so the chain direction is visible at the call site instead of
    baked in.
    """

    def _setup(self, tmp_path: Path, monkeypatch: Any) -> Path:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = tmp_path / "config_dropin"
        cfg_dropin.mkdir()
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony, "current_host", lambda: "test-host")
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        return cfg_file

    def test_pending_only_resolves_via_pending_not_runnable_or_current(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._setup(tmp_path, monkeypatch)
        uuid_a = "11111111-aaaa-bbbb-cccc-dddddddddddd"
        cfg_file.write_text(
            f'[job.a]\nuuid = "{uuid_a}"\n'
            'command = "true"\nschedule = "daily"\n'
            '[target.darwin]\njobs = ["a"]\n',
            encoding="utf-8",
        )
        config = crony.load_config()
        assert config.resolve_pending("default.a") is not None
        # Never applied -> not in current, so trigger / destroy
        # have nothing on disk to act on.
        assert config.resolve_runnable("default.a") is None
        assert config.resolve_current("default.a") is None

    def test_broken_current_but_not_runnable_or_pending(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A broken entry is in `current` from destroy's
        # perspective (state dir on disk to wipe) but never
        # `runnable` (snapshot can't load, so a fire would bail)
        # and never `pending` (no TOML entry for it).
        cfg_file = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text("", encoding="utf-8")
        uuid_b = "22222222-aaaa-bbbb-cccc-dddddddddddd"
        sd = crony.STATE_DIR / "default" / uuid_b
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps(
                {"schema": 999, "kind": "job", "name": "default.legacy"}
            ),
            encoding="utf-8",
        )
        config = crony.load_config()
        ref = crony.EntityRef("default", uuid_b)
        assert config.resolve_runnable("default.legacy") is None
        assert config.resolve_current("default.legacy") == ref
        assert config.resolve_pending("default.legacy") is None


class TestBundleNamespacing:
    """Job names from different bundles get distinct namespaced
    forms; bundle-local short names can collide freely."""

    def test_same_short_name_in_two_bundles_ok(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = tmp_path / "config_dropin"
        cfg_dropin.mkdir()
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "CONFIG_DROPIN_DIR", cfg_dropin)

        cfg_file.write_text(
            _uuid_toml(
                '[job.daily-update]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        (cfg_dropin / "borgadm.toml").write_text(
            _uuid_toml(
                '[job.daily-update]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        # Both bundles loaded successfully despite identical short.
        assert {b.name for b in bundles.bundles} == {"default", "borgadm"}
        # Full names are distinct.
        full_names = bundles.all_full_names()
        assert "default.daily-update" in full_names
        assert "borgadm.daily-update" in full_names


class TestWaitForPidExit:
    """Kernel-level pid-exit wait primitive (kqueue / pidfd).

    These are the building block for `_trigger_unit_sync`; they
    must be reliable on both darwin and linux without polling.
    """

    def test_live_pid_exits_during_wait(self) -> None:
        proc = subprocess.Popen(["sleep", "0.3"])
        try:
            t0 = time.monotonic()
            reason = crony._wait_for_pid_exit(proc.pid, timeout=5.0)
            dt = time.monotonic() - t0
            assert reason == "exit"
            assert 0.2 < dt < 2.0, f"unexpected wait duration: {dt}"
        finally:
            proc.wait()

    def test_already_dead_pid_returns_exit(self) -> None:
        proc = subprocess.Popen(["true"])
        proc.wait()
        # Either the kernel still has zombie info (kqueue/pidfd
        # returns immediately) or the pid has been recycled
        # (we wait for a new process to exit, possibly hitting
        # timeout). Both are acceptable; the call must not hang
        # past the timeout.
        reason = crony._wait_for_pid_exit(proc.pid, timeout=2.0)
        assert reason in {"exit", "timeout"}

    def test_long_running_pid_hits_timeout(self) -> None:
        proc = subprocess.Popen(["sleep", "5"])
        try:
            t0 = time.monotonic()
            reason = crony._wait_for_pid_exit(proc.pid, timeout=0.2)
            dt = time.monotonic() - t0
            assert reason == "timeout"
            assert 0.15 < dt < 0.6, f"unexpected wait duration: {dt}"
        finally:
            proc.terminate()
            proc.wait()


class TestTriggerUnitSync:
    """`_trigger_unit_sync` wraps the kickstart + pid-watch +
    last-run.json cross-check. Stub the platform trigger and
    write a synthetic last-run.json to exercise the waiter loop
    without requiring real launchd / systemd."""

    def test_returns_recent_completion(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")

        def _stub_trigger(name: str, platform: str, **kw: Any) -> None:
            # Pretend the runner ran and wrote a fresh result.
            (sd / "last-run.json").write_text(
                '{"ended_at": "2099-01-01T00:00:00-08:00",'
                ' "exit_code": 0, "exit_class": "ok"}',
                encoding="utf-8",
            )

        monkeypatch.setattr(crony, "_trigger_unit", _stub_trigger)
        rec = crony._trigger_unit_sync(
            full, state_dir=sd, job_timeout=5.0, trigger_timeout=5.0
        )
        assert rec["exit_code"] == 0
        assert rec["exit_class"] == "ok"

    def test_trigger_start_timeout_raises(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")
        monkeypatch.setattr(crony, "_trigger_unit", lambda *a, **kw: None)
        with pytest.raises(crony.TriggerStartTimeout, match="never produced"):
            crony._trigger_unit_sync(
                full, state_dir=sd, job_timeout=5.0, trigger_timeout=1.0
            )

    def test_stale_last_run_json_loops_until_fresh_arrives(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Pre-existing last-run.json from a prior run (ended_at
        # before the trigger). The waiter should NOT accept it as
        # the answer; it should keep waiting until either a fresh
        # one appears or the trigger_timeout fires.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")
        (sd / "last-run.json").write_text(
            '{"ended_at": "1970-01-01T00:00:00-00:00",'
            ' "exit_code": 0, "exit_class": "ok"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(crony, "_trigger_unit", lambda *a, **kw: None)
        with pytest.raises(crony.TriggerStartTimeout):
            crony._trigger_unit_sync(
                full, state_dir=sd, job_timeout=5.0, trigger_timeout=1.0
            )

    def test_subsecond_run_is_recognized_as_fresh(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `pre_trigger` and `ended_at` must compare at the same
        # precision: the runner's `_now_iso()` truncates to whole
        # seconds, so a run that completes within the same second
        # as the trigger needs `pre_trigger` truncated too --
        # otherwise the waiter sees `ended_at < pre_trigger` and
        # spins until `trigger_timeout`.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")

        def _stub_trigger(name: str, platform: str, **kw: Any) -> None:
            # Write a last-run.json whose ended_at is the same
            # whole-second timestamp `_now_iso()` would produce
            # right now -- modeling a sub-second run.
            (sd / "last-run.json").write_text(
                '{"ended_at": "%s", "exit_code": 4, "exit_class": "fail"}'
                % crony._now_iso(),
                encoding="utf-8",
            )

        monkeypatch.setattr(crony, "_trigger_unit", _stub_trigger)
        rec = crony._trigger_unit_sync(
            full, state_dir=sd, job_timeout=5.0, trigger_timeout=2.0
        )
        assert rec["exit_class"] == "fail"
        assert rec["exit_code"] == 4

    def test_long_run_past_trigger_timeout_still_returns(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `trigger_timeout` is the "did the platform respond?"
        # detector, not a completion deadline. Once the runner's
        # pid file appears, the waiter must switch to
        # `job_timeout`-bounded watching so a long but
        # well-behaved run completes normally; otherwise a job
        # whose execution exceeds `trigger_timeout` would record
        # as `timeout` even after a clean `ok` exit.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")

        # Stub the platform trigger to write a pid file
        # immediately. The pid points at the real test process
        # (which won't exit during the test) -- we additionally
        # stub `_wait_for_pid_exit` to simulate the runner
        # finishing AFTER trigger_timeout has already elapsed.
        (sd / "run.pid").write_text(f"{os.getpid()}\n")
        wait_calls: list[float] = []

        def _stub_wait(pid: int, timeout: float) -> str:
            wait_calls.append(timeout)
            # Simulate the runner completing now: write a fresh
            # last-run.json and unlink the pid.
            (sd / "last-run.json").write_text(
                f'{{"ended_at": "{crony._now_iso()}",'
                ' "exit_code": 0, "exit_class": "ok"}',
                encoding="utf-8",
            )
            (sd / "run.pid").unlink(missing_ok=True)
            return "exit"

        monkeypatch.setattr(crony, "_trigger_unit", lambda *a, **kw: None)
        monkeypatch.setattr(crony, "_wait_for_pid_exit", _stub_wait)
        rec = crony._trigger_unit_sync(
            full, state_dir=sd, job_timeout=120.0, trigger_timeout=1.0
        )
        # The runner completed, even though trigger_timeout (1s)
        # was tighter than what a real-world startup might take.
        assert rec["exit_class"] == "ok"
        # The wait was bounded by the larger job_timeout, not
        # trigger_timeout: confirms the deadline switch happened
        # once the pid was observed.
        assert wait_calls and wait_calls[0] > 1.0


class TestPlatformUnitDiscovery:
    """`_platform_unit_names` walks the platform unit directory
    and returns crony-managed entries by parsing their filenames.
    Used so units lingering after a state wipe still surface as
    orphans for status / destroy.
    """

    def test_finds_plist_on_darwin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.agents.mkdir(parents=True, exist_ok=True)
        (h.agents / "org.crony.default.foo.plist").write_text("")
        (h.agents / "org.crony.bundle.bar.plist").write_text("")
        # Non-crony plist must be ignored.
        (h.agents / "com.other.app.plist").write_text("")
        names = crony._platform_unit_names()
        assert names == {"default.foo", "bundle.bar"}

    def test_finds_service_and_timer_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        (h.sysd / "crony-default.foo.service").write_text("")
        (h.sysd / "crony-default.foo.timer").write_text("")
        (h.sysd / "crony-bundle.bar.service").write_text("")
        # Foreign unit must be ignored.
        (h.sysd / "myapp.service").write_text("")
        names = crony._platform_unit_names()
        assert names == {"default.foo", "bundle.bar"}

    def test_missing_unit_dir_returns_empty(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Fresh install or otherwise no unit dir: no crash, just empty.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        if h.agents.exists():
            shutil.rmtree(h.agents)
        assert crony._platform_unit_names() == set()


class TestSnapshotLifecycle:
    """Apply pins runtime parameters into a per-entry snapshot JSON;
    the runner reads from the snapshot, not the live config. These
    tests exercise the snapshot file lifecycle (write, read, refuse
    on schema mismatch) and the drift-detection invariant (an edit
    to any snapshot-covered field surfaces as "updated" via
    dataclass-equality comparison against the on-disk snapshot).
    """

    def test_apply_writes_snapshot(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "job_timeout_sec": 600,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        snap_path = h.state_dir("j") / "snapshot.json"
        assert snap_path.exists()
        snap = _cast_dict(snap_path.read_text())
        assert snap["kind"] == "job"
        assert snap["name"] == h.full("j")
        assert snap["command"] == "true"
        assert snap["job_timeout_sec"] == 600
        assert snap["schema"] == crony._SNAPSHOT_SCHEMA

    def test_apply_state_is_co_located_in_entry_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The snapshot lives alongside per-run artifacts in the
        # entry's state dir, not in a separate `installed/`
        # registry. Verify the layout so a refactor doesn't quietly
        # split them again.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        entry_dir = h.state_dir("j")
        assert (entry_dir / "snapshot.json").is_file()
        assert not (h.state / "installed").exists()

    def test_apply_writes_group_snapshot_with_pinned_budget(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "a": {"command": "true", "job_timeout_sec": 100},
                    "b": {"command": "true", "job_timeout_sec": 200},
                },
                "job-group": {
                    "g": {
                        "jobs": ["a", "b"],
                        "schedule": "*-*-* 03:00",
                    }
                },
            },
            default_target_jobs=["g"],
        )
        # Apply children first so the group's resolution sees them.
        h.apply("a")
        h.apply("b")
        h.apply("g")
        snap_path = h.state_dir("g") / "snapshot.json"
        snap = _cast_dict(snap_path.read_text())
        assert snap["kind"] == "group"
        # Children are uuids on disk (rename-stable identity edge);
        # the runner resolves each back to a full name at dispatch.
        assert snap["children"] == [
            cfg.jobs["a"].uuid,
            cfg.jobs["b"].uuid,
        ]
        # 1.05 * (100 + 200) = 315
        assert snap["group_budget_sec"] == 315

    def test_group_snapshot_drops_host_masked_child(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A parent group can reference a child whose own `hosts`
        # filter excludes the current host. The reference is a
        # no-op here: the child isn't installed (its own filter
        # masks it from selection), so the parent's snapshot must
        # not list it -- otherwise group dispatch would call
        # `systemctl --user start` on a unit that doesn't exist.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "a": {"command": "true", "job_timeout_sec": 100},
                    "b": {
                        "command": "true",
                        "job_timeout_sec": 200,
                        "hosts": ["some-other-host"],
                    },
                },
                "job-group": {
                    "g": {
                        "jobs": ["a", "b"],
                        "schedule": "*-*-* 03:00",
                    }
                },
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        # `b` is masked on test-host; apply skips it via target
        # selection. The group snapshot must do the same.
        h.apply("g")
        snap_path = h.state_dir("g") / "snapshot.json"
        snap = _cast_dict(snap_path.read_text())
        assert snap["children"] == [cfg.jobs["a"].uuid]
        # Budget reflects only `a`: 1.05 * 100 = 105.
        assert snap["group_budget_sec"] == 105

    def test_group_snapshot_drops_platform_masked_child(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Mirror of the host-mask case for the platform axis.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "a": {"command": "true", "job_timeout_sec": 100},
                    "b": {
                        "command": "true",
                        "job_timeout_sec": 200,
                        "platforms": ["linux"],
                    },
                },
                "job-group": {
                    "g": {
                        "jobs": ["a", "b"],
                        "schedule": "*-*-* 03:00",
                    }
                },
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        h.apply("g")
        snap_path = h.state_dir("g") / "snapshot.json"
        snap = _cast_dict(snap_path.read_text())
        assert snap["children"] == [cfg.jobs["a"].uuid]
        assert snap["group_budget_sec"] == 105

    def test_group_snapshot_drops_masked_child_subgroup(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Same rule applies when the masked direct child is itself
        # a sub-group. The parent's snapshot drops the reference;
        # the sub-group never gets installed on this host.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "a": {"command": "true", "job_timeout_sec": 100},
                    "x": {"command": "true", "job_timeout_sec": 50},
                },
                "job-group": {
                    "sub": {
                        "jobs": ["x"],
                        "hosts": ["some-other-host"],
                    },
                    "g": {
                        "jobs": ["a", "sub"],
                        "schedule": "*-*-* 03:00",
                    },
                },
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        h.apply("g")
        snap_path = h.state_dir("g") / "snapshot.json"
        snap = _cast_dict(snap_path.read_text())
        assert snap["children"] == [cfg.jobs["a"].uuid]
        assert snap["group_budget_sec"] == 105

    def test_command_edit_flags_drift(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # Same schedule -- only the command changed; this must
        # land "updated" because the snapshot's `command` field
        # differs from the on-disk one.
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true; echo updated",
                        "schedule": "*-*-* 03:00",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        result = h.apply("j")
        assert result == "updated"

    def test_snapshot_stable_across_os_environ_changes(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The snapshot must pin only the user-written `env` dict,
        # not the merged runtime env: variables inherited from
        # the apply shell (SSH_AUTH_SOCK, transient session
        # state, etc.) would otherwise enter the snapshot, and a
        # subsequent apply / status from a different shell would
        # report the entry as stale despite no config change.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent-A.sock")
        first = h.apply("j")
        assert first == "added"
        # Same config, different SSH_AUTH_SOCK: should be a no-op.
        # PATH is intentionally NOT mutated -- apply needs to find
        # uv on PATH at apply time -- but SSH_AUTH_SOCK is the
        # realistic per-session-volatile case the regression
        # protects against.
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent-B.sock")
        second = h.apply("j")
        assert second == "unchanged"

    def test_snapshot_env_stores_user_literal(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # snap.env carries the literal toml `env` dict, not the
        # merged + expanded runtime env. The runner expands at fire
        # time (see TestRuntimeEnvExpansion).
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "env": {"PATH": "/extra:$PATH"},
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        snap_path = h.state_dir("j") / "snapshot.json"
        snap = _cast_dict(snap_path.read_text())
        # Literal $PATH preserved -- expansion happens at fire time.
        assert snap["env"] == {"PATH": "/extra:$PATH"}

    def test_env_edit_flags_drift(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "env": {"FOO": "1"},
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "env": {"FOO": "2"},
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert h.apply("j") == "updated"

    def test_timeout_edit_flags_drift(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "job_timeout_sec": 60,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "job_timeout_sec": 120,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert h.apply("j") == "updated"

    def test_load_snapshot_refuses_schema_mismatch(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        uuid_value = "deadbeef-uuid"
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        # schema=999 simulates a future version we don't support.
        (sd / "snapshot.json").write_text(
            '{"schema": 999, "kind": "job", "name": "default.j"}',
            encoding="utf-8",
        )
        with pytest.raises(crony.PreconditionError, match="schema 999"):
            crony._load_snapshot(
                crony.EntityRef(crony.DEFAULT_BUNDLE_NAME, uuid_value)
            )

    def test_load_snapshot_refuses_missing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        _ = _RunnerHarness(tmp_path, monkeypatch)
        with pytest.raises(crony.PreconditionError, match="no snapshot"):
            crony._load_snapshot(
                crony.EntityRef(crony.DEFAULT_BUNDLE_NAME, "never-applied-uuid")
            )

    def test_load_snapshot_refuses_malformed_schema_match(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # schema matches but the dataclass ctor would TypeError on
        # an unexpected field. The runner must get a clean
        # PreconditionError (-> records `canceled`), not a raw
        # TypeError traceback the scheduler never sees.
        h = _RunnerHarness(tmp_path, monkeypatch)
        uuid_value = "deadbeef-uuid"
        sd = h.state / crony.DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps(
                {
                    "schema": crony._SNAPSHOT_SCHEMA,
                    "kind": "job",
                    "name": "default.j",
                    "bogus_field": "unexpected",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(crony.PreconditionError, match="malformed fields"):
            crony._load_snapshot(
                crony.EntityRef(crony.DEFAULT_BUNDLE_NAME, uuid_value)
            )

    def test_topological_apply_propagates_leaf_edit_to_group(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # When a leaf's job_timeout_sec changes and the user runs
        # `crony apply <group>`, the group's snapshot must reflect
        # the new leaf budget. Topological apply walks leaves first
        # within the same pass.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "leaf": {"command": "true", "job_timeout_sec": 100},
                },
                "job-group": {
                    "g": {
                        "jobs": ["leaf"],
                        "schedule": "*-*-* 03:00",
                    }
                },
            },
            default_target_jobs=["g"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        snap_path = h.state_dir("g") / "snapshot.json"
        snap = _cast_dict(snap_path.read_text())
        # 1.05 * 100 = 105
        assert snap["group_budget_sec"] == 105

        # Bump leaf to 200; apply the group only and confirm the
        # group's pinned budget tracks the new leaf.
        h.config(
            {
                "job": {
                    "leaf": {"command": "true", "job_timeout_sec": 200},
                },
                "job-group": {
                    "g": {
                        "jobs": ["leaf"],
                        "schedule": "*-*-* 03:00",
                    }
                },
            },
            default_target_jobs=["g"],
        )
        crony.do_apply(jobs=[h.full("g")], verbose=False, bundle=None)
        snap_after = _cast_dict(snap_path.read_text())
        assert snap_after["group_budget_sec"] == 210

    def test_destroy_removes_snapshot(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        snap_path = h.state_dir("j") / "snapshot.json"
        assert snap_path.exists()
        crony.destroy_one(h.full("j"), h.state_dir("j"))
        assert not snap_path.exists()

    def test_run_subcommand_hidden_from_top_level_help(self) -> None:
        # `run` is the platform unit's entry point, not user-facing.
        # It must not appear in the usage line's choices summary
        # nor in the subcommand description block. Free-form prose
        # in the epilog can still mention "run" as a verb -- this
        # test scopes to the structural surfaces only.
        parser = crony.build_parser()
        usage_line = parser.format_usage()
        assert "trigger" not in usage_line  # uses metavar, not choices
        assert "run" not in usage_line, (
            f"`run` leaked into usage line: {usage_line!r}"
        )
        # In the subcommand description block, each entry appears
        # as `    <name>          <help>`. `run` shouldn't.
        help_text = parser.format_help()
        assert not re.search(r"^    run\b", help_text, flags=re.MULTILINE), (
            f"`run` leaked into subcommand description block:\n{help_text}"
        )
        # Sanity: `trigger` IS user-facing and must show up.
        assert re.search(r"^    trigger\b", help_text, flags=re.MULTILINE), (
            "`trigger` missing from subcommand description block"
        )


class TestExtractUnitExecPaths:
    """`_extract_unit_exec_paths` parses the on-disk unit file
    to recover the `(uv, crony)` paths apply baked in. Anything
    that doesn't match the expected argv shape returns None so
    the drift check treats the file as stale (apply will
    re-render it from the snapshot).
    """

    def test_extracts_paths_from_plist(self) -> None:
        plist = crony._render_plist(
            "j",
            crony.EntityRef("default", "u-test"),
            "*-*-* 03:00",
            None,
            uv_path=Path("/abs/uv"),
            crony_path=Path("/abs/crony"),
        )
        assert crony._extract_unit_exec_paths(plist, "darwin") == (
            Path("/abs/uv"),
            Path("/abs/crony"),
        )

    def test_extracts_paths_from_systemd_service(self) -> None:
        svc = crony._render_systemd_service(
            "j",
            crony.EntityRef("default", "u-test"),
            uv_path=Path("/abs/uv"),
            crony_path=Path("/abs/crony"),
        )
        assert crony._extract_unit_exec_paths(svc, "linux") == (
            Path("/abs/uv"),
            Path("/abs/crony"),
        )

    def test_returns_none_for_malformed_plist(self) -> None:
        assert crony._extract_unit_exec_paths("not xml", "darwin") is None

    def test_returns_none_for_plist_missing_program_arguments(self) -> None:
        assert (
            crony._extract_unit_exec_paths(
                '<?xml version="1.0"?><plist><dict>'
                "<key>Label</key><string>x</string></dict></plist>",
                "darwin",
            )
            is None
        )

    def test_returns_none_for_plist_wrong_argv_shape(self) -> None:
        bogus = (
            '<?xml version="1.0"?><plist><dict>'
            "<key>ProgramArguments</key><array>"
            "<string>/abs/uv</string><string>weird</string>"
            "<string>--script</string><string>/abs/crony</string>"
            "<string>run</string><string>x:y</string>"
            "</array></dict></plist>"
        )
        assert crony._extract_unit_exec_paths(bogus, "darwin") is None

    def test_returns_none_for_systemd_missing_exec_start(self) -> None:
        no_exec = "[Service]\nType=oneshot\n"
        assert crony._extract_unit_exec_paths(no_exec, "linux") is None

    def test_returns_none_for_systemd_unparseable_ini(self) -> None:
        # Leading non-section content trips configparser.
        assert (
            crony._extract_unit_exec_paths(
                "ExecStart=/abs/uv run --script /abs/crony run x:y\n",
                "linux",
            )
            is None
        )

    def test_returns_none_for_systemd_wrong_argv_shape(self) -> None:
        bogus = (
            "[Service]\nExecStart=/abs/uv weird --script /abs/crony run x:y\n"
        )
        assert crony._extract_unit_exec_paths(bogus, "linux") is None


class TestUnitDriftDetection:
    """`load_config` runs a per-entity integrity check on the
    installed platform unit: file present, content matches what
    apply would render given the embedded uv / crony paths, the
    embedded paths still resolve to files, and the scheduler has
    the unit loaded. Any divergence sets `RuntimeState.unit_is_stale
    = True` so status reports `config=stale` and the next apply
    re-renders even if the snapshot is unchanged.
    """

    def _apply_and_load(
        self, tmp_path: Path, monkeypatch: Any, platform: str = "darwin"
    ) -> tuple[_ApplyHarness, Any, Path]:
        h = _ApplyHarness(tmp_path, monkeypatch, platform=platform)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=[h.full("j")], verbose=False, bundle=None)
        config = crony.load_config()
        unit_dir = h.agents if platform == "darwin" else h.sysd
        if platform == "darwin":
            unit_config = unit_dir / f"org.crony.{h.full('j')}.plist"
        else:
            unit_config = unit_dir / f"crony-{h.full('j')}.timer"
        return h, config, unit_config

    def test_clean_apply_is_not_stale(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        _, config, _ = self._apply_and_load(tmp_path, monkeypatch)
        ref = config.current.by_full_name["default.j"]
        assert config.runtime[ref].unit_is_stale is False

    def test_hand_edited_plist_flags_stale(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        content = unit_config.read_text()
        # Flip Hour 3 -> Hour 5: snapshot still says 03:00, but the
        # on-disk plist now says 05:00. apply / load_config should
        # notice and flag the install stale.
        munged = content.replace(
            "<key>Hour</key>\n        <integer>3</integer>",
            "<key>Hour</key>\n        <integer>5</integer>",
        )
        assert munged != content
        unit_config.write_text(munged)
        config = crony.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.runtime[ref].unit_is_stale is True

    def test_missing_unit_file_flags_stale(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
        config = crony.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.runtime[ref].unit_is_stale is True

    def test_unloaded_unit_flags_stale(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h, _, _ = self._apply_and_load(tmp_path, monkeypatch)
        # Simulate the scheduler having unloaded the unit (e.g.
        # the user ran `launchctl bootout` directly). File on
        # disk still intact but `_unit_state` reports "none".
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "none")
        config = crony.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.runtime[ref].unit_is_stale is True

    def test_grouped_entry_not_stale_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A grouped (schedule-less) entry installs only a .service on
        # linux -- no .timer. `_unit_state` queries the timer, so a
        # grouped entry reads "none" at the scheduler, but that is its
        # correct resting state (a static, on-demand service), not
        # drift. A clean apply must leave it unit_is_stale=False.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {"jobs": ["a"], "schedule": "*-*-* 03:00"},
                },
            },
            default_target_jobs=["g"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        # Faithful systemctl: a unit is "enabled" only if its file is
        # present. The grouped child has no .timer, so its timer query
        # returns "" -> _unit_state "none" -- the real linux behavior
        # the harness's blanket `enabled` stub hides.
        monkeypatch.setattr(
            crony,
            "_systemd_is_enabled",
            lambda u: "enabled" if (h.sysd / u).is_file() else "",
        )
        config = crony.load_config()
        a_ref = config.current.by_full_name[h.full("a")]
        g_ref = config.current.by_full_name[h.full("g")]
        # The scheduled group keeps its loaded timer (sanity); the
        # grouped child must not be flagged stale for lacking one.
        assert config.runtime[g_ref].unit_is_stale is False
        assert config.runtime[a_ref].unit_is_stale is False

    def test_missing_baked_uv_path_flags_stale(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        # Replace the baked uv path with one pointing at a
        # nonexistent file. The unit-drift check resolves the
        # extracted paths against the filesystem and flags the
        # install as broken when either's gone.
        content = unit_config.read_text()
        live_uv = str(crony._uv_executable())
        bogus_uv = str(tmp_path / "nonexistent" / "uv")
        unit_config.write_text(content.replace(live_uv, bogus_uv))
        config = crony.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.runtime[ref].unit_is_stale is True

    def test_apply_refreshes_stale_install(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        result = h.apply("j")
        # Snapshot equality alone would say `unchanged`; the
        # integrity check escalates to `updated` so the unit file
        # gets re-rendered.
        assert result == "updated"
        assert unit_config.exists()

    def test_do_apply_refreshes_stale_install_via_model(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # `do_apply` reads the unit-drift verdict from the Config it
        # loaded once at start (apply_one's `model` path), not by
        # re-probing disk per entry. A unit deleted after the first
        # apply is `unit_is_stale` at load time, so the no-arg apply
        # re-renders it.
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
        with caplog.at_level(logging.INFO, logger="crony"):
            crony.do_apply(jobs=[], verbose=False, bundle=None)
        assert unit_config.exists()
        msgs = [r.getMessage() for r in caplog.records]
        assert any(f"{h.full('j')}: updated" in m for m in msgs), msgs

    def test_status_reports_stale_for_drifted_install(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # Find the row for default.j and confirm CONFIG is stale.
        for line in out.splitlines():
            if h.full("j") in line:
                assert "stale" in line
                break
        else:
            raise AssertionError(f"no row found for {h.full('j')}:\n{out}")

    def test_in_config_unit_lingers_after_snapshot_wipe_is_stale(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Entry still in config and its platform unit on disk, but
        # the state-dir snapshot was wiped. The in-memory model
        # records this as a unit-only orphan; `_config_axis`
        # upgrades the in-config "missing" verdict to "stale" so
        # the operator is steered to re-apply rather than seeing a
        # bare "not applied."
        h, _, _ = self._apply_and_load(tmp_path, monkeypatch)
        (h.state_dir("j") / "snapshot.json").unlink()
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        for line in out.splitlines():
            if h.full("j") in line:
                assert "stale" in line
                break
        else:
            raise AssertionError(f"no row found for {h.full('j')}:\n{out}")

    def test_stale_orphan_timer_flags_stale_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Transit group: no schedule of its own; only the
        # .service should be rendered. Drop an orphan .timer
        # next to it (simulating a leftover from a schedule ->
        # unscheduled transition that apply didn't clean up).
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {
                "job": {"a": {"command": "true", "job_timeout_sec": 100}},
                "job-group": {
                    "transit": {"jobs": ["a"]},
                    # A scheduled parent makes the transit group a
                    # valid schedule-less dispatch target.
                    "root": {
                        "jobs": ["transit"],
                        "schedule": "*-*-* 03:00",
                    },
                },
            },
            default_target_jobs=["root"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        timer = h.sysd / f"crony-{h.full('transit')}.timer"
        timer.write_text(
            crony._render_systemd_timer(h.full("transit"), "*-*-* 03:00", None)
        )
        config = crony.load_config()
        ref = config.current.by_full_name[h.full("transit")]
        assert config.runtime[ref].unit_is_stale is True


class TestSnapshotBackwardLoad:
    """A snapshot.json written before `schedule` / `interval` were
    snapshot fields must still load without raising. Treats the
    fields as None and lets status's schedule column fall back to
    the pending config value.
    """

    def test_legacy_job_snapshot_loads_with_none_schedule(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        full = h.full("j")
        legacy_uuid = "11112222-3333-4444-5555-666677778888"
        snap_dir = h.state / crony.DEFAULT_BUNDLE_NAME / legacy_uuid
        snap_dir.mkdir(parents=True)
        # Pre-existing snapshot lacking schedule / interval keys.
        legacy = {
            "schema": crony._SNAPSHOT_SCHEMA,
            "kind": "job",
            "name": full,
            "bundle": crony.DEFAULT_BUNDLE_NAME,
            "uuid": legacy_uuid,
            "command": "true",
            "script": None,
            "args": [],
            "gate": None,
            "gate_script": None,
            "gate_args": [],
            "env": {},
            "job_timeout_sec": 600,
        }
        (snap_dir / "snapshot.json").write_text(json.dumps(legacy))
        _ = full
        snap = crony._load_snapshot(
            crony.EntityRef(crony.DEFAULT_BUNDLE_NAME, legacy_uuid)
        )
        assert isinstance(snap, crony.Job)
        assert snap.schedule is None
        assert snap.interval is None


class TestLifecycleSmoke:
    """End-to-end smoke covering config-init -> edit -> config-validate
    -> apply -> status -> destroy via the public function entry points.
    Catches
    regressions where subcommands stop composing even when each one
    passes its own tests in isolation.
    """

    def test_full_lifecycle(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Isolate state and config to tmp_path.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        # init -> default template at the redirected CONFIG_FILE
        crony.do_init(force=False, bundle=None)
        assert h.cfg_file.exists()
        # Replace the template with a small real config so apply
        # has something concrete to install.
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_validate(bundle=None)
        # apply -> renders + activates
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        # status -> prints the synced/enabled tuple (sched stub)
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        capsys.readouterr()  # drop earlier output
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "synced" in out
        # destroy -> factory reset
        crony.do_destroy(jobs=[], bundle=None, orphans=False)
        assert not (h.agents / f"org.crony.{h.full('j')}.plist").exists()


class TestInteractiveHelpers:
    """Unit tests for the darwin idle / lock / dialog helpers."""

    def test_hid_idle_parses_nanoseconds(self, monkeypatch: Any) -> None:
        sample = (
            '  | |   "HIDIdleTime" = 7500000000\n'
            '  | |   "HIDKeyboardCapsLockOn" = No\n'
        )

        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(a, 0, stdout=sample, stderr="")

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        assert crony._darwin_hid_idle_seconds() == 7.5

    def test_hid_idle_missing_field_returns_zero(
        self, monkeypatch: Any
    ) -> None:
        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(a, 0, stdout="", stderr="")

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        assert crony._darwin_hid_idle_seconds() == 0.0

    def test_hid_idle_subprocess_failure_returns_zero(
        self, monkeypatch: Any
    ) -> None:
        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("ioreg not found")

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        assert crony._darwin_hid_idle_seconds() == 0.0

    def test_screen_locked_yes(self, monkeypatch: Any) -> None:
        out = (
            "IOConsoleUsers = "
            '({"CGSSessionScreenIsLocked"=Yes,"kCGSSessionUserNameKey"="me"})'
        )

        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(a, 0, stdout=out, stderr="")

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        assert crony._darwin_screen_locked() is True

    def test_screen_locked_no(self, monkeypatch: Any) -> None:
        out = 'IOConsoleUsers = ({"kCGSSessionUserNameKey"="me"})'

        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(a, 0, stdout=out, stderr="")

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        assert crony._darwin_screen_locked() is False

    def test_wait_returns_after_continuous_active(
        self, monkeypatch: Any
    ) -> None:
        # Script: first poll shows the user idle (gap), every
        # subsequent poll shows them active. After enough active
        # polls, the accumulator hits the threshold and the wait
        # returns. With poll_sec=30 and required=120, the first
        # active poll records active_since = (30 - 10) = 20s; the
        # accumulator hits 120s when monotonic reaches 140 (poll 5).
        idle_values = iter([100.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
        monkeypatch.setattr(
            crony, "_darwin_hid_idle_seconds", lambda: next(idle_values)
        )
        monkeypatch.setattr(crony, "_darwin_screen_locked", lambda: False)
        now = [0.0]
        monkeypatch.setattr(crony.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony.time, "sleep", lambda s: now.__setitem__(0, now[0] + s)
        )
        crony._wait_for_user_active(120, poll_sec=30, idle_break_sec=60)
        # Reached the return path; bound the elapsed time so a
        # broken loop would have hung the test.
        assert now[0] <= 300

    def test_wait_resets_on_idle_break(self, monkeypatch: Any) -> None:
        # Active for two polls, then a long idle gap resets the
        # accumulator, then active again until threshold met.
        idle_values = iter(
            [10.0, 10.0, 200.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
        )
        monkeypatch.setattr(
            crony, "_darwin_hid_idle_seconds", lambda: next(idle_values)
        )
        monkeypatch.setattr(crony, "_darwin_screen_locked", lambda: False)
        now = [0.0]
        monkeypatch.setattr(crony.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony.time, "sleep", lambda s: now.__setitem__(0, now[0] + s)
        )
        crony._wait_for_user_active(120, poll_sec=30, idle_break_sec=60)

    def test_wait_treats_locked_screen_as_idle(self, monkeypatch: Any) -> None:
        # Even with idle == 0, a locked screen prevents the active
        # accumulator from advancing.
        locked_values = iter([True, True, False, False, False, False, False])
        monkeypatch.setattr(crony, "_darwin_hid_idle_seconds", lambda: 0.0)
        monkeypatch.setattr(
            crony, "_darwin_screen_locked", lambda: next(locked_values)
        )
        now = [0.0]
        monkeypatch.setattr(crony.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony.time, "sleep", lambda s: now.__setitem__(0, now[0] + s)
        )
        crony._wait_for_user_active(60, poll_sec=30, idle_break_sec=60)

    def test_wait_bypass_check_short_circuits(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_darwin_hid_idle_seconds", lambda: 0.0)
        monkeypatch.setattr(crony, "_darwin_screen_locked", lambda: False)
        # Trip the bypass on the first poll; idle / lock checks are
        # never consulted.
        assert (
            crony._wait_for_user_active(
                100_000, bypass_check=lambda: True, poll_sec=1
            )
            is False
        )

    def test_wait_returns_true_when_threshold_met(
        self, monkeypatch: Any
    ) -> None:
        # Same harness as the threshold-met test, but with a
        # bypass_check that always returns False -- the wait should
        # complete normally and return True.
        idle_values = iter([100.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
        monkeypatch.setattr(
            crony, "_darwin_hid_idle_seconds", lambda: next(idle_values)
        )
        monkeypatch.setattr(crony, "_darwin_screen_locked", lambda: False)
        now = [0.0]
        monkeypatch.setattr(crony.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony.time, "sleep", lambda s: now.__setitem__(0, now[0] + s)
        )
        assert (
            crony._wait_for_user_active(
                120,
                bypass_check=lambda: False,
                poll_sec=30,
                idle_break_sec=60,
            )
            is True
        )

    def test_delay_or_bypass_completes_full_delay(
        self, monkeypatch: Any
    ) -> None:
        now = [0.0]
        monkeypatch.setattr(crony.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony.time, "sleep", lambda s: now.__setitem__(0, now[0] + s)
        )
        assert (
            crony._delay_or_bypass(120, bypass_check=lambda: False, poll_sec=30)
            is True
        )
        assert now[0] >= 120

    def test_delay_or_bypass_short_circuits_on_bypass(
        self, monkeypatch: Any
    ) -> None:
        # First two polls return False; third returns True. The
        # sleep should exit early without completing the full delay.
        bypass_values = iter([False, False, True])
        monkeypatch.setattr(crony.time, "monotonic", lambda: 0.0)
        sleeps: list[float] = []
        monkeypatch.setattr(crony.time, "sleep", sleeps.append)
        assert (
            crony._delay_or_bypass(
                3600,
                bypass_check=lambda: next(bypass_values),
                poll_sec=30,
            )
            is False
        )
        # Two sleep chunks happened (one between each bypass check)
        # before the third check fired.
        assert sleeps == [30, 30]

    def test_dialog_run_button(self, monkeypatch: Any) -> None:
        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                a, 0, stdout="button returned:Run Job\n", stderr=""
            )

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        assert crony._show_interactive_dialog("foo", "msg") == "run"

    def test_dialog_delay_button(self, monkeypatch: Any) -> None:
        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                a, 0, stdout="button returned:Delay Job\n", stderr=""
            )

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        assert crony._show_interactive_dialog("foo", "msg") == "delay"

    def test_dialog_cancel_button(self, monkeypatch: Any) -> None:
        # osascript exits non-zero when the cancel button is the
        # bound cancel.
        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(a, 1, stdout="", stderr="")

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        assert crony._show_interactive_dialog("foo", "msg") == "cancel"

    def test_dialog_osascript_missing_maps_to_cancel(
        self, monkeypatch: Any
    ) -> None:
        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("osascript not found")

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        assert crony._show_interactive_dialog("foo", "msg") == "cancel"


class TestUserTriggerFlag:
    """The one-shot sentinel file written by `_trigger_unit` when
    the user invokes `crony trigger` and consumed by `crony run` to
    bypass the interactive wait.
    """

    def test_write_and_consume_roundtrip(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        # The flag lives inside the entity's uuid-keyed state dir so
        # it sits alongside run.lock / pending.flag / last-run.json
        # rather than under a phantom legacy path.
        sd = h.fabricate_orphan("iv")
        assert not crony._consume_user_trigger_flag(sd)
        crony._write_user_trigger_flag(sd)
        assert (sd / "user-trigger.flag").exists()
        assert crony._consume_user_trigger_flag(sd)
        assert not (sd / "user-trigger.flag").exists()
        # Second consume on absent flag returns False.
        assert not crony._consume_user_trigger_flag(sd)

    def test_trigger_unit_writes_flag_only_when_user_initiated(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("iv")
        sd = h.fabricate_orphan("iv")
        unit = tmp_path / "fake.plist"
        unit.write_text("")
        monkeypatch.setattr(crony, "_dispatch_unit_path", lambda *a, **kw: unit)
        monkeypatch.setattr(
            crony.subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0),
        )
        crony._trigger_unit(
            full, "darwin", triggered_by_user=True, state_dir=sd
        )
        flag = sd / "user-trigger.flag"
        assert flag.exists()
        flag.unlink()
        crony._trigger_unit(full, "darwin")
        assert not flag.exists()

    def test_trigger_unit_unlinks_flag_on_kickstart_failure(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # If the platform-scheduler kick fails after we've written
        # the bypass flag, the flag must NOT be left on disk -- the
        # next legitimately scheduled fire would otherwise consume
        # it and silently skip its wait.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("iv")
        sd = h.fabricate_orphan("iv")
        unit = tmp_path / "fake.plist"
        unit.write_text("")
        monkeypatch.setattr(crony, "_dispatch_unit_path", lambda *a, **kw: unit)

        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(1, a[0])

        monkeypatch.setattr(crony.subprocess, "run", fake_run)
        with pytest.raises(subprocess.CalledProcessError):
            crony._trigger_unit(
                full, "darwin", triggered_by_user=True, state_dir=sd
            )
        assert not (sd / "user-trigger.flag").exists()


class TestRunJobInteractive:
    """End-to-end run_job behavior for interactive jobs. The wait /
    prompt helper is monkeypatched to return scripted choices so
    the tests don't depend on idle detection or osascript.
    """

    def test_run_path_execs_command(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "iv": {
                        "command": "true",
                        "schedule": "daily",
                        "interactive": True,
                    }
                }
            },
            default_target_jobs=["iv"],
        )
        monkeypatch.setattr(
            crony,
            "_interactive_wait_and_prompt",
            lambda snap, log_file: "run",
        )
        rc = crony.run_job(h.snap(cfg, "iv"))
        assert rc == 0
        rec = h.last_run("iv")
        assert rec["exit_class"] == "ok"
        assert not (h.state / h.full("iv") / "pending.flag").exists()

    def test_cancel_path_records_canceled_without_exec(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        sentinel = tmp_path / "exec-sentinel"
        cfg = h.config(
            {
                "job": {
                    "iv": {
                        "command": f"touch {sentinel}",
                        "schedule": "daily",
                        "interactive": True,
                    }
                }
            },
            default_target_jobs=["iv"],
        )
        monkeypatch.setattr(
            crony,
            "_interactive_wait_and_prompt",
            lambda snap, log_file: "cancel",
        )
        rc = crony.run_job(h.snap(cfg, "iv"))
        assert rc == 0
        rec = h.last_run("iv")
        assert rec["exit_class"] == "canceled"
        # The wrapped command never ran.
        assert not sentinel.exists()
        assert not (h.state / h.full("iv") / "pending.flag").exists()

    def test_user_trigger_flag_bypasses_wait_loop(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "iv": {
                        "command": "true",
                        "schedule": "daily",
                        "interactive": True,
                    }
                }
            },
            default_target_jobs=["iv"],
        )
        # Snapshot resolution creates the state dir, but the flag
        # might be written by trigger BEFORE the runner starts. Mimic
        # that: write the flag before run_job, into the uuid-keyed
        # state dir.
        sd = h.state_dir("iv", cfg=cfg)
        crony._write_user_trigger_flag(sd)

        called: list[bool] = []

        def _no_wait(snap: Any, log_file: Any) -> str:
            called.append(True)
            return "run"

        monkeypatch.setattr(crony, "_interactive_wait_and_prompt", _no_wait)
        rc = crony.run_job(h.snap(cfg, "iv"))
        assert rc == 0
        assert called == []
        rec = h.last_run("iv")
        assert rec["exit_class"] == "ok"
        # The flag was consumed.
        assert not (sd / "user-trigger.flag").exists()

    def test_user_trigger_mid_wait_breaks_out_and_execs(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The P1 case from the prior review: a `crony trigger` that
        # arrives WHILE the interactive runner is already in its
        # wait loop must break the waiter out and run the command.
        # Without this guarantee, the bypass flag would sit on disk
        # until the next scheduled fire.
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "iv": {
                        "command": "true",
                        "schedule": "daily",
                        "interactive": True,
                    }
                }
            },
            default_target_jobs=["iv"],
        )
        sd = h.state_dir("iv", cfg=cfg)
        # Drive the real _interactive_wait_and_prompt: HID idle is
        # high (idle break), screen is locked -- no natural
        # accumulation -- but on the second poll a `crony trigger`
        # writes the bypass flag and the wait short-circuits.
        monkeypatch.setattr(crony, "_darwin_hid_idle_seconds", lambda: 999.0)
        monkeypatch.setattr(crony, "_darwin_screen_locked", lambda: True)
        sleeps = [0]

        def fake_sleep(s: float) -> None:
            sleeps[0] += 1
            if sleeps[0] == 1:
                crony._write_user_trigger_flag(sd)

        monkeypatch.setattr(crony.time, "sleep", fake_sleep)

        rc = crony.run_job(h.snap(cfg, "iv"))
        assert rc == 0
        rec = h.last_run("iv")
        assert rec["exit_class"] == "ok"
        # The flag was consumed during the wait.
        assert not (sd / "user-trigger.flag").exists()


class TestLastRunStateInteractive:
    """`_last_run_state` reports `pending` for an interactive job
    sitting in its wait loop, and `canceled` for a completed run
    whose user clicked Cancel Job.
    """

    def test_pending_when_lock_held_and_flag_present(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        full = h.full("iv")
        sd = h.fabricate_orphan("iv")
        (sd / "pending.flag").write_bytes(b"")
        with crony._acquire_lock(sd / "run.lock"):
            config = crony.load_config()
            assert crony._last_run_state(config, full) == "pending"

    def test_running_when_lock_held_without_flag(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        full = h.full("iv")
        sd = h.fabricate_orphan("iv")
        with crony._acquire_lock(sd / "run.lock"):
            config = crony.load_config()
            assert crony._last_run_state(config, full) == "running"

    def test_canceled_from_last_run_json(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        full = h.full("iv")
        sd = h.fabricate_orphan("iv")
        (sd / "last-run.json").write_text(
            '{"exit_class": "canceled", '
            '"started_at": "2099-01-01T00:00:00-08:00",'
            ' "host": "h", "platform": "darwin",'
            ' "ended_at": "2099-01-01T00:00:01-08:00",'
            ' "duration_sec": 1.0, "exit_code": 0,'
            ' "signal": null, "gate": "none",'
            ' "log_path": "/tmp/run.log", "log_bytes_this_run": 0}',
            encoding="utf-8",
        )
        config = crony.load_config()
        assert crony._last_run_state(config, full) == "canceled"


class TestStatusRenameUuidModel:
    """`status` keys rows by uuid. A rename (same uuid, new config
    name) that hasn't been re-applied is one row, not two: the entity
    is shown under the name from the active source -- the new config
    name by default / --config-pending, the old applied name under
    --config-current -- with its run history and schedule resolved by
    uuid in both views.
    """

    def _stamp_run(self, sd: Path, started: str) -> None:
        (sd / "last-run.json").write_text(
            '{"exit_class": "ok", '
            f'"started_at": "{started}", '
            '"host": "h", "platform": "darwin", '
            f'"ended_at": "{started}", '
            '"duration_sec": 1.0, "exit_code": 0, '
            '"signal": null, "gate": "none", '
            '"log_path": "/tmp/run.log", "log_bytes_this_run": 0}',
            encoding="utf-8",
        )

    def _renamed_config(
        self, tmp_path: Path, monkeypatch: Any
    ) -> tuple["_ApplyHarness", str, str]:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {"oss-nvm": {"command": "true"}},
                "job-group": {
                    "u-oss": {"jobs": ["oss-nvm"], "schedule": "daily"},
                },
            },
            default_target_jobs=["u-oss"],
        )
        crony.do_apply(jobs=[], verbose=False, bundle=None)
        started = "2020-01-01T00:00:00-08:00"
        self._stamp_run(h.state_dir("u-oss", cfg=cfg), started)
        self._stamp_run(h.state_dir("oss-nvm", cfg=cfg), started)
        group_uuid = cfg.job_groups["u-oss"].uuid
        member_uuid = cfg.jobs["oss-nvm"].uuid
        # Rename group and member, keeping uuids; do NOT re-apply.
        h.config(
            {
                "job": {"bins-nvm": {"uuid": member_uuid, "command": "true"}},
                "job-group": {
                    "u-bins": {
                        "uuid": group_uuid,
                        "jobs": ["bins-nvm"],
                        "schedule": "daily",
                    },
                },
            },
            default_target_jobs=["u-bins"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        return h, group_uuid, member_uuid

    def test_default_view_shows_new_names_once_with_history(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h, group_uuid, member_uuid = self._renamed_config(tmp_path, monkeypatch)
        crony.do_status(
            jobs=[],
            cols="job,uuid,schedule,last,last-ran",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # New config names, each entity once, old names absent.
        assert h.full("u-bins") in out
        assert h.full("bins-nvm") in out
        assert h.full("u-oss") not in out
        assert h.full("oss-nvm") not in out
        assert out.count(group_uuid) == 1
        assert out.count(member_uuid) == 1
        # History and schedule resolve by uuid: the group's row keeps
        # its applied run and shows the (pending) schedule.
        for line in out.splitlines():
            if h.full("u-bins") in line:
                assert "daily" in line
                assert " ok " in f" {line} "
                assert "4" in line and "ago" in line  # e.g. "Nd ago"

    def test_config_current_shows_old_applied_names(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h, group_uuid, member_uuid = self._renamed_config(tmp_path, monkeypatch)
        crony.do_status(
            jobs=[],
            cols="job,uuid,schedule",
            show_masked=False,
            bundle=None,
            config_current=True,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # --config-current shows the applied (old) names, still one row
        # per uuid, with the applied schedule populated (not blank).
        assert h.full("u-oss") in out
        assert h.full("oss-nvm") in out
        assert h.full("u-bins") not in out
        assert h.full("bins-nvm") not in out
        assert out.count(group_uuid) == 1
        assert out.count(member_uuid) == 1
        for line in out.splitlines():
            if h.full("u-oss") in line:
                assert "daily" in line

    def test_rename_flags_name_and_unit_name(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Every diverging dual-source column carries the `^` marker
        # (no leading space). On a rename the identity and unit-name
        # diverge (config vs applied name); the uuid never does.
        _h, group_uuid, _member_uuid = self._renamed_config(
            tmp_path, monkeypatch
        )
        crony.do_status(
            jobs=[],
            cols="job-or-uuid,uuid,unit-name",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        grp = next(
            line for line in out.splitlines() if "default.u-bins" in line
        )
        # Identity shows the config name, flagged; unit-name shows the
        # label apply would render, flagged.
        assert "default.u-bins^" in grp
        assert "org.crony.default.u-bins^" in grp
        # The uuid column (stable identity) is never flagged.
        assert f"{group_uuid}^" not in out
        assert "stale" in out  # footer printed


class TestStatusColor:
    """The status table colors broken / failed states red and drift
    (a `stale` verdict or a divergence-flagged cell) yellow, but only
    when stdout is a color-capable TTY. The `^` marker stays uncolored.
    """

    R = crony._ANSI_RED
    Y = crony._ANSI_YELLOW
    X = crony._ANSI_RESET

    def _force_color(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        monkeypatch.setattr(crony, "_color_supported", lambda: True)

    def test_stale_and_divergence_are_yellow_missing_is_red(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # j: schedule edited (stale + divergence). k: new, never applied
        # (missing).
        h.config(
            {
                "job": {
                    "j": {"command": "true", "schedule": "*-*-* 09:00"},
                    "k": {"command": "true", "schedule": "*-*-* 05:00"},
                }
            },
            default_target_jobs=["j", "k"],
        )
        self._force_color(monkeypatch)
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # `stale` verdict -> yellow.
        assert f"{self.Y}stale{self.X}" in out
        # Divergence-flagged schedule -> yellow value, `^` left plain.
        assert f"{self.Y}*-*-* 09:00{self.X}^" in out
        # `missing` verdict -> red.
        assert f"{self.R}missing{self.X}" in out

    def test_last_fail_is_red(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        (h.state_dir("j", cfg=cfg) / "last-run.json").write_text(
            '{"exit_class": "fail", '
            '"started_at": "2020-01-01T00:00:00-08:00", '
            '"host": "h", "platform": "darwin", '
            '"ended_at": "2020-01-01T00:00:01-08:00", '
            '"duration_sec": 1.0, "exit_code": 1, '
            '"signal": null, "gate": "none", '
            '"log_path": "/tmp/run.log", "log_bytes_this_run": 0}',
            encoding="utf-8",
        )
        self._force_color(monkeypatch)
        crony.do_status(
            jobs=[],
            cols="job,last",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert f"{self.R}fail{self.X}" in out

    def test_orphan_is_red(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.fabricate_orphan("ghost")
        h.config({}, default_target_jobs=[])
        self._force_color(monkeypatch)
        crony.do_status(
            jobs=[],
            cols="job,config",
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert f"{self.R}orphan{self.X}" in out

    def test_no_color_when_stdout_not_tty(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # capsys replaces stdout with a non-TTY buffer, so the default
        # path emits no escape codes even for a stale row.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 09:00"}}},
            default_target_jobs=["j"],
        )
        monkeypatch.setattr(crony, "_unit_state", lambda n, p: "enabled")
        crony.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "stale" in out
        assert "\033[" not in out

    def test_color_supported_respects_no_color_and_tty(
        self, monkeypatch: Any
    ) -> None:
        class _Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(crony.sys, "stdout", _Tty())
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert crony._color_supported() is True
        monkeypatch.setenv("NO_COLOR", "1")
        assert crony._color_supported() is False
        # Non-TTY stream never colors, regardless of NO_COLOR.
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(crony.sys, "stdout", io.StringIO())
        assert crony._color_supported() is False


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
