#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "pytest-cov", "tomlkit", "pydantic>=2"]
# ///
# This is AI generated code

"""Unit tests for crony.config."""

import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_crony import (  # noqa: E402
    _assert_errored_host_target,
    _assert_errored_job,
    _assert_errored_job_group,
    _assert_errored_platform_target,
    _bundle_set,
    _email_block,
    _inject_uuids,
    _isolate_home,  # noqa: E402, F401
    _job,
    _ntfy_block,
    _parse,
    _RunnerHarness,
    _uuid_toml,
)

from crony import commands as crony_commands  # noqa: E402
from crony import config as crony_config  # noqa: E402
from crony import notify as crony_notify  # noqa: E402
from crony import paths as crony_paths  # noqa: E402
from crony import platform as crony_platform  # noqa: E402
from crony.config import (  # noqa: E402
    NOTIFY_INHERIT_TOKEN,
    JobFlags,
    MaskReason,
    TomlBundle,
    TomlBundleConfig,
    TomlConfig,
)
from crony.errors import (  # noqa: E402
    ConfigError,
)
from crony.unit import (  # noqa: E402
    Interval,
    PriorityClass,
    Schedule,
)

_script_path = REPO_ROOT / "src" / "crony" / "config.py"


class TestParseDefaults:
    def test_empty_config_uses_defaults(self) -> None:
        cfg = _parse({})
        assert cfg.defaults.notify_channels == []
        assert cfg.defaults.job_timeout_sec == 1800
        assert cfg.defaults.notify_attach_log is True
        assert cfg.defaults.notify_channel_defs == {}
        assert cfg.defaults.priority is None
        assert cfg.defaults.keep_awake is False
        assert cfg.defaults.env == {}

    def test_override_defaults(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify_attach_log": False,
                    "notify_attach_max_kb": 512,
                    "job_timeout_sec": 3600,
                    "log_keep_runs": 50,
                    "priority": "high",
                    "keep_awake": True,
                    "env": {"PATH": "$HOME/.local/bin:$PATH"},
                    "notify": {"ntfy": _ntfy_block()},
                }
            }
        )
        assert cfg.defaults.notify_channels == ["ntfy"]
        assert cfg.defaults.notify_attach_log is False
        assert cfg.defaults.notify_attach_max_kb == 512
        assert cfg.defaults.job_timeout_sec == 3600
        assert cfg.defaults.log_keep_runs == 50
        assert cfg.defaults.priority == PriorityClass.HIGH
        assert cfg.defaults.keep_awake is True
        assert cfg.defaults.env == {"PATH": "$HOME/.local/bin:$PATH"}
        assert "ntfy" in cfg.defaults.notify_channel_defs

    def test_listed_channel_must_be_defined(self) -> None:
        # Listing a channel that has no [defaults.notify.<name>]
        # block is a config error -- the dispatcher would have
        # nothing to send through.
        with pytest.raises(ConfigError, match="not defined"):
            _parse({"defaults": {"notify_channels": ["carrier-pigeon"]}})

    def test_duplicate_notify_channels_rejected(self) -> None:
        with pytest.raises(ConfigError, match="listed twice"):
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
        with pytest.raises(ConfigError, match="unknown key"):
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
        with pytest.raises(ConfigError, match="required"):
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
        with pytest.raises(ConfigError, match="transport"):
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
        with pytest.raises(ConfigError, match="transport"):
            _parse(
                {
                    "defaults": {
                        "notify": {"carrier-pigeon": {"transport": "carrier"}}
                    }
                }
            )

    def test_reserved_email_header_rejected(self) -> None:
        with pytest.raises(ConfigError, match="cannot be overridden"):
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
        with pytest.raises(ConfigError, match="cannot be overridden"):
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
        with pytest.raises(ConfigError, match="cannot be overridden"):
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
            "only valid with 'gate-script'",
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
        assert cfg.jobs["j"].timing == Interval.from_str("1h30min")

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
            "notify-channels",
        )

    def test_negative_timeout(self) -> None:
        _assert_errored_job(self._cfg(_job(job_timeout_sec=-1)), "j", ">= 0")

    def test_zero_timeout_means_no_cap(self) -> None:
        # 0 is the "no wallclock cap" sentinel, not an error.
        cfg = _parse(self._cfg(_job(job_timeout_sec=0)))
        assert cfg.jobs["j"].job_timeout_sec == 0

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
        assert cfg.jobs["a"].timing is None

    def test_interactive_does_not_tag_platforms_at_parse(self) -> None:
        # The darwin restriction is applied at selection time now, not
        # by mutating the job's platforms during parse.
        cfg = _parse(self._cfg(_job(interactive=True)))
        assert cfg.jobs["j"].interactive is True
        assert cfg.jobs["j"].platforms == []

    def test_interactive_explicit_darwin_platform_ok(self) -> None:
        cfg = _parse(self._cfg(_job(interactive=True, platforms=["darwin"])))
        assert cfg.jobs["j"].interactive is True

    def test_interactive_with_other_platform_accepted(self) -> None:
        # No longer a parse error: the job parses and is masked off
        # non-darwin at selection (and off the explicit linux too).
        cfg = _parse(self._cfg(_job(interactive=True, platforms=["linux"])))
        assert cfg.jobs["j"].interactive is True
        assert cfg.jobs["j"].platforms == ["linux"]

    def test_interactive_with_multi_platform_accepted(self) -> None:
        cfg = _parse(
            self._cfg(_job(interactive=True, platforms=["darwin", "linux"]))
        )
        assert cfg.jobs["j"].interactive is True

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
        assert cfg.job_groups["g"].timing == Schedule.from_str("*-*-* 03:00")

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
        assert cfg.job_groups["g"].timing is None

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
        cfg = TomlBundleConfig.from_raw({"job": {"j": _job()}})
        assert "j" in cfg.errored_jobs
        assert "uuid" in cfg.errored_jobs["j"]
        assert "crony config update" in cfg.errored_jobs["j"]
        assert "j" not in cfg.jobs

    def test_group_missing_uuid_is_errored(self) -> None:
        cfg = TomlBundleConfig.from_raw(
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
    bundle's TomlBundleConfig.from_raw. Both entries sharing the
    duplicate UUID are demoted into the errored maps with the same
    message -- the user sees the conflict on every side, not just
    one, and other bundles/entries remain operational.
    """

    GOOD = "aabbccdd-1234-5678-9abc-aabbccddeeff"

    def _write_and_load(self, tmp_path: Path, body: str) -> Any:
        path = tmp_path / "bundle.toml"
        path.write_text(body, encoding="utf-8")
        return TomlBundle.load("default", path)

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
        with pytest.raises(ConfigError, match="unknown key"):
            _parse(
                {
                    "job": {"a": _job()},
                    "target": {"darwin": {"jobs": ["a"], "surprise": "x"}},
                }
            )


class TestValidateConfig:
    def test_name_collision(self) -> None:
        with pytest.raises(ConfigError, match="name collision"):
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
        assert cfg.job_groups["leaf"].timing is None
        assert cfg.job_groups["root"].timing == Schedule.from_str("daily")

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
        monkeypatch.setattr(crony_platform, "current_platform", lambda: "linux")
        monkeypatch.setattr(crony_platform, "current_host", lambda: "host-l")
        cfg = _parse(
            {
                "job": {
                    "a": _job(platforms=["darwin"]),
                },
                "target": {"linux": {"jobs": ["a"]}},
            }
        )
        # Parse succeeds; selection on linux excludes `a`.
        target = cfg.resolve_target("host-l", "linux")
        sel_jobs, _ = cfg.selected_jobs_and_groups(target)
        assert "a" not in sel_jobs


class TestUnknownTopLevel:
    def test_unknown_toplevel_rejected(self) -> None:
        with pytest.raises(ConfigError, match="unknown"):
            _parse({"surprise": {}})


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
        cfg = TomlBundleConfig.load(f)
        assert "brew-update" in cfg.jobs
        assert cfg.jobs["brew-update"].timing == Schedule.from_str(
            "*-*-* 03:15"
        )

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            TomlBundleConfig.load(tmp_path / "absent.toml")

    def test_bad_toml(self, tmp_path: Path) -> None:
        f = tmp_path / "config.toml"
        f.write_text("this is not [toml")
        with pytest.raises(ConfigError, match="TOML parse error"):
            TomlBundleConfig.load(f)


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
        target = cfg.resolve_target("my-host", "darwin")
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
        target = cfg.resolve_target("other-host", "darwin")
        assert target is not None
        assert target.jobs == ["a"]
        assert target.kind == "platform"

    def test_no_target_returns_none(self) -> None:
        cfg = _parse({})
        assert cfg.resolve_target("h", "darwin") is None

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
        target = cfg.resolve_target("h", "darwin")
        jobs, groups = cfg.selected_jobs_and_groups(target)
        assert jobs == {"a", "b", "c"}
        assert groups == {"g"}

    def test_selected_for_no_target_is_empty(self) -> None:
        cfg = _parse({})
        jobs, groups = cfg.selected_jobs_and_groups(None)
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
        target = cfg.resolve_target("h", "darwin")
        assert cfg.resolved_notify_channels(target, cfg.jobs["a"]) == ["ntfy"]

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
        target = cfg.resolve_target("h", "darwin")
        assert cfg.resolved_notify_channels(target, cfg.jobs["a"]) == ["email"]

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
        target = cfg.resolve_target("h", "darwin")
        assert cfg.resolved_notify_channels(target, cfg.jobs["a"]) == ["ntfy"]

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
        target = cfg.resolve_target("h", "darwin")
        assert cfg.resolved_notify_channels(target, cfg.jobs["a"]) == []

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
        target = cfg.resolve_target("h", "darwin")
        assert cfg.resolved_notify_channels(target, cfg.jobs["a"]) == [
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
        assert cfg.resolved_job_timeout_sec(cfg.jobs["a"]) == 200

    def test_target_rejects_job_timeout_sec(self) -> None:
        # Targets deliberately have no timeout knob: timeouts are a
        # per-leaf-job concern. An attempt to set one in the target
        # block must surface as a config error, not silently no-op.
        with pytest.raises(ConfigError, match="unknown key"):
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
        assert cfg.resolved_job_timeout_sec(cfg.jobs["a"]) == 100


class TestNotifyInherit:
    """`notify_channels = ["default"]` inherit sentinel: a non-default
    bundle notifies as the default bundle would, and inherits it
    implicitly when it omits notify config. The sentinel may also be
    combined with explicit siblings (the resolved set is their union,
    de-duped).
    """

    def test_implicit_default_for_nondefault_bundle(self) -> None:
        cfg = TomlBundleConfig.from_raw(
            _inject_uuids({"job": {"a": _job()}}), bundle_name="borgadm"
        )
        assert cfg.defaults.notify_channels == [NOTIFY_INHERIT_TOKEN]

    def test_implicit_default_with_defaults_but_no_notify(self) -> None:
        cfg = TomlBundleConfig.from_raw(
            _inject_uuids(
                {"defaults": {"job_timeout_sec": 60}, "job": {"a": _job()}}
            ),
            bundle_name="borgadm",
        )
        assert cfg.defaults.notify_channels == [NOTIFY_INHERIT_TOKEN]

    def test_default_bundle_stays_empty(self) -> None:
        cfg = TomlBundleConfig.from_raw(_inject_uuids({"job": {"a": _job()}}))
        assert cfg.defaults.notify_channels == []

    def test_explicit_empty_opts_out(self) -> None:
        cfg = TomlBundleConfig.from_raw(
            _inject_uuids(
                {"defaults": {"notify_channels": []}, "job": {"a": _job()}}
            ),
            bundle_name="borgadm",
        )
        assert cfg.defaults.notify_channels == []

    def test_explicit_token_in_nondefault_ok(self) -> None:
        cfg = TomlBundleConfig.from_raw(
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
        with pytest.raises(ConfigError, match="cannot inherit its own"):
            TomlBundleConfig.from_raw(
                _inject_uuids({"defaults": {"notify_channels": ["default"]}})
            )

    def test_token_combines_with_siblings(self) -> None:
        # The sentinel may now sit alongside explicit channels.
        cfg = TomlBundleConfig.from_raw(
            _inject_uuids(
                {
                    "defaults": {
                        "notify_channels": ["default", "email"],
                        "notify": {"email": _email_block()},
                    },
                    "job": {"a": _job()},
                }
            ),
            bundle_name="borgadm",
        )
        assert cfg.defaults.notify_channels == ["default", "email"]
        assert "a" not in cfg.errored_jobs

    def test_token_still_rejected_in_default_bundle_with_siblings(
        self,
    ) -> None:
        # Even combined with siblings, the default bundle can't inherit
        # itself.
        with pytest.raises(ConfigError, match="cannot inherit its own"):
            TomlBundleConfig.from_raw(
                _inject_uuids(
                    {
                        "defaults": {
                            "notify_channels": ["default", "dialog-popup"]
                        }
                    }
                )
            )

    def test_reserved_channel_name_rejected(self) -> None:
        with pytest.raises(ConfigError, match="reserved channel name"):
            TomlBundleConfig.from_raw(
                _inject_uuids(
                    {"defaults": {"notify": {"default": _ntfy_block()}}}
                )
            )

    def test_job_level_token_ok_in_nondefault(self) -> None:
        cfg = TomlBundleConfig.from_raw(
            _inject_uuids({"job": {"a": _job(notify_channels=["default"])}}),
            bundle_name="borgadm",
        )
        assert "a" in cfg.jobs
        assert cfg.jobs["a"].notify_channels == ["default"]

    def test_job_level_token_demoted_in_default(self) -> None:
        cfg = TomlBundleConfig.from_raw(
            _inject_uuids({"job": {"a": _job(notify_channels=["default"])}})
        )
        assert "a" in cfg.errored_jobs
        assert "cannot inherit its own" in cfg.errored_jobs["a"]

    def _two_bundles(self, default_notify: list[str]) -> tuple[Any, Any]:
        default_cfg = TomlBundleConfig.from_raw(
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
        borgadm_cfg = TomlBundleConfig.from_raw(
            _inject_uuids({"job": {"a": _job()}}), bundle_name="borgadm"
        )
        return default_cfg, borgadm_cfg

    def test_expand_pulls_default_bundle(self) -> None:
        default_cfg, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(
            ("default", default_cfg), ("borgadm", borgadm_cfg)
        )
        channels, defaults = crony_notify.expand_notify_inherit(
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
        channels, defaults = crony_notify.expand_notify_inherit(
            ["email"], "borgadm", bundles, borgadm_cfg.defaults
        )
        assert channels == ["email"]
        assert defaults is borgadm_cfg.defaults

    def test_expand_default_self_inherit_guarded(self) -> None:
        default_cfg, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(
            ("default", default_cfg), ("borgadm", borgadm_cfg)
        )
        channels, defaults = crony_notify.expand_notify_inherit(
            ["default"], "default", bundles, default_cfg.defaults
        )
        assert channels == []
        assert defaults is default_cfg.defaults

    def test_expand_missing_default_bundle(self) -> None:
        _, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(("borgadm", borgadm_cfg))
        channels, defaults = crony_notify.expand_notify_inherit(
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
        channels, _ = crony_notify.expand_notify_inherit(
            ["default"], "borgadm", bundles, borgadm_cfg.defaults
        )
        assert channels == ["ntfy"]

    def test_expand_unions_with_extras(self) -> None:
        default_cfg, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(
            ("default", default_cfg), ("borgadm", borgadm_cfg)
        )
        channels, defaults = crony_notify.expand_notify_inherit(
            ["default", "dialog-popup"],
            "borgadm",
            bundles,
            borgadm_cfg.defaults,
        )
        # Inherited channels first, then the new sibling.
        assert channels == ["ntfy", "dialog-popup"]
        # Inherited defs still come from the default bundle.
        assert "ntfy" in defaults.notify_channel_defs

    def test_expand_dedups_overlap(self) -> None:
        # The default bundle ALSO has dialog-popup, and the inheriting
        # bundle lists it again: the union must fire it once.
        default_cfg, borgadm_cfg = self._two_bundles(["ntfy", "dialog-popup"])
        bundles = _bundle_set(
            ("default", default_cfg), ("borgadm", borgadm_cfg)
        )
        channels, _ = crony_notify.expand_notify_inherit(
            ["default", "dialog-popup"],
            "borgadm",
            bundles,
            borgadm_cfg.defaults,
        )
        assert channels == ["ntfy", "dialog-popup"]
        assert channels.count("dialog-popup") == 1

    def test_expand_merges_local_def_for_new_sibling(self) -> None:
        # A sibling defined only in the inheriting bundle must still be
        # dispatchable: its def is merged alongside the inherited ones.
        default_cfg = TomlBundleConfig.from_raw(
            _inject_uuids(
                {
                    "defaults": {
                        "notify_channels": ["ntfy"],
                        "notify": {"ntfy": _ntfy_block()},
                    }
                }
            )
        )
        local_cfg = TomlBundleConfig.from_raw(
            _inject_uuids(
                {
                    "defaults": {
                        "notify_channels": ["default", "myemail"],
                        "notify": {"myemail": _email_block(transport="email")},
                    },
                    "job": {"a": _job()},
                }
            ),
            bundle_name="borgadm",
        )
        bundles = _bundle_set(("default", default_cfg), ("borgadm", local_cfg))
        channels, defaults = crony_notify.expand_notify_inherit(
            ["default", "myemail"], "borgadm", bundles, local_cfg.defaults
        )
        assert channels == ["ntfy", "myemail"]
        assert "ntfy" in defaults.notify_channel_defs
        assert "myemail" in defaults.notify_channel_defs

    def test_expand_extras_only_without_default_bundle(self) -> None:
        _, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(("borgadm", borgadm_cfg))
        channels, defaults = crony_notify.expand_notify_inherit(
            ["default", "dialog-popup"],
            "borgadm",
            bundles,
            borgadm_cfg.defaults,
        )
        assert channels == ["dialog-popup"]
        assert defaults is borgadm_cfg.defaults

    def test_expand_default_firing_keeps_extras(self) -> None:
        default_cfg, borgadm_cfg = self._two_bundles(["ntfy"])
        bundles = _bundle_set(
            ("default", default_cfg), ("borgadm", borgadm_cfg)
        )
        channels, defaults = crony_notify.expand_notify_inherit(
            ["default", "dialog-popup"],
            "default",
            bundles,
            default_cfg.defaults,
        )
        assert channels == ["dialog-popup"]
        assert defaults is default_cfg.defaults

    def test_runtime_union_inherit_plus_dialog(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The borgadm shape after this change: a non-default bundle
        # inherits the default bundle's channels AND adds dialog-popup.
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
                "[defaults]\n"
                'notify_channels = ["default", "dialog-popup"]\n'
                '[job.check]\ncommand = "true"\n'
            ),
            encoding="utf-8",
        )
        channels, defaults = crony_notify.resolve_notify_at_runtime(
            "borgadm.check"
        )
        assert channels == ["ntfy", "dialog-popup"]
        assert "ntfy" in defaults.notify_channel_defs

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
        channels, defaults = crony_notify.resolve_notify_at_runtime(
            "borgadm.check"
        )
        assert channels == ["ntfy"]
        assert "ntfy" in defaults.notify_channel_defs
        disabled, _ = crony_notify.resolve_notify_at_runtime("borgadm.create")
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        cfg = self._cfg(
            {
                "job": {"a": _job(platforms=["darwin"])},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        target = cfg.resolve_target("h", "darwin")
        sel_jobs, _ = cfg.selected_jobs_and_groups(target)
        assert "a" in sel_jobs

    def test_job_with_excluding_platform_filtered_out(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
        monkeypatch.setattr(crony_platform, "current_platform", lambda: "linux")
        cfg = self._cfg(
            {
                "job": {"a": _job(platforms=["darwin"])},
                "target": {"linux": {"jobs": ["a"]}},
            }
        )
        target = cfg.resolve_target("h", "linux")
        sel_jobs, _ = cfg.selected_jobs_and_groups(target)
        assert "a" not in sel_jobs

    def test_job_hosts_filter(self, monkeypatch: Any) -> None:
        cfg = self._cfg(
            {
                "job": {"a": _job(hosts=["alpha", "beta"])},
                "target": {"darwin": {"jobs": ["a"]}},
            }
        )
        # On a listed host: selected.
        monkeypatch.setattr(crony_platform, "current_host", lambda: "alpha")
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        target = cfg.resolve_target("alpha", "darwin")
        sel_jobs, _ = cfg.selected_jobs_and_groups(target)
        assert "a" in sel_jobs
        # On a non-listed host: filtered out.
        monkeypatch.setattr(crony_platform, "current_host", lambda: "gamma")
        target = cfg.resolve_target("gamma", "darwin")
        sel_jobs, _ = cfg.selected_jobs_and_groups(target)
        assert "a" not in sel_jobs

    def test_group_filter_skips_recursion(self, monkeypatch: Any) -> None:
        # When a group's filter excludes the current host /
        # platform, the walk does not recurse into its children
        # via that group. A child reachable only through the
        # filtered group is therefore not selected.
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
        monkeypatch.setattr(crony_platform, "current_platform", lambda: "linux")
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
        target = cfg.resolve_target("h", "linux")
        sel_jobs, sel_groups = cfg.selected_jobs_and_groups(target)
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "alpha")
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        target = cfg.resolve_target("alpha", "darwin")
        sel_jobs, sel_groups = cfg.selected_jobs_and_groups(target)
        assert "g" in sel_groups
        assert "a" in sel_jobs
        # On a different host: group filtered, child not reached.
        monkeypatch.setattr(crony_platform, "current_host", lambda: "beta")
        target = cfg.resolve_target("beta", "darwin")
        sel_jobs, sel_groups = cfg.selected_jobs_and_groups(target)
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
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        # On a non-listed host: selected.
        monkeypatch.setattr(crony_platform, "current_host", lambda: "alpha")
        target = cfg.resolve_target("alpha", "darwin")
        sel_jobs, _ = cfg.selected_jobs_and_groups(target)
        assert "a" in sel_jobs
        # On the listed (denied) host: filtered out.
        monkeypatch.setattr(crony_platform, "current_host", lambda: "squee")
        target = cfg.resolve_target("squee", "darwin")
        sel_jobs, _ = cfg.selected_jobs_and_groups(target)
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
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        # On a non-denied host: group + child selected.
        monkeypatch.setattr(crony_platform, "current_host", lambda: "alpha")
        target = cfg.resolve_target("alpha", "darwin")
        sel_jobs, sel_groups = cfg.selected_jobs_and_groups(target)
        assert "g" in sel_groups
        assert "a" in sel_jobs
        # On the denied host: group filtered, child not reached.
        monkeypatch.setattr(crony_platform, "current_host", lambda: "squee")
        target = cfg.resolve_target("squee", "darwin")
        sel_jobs, sel_groups = cfg.selected_jobs_and_groups(target)
        assert "g" not in sel_groups
        assert "a" not in sel_jobs

    def test_group_with_all_masked_children_is_masked_empty(
        self, monkeypatch: Any
    ) -> None:
        # A group whose every direct child is itself masked on
        # this host has nothing to dispatch -- the reference is a
        # no-op and the group joins masked with reason "empty".
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
        monkeypatch.setattr(crony_platform, "current_platform", lambda: "linux")
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
        target = cfg.resolve_target("h", "linux")
        _sel_jobs, sel_groups, masked = cfg.selected_and_masked_jobs_and_groups(
            target
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
        monkeypatch.setattr(crony_platform, "current_platform", lambda: "linux")
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
        target = cfg.resolve_target("h", "linux")
        _sel_jobs, sel_groups, masked = cfg.selected_and_masked_jobs_and_groups(
            target
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
        monkeypatch.setattr(crony_platform, "current_platform", lambda: "linux")
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
        target = cfg.resolve_target("h", "linux")
        sel_jobs, sel_groups, masked = cfg.selected_and_masked_jobs_and_groups(
            target
        )
        assert "g" in sel_groups
        assert "a" in sel_jobs
        assert masked.get("b") == "host"
        assert "g" not in masked


class TestGenerateUuidAction:
    """`crony config generate-uuid` prints a single canonical UUID
    on stdout. Used by users hand-editing a config before the file
    is otherwise valid (the `config update` path requires a parsable
    file).
    """

    def test_emits_one_canonical_uuid(self, capsys: Any) -> None:
        crony_commands.do_generate_uuid()
        out = capsys.readouterr().out.strip()
        parsed = uuid.UUID(out)
        assert str(parsed) == out


class TestBundleLoading:
    """`TomlConfig.load_all` discovers config.toml + config/*.toml,
    isolates per-bundle failures, and rejects collisions."""

    def _setup(self, tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path]:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = tmp_path / "config_dropin"
        cfg_dropin.mkdir()
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)
        return cfg_file, cfg_dropin

    def test_default_only(self, tmp_path: Path, monkeypatch: Any) -> None:
        cfg_file, _ = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text(
            _uuid_toml(
                '[job.j]\ncommand = "true"\nschedule = "daily"\n',
            ),
            encoding="utf-8",
        )
        bundles = TomlConfig.load_all()
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
        bundles = TomlConfig.load_all()
        names = sorted(b.name for b in bundles.bundles)
        assert names == ["borgadm", "default"]
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        assert "prune" in borgadm.config.jobs

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
        bundles = TomlConfig.load_all()
        assert [b.name for b in bundles.bundles] == ["private"]

    def test_no_configs_at_all_returns_empty(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `TomlConfig.load_all` tolerates the no-config case so the
        # runner / destroy / status keep working off on-disk
        # state alone. `apply` is the only caller that enforces
        # "must have a config" -- it needs pending data.
        self._setup(tmp_path, monkeypatch)
        bundles = TomlConfig.load_all()
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
        bundles = TomlConfig.load_all()
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
        with caplog.at_level(logging.ERROR, logger=crony_config.logger.name):
            bundles = TomlConfig.load_all()
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
        # `logs`; TomlConfig.load_all returns an empty TomlConfig
        # with the parse failures captured.
        cfg_file, dropin = self._setup(tmp_path, monkeypatch)
        cfg_file.write_text("this is not [valid toml", encoding="utf-8")
        (dropin / "alpha.toml").write_text("also broken (", encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger=crony_config.logger.name):
            bundles = TomlConfig.load_all()
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
        with caplog.at_level(logging.ERROR, logger=crony_config.logger.name):
            bundles = TomlConfig.load_all()
        assert [b.name for b in bundles.bundles] == ["default"]
        default = bundles.by_name("default")
        assert default is not None
        config = default.config
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
        with caplog.at_level(logging.ERROR, logger=crony_config.logger.name):
            bundles = TomlConfig.load_all()
        assert [b.name for b in bundles.bundles] == ["default"]
        default = bundles.by_name("default")
        assert default is not None
        config = default.config
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
        with caplog.at_level(logging.ERROR, logger=crony_config.logger.name):
            bundles = TomlConfig.load_all()
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
        with caplog.at_level(logging.ERROR, logger=crony_config.logger.name):
            bundles = TomlConfig.load_all()
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
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)

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
        bundles = TomlConfig.load_all()
        # Both bundles loaded successfully despite identical short.
        assert {b.name for b in bundles.bundles} == {"default", "borgadm"}
        # Full names are distinct.
        full_names = bundles.all_full_names()
        assert "default.daily-update" in full_names
        assert "borgadm.daily-update" in full_names


class TestDashKeys:
    """Config keys are canonically dash-spelled. The underscore
    spelling is accepted for back-compat and folds onto the dash form,
    so both parse identically; setting a field under both spellings is
    rejected. Only field-name keys are folded -- the keys inside `env`,
    `headers`, and host / channel sub-tables are user data and keep
    their literal spelling.
    """

    def test_dash_and_underscore_parse_identically(self) -> None:
        defaults = {
            "job_timeout_sec": 3600,
            "keep_awake": True,
            "notify_attach_log": False,
        }
        job = {
            "command": "true",
            "schedule": "daily",
            "uuid": "11111111-2222-3333-4444-555555555555",
            "keep_awake": False,
            "success_exit_codes": [3],
            "gate_script": "/bin/true",
        }
        under = _parse({"defaults": dict(defaults), "job": {"j": dict(job)}})

        def _dash(d: dict[str, Any]) -> dict[str, Any]:
            return {k.replace("_", "-"): v for k, v in d.items()}

        dash = _parse({"defaults": _dash(defaults), "job": {"j": _dash(job)}})
        assert dash.defaults == under.defaults
        assert dash.jobs == under.jobs

    def test_dash_in_notify_channel_block(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify-channels": ["email"],
                    "notify": {
                        "email": {
                            "to": "you@example.com",
                            "smtp-host": "smtp.example.com",
                            "smtp-user": "you",
                        }
                    },
                }
            }
        )
        ch = cfg.defaults.notify_channel_defs["email"]
        assert ch.email is not None
        assert ch.email.smtp_host == "smtp.example.com"

    def test_both_spellings_rejected(self) -> None:
        with pytest.raises(ConfigError, match="use one"):
            _parse({"defaults": {"keep-awake": True, "keep_awake": False}})

    def test_env_var_names_not_folded(self) -> None:
        # Underscores in env var NAMES are user data, never rewritten.
        cfg = _parse({"job": {"j": _job(env={"MY_VAR": "x", "A_B_C": "y"})}})
        assert cfg.jobs["j"].env == {"MY_VAR": "x", "A_B_C": "y"}

    def test_legacy_keys_recorded_for_validate_warning(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "keep_awake": True,
                    "notify_channels": ["email"],
                    "notify": {
                        "email": {
                            "to": "you@example.com",
                            "smtp_host": "smtp.example.com",
                            "smtp_user": "you",
                        }
                    },
                },
                "job": {"j": _job(job_timeout_sec=10)},
            }
        )
        # A nested channel/transport key (smtp-host) is recorded too,
        # proving the scan reaches every table, not just the top level.
        assert set(cfg.legacy_underscore_keys) >= {
            "keep_awake",
            "notify_channels",
            "smtp_host",
            "job_timeout_sec",
        }

    def test_no_legacy_keys_for_dash_config(self) -> None:
        cfg = _parse(
            {
                "defaults": {"keep-awake": True},
                "job": {"j": _job(**{"job-timeout-sec": 10})},
            }
        )
        assert cfg.legacy_underscore_keys == []


class TestJobFlagsField:
    """The `flags = [...]` job field and the per-flag scalar keys (one
    per flag, spelled by its dash token) are two spellings of the same
    per-level delta; a flag set both ways at one level is rejected. The
    scalar keys are derived generically from `JobFlags.members()`, so
    every flag -- current and future -- gets both spellings."""

    @staticmethod
    def _cfg(body: dict[str, Any]) -> dict[str, Any]:
        return {"job": {"j": body}}

    def test_every_flag_is_documented(self) -> None:
        # The `crony status --help` FLAG values reference renders each
        # flag's `.description`; a flag without one would show blank.
        # Adding a flag without documenting it fails here.
        for flag in JobFlags.members():
            assert flag.description, f"{flag!r} has no description"

    def test_every_mask_reason_is_documented(self) -> None:
        # The MASKED values reference renders each reason's description.
        for reason in MaskReason:
            assert reason.description, f"{reason!r} has no description"

    def test_flag_enables(self) -> None:
        cfg = _parse(self._cfg(_job(flags=["keep-awake"])))
        assert cfg.jobs["j"].keep_awake is True

    def test_flag_disable_form(self) -> None:
        cfg = _parse(self._cfg(_job(flags=["keep-awake=false"])))
        assert cfg.jobs["j"].keep_awake is False

    def test_flag_explicit_true(self) -> None:
        cfg = _parse(self._cfg(_job(flags=["keep-awake=true"])))
        assert cfg.jobs["j"].keep_awake is True

    def test_interactive_flag_does_not_tag_platforms(self) -> None:
        cfg = _parse(self._cfg(_job(flags=["interactive"])))
        assert cfg.jobs["j"].interactive is True
        assert cfg.jobs["j"].platforms == []

    def test_full_disk_access_flag_parses(self) -> None:
        cfg = _parse(self._cfg(_job(flags=["full-disk-access"])))
        assert cfg.jobs["j"].flags == {JobFlags.FULL_DISK_ACCESS: True}

    def test_full_disk_access_scalar_key(self) -> None:
        # The standalone scalar spelling, generic for every flag: a new
        # flag is set as `<token> = true` with no per-flag parser edit.
        cfg = _parse(self._cfg(_job(**{"full-disk-access": True})))
        assert cfg.jobs["j"].flags == {JobFlags.FULL_DISK_ACCESS: True}

    def test_full_disk_access_set_both_ways_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(
                _job(flags=["full-disk-access"], **{"full-disk-access": True})
            ),
            "j",
            "use one",
        )

    @pytest.mark.parametrize("flag", list(JobFlags.members()))
    def test_every_flag_has_a_scalar_key(self, flag: JobFlags) -> None:
        # The scalar surface is derived from JobFlags.members(), so each
        # member is accepted as a bare boolean key spelled by its token.
        cfg = _parse(self._cfg(_job(**{flag.token: True})))
        assert cfg.jobs["j"].flags == {flag: True}

    def test_scalar_and_flag_for_different_flags_ok(self) -> None:
        cfg = _parse(self._cfg(_job(interactive=True, flags=["keep-awake"])))
        assert cfg.jobs["j"].interactive is True
        assert cfg.jobs["j"].keep_awake is True

    def test_unknown_flag_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(flags=["turbo"])), "j", "unknown flag"
        )

    def test_bad_flag_value_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(flags=["keep-awake=maybe"])), "j", "true.*false"
        )

    def test_duplicate_flag_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(flags=["keep-awake", "keep-awake=false"])),
            "j",
            "more than once",
        )

    def test_flag_error_renders_bare_token(self) -> None:
        # The message quotes the plain token, not the JobFlagNames repr
        # (`<JobFlagNames.KEEP_AWAKE: ...>`).
        _assert_errored_job(
            self._cfg(_job(flags=["keep-awake=maybe"])),
            "j",
            r"flag 'keep-awake' value",
        )

    def test_flags_must_be_list_of_strings(self) -> None:
        _assert_errored_job(
            self._cfg(_job(flags="interactive")), "j", "list of strings"
        )

    def test_keep_awake_set_both_ways_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(keep_awake=True, flags=["keep-awake"])),
            "j",
            "use one",
        )

    def test_interactive_set_both_ways_rejected(self) -> None:
        _assert_errored_job(
            self._cfg(_job(interactive=True, flags=["interactive"])),
            "j",
            "use one",
        )

    def test_job_partial_records_explicit_settings(self) -> None:
        cfg = _parse(self._cfg(_job(flags=["interactive", "keep-awake=false"])))
        assert cfg.jobs["j"].flags == {
            JobFlags.INTERACTIVE: True,
            JobFlags.KEEP_AWAKE: False,
        }

    def test_job_partial_empty_when_unset(self) -> None:
        cfg = _parse(self._cfg(_job()))
        assert cfg.jobs["j"].flags == {}


class TestFlagsAtDefaultsAndGroup:
    """`flags = [...]` and the per-flag scalar keys are accepted at the
    defaults and group levels too, recording each level's explicit
    per-flag delta. The scalar surface is generic across levels."""

    def test_defaults_flags_stored_and_keep_awake_derived(self) -> None:
        cfg = _parse({"defaults": {"flags": ["keep-awake", "interactive"]}})
        assert cfg.defaults.flags == {
            JobFlags.KEEP_AWAKE: True,
            JobFlags.INTERACTIVE: True,
        }
        # The keep_awake scalar this level yields is still derived.
        assert cfg.defaults.keep_awake is True

    def test_defaults_scalar_flag_key(self) -> None:
        cfg = _parse({"defaults": {"full-disk-access": True}})
        assert cfg.defaults.flags == {JobFlags.FULL_DISK_ACCESS: True}

    def test_group_scalar_flag_key(self) -> None:
        cfg = _parse(
            {
                "job": {"a": _job()},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "full-disk-access": True,
                    }
                },
            }
        )
        assert cfg.job_groups["g"].flags == {JobFlags.FULL_DISK_ACCESS: True}

    def test_defaults_scalar_and_flag_conflict(self) -> None:
        with pytest.raises(ConfigError, match="use one"):
            _parse({"defaults": {"keep-awake": True, "flags": ["keep-awake"]}})

    def test_group_scalar_and_flag_conflict(self) -> None:
        _assert_errored_job_group(
            {
                "job": {"a": _job()},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "interactive": True,
                        "flags": ["interactive"],
                    }
                },
            },
            "g",
            "use one",
        )

    def test_group_flags_stored(self) -> None:
        cfg = _parse(
            {
                "job": {"a": _job()},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "flags": ["interactive", "keep-awake=false"],
                    }
                },
            }
        )
        assert cfg.job_groups["g"].flags == {
            JobFlags.INTERACTIVE: True,
            JobFlags.KEEP_AWAKE: False,
        }

    def test_group_unknown_flag_rejected(self) -> None:
        _assert_errored_job_group(
            {
                "job": {"a": _job()},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "flags": ["turbo"],
                    }
                },
            },
            "g",
            "unknown flag",
        )


class TestFlagsCascade:
    """`resolved_flags_by_name` composes the per-level deltas down the
    target tree: defaults, then each ancestor group, then the entry."""

    @staticmethod
    def _resolve(monkeypatch: Any, raw: dict[str, Any]) -> Any:
        monkeypatch.setattr(crony_platform, "current_platform", lambda: "linux")
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
        cfg = _parse(raw)
        return cfg.resolved_flags_by_name(cfg.resolve_target("h", "linux"))

    def test_defaults_inherited_by_unset_job(self, monkeypatch: Any) -> None:
        flags = self._resolve(
            monkeypatch,
            {
                "defaults": {"flags": ["keep-awake"]},
                "job": {"a": _job()},
                "target": {"linux": {"jobs": ["a"]}},
            },
        )
        assert flags["a"] == JobFlags.KEEP_AWAKE

    def test_group_overrides_defaults_job_overrides_group(
        self, monkeypatch: Any
    ) -> None:
        flags = self._resolve(
            monkeypatch,
            {
                "defaults": {"flags": ["keep-awake"]},
                "job": {"a": _job(flags=["keep-awake"])},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "daily",
                        "flags": ["keep-awake=false"],
                    }
                },
                "target": {"linux": {"jobs": ["g"]}},
            },
        )
        # defaults ON -> group OFF -> job ON again: job wins.
        assert flags["a"] == JobFlags.KEEP_AWAKE
        # The group's own resolved flags reflect defaults ON then its
        # own OFF.
        assert flags["g"] == JobFlags(0)

    def test_full_chain_through_nested_groups(self, monkeypatch: Any) -> None:
        flags = self._resolve(
            monkeypatch,
            {
                "job": {"a": _job()},
                "job-group": {
                    "outer": {
                        "jobs": ["inner"],
                        "schedule": "daily",
                        "flags": ["keep-awake"],
                    },
                    "inner": {"jobs": ["a"], "flags": ["interactive"]},
                },
                "target": {"linux": {"jobs": ["outer"]}},
            },
        )
        # a inherits keep-awake (outer) + interactive (inner).
        assert flags["a"] == JobFlags.KEEP_AWAKE | JobFlags.INTERACTIVE


class TestInteractiveDarwinMasking:
    """An interactive job is darwin-only, enforced at selection time
    after the cascade resolves -- whether interactive is set on the job
    or inherited."""

    @staticmethod
    def _select(monkeypatch: Any, raw: dict[str, Any], platform: str) -> Any:
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: platform
        )
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
        cfg = _parse(raw)
        target = cfg.resolve_target("h", platform)
        return cfg.selected_and_masked_jobs_and_groups(target)

    def test_interactive_job_selected_on_darwin(self, monkeypatch: Any) -> None:
        jobs, _, masked = self._select(
            monkeypatch,
            {
                "job": {"a": _job(interactive=True)},
                "target": {"darwin": {"jobs": ["a"]}},
            },
            "darwin",
        )
        assert "a" in jobs
        assert "a" not in masked

    def test_interactive_job_masked_on_linux(self, monkeypatch: Any) -> None:
        jobs, _, masked = self._select(
            monkeypatch,
            {
                "job": {"a": _job(interactive=True)},
                "target": {"linux": {"jobs": ["a"]}},
            },
            "linux",
        )
        assert "a" not in jobs
        assert masked.get("a") == "platform"

    def test_inherited_interactive_masked_on_linux(
        self, monkeypatch: Any
    ) -> None:
        jobs, _, masked = self._select(
            monkeypatch,
            {
                "defaults": {"flags": ["interactive"]},
                "job": {"a": _job()},
                "target": {"linux": {"jobs": ["a"]}},
            },
            "linux",
        )
        assert "a" not in jobs
        assert masked.get("a") == "platform"


class TestJobFlags:
    def test_token_round_trips_for_every_member(self) -> None:
        for flag in JobFlags.members():
            assert JobFlags.from_token(flag.token) is flag

    def test_token_spellings(self) -> None:
        assert JobFlags.INTERACTIVE.token == "interactive"
        assert JobFlags.KEEP_AWAKE.token == "keep-awake"
        assert JobFlags.FULL_DISK_ACCESS.token == "full-disk-access"

    def test_from_token_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="unknown flag"):
            JobFlags.from_token("turbo")

    def test_from_token_accepts_non_canonical_spellings(self) -> None:
        # Input is normalized, so case and `_`/`-` differences from the
        # canonical dash token still resolve.
        assert JobFlags.from_token("keep_awake") is JobFlags.KEEP_AWAKE
        assert JobFlags.from_token("KEEP-AWAKE") is JobFlags.KEEP_AWAKE
        assert JobFlags.from_token("Interactive") is JobFlags.INTERACTIVE

    def test_combines_as_bitmask(self) -> None:
        only_iv = JobFlags.INTERACTIVE
        assert JobFlags.INTERACTIVE in only_iv
        assert JobFlags.KEEP_AWAKE not in only_iv
        both = JobFlags.INTERACTIVE | JobFlags.KEEP_AWAKE
        assert JobFlags.INTERACTIVE in both
        assert JobFlags.KEEP_AWAKE in both

    def test_token_undefined_for_combined_value(self) -> None:
        with pytest.raises(ValueError, match="single flag"):
            _ = (JobFlags.INTERACTIVE | JobFlags.KEEP_AWAKE).token

    def test_members_are_the_single_flags_in_order(self) -> None:
        assert JobFlags.members() == [
            JobFlags.INTERACTIVE,
            JobFlags.KEEP_AWAKE,
            JobFlags.FULL_DISK_ACCESS,
        ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
