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
import re
import sys
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


class TestParseDefaults:
    def test_empty_config_uses_defaults(self) -> None:
        cfg = crony.parse_config({})
        assert cfg.defaults.notify_channel == "log-only"
        assert cfg.defaults.timeout_sec == 1800
        assert cfg.defaults.notify_attach_log is True
        assert cfg.defaults.notify_email is None
        assert cfg.defaults.notify_ntfy is None

    def test_override_defaults(self) -> None:
        cfg = crony.parse_config(
            {
                "defaults": {
                    "notify_channel": "ntfy",
                    "notify_attach_log": False,
                    "notify_attach_max_kb": 512,
                    "timeout_sec": 3600,
                    "log_keep_runs": 50,
                }
            }
        )
        assert cfg.defaults.notify_channel == "ntfy"
        assert cfg.defaults.notify_attach_log is False
        assert cfg.defaults.notify_attach_max_kb == 512
        assert cfg.defaults.timeout_sec == 3600
        assert cfg.defaults.log_keep_runs == 50

    def test_invalid_notify_channel(self) -> None:
        with pytest.raises(
            crony.ConfigError, match="notify_channel must be one of"
        ):
            crony.parse_config(
                {"defaults": {"notify_channel": "carrier-pigeon"}}
            )

    def test_notify_email_subsection(self) -> None:
        cfg = crony.parse_config(
            {
                "defaults": {
                    "notify": {
                        "email": {
                            "to": "edp@example.com",
                            "smtp_host": "smtp.example.com",
                            "smtp_user": "edp",
                            "smtp_port": 465,
                            "smtp_starttls": False,
                            "smtp_pass_keychain_service": "crony-smtp",
                            "smtp_pass_keychain_account": "edp",
                        }
                    }
                }
            }
        )
        assert cfg.defaults.notify_email is not None
        assert cfg.defaults.notify_email.to == "edp@example.com"
        assert cfg.defaults.notify_email.smtp_port == 465
        assert cfg.defaults.notify_email.smtp_starttls is False
        assert (
            cfg.defaults.notify_email.smtp_pass_keychain_service == "crony-smtp"
        )
        assert cfg.defaults.notify_email.smtp_pass_keychain_account == "edp"

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
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_keychain_service": "ntfy-token",
                        }
                    }
                }
            }
        )
        assert cfg.defaults.notify_ntfy is not None
        assert cfg.defaults.notify_ntfy.url == "https://ntfy.example.com/x"

    def test_notify_unknown_subsection(self) -> None:
        with pytest.raises(crony.ConfigError, match="not a known channel"):
            crony.parse_config({"defaults": {"notify": {"sms": {}}}})


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
        with pytest.raises(
            crony.ConfigError, match="notify_channel must be one of"
        ):
            crony.parse_config(self._cfg(_job(notify_channel="carrier-pigeon")))

    def test_negative_timeout(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config(self._cfg(_job(timeout_sec=-1)))

    def test_zero_timeout(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config(self._cfg(_job(timeout_sec=0)))

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

    def test_no_schedule_no_interval(self) -> None:
        with pytest.raises(crony.ConfigError, match="must define exactly one"):
            crony.parse_config(
                {
                    "job": {"a": {"command": "true"}},
                    "job-group": {"g": {"jobs": ["a"]}},
                }
            )

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

    def test_group_references_undefined_job(self) -> None:
        with pytest.raises(crony.ConfigError, match="undefined job"):
            crony.parse_config(
                {"job-group": {"g": {"jobs": ["nope"], "schedule": "daily"}}}
            )

    def test_group_references_another_group_v1_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="another group"):
            crony.parse_config(
                {
                    "job": {"a": {"command": "true"}},
                    "job-group": {
                        "g1": {"jobs": ["a"], "schedule": "daily"},
                        "g2": {"jobs": ["g1"], "schedule": "daily"},
                    },
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

    def test_unreferenced_group_only_job_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="not referenced"):
            crony.parse_config({"job": {"a": {"command": "true"}}})

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
            'notify_channel = "log-only"\n'
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
                "defaults": {"notify_channel": "log-only"},
                "job": {"a": _job(notify_channel="email")},
                "target": {
                    "darwin": {
                        "jobs": ["a"],
                        "notify_channel": "ntfy",
                    }
                },
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert (
            crony.resolved_notify_channel(cfg, target, cfg.jobs["a"]) == "ntfy"
        )

    def test_notify_job_overrides_defaults(self) -> None:
        cfg = crony.parse_config(
            {
                "defaults": {"notify_channel": "log-only"},
                "job": {"a": _job(notify_channel="email")},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert (
            crony.resolved_notify_channel(cfg, target, cfg.jobs["a"]) == "email"
        )

    def test_notify_default_fallback(self) -> None:
        cfg = crony.parse_config(
            {
                "defaults": {"notify_channel": "ntfy"},
                "job": {"a": _job()},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert (
            crony.resolved_notify_channel(cfg, target, cfg.jobs["a"]) == "ntfy"
        )

    def test_timeout_cascade(self) -> None:
        cfg = crony.parse_config(
            {
                "defaults": {"timeout_sec": 100},
                "job": {"a": _job(timeout_sec=200)},
                "target": {"darwin": {"jobs": ["a"], "timeout_sec": 300}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_timeout_sec(cfg, target, cfg.jobs["a"]) == 300

    def test_timeout_default_fallback(self) -> None:
        cfg = crony.parse_config(
            {
                "defaults": {"timeout_sec": 100},
                "job": {"a": _job()},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = crony.resolve_target(cfg, "h", "darwin")
        assert crony.resolved_timeout_sec(cfg, target, cfg.jobs["a"]) == 100


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
        crony.do_init(force=False)
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
        crony.do_init(force=False)
        assert cfg_file.parent.is_dir()

    def test_refuses_to_overwrite_without_force(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text("user content", encoding="utf-8")
        with pytest.raises(crony.UsageError, match="already exists"):
            crony.do_init(force=False)
        # File untouched.
        assert cfg_file.read_text() == "user content"

    def test_overwrites_with_force(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text("user content", encoding="utf-8")
        crony.do_init(force=True)
        body = cfg_file.read_text(encoding="utf-8")
        assert "user content" not in body
        assert "[defaults]" in body

    def test_template_is_ascii_only(self) -> None:
        """All persistent files in this repo are ASCII; the template
        we ship as a starting point must be too.
        """
        crony._DEFAULT_CONFIG_TEMPLATE.encode("ascii")  # raises if not

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
        monkeypatch.setenv("CRONY_STATE_DIR", str(state))
        monkeypatch.setenv("CRONY_CONFIG_DIR", str(cfg_dir))
        monkeypatch.setenv("CRONY_CONFIG_FILE", str(cfg_file))
        monkeypatch.setattr(crony, "STATE_DIR", state)
        monkeypatch.setattr(crony, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony, "current_host", lambda: "test-host")
        monkeypatch.setattr(crony, "current_platform", lambda: "darwin")
        self.state = state
        self.cfg_file = cfg_file

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
    text = (state / name / "last-run.json").read_text()
    return _cast_dict(text)


def _cast_dict(text: str) -> dict[str, Any]:
    """Read JSON into a typed dict for test assertions."""
    import json as _json

    out = _json.loads(text)
    assert isinstance(out, dict)
    return out


class TestRuntimeEnv:
    """`_runtime_env` carries forward shell-essential vars from the
    invoking process (where launchd / systemd populate them) so a
    wrapped command can reach things like the user's ssh-agent."""

    def _job(self) -> Any:
        return crony.Job(name="j", command="true")

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
        assert rec["gate_exit"] is None

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
        assert not (h.state / "ok" / "last-run.json").exists()


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
        assert rec["gate_exit"] == 0

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
        assert rec["gate_exit"] == 1
        # Main command never ran -> exit_code recorded as 0 placeholder
        assert rec["exit_code"] == 0
        log = (h.state / "g" / "run.log").read_text()
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
        assert rec["gate_exit"] is None


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
        sd = h.state / "j"
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
    def test_log_only_is_noop(self, tmp_path: Path, monkeypatch: Any) -> None:
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
        assert rec["notify_channel"] == "log-only"
        assert rec["notify_sent"] is False
        assert rec["notify_error"] is None

    def test_unconfigured_channel_records_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # ntfy channel selected but no [defaults.notify.ntfy] section.
        # The runner records the misconfiguration in notify_error
        # rather than letting the missing config crash the run.
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "defaults": {"notify_channel": "ntfy"},
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
        assert rec["notify_channel"] == "ntfy"
        assert rec["notify_sent"] is False
        assert "not configured" in (rec["notify_error"] or "")


class TestRunGroup:
    def test_group_runs_each_child(
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
        rc = crony.run_group(cfg, "g")
        assert rc == 0
        rec = _last_run(h.state, "g")
        names = [c["name"] for c in rec["jobs_run"]]
        assert names == ["a", "b"]
        # Each child ran via subprocess invocation of `crony run`,
        # so they each wrote their own last-run.json.
        for child in ("a", "b"):
            assert (h.state / child / "last-run.json").exists()

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
        rc = crony.run_group(cfg, "g")
        # Group orchestration succeeds even if a child failed.
        assert rc == 0
        rec = _last_run(h.state, "g")
        assert rec["jobs_run"][0]["name"] == "bad"
        assert rec["jobs_run"][0]["exit_class"] == "fail"
        assert rec["jobs_run"][0]["exit_code"] == 3
        assert rec["jobs_run"][1]["name"] == "good"
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
        rc = crony.run_group(cfg, "g", dry_run=True)
        assert rc == 0
        # No last-run.json for either group or child on dry-run
        assert not (h.state / "g" / "last-run.json").exists()
        assert not (h.state / "a" / "last-run.json").exists()


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
                    "notify_channel": "email",
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
                "job": {"j": _job(notify_channel="email")},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )

    def _make_failed_result(self) -> Any:
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
            gate_exit=None,
            log_path="/tmp/run.log",
            log_bytes_this_run=42,
            notify_channel="email",
            notify_sent=False,
            notify_error=None,
        )

    def test_sends_via_smtp(self, tmp_path: Path, monkeypatch: Any) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result()

        # autospec exercises the real SMTP signature; the resulting
        # mock instance plays the context-manager role with the same
        # return-value contract.
        smtp_cls = create_autospec(crony.smtplib.SMTP)
        smtp_inst = smtp_cls.return_value
        smtp_inst.__enter__.return_value = smtp_inst
        smtp_inst.__exit__.return_value = None
        monkeypatch.setattr(crony.smtplib, "SMTP", smtp_cls)

        crony._dispatch_notify(result, "log content here", cfg.defaults)

        assert result.notify_sent is True
        assert result.notify_error is None
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
        result = self._make_failed_result()

        smtp_cls = create_autospec(
            crony.smtplib.SMTP, side_effect=ConnectionRefusedError("no")
        )
        monkeypatch.setattr(crony.smtplib, "SMTP", smtp_cls)

        crony._dispatch_notify(result, "log", cfg.defaults)
        assert result.notify_sent is False
        assert "ConnectionRefusedError" in (result.notify_error or "")

    def test_missing_smtp_password_records_error(self, tmp_path: Path) -> None:
        # Build a config that omits smtp_pass_*.
        cfg = crony.parse_config(
            {
                "defaults": {
                    "notify_channel": "email",
                    "notify": {
                        "email": {
                            "to": "y@e.com",
                            "smtp_host": "x",
                            "smtp_user": "u",
                        }
                    },
                },
                "job": {"j": _job(notify_channel="email")},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )
        result = self._make_failed_result()
        crony._dispatch_notify(result, "log", cfg.defaults)
        assert result.notify_sent is False
        assert "no SMTP password" in (result.notify_error or "")


class TestNtfyNotify:
    """ntfy channel routing via urllib (mocked)."""

    def _common_config(self, tmp_path: Path) -> Any:
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        return crony.parse_config(
            {
                "defaults": {
                    "notify_channel": "ntfy",
                    "notify_attach_log": True,
                    "notify_attach_max_kb": 1,
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                },
                "job": {"j": _job(notify_channel="ntfy")},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )

    def _make_failed_result(self) -> Any:
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
            gate_exit=None,
            log_path="/tmp/run.log",
            log_bytes_this_run=42,
            notify_channel="ntfy",
            notify_sent=False,
            notify_error=None,
        )

    def test_sends_via_urllib(self, tmp_path: Path, monkeypatch: Any) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result()

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

        assert result.notify_sent is True
        assert result.notify_error is None
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
        result = self._make_failed_result()

        # urllib raises HTTPError for 4xx/5xx responses; mirror that
        # so the test reflects real-world failure.
        def _raise(req: Any, timeout: Any = None) -> Any:
            raise crony.urllib.error.HTTPError(
                req.full_url, 503, "service unavailable", {}, None
            )

        monkeypatch.setattr(crony.urllib.request, "urlopen", _raise)
        crony._dispatch_notify(result, "log", cfg.defaults)
        assert result.notify_sent is False
        assert "503" in (result.notify_error or "")


class TestNotifyTestSubcommand:
    """`crony notify-test` synth event invocation."""

    def test_log_only_is_quiet(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        # log-only path should not raise
        crony.do_notify_test(channel=None)

    def test_unconfigured_channel_raises_config_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Missing config bits should surface as CONFIG (3), not the
        # generic ERROR (4) -- the user can act on config errors.
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {"defaults": {"notify_channel": "email"}},
            default_target_jobs=[],
        )
        with pytest.raises(crony.ConfigError, match="notify-test failed"):
            crony.do_notify_test(channel=None)


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
            crony.parse_config({"defaults": {"timeout_sec": True}})

    def test_negative_default_timeout_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config({"defaults": {"timeout_sec": -5}})

    def test_zero_default_timeout_rejected(self) -> None:
        with pytest.raises(crony.ConfigError, match="positive"):
            crony.parse_config({"defaults": {"timeout_sec": 0}})

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


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
