#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov"]
# ///
# This is AI generated code

"""Comprehensive unit tests for crony."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import logging
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec

import pytest  # type: ignore[import-not-found]
from conftest import (
    CmdCallbacksBase,
    CodeQualityBase,
    ExceptionHierarchyBase,
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


class TestExceptionHierarchy(ExceptionHierarchyBase):
    """Verify every non-excluded ExitCode has a matching exception."""

    BASE_ERROR = crony.CronyError
    EXIT_CODE = crony.ExitCode
    EXCLUDED_CODES = {
        crony.ExitCode.SUCCESS,
        crony.ExitCode.WARNING,
    }


class TestCodeQuality(CodeQualityBase):
    """Test code quality with ruff and mypy."""

    SCRIPT_PATH = _script_path
    TEST_PATH = REPO_ROOT / "tests" / "test_crony.py"


class TestHelpOutput:
    """`crony --help` surfaces the design block appended to the epilog."""

    def test_help_includes_design_block(self) -> None:
        parser = crony.build_parser()
        text = parser.format_help()
        # Design block: 3-tuple status legend.
        assert "CONFIG  synced" in text
        assert "SCHED   enabled" in text
        assert "LAST    ok" in text
        # Exit codes still rendered.
        assert "exit codes:" in text
        # Design block is appended *after* the exit codes -- the
        # short tagline lives in description, design lives in
        # epilog after the exit-code list.
        assert text.index("exit codes:") < text.index("CONFIG  synced")


class TestCmdCallbacks(CmdCallbacksBase):
    """Test command callback dispatch table."""

    CALLBACKS = crony.COMMAND_CALLBACKS
    PARSER_FUNC = crony.build_parser
    CLI_FUNC = staticmethod(crony.cli)
    MODULE = crony
    EXIT_CODE_USAGE = crony.ExitCode.USAGE
    TEST_SUBCOMMAND = "validate"
    EXCEPTION_EXIT_CODE_MAP = [
        (crony.UsageError("t"), crony.ExitCode.USAGE),
        (crony.ConfigError("t"), crony.ExitCode.CONFIG),
        (
            crony.SubprocessError(1, ["bogus"]),
            crony.ExitCode.SUBPROCESS,
        ),
        (crony.AuditFailedError("t"), crony.ExitCode.AUDIT_FAILED),
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


# =============================================================================
# Helpers
# =============================================================================


def _job(**overrides: Any) -> dict[str, Any]:
    """Build a minimal job body with overrides."""
    base: dict[str, Any] = {"command": "true", "schedule": "daily"}
    base.update(overrides)
    return base


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
# Config parsing - structural
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
        cfg = crony.parse_config({})
        assert cfg.defaults.notify_channels == []
        assert cfg.defaults.job_timeout_sec == 1800
        assert cfg.defaults.notify_attach_log is True
        assert cfg.defaults.notify_channel_defs == {}

    def test_override_defaults(self) -> None:
        cfg = crony.parse_config(
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
            crony.parse_config(
                {"defaults": {"notify_channels": ["carrier-pigeon"]}}
            )

    def test_duplicate_notify_channels_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="listed twice"):
            crony.parse_config(
                {"defaults": {"notify_channels": ["ntfy", "ntfy"]}}
            )

    def test_multi_channel_defaults(self) -> None:
        cfg = crony.parse_config(
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
            crony.parse_config({"defaults": {"notify_channel": "ntfy"}})

    def test_notify_email_subsection(self) -> None:
        cfg = crony.parse_config(
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
            crony.parse_config(
                {"defaults": {"notify": {"email": {"to": "x@y.com"}}}}
            )

    def test_notify_ntfy_subsection(self) -> None:
        cfg = crony.parse_config(
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
            crony.parse_config({"defaults": {"notify": {"foo": _ntfy_block()}}})

    def test_arbitrary_channel_with_transport(self) -> None:
        cfg = crony.parse_config(
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
            crony.parse_config(
                {
                    "defaults": {
                        "notify": {"carrier-pigeon": {"transport": "carrier"}}
                    }
                }
            )

    def test_reserved_email_header_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="cannot be overridden"):
            crony.parse_config(
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
            crony.parse_config(
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
        # _post_ntfy sets Filename itself when attach_log is true;
        # let user override only on the no-attach path would be
        # confusing. Reserve to keep behavior consistent.
        with pytest.raises(crony.ConfigError, match="cannot be overridden"):
            crony.parse_config(
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
        cfg = crony.parse_config(
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
        cfg = crony.parse_config(self._cfg(_job()))
        assert cfg.jobs["j"].command == "true"
        assert cfg.jobs["j"].script is None

    def test_script_with_args(self) -> None:
        cfg = crony.parse_config(
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
        with pytest.raises(crony.ConfigError, match="exactly one of"):
            crony.parse_config(
                self._cfg(
                    {
                        "command": "x",
                        "script": "y",
                        "schedule": "daily",
                    }
                )
            )

    def test_command_xor_script_neither(self) -> None:
        with pytest.raises(crony.ConfigError, match="exactly one of"):
            crony.parse_config(self._cfg({"schedule": "daily"}))

    def test_args_with_command_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="only valid with"):
            crony.parse_config(self._cfg(_job(args=["a"])))

    def test_gate_xor_gate_script(self) -> None:
        with pytest.raises(crony.ConfigError, match="mutually exclusive"):
            crony.parse_config(self._cfg(_job(gate="x", gate_script="y.sh")))

    def test_gate_args_without_gate_script(self) -> None:
        with pytest.raises(
            crony.ConfigError, match="only valid with 'gate_script'"
        ):
            crony.parse_config(self._cfg(_job(gate="x", gate_args=["a"])))

    def test_schedule_xor_interval(self) -> None:
        with pytest.raises(crony.ConfigError, match="mutually exclusive"):
            crony.parse_config(
                self._cfg(
                    {
                        "command": "x",
                        "schedule": "daily",
                        "interval": "30min",
                    }
                )
            )

    def test_interval_form(self) -> None:
        cfg = crony.parse_config(
            self._cfg({"command": "x", "interval": "1h30min"})
        )
        assert cfg.jobs["j"].interval == "1h30min"
        assert cfg.jobs["j"].schedule is None

    def test_invalid_platforms_value(self) -> None:
        with pytest.raises(crony.ConfigError, match="not in"):
            crony.parse_config(self._cfg(_job(platforms=["windows"])))

    def test_invalid_notify_channel(self) -> None:
        with pytest.raises(crony.ConfigError, match="notify_channels"):
            crony.parse_config(
                self._cfg(_job(notify_channels=["carrier-pigeon"]))
            )

    def test_negative_timeout(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config(self._cfg(_job(job_timeout_sec=-1)))

    def test_zero_timeout(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config(self._cfg(_job(job_timeout_sec=0)))

    def test_env_must_be_string_dict(self) -> None:
        with pytest.raises(crony.ConfigError, match="string -> string"):
            crony.parse_config(self._cfg(_job(env={"FOO": 42})))

    def test_unknown_job_key(self) -> None:
        with pytest.raises(crony.ConfigError, match="unknown key"):
            crony.parse_config(self._cfg(_job(surprise="boom")))

    def test_group_only_job_no_schedule(self) -> None:
        # Valid only when referenced by a group.
        cfg = crony.parse_config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
            }
        )
        assert cfg.jobs["a"].schedule is None
        assert cfg.jobs["a"].interval is None


class TestParseJobGroup:
    def test_valid_group(self) -> None:
        cfg = crony.parse_config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            }
        )
        assert cfg.job_groups["g"].jobs == ["a"]
        assert cfg.job_groups["g"].schedule == "*-*-* 03:00"

    def test_empty_jobs_list(self) -> None:
        with pytest.raises(crony.ConfigError, match="non-empty list"):
            crony.parse_config(
                {"job-group": {"g": {"jobs": [], "schedule": "daily"}}}
            )

    def test_schedule_optional(self) -> None:
        # A group with no schedule / no interval is a transit group:
        # it parses fine, but its chains are checked at validate
        # time (a target referencing it through a path with no
        # schedule errors).
        cfg = crony.parse_config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"]}},
            }
        )
        assert cfg.job_groups["g"].schedule is None
        assert cfg.job_groups["g"].interval is None

    def test_both_schedule_and_interval(self) -> None:
        with pytest.raises(crony.ConfigError, match="mutually exclusive"):
            crony.parse_config(
                {
                    "job": {"a": {"command": "true"}},
                    "job-group": {
                        "g": {
                            "jobs": ["a"],
                            "schedule": "daily",
                            "interval": "1h",
                        }
                    },
                }
            )

    def test_unknown_group_key(self) -> None:
        with pytest.raises(crony.ConfigError, match="unknown key"):
            crony.parse_config(
                {
                    "job": {"a": {"command": "true"}},
                    "job-group": {
                        "g": {
                            "jobs": ["a"],
                            "schedule": "daily",
                            "surprise": True,
                        }
                    },
                }
            )

    def test_group_rejects_notify_channels(self) -> None:
        # Groups don't carry notify settings: per-child cascade
        # resolves notify via job/target/defaults instead.
        with pytest.raises(crony.ConfigError, match="unknown key"):
            crony.parse_config(
                {
                    "job": {"a": {"command": "true"}},
                    "job-group": {
                        "g": {
                            "jobs": ["a"],
                            "schedule": "daily",
                            "notify_channels": ["ntfy"],
                        }
                    },
                }
            )


class TestParseTarget:
    def test_platform_target(self) -> None:
        cfg = crony.parse_config(
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
        cfg = crony.parse_config(
            {
                "job": {"a": _job()},
                "target": {"host": {"my-host": {"jobs": ["a"]}}},
            }
        )
        assert "my-host" in cfg.host_targets
        assert cfg.host_targets["my-host"].kind == "host"

    def test_invalid_platform_name(self) -> None:
        with pytest.raises(crony.ConfigError, match="platform must be one of"):
            crony.parse_config(
                {
                    "job": {"a": _job()},
                    "target": {"windows": {"jobs": ["a"]}},
                }
            )

    def test_target_unknown_key(self) -> None:
        with pytest.raises(crony.ConfigError, match="unknown key"):
            crony.parse_config(
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
            crony.parse_config(
                {
                    "job": {"foo": _job()},
                    "job-group": {
                        "foo": {"jobs": ["foo"], "schedule": "daily"}
                    },
                }
            )

    def test_group_references_undefined_name(self) -> None:
        with pytest.raises(crony.ConfigError, match="undefined name"):
            crony.parse_config(
                {"job-group": {"g": {"jobs": ["nope"], "schedule": "daily"}}}
            )

    def test_nested_groups_supported(self) -> None:
        # A group can reference another group; only the chain to a
        # target needs to contain a schedule somewhere.
        cfg = crony.parse_config(
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
        # `a` would never fire, so reject at validate time.
        with pytest.raises(crony.ConfigError, match="no schedule anywhere"):
            crony.parse_config(
                {
                    "job": {"a": {"command": "true"}},
                    "job-group": {
                        "leaf": {"jobs": ["a"]},
                        "root": {"jobs": ["leaf"]},
                    },
                    "target": {"darwin": {"jobs": ["root"]}},
                }
            )

    def test_chain_cycle_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="cycle"):
            crony.parse_config(
                {
                    "job": {"a": {"command": "true"}},
                    "job-group": {
                        "g1": {"jobs": ["g2"], "schedule": "daily"},
                        "g2": {"jobs": ["g1"]},
                    },
                    "target": {"darwin": {"jobs": ["g1"]}},
                }
            )

    def test_target_references_undefined_name(self) -> None:
        with pytest.raises(crony.ConfigError, match="undefined name"):
            crony.parse_config({"target": {"darwin": {"jobs": ["nope"]}}})

    def test_target_references_group_ok(self) -> None:
        cfg = crony.parse_config(
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
        cfg = crony.parse_config({"job": {"a": {"command": "true"}}})
        assert "a" in cfg.jobs

    def test_target_reaching_schedule_less_job_directly_rejected(
        self,
    ) -> None:
        # A target referencing a job with no schedule and no chain
        # to a schedule is the canonical "this would never fire"
        # case.
        with pytest.raises(crony.ConfigError, match="no schedule anywhere"):
            crony.parse_config(
                {
                    "job": {"a": {"command": "true"}},
                    "target": {"darwin": {"jobs": ["a"]}},
                }
            )

    def test_referenced_group_only_job_ok(self) -> None:
        cfg = crony.parse_config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
            }
        )
        assert "a" in cfg.jobs

    def test_job_platform_excluded_by_target(self) -> None:
        with pytest.raises(crony.ConfigError, match="excludes"):
            crony.parse_config(
                {
                    "job": {
                        "a": _job(platforms=["darwin"]),
                    },
                    "target": {"linux": {"jobs": ["a"]}},
                }
            )


class TestUnknownTopLevel:
    def test_unknown_toplevel_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="unknown"):
            crony.parse_config({"surprise": {}})


# =============================================================================
# Loading from file
# =============================================================================


class TestLoadConfigFromFile:
    def test_loads_valid_config(self, tmp_path: Path) -> None:
        cfg_text = (
            "[defaults]\n"
            "notify_channels = []\n"
            "\n"
            "[job.brew-update]\n"
            'command = "brew update && brew upgrade"\n'
            'schedule = "*-*-* 03:15"\n'
        )
        f = tmp_path / "config.toml"
        f.write_text(cfg_text)
        cfg = crony.load_config(f)
        assert "brew-update" in cfg.jobs
        assert cfg.jobs["brew-update"].schedule == "*-*-* 03:15"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(crony.ConfigError, match="not found"):
            crony.load_config(tmp_path / "absent.toml")

    def test_bad_toml(self, tmp_path: Path) -> None:
        f = tmp_path / "config.toml"
        f.write_text("this is not [toml")
        with pytest.raises(crony.ConfigError, match="TOML parse error"):
            crony.load_config(f)


# =============================================================================
# Resolution
# =============================================================================


class TestResolution:
    def test_host_target_wins(self) -> None:
        cfg = crony.parse_config(
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
        cfg = crony.parse_config(
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
        cfg = crony.parse_config({})
        assert crony.resolve_target(cfg, "h", "darwin") is None

    def test_selected_includes_group_children(self) -> None:
        cfg = crony.parse_config(
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
        cfg = crony.parse_config({})
        jobs, groups = crony.selected_jobs_and_groups(cfg, None)
        assert jobs == set()
        assert groups == set()

    def test_notify_target_wins(self) -> None:
        cfg = crony.parse_config(
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
        cfg = crony.parse_config(
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
        cfg = crony.parse_config(
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
        cfg = crony.parse_config(
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
        cfg = crony.parse_config(
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

    def test_timeout_cascade(self) -> None:
        cfg = crony.parse_config(
            {
                "defaults": {"job_timeout_sec": 100},
                "job": {"a": _job(job_timeout_sec=200)},
                "target": {"darwin": {"jobs": ["a"], "job_timeout_sec": 300}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_job_timeout_sec(cfg, target, cfg.jobs["a"]) == 300

    def test_timeout_default_fallback(self) -> None:
        cfg = crony.parse_config(
            {
                "defaults": {"job_timeout_sec": 100},
                "job": {"a": _job()},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_job_timeout_sec(cfg, target, cfg.jobs["a"]) == 100


# =============================================================================
# crony init
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
        crony.parse_config(tomllib.loads(text))


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

    def full(self, short: str) -> str:
        """The full namespaced name for a short job/group name in the
        default bundle, used for state-path / unit-label assertions.
        """
        return f"{crony.DEFAULT_BUNDLE_NAME}.{short}"

    def config(
        self, raw: dict[str, Any], *, default_target_jobs: list[str]
    ) -> Any:
        """Build a Config with a darwin target selecting these jobs.

        Persists the raw config to the on-disk file so subprocess
        re-invocations of `crony run <child>` (group dispatch) load
        the same config we hand to run_group.
        """
        full = dict(raw)
        full.setdefault("target", {})
        target_section = full["target"]
        if isinstance(target_section, dict):
            target_section.setdefault("darwin", {})
            assert isinstance(target_section["darwin"], dict)
            target_section["darwin"].setdefault("jobs", default_target_jobs)
        self.cfg_file.write_text(_toml_dump(full), encoding="utf-8")
        return crony.parse_config(full)


def _toml_dump(data: dict[str, Any]) -> str:
    """Minimal TOML emitter for the test harness's small configs.

    Python's stdlib has tomllib for reading but no writer; rather
    than pull in a third-party dep just for tests, this emits the
    subset of TOML the harness actually produces.
    """
    out: list[str] = []

    def _val(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if isinstance(v, list):
            return "[" + ", ".join(_val(x) for x in v) + "]"
        raise TypeError(f"unsupported value type: {type(v).__name__}")

    def _emit(prefix: list[str], body: dict[str, Any]) -> None:
        scalars = [(k, v) for k, v in body.items() if not isinstance(v, dict)]
        tables = [(k, v) for k, v in body.items() if isinstance(v, dict)]
        if prefix:
            out.append(f"[{'.'.join(prefix)}]")
        for k, v in scalars:
            out.append(f"{k} = {_val(v)}")
        if scalars and tables:
            out.append("")
        for k, v in tables:
            _emit(prefix + [k], v)
            out.append("")

    _emit([], data)
    return "\n".join(out) + "\n"


def _last_run(state: Path, name: str) -> dict[str, Any]:
    """Read last-run.json by job name.

    A bare short name resolves against the default bundle so call
    sites stay terse. A full namespaced name (containing a dot)
    looks up that exact path.
    """
    if "." not in name:
        name = f"{crony.DEFAULT_BUNDLE_NAME}.{name}"
    text = (state / name / "last-run.json").read_text()
    return _cast_dict(text)


def _cast_dict(text: str) -> dict[str, Any]:
    """Read JSON into a typed dict for test assertions."""
    import json as _json

    out = _json.loads(text)
    assert isinstance(out, dict)
    return out


class TestRuntimeEnvExpansion:
    """`_runtime_env` carries forward shell-essential vars from the
    invoking process (so wrapped commands reach the user's ssh-agent
    via SSH_AUTH_SOCK) and expands `$VAR` / `${VAR}` in job.env
    values.
    """

    def _job(self, env_dict: dict[str, str] | None = None) -> Any:
        return crony.Job(name="j", command="true", env=env_dict or {})

    def test_inherits_path_when_no_env_override(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        env = crony._runtime_env(self._job({}))
        assert env["PATH"] == "/usr/bin:/bin"

    def test_ssh_auth_sock_forwarded_when_present(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
        env = crony._runtime_env(self._job())
        assert env.get("SSH_AUTH_SOCK") == "/tmp/agent.sock"

    def test_ssh_auth_sock_absent_when_unset(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
        env = crony._runtime_env(self._job())
        assert "SSH_AUTH_SOCK" not in env

    def test_dollar_var_resolves_against_inherited(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        env = crony._runtime_env(self._job({"PATH": "/extra:$PATH"}))
        assert env["PATH"] == "/extra:/usr/bin:/bin"

    def test_brace_form_resolves(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("HOME", "/Users/edp")
        env = crony._runtime_env(self._job({"TMPDIR": "${HOME}/.local/tmp"}))
        assert env["TMPDIR"] == "/Users/edp/.local/tmp"

    def test_unknown_var_stays_literal(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("CRONY_NOPE", raising=False)
        env = crony._runtime_env(self._job({"FOO": "$CRONY_NOPE"}))
        assert env["FOO"] == "$CRONY_NOPE"

    def test_double_dollar_escapes_to_literal(self, monkeypatch: Any) -> None:
        env = crony._runtime_env(self._job({"MSG": "cost: $$5"}))
        assert env["MSG"] == "cost: $5"

    def test_iteration_order_lets_later_keys_see_earlier(
        self, monkeypatch: Any
    ) -> None:
        # Python dicts preserve insertion order; toml parsers do too.
        # An earlier job.env key should be visible to a later one.
        monkeypatch.setenv("PATH", "/usr/bin")
        env = crony._runtime_env(
            self._job(
                {
                    "PATH": "/extra:$PATH",
                    "LD_LIBRARY_PATH": "$PATH/../lib",
                }
            )
        )
        assert env["PATH"] == "/extra:/usr/bin"
        assert env["LD_LIBRARY_PATH"] == "/extra:/usr/bin/../lib"

    def test_inherited_keys_not_overridden_by_unset_job_env(
        self, monkeypatch: Any
    ) -> None:
        # Job env is overlay; absent keys inherit unchanged.
        monkeypatch.setenv("HOME", "/Users/edp")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        env = crony._runtime_env(self._job({"FOO": "bar"}))
        assert env["HOME"] == "/Users/edp"
        assert env["LANG"] == "en_US.UTF-8"
        assert env["FOO"] == "bar"

    def test_malformed_references_stay_literal(self, monkeypatch: Any) -> None:
        # safe_substitute leaves bad-shape references untouched
        # rather than raising. $1 isn't a valid identifier; a
        # trailing bare $ has nothing to consume; ${UNCLOSED has
        # no closing brace.
        env = crony._runtime_env(
            self._job(
                {
                    "DIGIT": "$1 is not an identifier",
                    "TRAILING": "ends with $",
                    "BRACE_NO_CLOSE": "starts ${UNCLOSED",
                }
            )
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
        rc = crony.run_job(cfg, "ok")
        assert rc == 0
        rec = _last_run(h.state, "ok")
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
        rc = crony.run_job(cfg, "fail")
        assert rc == 17
        rec = _last_run(h.state, "fail")
        assert rec["exit_class"] == "fail"
        assert rec["exit_code"] == 17

    def test_unknown_job_raises_precondition(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config({}, default_target_jobs=[])
        with pytest.raises(crony.PreconditionError, match="unknown"):
            crony.run_job(cfg, "ghost")

    def test_unselected_job_raises_precondition(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        # Job exists but the target's `jobs` list is empty.
        cfg = h.config(
            {"job": {"ok": {"command": "true", "schedule": "daily"}}},
            default_target_jobs=[],
        )
        with pytest.raises(crony.PreconditionError, match="not selected"):
            crony.run_job(cfg, "ok")

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
        rc = crony.run_job(cfg, "ok", dry_run=True)
        assert rc == 0
        # No last-run.json written on dry-run
        assert not (h.state / h.full("ok") / "last-run.json").exists()


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
        rc = crony.run_job(cfg, "g")
        assert rc == 0
        rec = _last_run(h.state, "g")
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
        rc = crony.run_job(cfg, "g")
        assert rc == 0  # gated exits 0
        rec = _last_run(h.state, "g")
        assert rec["exit_class"] == "gated"
        assert rec["gate"] == "failed"
        # Main command never ran -> exit_code recorded as 0 placeholder
        assert rec["exit_code"] == 0
        log = (h.state / h.full("g") / "run.log").read_text()
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
        rc = crony.run_job(cfg, "g", skip_gate=True)
        assert rc == 0
        rec = _last_run(h.state, "g")
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
        # Pre-acquire the lock from another file descriptor.
        sd = h.state / h.full("j")
        sd.mkdir()
        lock = sd / "run.lock"
        import fcntl as _fcntl

        held = open(lock, "w")
        _fcntl.flock(held, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        try:
            rc = crony.run_job(cfg, "j")
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
        crony.run_job(cfg, "fail")
        rec = _last_run(h.state, "fail")
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
        full_name: str, *, job_timeout: float, trigger_timeout: float
    ) -> dict[str, Any]:
        ledger.append(
            {
                "full_name": full_name,
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
        _stub_trigger_sync(
            monkeypatch,
            {
                h.full("a"): {"exit_code": 0, "exit_class": "ok"},
                h.full("b"): {"exit_code": 0, "exit_class": "ok"},
            },
        )
        rc = crony.run_group(cfg, "g")
        assert rc == 0
        rec = _last_run(h.state, "g")
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
        _stub_trigger_sync(
            monkeypatch,
            {
                h.full("bad"): {"exit_code": 3, "exit_class": "fail"},
                h.full("good"): {"exit_code": 0, "exit_class": "ok"},
            },
        )
        rc = crony.run_group(cfg, "g")
        # Group orchestration succeeds even if a child failed.
        assert rc == 0
        rec = _last_run(h.state, "g")
        assert rec["jobs_run"][0]["name"] == h.full("bad")
        assert rec["jobs_run"][0]["exit_class"] == "fail"
        assert rec["jobs_run"][0]["exit_code"] == 3
        assert rec["jobs_run"][1]["name"] == h.full("good")
        assert rec["jobs_run"][1]["exit_class"] == "ok"

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
        rc = crony.run_group(cfg, "g", dry_run=True)
        assert rc == 0
        # No last-run.json for either group or child on dry-run.
        # The stub was never called.
        assert not (h.state / h.full("g") / "last-run.json").exists()
        assert not (h.state / h.full("a") / "last-run.json").exists()
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
            full_name: str, *, job_timeout: float, trigger_timeout: float
        ) -> dict[str, Any]:
            # Burn 11 seconds of monotonic time using a fake clock;
            # we monkeypatch time.monotonic to make this fast.
            return {"exit_code": 0, "exit_class": "ok"}

        # Simulate elapsed time by returning a moving monotonic value.
        clock = {"now": 0.0}
        real_monotonic = crony.time.monotonic

        def fake_monotonic() -> float:
            return real_monotonic() + clock["now"]

        monkeypatch.setattr(crony.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(crony, "_trigger_unit_sync", _slow)

        # Advance the fake clock forward in the stub so the second
        # iteration sees no remaining budget.
        called: list[str] = []

        def _stub_advance(
            full_name: str,
            *,
            job_timeout: float,
            trigger_timeout: float,
        ) -> dict[str, Any]:
            called.append(full_name)
            clock["now"] += 11.0  # past 1.05*(5+5) budget
            return {"exit_code": 0, "exit_class": "ok"}

        monkeypatch.setattr(crony, "_trigger_unit_sync", _stub_advance)

        rc = crony.run_group(cfg, "g")
        assert rc == 0
        rec = _last_run(h.state, "g")
        # Only `a` actually fired; `b` was budget-skipped.
        assert called == [h.full("a")]
        assert rec["jobs_run"][0]["name"] == h.full("a")
        assert rec["jobs_run"][0]["exit_class"] == "ok"
        assert rec["jobs_run"][1]["name"] == h.full("b")
        assert rec["jobs_run"][1]["exit_class"] == "timeout"


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
        return crony.parse_config(
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
            job="j",
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

        crony._dispatch_notify(result, "log content here", cfg.defaults)

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
        assert "Job:        j" in body
        assert "fail" in body
        assert "log content here" in body

    def test_records_smtp_failure_no_propagate(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["email"])

        smtp_cls = create_autospec(
            crony.smtplib.SMTP, side_effect=ConnectionRefusedError("no")
        )
        monkeypatch.setattr(crony.smtplib, "SMTP", smtp_cls)

        crony._dispatch_notify(result, "log", cfg.defaults)
        assert result.notifications["email"].sent is False
        assert "ConnectionRefusedError" in (
            result.notifications["email"].error or ""
        )

    def test_missing_smtp_password_records_error(self, tmp_path: Path) -> None:
        # Build a config that omits smtp_pass_*.
        cfg = crony.parse_config(
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
        crony._dispatch_notify(result, "log", cfg.defaults)
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
        cfg = crony.parse_config(
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

        crony._dispatch_notify(result, "log", cfg.defaults)
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
        return crony.parse_config(
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
            job="j",
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
        crony._dispatch_notify(result, "log content here", cfg.defaults)

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
        # attach_log=True -> body is the log content tail
        assert b"log content here" in captured["data"]

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
        crony._dispatch_notify(result, "log", cfg.defaults)
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
        cfg = crony.parse_config(
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
        crony._dispatch_notify(result, "log", cfg.defaults)
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
        return crony.parse_config(
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
            job="j",
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
        crony._dispatch_notify(result, "log content", cfg.defaults)

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
        crony.run_job(cfg, "fail")
        rec = _last_run(h.state, "fail")
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
        self.platform = platform
        self.agents = agents
        self.sysd = sysd


class TestPlistRendering:
    """_render_plist produces well-formed launchd plists."""

    def test_keyword_daily(self) -> None:
        plist = crony._render_plist("brew", "daily", None)
        assert "<key>Label</key>" in plist
        assert "<string>org.crony.brew</string>" in plist
        assert "<key>StartCalendarInterval</key>" in plist
        # daily -> 00:00
        assert "<key>Hour</key>" in plist
        assert "<integer>0</integer>" in plist

    def test_oncalendar_simple_time(self) -> None:
        plist = crony._render_plist("j", "*-*-* 03:15", None)
        assert "<key>Hour</key>" in plist
        assert "<integer>3</integer>" in plist
        assert "<integer>15</integer>" in plist

    def test_oncalendar_dow_with_time(self) -> None:
        plist = crony._render_plist("j", "Mon *-*-* 09:00", None)
        assert "<key>Weekday</key>" in plist
        assert "<integer>1</integer>" in plist  # Mon=1

    def test_oncalendar_first_of_month(self) -> None:
        plist = crony._render_plist("j", "*-*-01 03:00", None)
        assert "<key>Day</key>" in plist
        assert "<integer>1</integer>" in plist

    def test_interval(self) -> None:
        plist = crony._render_plist("j", None, "30min")
        assert "<key>StartInterval</key>" in plist
        assert "<integer>1800</integer>" in plist

    def test_step_pattern_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="step / range / list"):
            crony._render_plist("j", "*:0/15", None)

    def test_range_pattern_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="step / range / list"):
            crony._render_plist("j", "Mon..Fri *-*-* 09:00", None)

    def test_program_args_invoke_uv_with_absolute_path(
        self, monkeypatch: Any
    ) -> None:
        # launchd's per-agent PATH is /usr/bin:/bin:/usr/sbin:/sbin
        # which doesn't contain ~/.local/bin or homebrew's bin dir,
        # so the script's `env -S uv run --script` shebang fails to
        # find uv (exit 127). Render the absolute uv path into
        # ProgramArguments so the unit doesn't depend on PATH.
        monkeypatch.setattr(crony, "_uv_executable", lambda: "/abs/uv")
        monkeypatch.setattr(crony, "_crony_executable", lambda: "/abs/crony")
        plist = crony._render_plist("j", "daily", None)
        assert "<string>/abs/uv</string>" in plist
        assert "<string>run</string>" in plist
        assert "<string>--script</string>" in plist
        assert "<string>/abs/crony</string>" in plist
        assert "<string>j</string>" in plist


class TestSystemdRendering:
    def test_service_unit(self) -> None:
        svc = crony._render_systemd_service("brew")
        assert "[Unit]" in svc
        assert "[Service]" in svc
        assert "Type=oneshot" in svc
        assert "ExecStart=" in svc
        assert " run brew" in svc
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
        monkeypatch.setattr(crony, "_uv_executable", lambda: "/abs/uv")
        monkeypatch.setattr(crony, "_crony_executable", lambda: "/abs/crony")
        svc = crony._render_systemd_service("j")
        assert "ExecStart=/abs/uv run --script /abs/crony run j" in svc

    def test_uv_executable_errors_when_uv_not_on_path(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(crony.shutil, "which", lambda name: None)
        with pytest.raises(crony.PreconditionError, match="uv not found"):
            crony._uv_executable()


class TestApplyDarwin:
    def test_writes_plist_and_activates(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        result = crony.apply_one(cfg, "j")
        assert result == "added"
        plist_path = h.agents / f"org.crony.{h.full('j')}.plist"
        assert plist_path.exists()
        # Activated via launchctl (plus plutil validation)
        commands = [c[0] for c in h.calls]
        assert "plutil" in commands
        assert "launchctl" in commands
        # Hash stamp written
        assert (h.state / "installed" / f"{h.full('j')}.hash").exists()

    def test_idempotent_when_unchanged(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        h.calls.clear()
        result = crony.apply_one(cfg, "j")
        assert result == "unchanged"
        # No further launchctl invocations on no-op apply
        assert all(c[0] != "launchctl" for c in h.calls)

    def test_drift_triggers_update(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg1 = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg1, "j")
        cfg2 = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        h.calls.clear()
        result = crony.apply_one(cfg2, "j")
        assert result == "updated"
        plist = (h.agents / f"org.crony.{h.full('j')}.plist").read_text()
        assert "<integer>4</integer>" in plist


class TestApplyLinux:
    def test_writes_service_and_timer(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        assert (h.sysd / f"crony-{h.full('j')}.service").exists()
        assert (h.sysd / f"crony-{h.full('j')}.timer").exists()
        commands = [c[0] for c in h.calls]
        assert "systemctl" in commands


class TestApplyFullSync:
    def test_removes_orphans_on_no_arg_apply(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        # Pre-stamp an orphan
        inst = h.state / "installed"
        inst.mkdir()
        (inst / "old.hash").write_text("legacy\n")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=[])
        assert (h.state / "installed" / f"{h.full('j')}.hash").exists()
        assert not (h.state / "installed" / "old.hash").exists()

    def test_surgical_apply_leaves_orphans(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        inst = h.state / "installed"
        inst.mkdir()
        (inst / "old.hash").write_text("legacy\n")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.do_apply(jobs=["j"])
        assert (h.state / "installed" / "old.hash").exists()  # left alone


class TestDestroy:
    def test_factory_reset(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        assert (h.state / "installed" / f"{h.full('j')}.hash").exists()
        crony.do_destroy(jobs=[], purge_state=False)
        assert not (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        assert not (h.state / "installed" / f"{h.full('j')}.hash").exists()

    def test_surgical_destroy(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "a": {"command": "true", "schedule": "*-*-* 03:00"},
                    "b": {"command": "true", "schedule": "*-*-* 04:00"},
                }
            },
            default_target_jobs=["a", "b"],
        )
        crony.apply_one(cfg, "a")
        crony.apply_one(cfg, "b")
        crony.do_destroy(jobs=["a"], purge_state=False)
        assert not (h.state / "installed" / f"{h.full('a')}.hash").exists()
        assert (h.state / "installed" / f"{h.full('b')}.hash").exists()

    def test_purge_state_removes_state_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        # Pretend a run created state
        (h.state / h.full("j") / "run.log").parent.mkdir(
            parents=True, exist_ok=True
        )
        (h.state / h.full("j") / "run.log").write_text("...")
        crony.do_destroy(jobs=["j"], purge_state=True)
        assert not (h.state / h.full("j")).exists()

    def test_unknown_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown"):
            crony.do_destroy(jobs=["ghost"], purge_state=False)


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
            crony.parse_config({"defaults": {"job_timeout_sec": True}})

    def test_negative_default_timeout_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config({"defaults": {"job_timeout_sec": -5}})

    def test_zero_default_timeout_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config({"defaults": {"job_timeout_sec": 0}})

    def test_negative_default_attach_max_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config({"defaults": {"notify_attach_max_kb": -1}})

    def test_negative_default_log_keep_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config({"defaults": {"log_keep_runs": 0}})


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
        with pytest.raises(crony.ConfigError, match="must match"):
            crony.parse_config({"job": {bad_name: _job()}})

    @pytest.mark.parametrize(
        "good_name",
        ["a", "brew-update", "rust_update", "Job1", "x.y.z"],
    )
    def test_valid_job_name(self, good_name: str) -> None:
        cfg = crony.parse_config({"job": {good_name: _job()}})
        assert good_name in cfg.jobs

    def test_invalid_group_name(self) -> None:
        with pytest.raises(crony.ConfigError, match="must match"):
            crony.parse_config(
                {
                    "job": {"a": {"command": "true"}},
                    "job-group": {
                        "bad/name": {
                            "jobs": ["a"],
                            "schedule": "daily",
                        }
                    },
                }
            )

    def test_invalid_host_name(self) -> None:
        with pytest.raises(crony.ConfigError, match="must match"):
            crony.parse_config(
                {
                    "job": {"a": _job()},
                    "target": {"host": {"bad name": {"jobs": ["a"]}}},
                }
            )


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
            return real_path(p)

        monkeypatch.setattr(crony, "Path", fake_path)
        assert crony.linger_enabled(user="edp") is True


class TestSchedStateDarwin:
    def test_loaded_label_is_enabled(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_launchctl_print_disabled", lambda: "")
        monkeypatch.setattr(
            crony, "_launchctl_list", lambda: "-\t0\torg.crony.j\n"
        )
        assert crony._sched_state("j", "darwin") == "enabled"

    def test_disabled_record_takes_precedence(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            crony,
            "_launchctl_print_disabled",
            lambda: '"org.crony.j" => disabled',
        )
        monkeypatch.setattr(
            crony, "_launchctl_list", lambda: "-\t0\torg.crony.j\n"
        )
        assert crony._sched_state("j", "darwin") == "disabled"

    def test_unknown_when_neither_loaded_nor_disabled(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(crony, "_launchctl_print_disabled", lambda: "")
        monkeypatch.setattr(crony, "_launchctl_list", lambda: "")
        assert crony._sched_state("j", "darwin") == "unknown"


class TestSchedStateLinux:
    def test_enabled(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "enabled")
        assert crony._sched_state("j", "linux") == "enabled"

    def test_disabled(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "disabled")
        assert crony._sched_state("j", "linux") == "disabled"

    def test_unknown_on_empty(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "")
        assert crony._sched_state("j", "linux") == "unknown"


class TestConfigState:
    def test_missing_when_in_config_no_stamp(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        assert crony._config_state(cfg, "j", "darwin") == "missing"

    def test_synced_after_apply(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        assert crony._config_state(cfg, "j", "darwin") == "synced"

    def test_stale_when_config_changes(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg1 = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg1, "j")
        cfg2 = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        assert crony._config_state(cfg2, "j", "darwin") == "stale"

    def test_orphan_stamped_not_in_config(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        inst = h.state / "installed"
        inst.mkdir()
        # Stamp file follows the namespaced shape on disk.
        (inst / f"{h.full('old')}.hash").write_text("legacy\n")
        cfg = h.config({}, default_target_jobs=[])
        assert crony._config_state(cfg, "old", "darwin") == "orphan"


class TestEnableDisable:
    def test_enable_invokes_systemctl_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        h.calls.clear()
        crony.do_enable(jobs=["j"])
        cmd = next(c for c in h.calls if c[0] == "systemctl")
        assert cmd == [
            "systemctl",
            "--user",
            "enable",
            "--now",
            f"crony-{h.full('j')}.timer",
        ]

    def test_disable_invokes_launchctl_on_darwin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        h.calls.clear()
        crony.do_disable(jobs=["j"])
        verbs = [c[1] if len(c) > 1 else "" for c in h.calls]
        assert "unload" in verbs
        assert "disable" in verbs

    def test_unknown_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="not stamped"):
            crony.do_enable(jobs=["ghost"])

    def test_unknown_name_rejected_for_disable(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="not stamped"):
            crony.do_disable(jobs=["ghost"])

    def test_unscheduled_entry_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["g"],
        )
        crony.apply_one(cfg, "a")
        with pytest.raises(crony.UsageError, match="no schedule"):
            crony.do_enable(jobs=["a"])
        with pytest.raises(crony.UsageError, match="no schedule"):
            crony.do_disable(jobs=["a"])

    def test_trigger_invokes_launchctl_kickstart_on_darwin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        h.calls.clear()
        crony.do_trigger(jobs=["j"], wait=False, trigger_timeout=None)
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
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        h.calls.clear()
        crony.do_trigger(jobs=["j"], wait=False, trigger_timeout=None)
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
        with pytest.raises(crony.UsageError, match="not stamped"):
            crony.do_trigger(jobs=["ghost"], wait=False, trigger_timeout=None)

    def test_trigger_wait_maps_timeout_to_nonzero_exit(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `crony trigger --wait` must surface a non-zero exit code
        # when the job times out (exit_code is None for that class).
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
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
            crony.do_trigger(jobs=["j"], wait=True, trigger_timeout=None)
        assert exc.value.code == int(crony.ExitCode.TIMEOUT)

    def test_trigger_wait_maps_signal_to_128_plus_n(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
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
            crony.do_trigger(jobs=["j"], wait=True, trigger_timeout=None)
        assert exc.value.code == 137

    def test_trigger_wait_passes_through_command_exit_code(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "false", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
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
            crony.do_trigger(jobs=["j"], wait=True, trigger_timeout=None)
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
            crony.do_trigger(jobs=["j"], wait=False, trigger_timeout=10)

    def test_trigger_works_on_schedule_less_job(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Every entry installs a platform unit, including schedule-
        # less group-only jobs. trigger fires that unit directly.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["g"],
        )
        crony.apply_one(cfg, "a")
        h.calls.clear()
        crony.do_trigger(jobs=["a"], wait=False, trigger_timeout=None)
        cmd = next(c for c in h.calls if c[0] == "launchctl")
        assert cmd[1] == "kickstart"
        assert cmd[2].endswith(f"org.crony.{h.full('a')}")

    def test_apply_preserves_disabled_state_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        cfg1 = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg1, "j")
        monkeypatch.setattr(crony, "_systemd_is_enabled", lambda u: "disabled")
        cfg2 = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        h.calls.clear()
        crony.apply_one(cfg2, "j")
        verbs = [c[2] if len(c) > 2 else "" for c in h.calls]
        assert "daemon-reload" in verbs
        assert "enable" not in verbs


class TestStatusReport:
    def test_prints_table(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        monkeypatch.setattr(crony, "_sched_state", lambda n, p: "enabled")
        crony.do_status(jobs=[])
        out = capsys.readouterr().out
        assert "JOB" in out
        assert "CONFIG" in out
        assert "SCHED" in out
        assert "LAST" in out
        assert "j" in out
        assert "synced" in out

    def test_orphan_appears(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        inst = h.state / "installed"
        inst.mkdir()
        (inst / "ghost.hash").write_text("legacy\n")
        h.config({}, default_target_jobs=[])
        monkeypatch.setattr(crony, "_sched_state", lambda n, p: "enabled")
        crony.do_status(jobs=[])
        out = capsys.readouterr().out
        assert "ghost" in out
        assert "orphan" in out

    def test_long_names_keep_columns_aligned(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Names longer than the historical 30-char JOB column width
        # used to push later columns out of alignment. The width
        # should now adapt to the longest name actually printed.
        h = _ApplyHarness(tmp_path, monkeypatch)
        long_name = "this-is-a-deliberately-long-job-name-for-alignment"
        cfg = h.config(
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
        crony.apply_one(cfg, long_name)
        monkeypatch.setattr(crony, "_sched_state", lambda n, p: "enabled")
        crony.do_status(jobs=[])
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


# =============================================================================
# validate / audit / list / logs
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

    def test_orphan_warns(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        inst = h.state / "installed"
        inst.mkdir()
        (inst / "ghost.hash").write_text("legacy\n")
        h.config({}, default_target_jobs=[])
        with pytest.raises(SystemExit) as exc:
            crony.do_validate(bundle=None)
        assert exc.value.code == int(crony.ExitCode.WARNING)
        out = capsys.readouterr().out
        assert "orphans" in out

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
        inst = h.state / "installed"
        inst.mkdir()
        (inst / "ghost.hash").write_text("legacy\n")
        h.config({}, default_target_jobs=[])
        (h.cfg_dropin / "borgadm.toml").write_text(
            '[job.foo]\ncommand = "true"\nschedule = "daily"\n',
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


class TestAudit:
    def test_all_nominal(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        monkeypatch.setattr(crony, "_sched_state", lambda n, p: "enabled")
        last_run = h.state / h.full("j") / "last-run.json"
        last_run.parent.mkdir(parents=True, exist_ok=True)
        last_run.write_text('{"exit_class": "ok"}', encoding="utf-8")
        crony.do_audit(exclude_disabled=False, bundle=None)
        out = capsys.readouterr().out
        assert "all jobs nominal" in out

    def test_failed_last_run_flagged(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        monkeypatch.setattr(crony, "_sched_state", lambda n, p: "enabled")
        last_run = h.state / h.full("j") / "last-run.json"
        last_run.parent.mkdir(parents=True, exist_ok=True)
        last_run.write_text('{"exit_class": "fail"}', encoding="utf-8")
        with pytest.raises(crony.AuditFailedError):
            crony.do_audit(exclude_disabled=False, bundle=None)
        out = capsys.readouterr().out
        assert h.full("j") in out and "fail" in out

    def test_disabled_excluded_when_flag_set(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        monkeypatch.setattr(crony, "_sched_state", lambda n, p: "disabled")
        last_run = h.state / h.full("j") / "last-run.json"
        last_run.parent.mkdir(parents=True, exist_ok=True)
        last_run.write_text('{"exit_class": "ok"}', encoding="utf-8")
        with pytest.raises(crony.AuditFailedError):
            crony.do_audit(exclude_disabled=False, bundle=None)
        crony.do_audit(exclude_disabled=True, bundle=None)

    def test_bundle_filter_scopes_candidates(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Default bundle has a failing job; borgadm bundle has a
        # clean job. With --bundle borgadm, audit should pass
        # because the failing default job is out of scope.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony.apply_one(cfg, "j")
        last_run = h.state / h.full("j") / "last-run.json"
        last_run.parent.mkdir(parents=True, exist_ok=True)
        last_run.write_text('{"exit_class": "fail"}', encoding="utf-8")
        # borgadm bundle's job is clean (selected via [target.darwin]).
        (h.cfg_dropin / "borgadm.toml").write_text(
            '[job.k]\ncommand = "true"\nschedule = "*-*-* 04:00"\n'
            "\n"
            '[target.darwin]\njobs = ["k"]\n',
            encoding="utf-8",
        )
        # Apply borgadm.k so it's stamped + has a clean last-run.
        bundles = crony.load_all_bundles()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        crony.apply_one(borgadm.config, "k", bundle_name="borgadm")
        clean_last = h.state / "borgadm.k" / "last-run.json"
        clean_last.parent.mkdir(parents=True, exist_ok=True)
        clean_last.write_text('{"exit_class": "ok"}', encoding="utf-8")
        monkeypatch.setattr(crony, "_sched_state", lambda n, p: "enabled")
        crony.do_audit(exclude_disabled=False, bundle="borgadm")
        out = capsys.readouterr().out
        assert "all jobs nominal" in out
        assert "default.j" not in out

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_audit(exclude_disabled=False, bundle="ghost")

    def test_bundle_filter_includes_namespaced_orphans(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # An orphan stamp like `borgadm.gone.hash` -- borgadm's
        # bundle no longer defines `gone` -- still belongs to the
        # borgadm namespace and should surface under --bundle borgadm.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config({}, default_target_jobs=[])
        (h.cfg_dropin / "borgadm.toml").write_text(
            '[job.foo]\ncommand = "true"\nschedule = "daily"\n'
            "\n"
            '[target.darwin]\njobs = ["foo"]\n',
            encoding="utf-8",
        )
        inst = h.state / "installed"
        inst.mkdir()
        (inst / "borgadm.gone.hash").write_text("legacy\n", encoding="utf-8")
        monkeypatch.setattr(crony, "_sched_state", lambda n, p: "enabled")
        with pytest.raises(crony.AuditFailedError):
            crony.do_audit(exclude_disabled=False, bundle="borgadm")
        out = capsys.readouterr().out
        assert "borgadm.gone" in out
        assert "orphan" in out

    def test_bundle_filter_skips_linger_warning_on_linux(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # linger is a host-wide concern; --bundle scopes the audit
        # away from it so the report stays focused on that bundle.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config({}, default_target_jobs=[])
        monkeypatch.setattr(crony, "linger_enabled", lambda user=None: False)
        # Should not raise: no jobs in scope, linger warning skipped.
        crony.do_audit(exclude_disabled=False, bundle=crony.DEFAULT_BUNDLE_NAME)
        out = capsys.readouterr().out
        assert "linger" not in out
        assert "all jobs nominal" in out


class TestListSubcommand:
    def test_prints_each_entry(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "a": {"command": "true", "schedule": "*-*-* 03:00"},
                    "b": {"command": "true"},
                },
                "job-group": {"g": {"jobs": ["b"], "schedule": "*-*-* 04:00"}},
            },
            default_target_jobs=["a", "g"],
        )
        crony.do_list(bundle=None)
        out = capsys.readouterr().out
        assert "a" in out
        assert "b" in out
        assert "g" in out
        assert "group" in out
        assert "job" in out

    def test_bundle_filter_restricts_output(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"alpha": {"command": "true", "schedule": "daily"}}},
            default_target_jobs=["alpha"],
        )
        (h.cfg_dropin / "borgadm.toml").write_text(
            '[job.bravo]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        crony.do_list(bundle="borgadm")
        out = capsys.readouterr().out
        assert "borgadm.bravo" in out
        assert "alpha" not in out

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="unknown bundle"):
            crony.do_list(bundle="ghost")


class TestLogs:
    def test_n_lines(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state / h.full("j") / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "\n".join(f"line {i}" for i in range(20)) + "\n",
            encoding="utf-8",
        )
        crony.do_logs(name="j", n=5, since=None, tail=False, path=False)
        out = capsys.readouterr().out
        assert "line 19" in out
        assert "line 15" in out
        assert "line 14" not in out

    def test_missing_log_raises(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(crony.UsageError, match="no log"):
            crony.do_logs(
                name="ghost", n=10, since=None, tail=False, path=False
            )

    def test_path_prints_file_path(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state / h.full("j") / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("hello\n", encoding="utf-8")
        crony.do_logs(name="j", n=0, since=None, tail=False, path=True)
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
        crony.do_logs(name="j", n=0, since=None, tail=False, path=True)
        out = capsys.readouterr().out.strip()
        expected = h.state / h.full("j") / "run.log"
        assert out == str(expected)

    def test_since_filters_old_runs(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        log = h.state / h.full("j") / "run.log"
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
        crony.do_logs(name="j", n=0, since="1h", tail=False, path=False)
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
            '[job.j]\ncommand = "true"\nschedule = "daily"\n',
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
            '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        (dropin / "borgadm.toml").write_text(
            '[job.prune]\ncommand = "true"\nschedule = "daily"\n',
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
            '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        bundles = crony.load_all_bundles()
        assert [b.name for b in bundles.bundles] == ["private"]

    def test_no_configs_at_all_raises(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(crony.ConfigError, match="no config"):
            crony.load_all_bundles()

    def test_lex_sorted_dropin_order(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file, dropin = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text("", encoding="utf-8")
        for name in ("zulu", "alpha", "mike"):
            (dropin / f"{name}.toml").write_text(
                f'[job.j_{name}]\ncommand = "true"\nschedule = "daily"\n',
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
            '[job.good]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        (dropin / "broken.toml").write_text(
            "this is not [valid toml",
            encoding="utf-8",
        )
        (dropin / "ok.toml").write_text(
            '[job.fine]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        with caplog.at_level(logging.ERROR, logger=crony.logger.name):
            bundles = crony.load_all_bundles()
        names = sorted(b.name for b in bundles.bundles)
        assert names == ["default", "ok"]
        # The broken bundle's path is in the error output.
        assert any("broken.toml" in r.message for r in caplog.records)

    def test_invalid_filename_rejected(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # `config/has.dot.toml` -> stem "has.dot" -> not a valid
        # bundle name (contains the namespace separator).
        cfg_file, dropin = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text(
            '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        (dropin / "has.dot.toml").write_text(
            '[job.k]\ncommand = "true"\nschedule = "daily"\n',
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
            '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        (dropin / "default.toml").write_text(
            '[job.k]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        with caplog.at_level(logging.ERROR, logger=crony.logger.name):
            bundles = crony.load_all_bundles()
        names = [b.name for b in bundles.bundles]
        assert names == ["default"]
        # The colliding dropin is referenced in the error.
        assert any("default.toml" in r.message for r in caplog.records)


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
            '[job.daily-update]\ncommand = "true"\nschedule = "daily"\n',
            encoding="utf-8",
        )
        (cfg_dropin / "borgadm.toml").write_text(
            '[job.daily-update]\ncommand = "true"\nschedule = "daily"\n',
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
        sd = h.state / full
        sd.mkdir()

        def _stub_trigger(name: str, platform: str) -> None:
            # Pretend the runner ran and wrote a fresh result.
            (sd / "last-run.json").write_text(
                '{"ended_at": "2099-01-01T00:00:00-08:00",'
                ' "exit_code": 0, "exit_class": "ok"}',
                encoding="utf-8",
            )

        monkeypatch.setattr(crony, "_trigger_unit", _stub_trigger)
        rec = crony._trigger_unit_sync(
            full, job_timeout=5.0, trigger_timeout=5.0
        )
        assert rec["exit_code"] == 0
        assert rec["exit_class"] == "ok"

    def test_trigger_start_timeout_raises(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        (h.state / full).mkdir()
        monkeypatch.setattr(crony, "_trigger_unit", lambda *a, **kw: None)
        with pytest.raises(crony.TriggerStartTimeout, match="never produced"):
            crony._trigger_unit_sync(full, job_timeout=5.0, trigger_timeout=1.0)

    def test_stale_last_run_json_loops_until_fresh_arrives(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Pre-existing last-run.json from a prior run (ended_at
        # before the trigger). The waiter should NOT accept it as
        # the answer; it should keep waiting until either a fresh
        # one appears or the trigger_timeout fires.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.state / full
        sd.mkdir()
        (sd / "last-run.json").write_text(
            '{"ended_at": "1970-01-01T00:00:00-00:00",'
            ' "exit_code": 0, "exit_class": "ok"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(crony, "_trigger_unit", lambda *a, **kw: None)
        with pytest.raises(crony.TriggerStartTimeout):
            crony._trigger_unit_sync(full, job_timeout=5.0, trigger_timeout=1.0)

    def test_subsecond_run_is_recognized_as_fresh(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Regression: pre_trigger captured at microsecond precision
        # but the runner's `_now_iso()` truncates to whole seconds.
        # A run that completes within the same second as the trigger
        # would have ended_at < pre_trigger after parse, and the
        # waiter would loop until trigger_timeout. The fix: capture
        # pre_trigger at the same precision as ended_at.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.state / full
        sd.mkdir()

        def _stub_trigger(name: str, platform: str) -> None:
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
            full, job_timeout=5.0, trigger_timeout=2.0
        )
        assert rec["exit_class"] == "fail"
        assert rec["exit_code"] == 4


class TestLifecycleSmoke:
    """End-to-end smoke covering init -> edit -> validate -> apply ->
    status -> destroy via the public function entry points. Catches
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
        crony.do_apply(jobs=[])
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        # status -> prints the synced/enabled tuple (sched stub)
        monkeypatch.setattr(crony, "_sched_state", lambda n, p: "enabled")
        capsys.readouterr()  # drop earlier output
        crony.do_status(jobs=[])
        out = capsys.readouterr().out
        assert "synced" in out
        # destroy -> factory reset
        crony.do_destroy(jobs=[], purge_state=False)
        assert not (h.agents / f"org.crony.{h.full('j')}.plist").exists()


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
