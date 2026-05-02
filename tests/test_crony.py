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
                            "smtp_pass_keychain": "crony-smtp",
                        }
                    }
                }
            }
        )
        assert cfg.defaults.notify_email is not None
        assert cfg.defaults.notify_email.to == "edp@example.com"
        assert cfg.defaults.notify_email.smtp_port == 465
        assert cfg.defaults.notify_email.smtp_starttls is False

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
                            "token_keychain": "ntfy-token",
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
