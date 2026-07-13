#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "pytest-cov", "tomlkit", "pydantic>=2"]
# ///
# This is AI generated code

"""Unit tests for crony.commands."""

import argparse
import dataclasses
import importlib.resources
import io
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import tomlkit

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_crony import (  # noqa: E402
    _ApplyHarness,
    _cast_dict,
    _email_block,
    _idle_lock_host,
    _isolate_home,  # noqa: E402, F401
    _ntfy_block,
    _parse,
    _RunnerHarness,
    _uuid_toml,
)

from crony import cli as crony_cli  # noqa: E402
from crony import commands as crony_commands  # noqa: E402
from crony import model as crony_model  # noqa: E402
from crony import paths as crony_paths  # noqa: E402
from crony import platform as crony_platform  # noqa: E402
from crony import runner as crony_runner  # noqa: E402
from crony import runtime as crony_runtime  # noqa: E402
from crony.config import (  # noqa: E402
    DEFAULT_BUNDLE_NAME,
    JobFlagNames,
    JobFlags,
    MaskReason,
    TomlConfig,
)
from crony.errors import (  # noqa: E402
    ConfigError,
    ExitCode,
    LockBusyError,
    PreconditionError,
    UsageError,
)
from crony.model import (  # noqa: E402
    Job,
)
from crony.platform import fda as crony_fda  # noqa: E402
from crony.platform import (  # noqa: E402
    launchd,
    systemd,
)
from crony.platform.fda import FDAWrapper  # noqa: E402
from crony.snapshot import CURRENT_SNAPSHOT_SCHEMA  # noqa: E402
from crony.unit import (  # noqa: E402
    EntityRef,
)

_script_path = REPO_ROOT / "src" / "crony" / "commands.py"


class TestInit:
    """do_init writes the default config template, refuses to clobber."""

    def _redirect_config(self, monkeypatch: Any, tmp_path: Path) -> Path:
        """Point CONFIG_DIR / CONFIG_FILE at a tmp dir so do_init
        doesn't touch the user's real ~/.config/crony.
        """
        cfg_dir = tmp_path / "crony"
        cfg_file = cfg_dir / "config.toml"
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        return cfg_file

    def test_creates_file_when_absent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        assert not cfg_file.exists()
        crony_commands.do_init(force=False, bundle=None)
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
        crony_commands.do_init(force=False, bundle=None)
        assert cfg_file.parent.is_dir()

    def test_refuses_to_overwrite_without_force(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text("user content", encoding="utf-8")
        with pytest.raises(UsageError, match="already exists"):
            crony_commands.do_init(force=False, bundle=None)
        # File untouched.
        assert cfg_file.read_text() == "user content"

    def test_overwrites_with_force(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text("user content", encoding="utf-8")
        crony_commands.do_init(force=True, bundle=None)
        body = cfg_file.read_text(encoding="utf-8")
        assert "user content" not in body
        assert "[defaults]" in body

    def test_shipped_template_file_exists(self) -> None:
        """The package ships `default_config.toml` as data beside the
        code; `crony config init` reads it. Guard against the file (or
        the package-relative path to it) going missing."""
        res = importlib.resources.files("crony").joinpath("default_config.toml")
        assert res.is_file()

    def test_template_is_ascii_only(self) -> None:
        """All persistent files in this repo are ASCII; the template
        we ship as a starting point must be too.
        """
        crony_commands._default_config_template().encode("ascii")  # raises

    def test_init_emits_shipped_template_verbatim(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """`do_init` writes the shipped file's exact bytes, so what the
        validation tests check is what a user actually gets."""
        cfg_file = self._redirect_config(monkeypatch, tmp_path)
        crony_commands.do_init(force=False, bundle=None)
        shipped = (
            importlib.resources.files("crony")
            .joinpath("default_config.toml")
            .read_text(encoding="utf-8")
        )
        assert cfg_file.read_text(encoding="utf-8") == shipped

    def test_bundle_writes_to_dropin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_dir = tmp_path / "crony"
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = cfg_dir / "config"
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)
        crony_commands.do_init(force=False, bundle="borgadm")
        target = cfg_dropin / "borgadm.toml"
        assert target.is_file()
        assert "[defaults]" in target.read_text(encoding="utf-8")
        # config.toml is untouched.
        assert not cfg_file.exists()

    def test_bundle_default_rejected(self) -> None:
        # `--bundle default` would shadow config.toml; the parser
        # rejects it (exit 2) before dispatch.
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(
                ["config", "init", "--bundle", "default"]
            )
        assert exc.value.code == 2

    def test_bundle_invalid_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg_dir = tmp_path / "crony"
        cfg_file = cfg_dir / "config.toml"
        cfg_dropin = cfg_dir / "config"
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)
        with pytest.raises(UsageError, match="bundle name"):
            crony_commands.do_init(force=False, bundle="has.dot")

    def test_template_parses_when_uncommented(self) -> None:
        """The example schema in the template must be valid TOML.

        Commented-out directives carry a bare `#` (no space); prose
        carries `# ` (a space). Extract the directive lines (section
        headers `#[foo]` and `#foo = ...`), strip the single leading
        `#`, and feed the result to _from_raw. Prose, dividers, and
        inline-comment continuations all keep the space, so the
        strict patterns skip them.
        """
        extracted: list[str] = []
        section_re = re.compile(r"^#\[[\w.\-]+\]\s*$")
        kv_re = re.compile(r"^#[A-Za-z_][\w.\-]*\s*=")
        for line in crony_commands._default_config_template().splitlines():
            if section_re.match(line) or kv_re.match(line):
                extracted.append(line[1:])
        text = "\n".join(extracted)
        _parse(tomlkit.loads(text))


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
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)
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
        crony_commands.do_config_update(bundle=None)
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
        crony_commands.do_config_update(bundle=None)
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
        crony_commands.do_config_update(bundle=None)
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
        crony_commands.do_config_update(bundle="borgadm")
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
        with pytest.raises(UsageError, match="bundle 'ghost'"):
            crony_commands.do_config_update(bundle="ghost")

    def test_no_config_at_all_errors(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._redirect(monkeypatch, tmp_path)
        with pytest.raises(ConfigError, match="no config"):
            crony_commands.do_config_update(bundle=None)

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
            crony_commands.do_config_update(bundle=None)
        # Default bundle's job got a uuid even though broken.toml failed.
        default_doc = tomlkit.loads(cfg_file.read_text(encoding="utf-8"))
        assert "uuid" in default_doc["job"]["a"]
        # The broken file was reported.
        assert any("broken.toml" in r.message for r in caplog.records)


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
        # The verdict is the typed enum, not a bare string, so a
        # regression back to a raw str return is caught.
        assert isinstance(result, crony_runtime.ApplyResult)
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


class TestApplyHardTimeout:
    """Apply renders the hard-timeout guard into the unit's run command
    for a capped entry, with cap = entry timeout + padding, and renders a
    bare run for an uncapped one. Same backstop on both platforms.
    """

    _GUARD = crony_model.GUARD_SUBCOMMAND
    _PAD = crony_model._HARD_TIMEOUT_PADDING_SEC

    def test_darwin_capped_job_renders_guard(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "job-timeout-sec": 300,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        plist = (h.agents / f"org.crony.{h.full('j')}.plist").read_text()
        assert f"{self._GUARD} {300 + self._PAD} " in plist

    def test_darwin_uncapped_job_renders_bare_run(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "job-timeout-sec": 0,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        plist = (h.agents / f"org.crony.{h.full('j')}.plist").read_text()
        assert self._GUARD not in plist

    def test_linux_capped_job_renders_guard(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "job-timeout-sec": 300,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        svc = (h.sysd / f"crony-{h.full('j')}.service").read_text()
        assert f"{self._GUARD} {300 + self._PAD} " in svc

    def test_interactive_job_renders_no_guard(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An interactive job's pending wait / prompt / re-promptable delay
        # has no wallclock bound, so the hard guard would kill a healthy
        # waiting job. It renders unguarded even with a nonzero timeout.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "job-timeout-sec": 300,
                        "interactive": True,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        plist = (h.agents / f"org.crony.{h.full('j')}.plist").read_text()
        assert self._GUARD not in plist

    def test_capped_job_apply_is_idempotent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The guarded render must round-trip through the drift check: a
        # second apply of an unchanged capped job sees no drift.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "job-timeout-sec": 300,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        assert h.apply("j") == "unchanged"


class TestApplySelfUpdate:
    """A job whose own run performs the apply must not reload its own
    unit out from under itself on a scheduler whose reload terminates the
    running job (launchd). The runner exports CRONY_RUNNING_REF naming the
    in-flight entity; apply_one defers the unit change for that entry.
    """

    def test_darwin_defers_own_unit_change(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        plist_path = h.agents / f"org.crony.{h.full('j')}.plist"
        before = plist_path.read_text()
        # The running job re-applies itself with a unit-changing edit.
        monkeypatch.setenv(crony_runtime.RUNNING_REF_ENV, h.ref("j"))
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        h.calls.clear()
        result = h.apply("j")
        assert result == "deferred"
        # All-or-nothing: a deferred unit change writes nothing, so the
        # unit and the snapshot both stay at the pre-edit state and disk
        # remains internally consistent. A later apply does the full
        # update via the drift path.
        assert plist_path.read_text() == before
        assert all(c[0] != "launchctl" for c in h.calls)
        snap = json.loads((h.state_dir("j") / "snapshot.json").read_text())
        assert snap["schedule"] == "*-*-* 03:00"

    def test_darwin_snapshot_only_change_applies_without_reload(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A change that touches only snapshot fields the plist does not
        # render (the command runs from the snapshot, not the unit) needs
        # no unit reload, so the self path writes the snapshot and skips
        # activation entirely rather than deferring.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        plist_path = h.agents / f"org.crony.{h.full('j')}.plist"
        before = plist_path.read_text()
        monkeypatch.setenv(crony_runtime.RUNNING_REF_ENV, h.ref("j"))
        h.config(
            {"job": {"j": {"command": "false", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.calls.clear()
        result = h.apply("j")
        assert result == "updated"
        assert plist_path.read_text() == before
        assert all(c[0] != "launchctl" for c in h.calls)
        snap = json.loads((h.state_dir("j") / "snapshot.json").read_text())
        assert snap["command"] == "false"

    def test_linux_applies_own_unit_normally(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # systemd's reload does not stop a running service, so a self
        # apply proceeds normally -- the guard is launchd-specific.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setenv(crony_runtime.RUNNING_REF_ENV, h.ref("j"))
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        h.calls.clear()
        result = h.apply("j")
        assert result == "updated"
        assert any(c[0] == "systemctl" for c in h.calls)

    def test_different_running_entry_does_not_defer(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The guard keys on identity: an apply of `j` while a *different*
        # entry is the running one applies `j` normally.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {"command": "true", "schedule": "*-*-* 03:00"},
                    "k": {"command": "true", "schedule": "*-*-* 03:00"},
                }
            },
            default_target_jobs=["j", "k"],
        )
        h.apply("j")
        monkeypatch.setenv(crony_runtime.RUNNING_REF_ENV, h.ref("k"))
        h.config(
            {
                "job": {
                    "j": {"command": "true", "schedule": "*-*-* 04:00"},
                    "k": {"command": "true", "schedule": "*-*-* 03:00"},
                }
            },
            default_target_jobs=["j", "k"],
        )
        h.calls.clear()
        result = h.apply("j")
        assert result == "updated"
        assert any(c[0] == "launchctl" for c in h.calls)

    def test_do_apply_exits_warning_on_deferral(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setenv(crony_runtime.RUNNING_REF_ENV, h.ref("j"))
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        with pytest.raises(SystemExit) as exc:
            crony_commands.do_apply(jobs=["j"], verbose=False, bundle=None)
        assert exc.value.code == int(ExitCode.WARNING)


class TestApplyFullDiskAccess:
    """`crony apply` builds the FDA wrapper exactly when an entry being
    applied carries the full-disk-access flag, and only then. The
    fda module's build / state are stubbed so this runs on any
    platform."""

    def test_builds_wrapper_for_fda_job(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        built: list[bool] = []
        monkeypatch.setattr(
            crony_fda, "build_wrapper", lambda: built.append(True)
        )
        monkeypatch.setattr(crony_fda, "wrapper_state", lambda: FDAWrapper.OK)
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "flags": ["full-disk-access"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        crony_commands.do_apply(jobs=["j"], verbose=False, bundle=None)
        assert built == [True]

    def test_skips_wrapper_without_fda_job(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        built: list[bool] = []
        monkeypatch.setattr(
            crony_fda, "build_wrapper", lambda: built.append(True)
        )
        monkeypatch.setattr(crony_fda, "wrapper_state", lambda: FDAWrapper.OK)
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony_commands.do_apply(jobs=["j"], verbose=False, bundle=None)
        assert built == []

    def test_grant_warning_does_not_fail_apply(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        monkeypatch.setattr(crony_fda, "build_wrapper", lambda: None)
        monkeypatch.setattr(
            crony_fda, "wrapper_state", lambda: FDAWrapper.MISSING_FDA_GRANT
        )
        monkeypatch.setattr(
            crony_fda, "grant_instructions", lambda: "grant me FDA"
        )
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "flags": ["full-disk-access"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        with caplog.at_level(logging.WARNING):
            crony_commands.do_apply(jobs=["j"], verbose=False, bundle=None)
        assert "grant me FDA" in caplog.text
        # The plist still lands -- a missing grant warns, it does not
        # abort the apply.
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()


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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        crony_commands.do_apply(jobs=["j"], verbose=False, bundle=None)
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
            crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        crony_commands.do_apply(jobs=["j"], verbose=False, bundle=None)
        assert (h.state / "default" / new_uuid / "snapshot.json").is_file()
        assert not old_dir.exists()

    def _stage_uuid_change(
        self, tmp_path: Path, monkeypatch: Any
    ) -> tuple[Any, str, str, Path]:
        # apply j at U_old, then change its uuid in config to U_new
        # WITHOUT applying -- U_old is now a superseded orphan (its
        # uuid is gone from config) under the still-selected name.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        cfg1 = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        old_uuid = cfg1.jobs["j"].uuid
        h.apply("j")
        old_dir = h.state / "default" / old_uuid
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        assert old_dir.is_dir() and plist.exists()
        new_uuid = "abcdabcd-1111-2222-3333-444455556666"
        h.config(
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
        return h, old_uuid, new_uuid, old_dir

    def test_destroy_orphans_reclaims_superseded_uuid_in_full(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `destroy --orphans` tracks by uuid: the old uuid is gone from
        # config, so it is a full orphan -- its dir AND its name-keyed
        # unit go (the name reuse by the new uuid is incidental; the
        # new uuid installs its own unit on the next apply).
        h, _old, new_uuid, old_dir = self._stage_uuid_change(
            tmp_path, monkeypatch
        )
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=True)
        assert not old_dir.exists()
        assert not plist.exists()
        assert not (h.state / "default" / new_uuid).exists()

    def test_full_apply_reclaims_superseded_uuid_clean_first(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A full `crony apply` reclaims the old uuid (the same set as
        # `destroy --orphans`) before installing the new uuid.
        h, _old, new_uuid, old_dir = self._stage_uuid_change(
            tmp_path, monkeypatch
        )
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        assert not old_dir.exists()
        assert (h.state / "default" / new_uuid / "snapshot.json").is_file()
        assert plist.exists()

    def test_uuid_change_does_not_inherit_old_units_disabled_state(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A uuid change is a new job: it does NOT inherit the old uuid's
        # disabled overlay. A disabled job re-keyed to a new uuid comes
        # back enabled -- the new uuid has no prior snapshot for
        # load_config to mirror the disable from. (Same-uuid re-apply
        # preserves the disable -- covered separately.)
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_disable(jobs=["j"], bundle=None)
        h.config(
            {
                "job": {
                    "j": {
                        "uuid": "abcdabcd-1111-2222-3333-444455556666",
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        crony_commands.do_apply(jobs=["j"], verbose=False, bundle=None)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        node = config.current.job_from_ref(ref)
        assert node is not None and node.unit_disabled is False
        # The fresh unit is armed (schedule intact, not stripped).
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        assert "StartCalendarInterval" in plist.read_text()

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
        crony_commands.do_apply(jobs=["j"], verbose=False, bundle=None)
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        # Re-apply with no changes: nothing to print.
        with caplog.at_level(logging.INFO, logger="crony"):
            crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
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
        with pytest.raises(UsageError, match="unselected on this host"):
            crony_commands.do_apply(jobs=["j"], verbose=False, bundle=None)
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        with caplog.at_level(logging.INFO, logger="crony"):
            crony_commands.do_apply(jobs=[], verbose=True, bundle=None)
        messages = [r.getMessage() for r in caplog.records]
        assert any("unchanged" in m for m in messages), messages

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(UsageError, match="unknown bundle"):
            crony_commands.do_apply(jobs=[], verbose=False, bundle="ghost")

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
        crony_commands.do_apply(jobs=[], verbose=False, bundle="borgadm")
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
        crony_commands.do_apply(jobs=["k"], verbose=False, bundle="borgadm")
        bundles = TomlConfig.load_all()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        borgadm_cfg = borgadm.config
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
        with pytest.raises(UsageError, match="refusing the full-sync"):
            crony_commands.do_apply(jobs=[], verbose=False, bundle=None)

    def test_bundle_scoped_apply_proceeds_when_sibling_bundle_errored(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The full-sync refusal is for the unscoped (bundle=None)
        # sweep, whose orphan removal spans the broken bundle. A
        # `--bundle` sweep is scoped to that one bundle (confirmed
        # parsed by require_known), so a broken *sibling*
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
        crony_commands.do_apply(
            jobs=[], verbose=False, bundle=DEFAULT_BUNDLE_NAME
        )
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        # State dir didn't move (same uuid -> same path); log
        # survives untouched.
        assert (foo_dir / "run.log").read_text() == "from the foo era\n"
        # New label is wired up; old label is gone.
        assert (h.agents / f"org.crony.{h.full('bar')}.plist").exists()
        assert not (h.agents / f"org.crony.{h.full('foo')}.plist").exists()
        # The alias follows the rename: the old short is unlinked, the
        # new short points at the (shared) uuid-keyed dir.
        assert not (h.state / "default" / "foo").is_symlink()
        new_alias = h.state / "default" / "bar"
        assert new_alias.is_symlink()
        assert new_alias.resolve() == foo_dir.resolve()

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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        # The aliases end correctly cross-linked: `zzz` -> the renamed
        # entry's uuid, `aaa` -> the new claimant's uuid.
        assert (h.state / "default" / "zzz").resolve() == renamed_dir.resolve()
        assert (h.state / "default" / "aaa").resolve() == (
            h.state / "default" / new_aaa_uuid
        ).resolve()


class TestApplyAlias:
    """apply maintains the short-name alias symlink beside each uuid
    dir -- the uuid-free form `crony logs` / `crony status` report. The
    alias is a compared part of the snapshot, so a missing or
    mis-pointed link reads `stale` and a re-apply repairs it.
    """

    def test_apply_creates_relative_alias(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j", ensure_snapshot=False)
        alias = h.state / "default" / "j"
        assert alias.is_symlink()
        # Relative target is the bare uuid, so the tree stays movable.
        assert os.readlink(alias) == sd.name
        assert alias.resolve() == sd.resolve()
        assert (
            crony_runtime.load_config().cfg_status(
                EntityRef("default", sd.name)
            )
            == "synced"
        )

    def test_missing_alias_reads_stale_and_apply_repairs(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j", ensure_snapshot=False)
        ref = EntityRef("default", sd.name)
        alias = h.state / "default" / "j"
        alias.unlink()
        assert crony_runtime.load_config().cfg_status(ref) == "stale"
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        assert alias.is_symlink()
        assert crony_runtime.load_config().cfg_status(ref) == "synced"

    def test_mispointed_alias_reads_stale_and_apply_repairs(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j", ensure_snapshot=False)
        ref = EntityRef("default", sd.name)
        alias = h.state / "default" / "j"
        alias.unlink()
        alias.symlink_to("00000000-0000-0000-0000-000000000000")
        assert crony_runtime.load_config().cfg_status(ref) == "stale"
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        assert os.readlink(alias) == sd.name
        assert crony_runtime.load_config().cfg_status(ref) == "synced"


class TestDestroy:
    def test_factory_reset(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j", ensure_snapshot=False)
        alias = h.state / "default" / "j"
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        assert (sd / "snapshot.json").exists()
        assert alias.is_symlink()
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=False)
        assert not (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        assert not sd.exists()
        # The alias symlink is cleaned up with the rest of the entry.
        assert not alias.is_symlink()

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
        crony_commands.do_destroy(jobs=["a"], bundle=None, orphans=False)
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
        crony_commands.do_destroy(jobs=["j"], bundle=None, orphans=False)
        assert not sd.exists()

    def test_unknown_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(UsageError, match="unknown"):
            crony_commands.do_destroy(
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
            with pytest.raises(LockBusyError) as exc:
                crony_commands.do_destroy(
                    jobs=["j"],
                    bundle=None,
                    orphans=False,
                )
            assert "run in progress; will not destroy" in str(exc.value)
            assert exc.value.exit_code == ExitCode.LOCK_BUSY
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
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=False)
        assert not plist.exists()

    def test_destroy_finds_non_namespaced_unit(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A leftover crony unit whose name isn't <bundle>.<short>
        # (hand-created, or from an older crony) must still be reachable
        # by `crony destroy`: the scheduler keys on the unit name, not on
        # a parsed entity identity, so discovery + removal don't require
        # the name to be a valid EntityName.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        stray = h.agents / "org.crony.bogus.plist"
        stray.write_text("x")
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=False)
        assert not stray.exists()

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(UsageError, match="unknown bundle"):
            crony_commands.do_destroy(
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
        bundles = TomlConfig.load_all()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        h.apply("k", bundle="borgadm")
        k_dir = h.state / "borgadm" / borgadm.config.jobs["k"].uuid
        assert k_dir.exists()
        crony_commands.do_destroy(
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
        # broke, so `Config.require_addressable` must accept a
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
        bundles = TomlConfig.load_all()
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
        crony_commands.do_destroy(jobs=[], bundle="borgadm", orphans=False)
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
        with pytest.raises(UsageError, match="bundle 'default'"):
            crony_commands.do_destroy(
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
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=True)
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
        bundles = TomlConfig.load_all()
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
        crony_commands.do_destroy(
            jobs=[],
            bundle="borgadm",
            orphans=True,
        )
        assert (default_old_d_dir / "snapshot.json").exists()
        assert not borgadm_old_b_dir.exists()

    def test_orphans_flag_with_positional_names_rejected(self) -> None:
        # Arg-combination validation lives in the parser, so the bad
        # combination exits 2 before any dispatch.
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(
                ["destroy", "foo", "--orphans"]
            )
        assert exc.value.code == 2

    def test_bare_destroy_rejected(self) -> None:
        # A bare destroy would factory-reset everything; the parser
        # requires an explicit selector (exit 2) before dispatch.
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(["destroy"])
        assert exc.value.code == 2

    def test_bundle_only_destroy_rejected(self) -> None:
        # --bundle narrows the scope but isn't itself a selector; --all
        # is still required to wipe the whole bundle.
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(["destroy", "-b", "x"])
        assert exc.value.code == 2

    def test_all_with_names_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(["destroy", "--all", "foo"])
        assert exc.value.code == 2

    def test_all_with_orphans_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(
                ["destroy", "--all", "--orphans"]
            )
        assert exc.value.code == 2

    def test_all_and_orphans_parse_and_consume_all(self) -> None:
        # --all (optionally -b) and --orphans each parse on their own;
        # the validator consumes --all so it never reaches the handler.
        for argv in (
            ["destroy", "--all"],
            ["destroy", "--all", "-b", "x"],
            ["destroy", "--orphans"],
        ):
            args = crony_cli._build_parser().parse_command(argv)
            assert args.command == "destroy"
            assert not hasattr(args, "all_jobs")

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
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=True)
        assert (h.state_dir("j") / "snapshot.json").exists()
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()


class TestSchedulerVerifyEmission:
    """status / validate run Scheduler.verify and surface its
    SchedulerWarning -- here the systemd linger-disabled case. The
    per-backend verify logic itself lives in
    test_crony_platform_{launchd,systemd}.py."""

    def test_validate_surfaces_warning(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        monkeypatch.setattr(systemd, "_linger_enabled", lambda _u: False)
        with pytest.raises(SystemExit) as exc:
            crony_commands.do_validate(bundle=None, file=None)
        assert exc.value.code == int(ExitCode.WARNING)
        out = capsys.readouterr().out
        assert "linger is disabled" in out
        assert "enable-linger" in out

    def test_status_logs_warning(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        monkeypatch.setattr(systemd, "_linger_enabled", lambda _u: False)
        with caplog.at_level(logging.WARNING):
            crony_commands.do_status(
                jobs=[],
                cols=None,
                show_masked=False,
                bundle=None,
                config_current=False,
                config_pending=False,
                exclude_healthy=False,
            )
        assert "enable-linger" in caplog.text


class TestUvExecutable:
    """`_uv_executable` locates the uv binary baked into platform units.

    crony always runs under uv, which exports its own absolute path as
    `$UV`, so that is the authoritative source independent of PATH; a
    PATH lookup is the fallback for the rare run outside uv.
    """

    def test_prefers_env_uv(self, tmp_path: Path, monkeypatch: Any) -> None:
        uv = tmp_path / "real-uv"
        uv.write_text("")
        monkeypatch.setenv("UV", str(uv))
        # PATH would answer differently; $UV must win.
        monkeypatch.setattr(
            crony_runtime.shutil, "which", lambda _name: "/usr/bin/uv"
        )
        assert crony_runtime._uv_executable() == uv.resolve()

    def test_falls_back_to_path_when_env_uv_missing_file(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # $UV set but pointing at a path that no longer exists (uv moved):
        # fall back to the PATH lookup rather than baking a dead path.
        monkeypatch.setenv("UV", str(tmp_path / "gone" / "uv"))
        path_uv = tmp_path / "path-uv"
        path_uv.write_text("")
        monkeypatch.setattr(
            crony_runtime.shutil, "which", lambda _name: str(path_uv)
        )
        assert crony_runtime._uv_executable() == path_uv.resolve()

    def test_falls_back_to_path_when_env_uv_unset(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("UV", raising=False)
        path_uv = tmp_path / "path-uv"
        path_uv.write_text("")
        monkeypatch.setattr(
            crony_runtime.shutil, "which", lambda _name: str(path_uv)
        )
        assert crony_runtime._uv_executable() == path_uv.resolve()

    def test_errors_when_uv_not_found(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("UV", raising=False)
        monkeypatch.setattr(crony_runtime.shutil, "which", lambda _name: None)
        with pytest.raises(PreconditionError, match="uv not found"):
            crony_runtime._uv_executable()


class TestEnableDisable:
    def test_enable_restores_timer_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # disable removes the .timer (schedule-less re-render); enable
        # re-renders it, links it (`enable`) and arms it (`restart`).
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_disable(jobs=["j"], bundle=None)
        assert not (h.sysd / f"crony-{h.full('j')}.timer").exists()
        h.calls.clear()
        crony_commands.do_enable(jobs=["j"], bundle=None)
        timer = f"crony-{h.full('j')}.timer"
        assert ["systemctl", "--user", "--quiet", "enable", timer] in h.calls
        assert ["systemctl", "--user", "--quiet", "restart", timer] in h.calls
        assert (h.sysd / timer).exists()

    def test_disable_strips_schedule_on_darwin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # disable re-renders the plist with no schedule and reloads
        # (bootout + bootstrap) -- a loaded-but-dormant, still-triggerable
        # unit, with no launchctl `disable` record involved.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        assert "StartCalendarInterval" in plist.read_text()
        h.calls.clear()
        crony_commands.do_disable(jobs=["j"], bundle=None)
        verbs = [c[1] if len(c) > 1 else "" for c in h.calls]
        assert "bootout" in verbs and "bootstrap" in verbs
        assert "disable" not in verbs
        assert "StartCalendarInterval" not in plist.read_text()

    def test_disabled_job_synced_triggerable_and_shown_disabled(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # End to end: disabling a scheduled job leaves it config=synced
        # (the disable is a runtime overlay, not drift), shown SCHEDULE=
        # disabled, and still triggerable -- kickstart fires the loaded
        # schedule-less unit (the original disabled-trigger bug).
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_disable(jobs=["j"], bundle=None)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.cfg_status(ref) == "synced"
        node = config.current.job_from_ref(ref)
        assert node is not None and node.unit_disabled is True
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,schedule"),
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "disabled" in out
        h.calls.clear()
        crony_commands.do_trigger(
            jobs=["j"], wait=False, trigger_timeout=None, bundle=None
        )
        cmd = next(
            c for c in h.calls if c[0] == "launchctl" and c[1] == "kickstart"
        )
        assert cmd[2].endswith(f"org.crony.{h.full('j')}")

    def test_unknown_name_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(UsageError, match="not stamped"):
            crony_commands.do_enable(jobs=["ghost"], bundle=None)

    def test_unknown_name_rejected_for_disable(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(UsageError, match="not stamped"):
            crony_commands.do_disable(jobs=["ghost"], bundle=None)

    def _schedule_cell(self, full: str, capsys: Any) -> str:
        """The SCHEDULE cell `do_status` renders for `full`, NO_COLOR
        so the value is the bare token (no ANSI)."""
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job-or-uuid,schedule"),
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out: str = capsys.readouterr().out
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith(full):
                return stripped[len(full) :].strip()
        raise AssertionError(f"no status row for {full}:\n{out}")

    def test_grouped_entry_disable_enable_round_trips(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A grouped (schedule-less) child can be operator-disabled: with
        # no timer to disarm, disable just records `unit_disabled` on the
        # child's snapshot so the parent group skips it. The SCHEDULE
        # cell reads `disabled` (the disable wins over `grouped`); enable
        # clears the flag and the cell reads `grouped` again.
        monkeypatch.setenv("NO_COLOR", "1")
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {"g": {"jobs": ["a"], "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("a")
        crony_commands.do_disable(jobs=["a"], bundle=None)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("a")]
        node = config.current.job_from_ref(ref)
        assert node is not None and node.unit_disabled is True
        assert self._schedule_cell(h.full("a"), capsys) == "disabled"
        crony_commands.do_enable(jobs=["a"], bundle=None)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("a")]
        node = config.current.job_from_ref(ref)
        assert node is not None and node.unit_disabled is False
        assert self._schedule_cell(h.full("a"), capsys) == "grouped"

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
        crony_commands.do_trigger(
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
        crony_commands.do_trigger(
            jobs=["j"], wait=False, trigger_timeout=None, bundle=None
        )
        cmd = next(
            c for c in h.calls if c[:3] == ["systemctl", "--user", "start"]
        )
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
        with pytest.raises(UsageError, match="not runnable here"):
            crony_commands.do_trigger(
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
        with pytest.raises(UsageError, match="not in the current config"):
            crony_commands.do_trigger(
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
            crony_runner,
            "trigger_unit_sync",
            lambda *_a, **_kw: {
                "exit_class": "timeout",
                "exit_code": None,
                "signal": None,
            },
        )
        with pytest.raises(SystemExit) as exc:
            crony_commands.do_trigger(
                jobs=["j"], wait=True, trigger_timeout=None, bundle=None
            )
        assert exc.value.code == int(ExitCode.TIMEOUT)

    def test_trigger_wait_uncapped_job_passes_inf(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An uncapped job (job_timeout_sec=0) must reach the waiter as
        # math.inf, not 0.0, so --wait does a single unbounded wait
        # instead of falling back to a 1s poll.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "job_timeout_sec": 0,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        captured: dict[str, Any] = {}

        def _capture(*_args: object, **kw: Any) -> dict[str, Any]:
            captured["job_timeout"] = kw["job_timeout"]
            return {"exit_class": "ok", "exit_code": 0, "signal": None}

        monkeypatch.setattr(crony_runner, "trigger_unit_sync", _capture)
        crony_commands.do_trigger(
            jobs=["j"], wait=True, trigger_timeout=None, bundle=None
        )
        assert math.isinf(captured["job_timeout"])

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
            crony_runner,
            "trigger_unit_sync",
            lambda *_a, **_kw: {
                "exit_class": "signal",
                "exit_code": None,
                "signal": 9,
            },
        )
        with pytest.raises(SystemExit) as exc:
            crony_commands.do_trigger(
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
            crony_runner,
            "trigger_unit_sync",
            lambda *_a, **_kw: {
                "exit_class": "fail",
                "exit_code": 7,
                "signal": None,
            },
        )
        with pytest.raises(SystemExit) as exc:
            crony_commands.do_trigger(
                jobs=["j"], wait=True, trigger_timeout=None, bundle=None
            )
        assert exc.value.code == 7

    def test_trigger_wait_treats_ok_as_zero_despite_nonzero_code(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A success_exit_codes match records exit_class "ok" with a
        # non-zero exit_code; `trigger --wait` must still exit 0, not
        # surface the raw code.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(
            crony_runner,
            "trigger_unit_sync",
            lambda *_a, **_kw: {
                "exit_class": "ok",
                "exit_code": 1,
                "signal": None,
            },
        )
        # rc maps to 0, so do_trigger returns without raising SystemExit.
        crony_commands.do_trigger(
            jobs=["j"], wait=True, trigger_timeout=None, bundle=None
        )

    def test_trigger_timeout_requires_wait(self) -> None:
        # --trigger-timeout is only meaningful under --wait; the parser
        # rejects the combination (exit 2) before dispatch.
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(
                ["trigger", "j", "--trigger-timeout", "10"]
            )
        assert exc.value.code == 2

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
        with pytest.raises(UsageError, match="crony apply"):
            crony_commands.do_trigger(
                jobs=["j"], wait=False, trigger_timeout=None, bundle=None
            )

    def _rename_keeping_uuid(self, h: _ApplyHarness, old: str, new: str) -> str:
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
        sd = h.state / DEFAULT_BUNDLE_NAME / job_uuid
        h.calls.clear()
        crony_commands.do_trigger(
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
        # enable by the new name re-renders the installed (old-name) unit.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        self._rename_keeping_uuid(h, "j", "k")
        crony_commands.do_disable(jobs=["k"], bundle=None)
        plist = h.agents / "org.crony.default.j.plist"
        assert "StartCalendarInterval" not in plist.read_text()
        h.calls.clear()
        crony_commands.do_enable(jobs=["k"], bundle=None)
        # The reload targets the old-name label, and the schedule is back.
        assert any("org.crony.default.j" in part for c in h.calls for part in c)
        assert "StartCalendarInterval" in plist.read_text()

    def test_disable_renamed_entry_by_new_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # disable by the new name re-renders the installed (old-name) unit
        # schedule-less.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        self._rename_keeping_uuid(h, "j", "k")
        h.calls.clear()
        crony_commands.do_disable(jobs=["k"], bundle=None)
        plist = h.agents / "org.crony.default.j.plist"
        assert any("org.crony.default.j" in part for c in h.calls for part in c)
        assert "StartCalendarInterval" not in plist.read_text()

    def test_destroy_renamed_entry_by_new_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # destroy by the new name removes the installed (old-name) unit
        # and wipes the shared uuid state dir.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        job_uuid = self._rename_keeping_uuid(h, "j", "k")
        sd = h.state / DEFAULT_BUNDLE_NAME / job_uuid
        old_plist = h.agents / f"org.crony.{h.full('j')}.plist"
        assert old_plist.exists()
        assert sd.exists()
        crony_commands.do_destroy(jobs=["k"], bundle=None, orphans=False)
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
        with pytest.raises(UsageError, match="crony apply"):
            crony_commands.do_destroy(jobs=["j"], bundle=None, orphans=False)

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
        with pytest.raises(UsageError, match="crony apply"):
            crony_commands.do_trigger(
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
        crony_commands.do_trigger(
            jobs=["a"], wait=False, trigger_timeout=None, bundle=None
        )
        cmd = next(c for c in h.calls if c[0] == "launchctl")
        assert cmd[1] == "kickstart"
        assert cmd[2].endswith(f"org.crony.{h.full('a')}")

    def test_apply_preserves_disabled_state_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The disabled flag lives on the snapshot, so a same-uuid re-apply
        # (here a schedule edit) carries it forward: load_config mirrors
        # it onto the pending node, the entry re-renders schedule-less, and
        # the timer is never re-armed.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_disable(jobs=["j"], bundle=None)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 04:00"}}},
            default_target_jobs=["j"],
        )
        h.calls.clear()
        h.apply("j")
        # Strip leading flags (`--user`, `--quiet`, etc.) and pull the
        # systemctl subcommand verb so the test isn't tied to flag order.
        verbs = [
            next((a for a in c[1:] if not a.startswith("-")), "")
            for c in h.calls
        ]
        assert "daemon-reload" in verbs
        assert "enable" not in verbs
        assert not (h.sysd / f"crony-{h.full('j')}.timer").exists()
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        node = config.current.job_from_ref(ref)
        assert node is not None and node.unit_disabled is True

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(UsageError, match="unknown bundle"):
            crony_commands.do_enable(jobs=[], bundle="ghost")
        with pytest.raises(UsageError, match="unknown bundle"):
            crony_commands.do_disable(jobs=[], bundle="ghost")
        with pytest.raises(UsageError, match="unknown bundle"):
            crony_commands.do_trigger(
                jobs=[], wait=False, trigger_timeout=None, bundle="ghost"
            )

    def test_names_or_all_required(self) -> None:
        # An unscoped bulk verb (no names, no --all) is rejected by the
        # parser (exit 2) so it can't silently act on everything --
        # including with only --bundle, which narrows but doesn't select.
        for verb in ("enable", "disable", "trigger"):
            for argv in ([verb], [verb, "-b", "x"]):
                with pytest.raises(SystemExit) as exc:
                    crony_cli._build_parser().parse_command(argv)
                assert exc.value.code == 2

    def test_all_with_names_rejected(self) -> None:
        for verb in ("enable", "disable", "trigger"):
            with pytest.raises(SystemExit) as exc:
                crony_cli._build_parser().parse_command([verb, "--all", "foo"])
            assert exc.value.code == 2

    def test_all_parses_and_is_consumed(self) -> None:
        # --all (optionally -b) parses; the validator consumes it so it
        # never reaches the handler signature.
        for verb in ("enable", "disable", "trigger"):
            for argv in ([verb, "--all"], [verb, "--all", "-b", "x"]):
                args = crony_cli._build_parser().parse_command(argv)
                assert args.command == verb
                assert not hasattr(args, "all_jobs")

    def test_enable_disable_bulk_includes_grouped_in_bundle(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # TomlBundle has one scheduled job and one schedule-less group
        # member. A bulk `-b foo` disable / enable acts on every stamped
        # entry, including the grouped one: `a` gains / loses its
        # `unit_disabled` flag alongside the scheduled `b` and `g`. The
        # scheduled entries also disarm / re-arm their `.timer`; the
        # grouped entry has no timer to disarm, so its only observable is
        # the snapshot flag.
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

        def disabled_flags() -> dict[str, bool]:
            config = crony_runtime.load_config()
            out: dict[str, bool] = {}
            for short in ("a", "b", "g"):
                ref = config.current.by_full_name[h.full(short)]
                node = config.current.job_from_ref(ref)
                assert node is not None
                out[short] = node.unit_disabled
            return out

        crony_commands.do_disable(jobs=[], bundle="default")
        assert disabled_flags() == {"a": True, "b": True, "g": True}
        assert not (h.sysd / f"crony-{h.full('b')}.timer").exists()
        assert not (h.sysd / f"crony-{h.full('g')}.timer").exists()

        crony_commands.do_enable(jobs=[], bundle="default")
        assert disabled_flags() == {"a": False, "b": False, "g": False}
        assert (h.sysd / f"crony-{h.full('b')}.timer").exists()
        assert (h.sysd / f"crony-{h.full('g')}.timer").exists()

    def test_bulk_without_bundle_acts_on_every_stamped_entry(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `--all` with no `--bundle` reaches the handler as empty names
        # and a None bundle, which expands to every stamped entry rather
        # than failing -- the unscoped whole-host action.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {
                "job": {
                    "a": {"command": "true", "schedule": "*-*-* 03:00"},
                    "b": {"command": "true", "schedule": "*-*-* 04:00"},
                },
            },
            default_target_jobs=["a", "b"],
        )
        h.apply("a")
        h.apply("b")

        def disabled_flags() -> dict[str, bool]:
            config = crony_runtime.load_config()
            out: dict[str, bool] = {}
            for short in ("a", "b"):
                ref = config.current.by_full_name[h.full(short)]
                node = config.current.job_from_ref(ref)
                assert node is not None
                out[short] = node.unit_disabled
            return out

        crony_commands.do_disable(jobs=[], bundle=None)
        assert disabled_flags() == {"a": True, "b": True}

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
        crony_commands.do_trigger(
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

        # Disable first (re-renders the installed unit), then enable
        # (re-renders it back) -- each acts on borgadm's installed entry;
        # enabling an already-enabled entry would no-op with no calls.
        h.calls.clear()
        crony_commands.do_disable(jobs=[], bundle="borgadm")
        assert any("borgadm.k" in str(c) for c in h.calls)

        h.calls.clear()
        crony_commands.do_enable(jobs=[], bundle="borgadm")
        assert any("borgadm.k" in str(c) for c in h.calls)

        h.calls.clear()
        crony_commands.do_trigger(
            jobs=[], wait=False, trigger_timeout=None, bundle="borgadm"
        )
        assert any("borgadm.k" in str(c) for c in h.calls)


class TestStatusUuidColumn:
    """`uuid` is an opt-in column rendering the `<bundle>:<UUID>`
    ref form. Default `cols=None` hides it (the default identity
    column is `job-or-uuid`, which shows the plain name for an
    unambiguous entry); `--cols job,uuid` surfaces the stable
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,uuid"),
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
        crony_commands.do_status(
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,uuid,config"),
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert ghost_uuid in out
        assert "orphan" in out


class TestStatusFieldColumns:
    """The opt-in `timeout` / `priority` / `stale` columns surface
    entry config fields (and which fields make an entry stale)."""

    def _status(self, cols: str, capsys: Any) -> str:
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg(cols),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out: str = capsys.readouterr().out
        return out

    def test_timeout_column_value_and_divergence(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "job-timeout-sec": 300,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        assert "300s" in self._status("job,timeout", capsys)
        # Edit the timeout without re-applying: pending shown, flagged.
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "job-timeout-sec": 600,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert "600s^" in self._status("job,timeout", capsys)

    def test_priority_column_value_and_divergence(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
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
        assert "high" in self._status("job,priority", capsys)
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
        assert "low^" in self._status("job,priority", capsys)

    def test_stale_lists_changed_fields(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "job-timeout-sec": 300,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        # Synced: nothing diverges.
        out = self._status("job,stale", capsys)
        assert "command" not in out
        # Change two fields without re-applying.
        h.config(
            {
                "job": {
                    "j": {
                        "command": "false",
                        "schedule": "daily",
                        "job-timeout-sec": 600,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        out = self._status("job,stale", capsys)
        # Sorted, reported by config-file name (timeout -> job-timeout-sec).
        assert "command,job-timeout-sec" in out

    def test_timeout_shows_group_budget_priority_blank(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # timeout is a shared field: a group node yields its cumulative
        # budget. priority is job-only, so a group yields no cell.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"j": {"command": "true"}},
                "job-group": {"g": {"jobs": ["j"], "schedule": "daily"}},
            },
            default_target_jobs=["g"],
        )
        h.apply("g")
        config = crony_runtime.load_config()
        gnode = config.current.job_from_ref(
            config.current.by_full_name[h.full("g")]
        )
        assert gnode is not None
        assert gnode.timeout > 0
        assert crony_commands._timeout_display(gnode) == f"{gnode.timeout}s"
        assert crony_commands._priority_display(gnode) is None

    def test_stale_includes_unit_config_drift(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A snapshot whose installed unit file drifted reads stale; the
        # launchd plist is the config unit, so the stale column reports
        # `unit-config-1`.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "daily"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # Hand-edit the installed unit file so its normalized form no
        # longer matches the pending node's render, while the snapshot is
        # unchanged.
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        plist.write_text(plist.read_text() + "\n<!-- edited -->\n")
        out = self._status("job,stale,unit-config-1", capsys)
        assert "unit-config-1" in out
        # The drifted unit's own column is flagged.
        row = next(
            line for line in out.splitlines() if line.startswith(h.full("j"))
        )
        assert "^" in row

    def test_stale_fields_helper_branches(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "jx": {"command": "true", "schedule": "daily"},
                    "child": {"command": "true"},
                },
                "job-group": {"gx": {"jobs": ["child"], "schedule": "daily"}},
            },
            default_target_jobs=["jx", "gx"],
        )
        config = crony_runtime.load_config()
        jnode = config.pending.job_from_ref(
            config.pending.by_full_name[h.full("jx")]
        )
        gnode = config.pending.job_from_ref(
            config.pending.by_full_name[h.full("gx")]
        )
        d = crony_commands._stale_fields
        assert isinstance(jnode, Job)
        assert d(None, None) == ""
        assert d(jnode, None) == ""
        assert d(jnode, gnode) == "kind"  # job vs group
        assert d(jnode, jnode) == ""  # identical snapshots
        # The opaque rendered_units field expands to the per-unit labels
        # that drifted (`unit-config-1` / `unit-config-2`).
        cfg_drift = dataclasses.replace(
            jnode,
            rendered_units=crony_platform.RenderedUnits(
                (crony_platform.RenderedUnit(Path("f1"), "edited"),)
            ),
        )
        assert d(jnode, cfg_drift) == "unit-config-1"
        both = dataclasses.replace(
            jnode,
            rendered_units=crony_platform.RenderedUnits(
                (
                    crony_platform.RenderedUnit(Path("f1"), "x"),
                    crony_platform.RenderedUnit(Path("f2"), "y"),
                )
            ),
        )
        assert d(jnode, both) == "unit-config-1,unit-config-2"
        # A snapshot-format bump labels as `snapshot-schema`, not `schema`.
        bumped = dataclasses.replace(jnode, snapshot_schema=4)
        assert d(jnode, bumped) == "snapshot-schema"

    def test_stale_expands_changed_flag_to_its_token(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A changed capability flag shows its own token, not `flags`.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "daily"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "flags": ["keep-awake"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        out = self._status("job,stale", capsys)
        assert "keep-awake" in out
        assert "flags" not in out


class TestStatusDivergenceFooter:
    """The `^` legend footer prints iff a displayed cell carries the
    marker -- not merely because the entry is stale in a hidden
    column."""

    _FOOTER = "One or more flagged cells are stale"

    def _stale_schedule(self, tmp_path: Path, monkeypatch: Any) -> None:
        # Apply a schedule, then edit it (same uuid, same name) without
        # re-applying: the entry is stale via `schedule`, but its name /
        # identity cells do not diverge.
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

    def test_footer_prints_when_marker_displayed(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        self._stale_schedule(tmp_path, monkeypatch)
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("schedule"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "^" in out
        assert self._FOOTER in out

    def test_footer_suppressed_when_no_marker_displayed(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # The divergence is in the (hidden) schedule column; the shown
        # columns carry no `^`, so the legend has nothing to explain.
        self._stale_schedule(tmp_path, monkeypatch)
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("config"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "stale" in out  # the entry is stale (CONFIG cell)
        assert "^" not in out  # but no displayed cell is flagged
        assert self._FOOTER not in out  # so the footer is suppressed


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
        crony_commands.do_status(
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
        assert "STATUS" in out
        assert "j" in out
        assert "synced" in out

    def test_orphan_appears(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.fabricate_orphan("ghost")
        h.config({}, default_target_jobs=[])
        crony_commands.do_status(
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
        crony_commands.do_status(
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,status,last-ran"),
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        header = out.splitlines()[0]
        assert "JOB" in header
        assert "STATUS" in header
        assert "LAST RAN" in header
        # Columns omitted from --cols are absent.
        assert "CONFIG" not in header
        assert "SCHEDULE" not in header
        assert "UNIT" not in header

    def test_cols_unknown_name_rejected(self) -> None:
        # --cols names are validated by the parser (type=), so an
        # unknown column exits 2 before dispatch.
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(
                ["status", "--cols", "job,bogus"]
            )
        assert exc.value.code == 2

    def test_parse_cols_arg_rejects_unknown(self) -> None:
        # The type= converter names the offending column(s).
        with pytest.raises(
            argparse.ArgumentTypeError, match="unknown status column"
        ):
            crony_commands.parse_cols_arg("job,bogus")

    def test_parse_cols_arg_classifies_token_kinds(self) -> None:
        # Each token resolves to its enum: column, alias, per-flag.
        tokens = crony_commands.parse_cols_arg("job,default,interactive")
        assert tokens == [
            crony_commands._StatusCols.JOB,
            crony_commands._StatusAliases.DEFAULT,
            JobFlagNames.INTERACTIVE,
        ]
        assert [type(t).__name__ for t in tokens] == [
            "_StatusCols",
            "_StatusAliases",
            "JobFlagNames",
        ]

    def test_defaults_is_silent_alias_for_default(self) -> None:
        # `defaults` resolves identically to `default` but is never
        # advertised: absent from the parse-error alias listing.
        assert crony_commands.parse_cols_arg(
            "defaults"
        ) == crony_commands.parse_cols_arg("default")
        with pytest.raises(argparse.ArgumentTypeError) as exc:
            crony_commands.parse_cols_arg("bogus")
        assert "defaults" not in str(exc.value)

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
            _dt.datetime.now(_dt.UTC).astimezone() - _dt.timedelta(minutes=5)
        ).isoformat(timespec="seconds")
        (sd / "last-run.json").write_text(
            f'{{"started_at": "{five_min_ago}",'
            f' "ended_at": "{five_min_ago}",'
            ' "exit_code": 0, "exit_class": "ok"}',
            encoding="utf-8",
        )
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,last-ran"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,last-ran"),
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
        crony_commands.do_status(
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
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
        crony_commands.do_status(
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
        crony_commands.do_status(
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,uuid"),
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job-or-uuid,config,groups"),
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job-or-uuid,config,groups"),
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job-or-uuid,config,schedule"),
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("default,masked-by"),
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "h")
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
        crony_commands.do_status(
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("default,masked-by"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("default,masked-by"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert h.full("j") not in out
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("default,masked-by"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("default,masked-by"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("default,masked-by"),
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

    def _all_header(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        capsys: Any,
        *,
        platform: str,
        show_masked: bool,
    ) -> str:
        h = _ApplyHarness(tmp_path, monkeypatch, platform=platform)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("all"),
            show_masked=show_masked,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        out: str = capsys.readouterr().out
        return out.splitlines()[0]

    def test_cols_all_alias_trims_context_blank_columns(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # On darwin without -a, `all` carries the broad set but drops the
        # columns that would always be blank here: masked-by (no -a),
        # unit-config-2 (launchd has none), and the per-flag columns (FLAGS
        # covers them).
        header = self._all_header(
            tmp_path, monkeypatch, capsys, platform="darwin", show_masked=False
        )
        for present in (
            "JOB",
            "KIND",
            "CONFIG",
            "SCHEDULE",
            "UNIT",
            "STATUS",
            "LAST RAN",
            "UUID",
            "UNIT CONFIG 1",
            "FLAGS",
            "TIMEOUT",
            "STALE",
        ):
            assert present in header
        assert "MASKED BY" not in header
        assert "UNIT CONFIG 2" not in header
        assert "INTERACTIVE" not in header

    def test_cols_all_alias_includes_masked_by_with_show_masked(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `-a` surfaces a masked entry, whose masked-by cell carries a
        # reason, so `all` keeps the column.
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("all"),
            show_masked=True,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        assert "MASKED BY" in capsys.readouterr().out.splitlines()[0]

    def test_cols_all_alias_keeps_masked_by_for_named_masked_job(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Naming a pure-masked entry surfaces it without -a; `all` then
        # keeps masked-by so its reason stays visible.
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
        crony_commands.do_status(
            jobs=["j"],
            cols=crony_commands.parse_cols_arg("all"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "MASKED BY" in out.splitlines()[0]
        row = next(
            line for line in out.splitlines() if line.startswith(h.full("j"))
        )
        assert "host" in row.split()

    def test_cols_all_alias_includes_unit_timer_on_linux(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        header = self._all_header(
            tmp_path, monkeypatch, capsys, platform="linux", show_masked=False
        )
        assert "UNIT CONFIG 2" in header

    def test_cols_all_alias_keeps_masked_by_for_orphan_remnant(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # An applied entry later masked here leaves an orphan-masked
        # remnant that surfaces its mask reason in the default view, so
        # `all` keeps masked-by even without -a.
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("all"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "MASKED BY" in out.splitlines()[0]
        row = next(
            line for line in out.splitlines() if line.startswith(h.full("j"))
        )
        assert "host" in row.split()

    def test_cols_default_alias_matches_no_cols(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            config_current=False,
            config_pending=False,
            bundle=None,
            exclude_healthy=False,
        )
        baseline = capsys.readouterr().out
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("default"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        aliased = capsys.readouterr().out
        assert baseline == aliased

    def test_cols_unit_files_alias_expands_to_config_and_timer(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `unit-files` is shorthand for both unit path columns; on Linux
        # a scheduled job has a .service (config) and a .timer.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,unit-files"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "UNIT CONFIG 1" in out
        assert "UNIT CONFIG 2" in out
        assert "crony-default.j.service" in out
        assert "crony-default.j.timer" in out

    def test_cols_unit_files_alias_drops_timer_on_darwin(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # launchd pins the schedule in the plist, so there is no timer
        # file; `unit-files` is just unit-config-1 on darwin.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,unit-files"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        header = capsys.readouterr().out.splitlines()[0]
        assert "UNIT CONFIG 1" in header
        assert "UNIT CONFIG 2" not in header

    def test_explicit_column_overrides_alias_trimming(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `all` drops unit-config-2 on darwin, but naming it explicitly
        # alongside the alias still shows it.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("all,unit-config-2"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        assert "UNIT CONFIG 2" in capsys.readouterr().out.splitlines()[0]

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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("default,masked-by"),
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
            "STATUS",
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
        with pytest.raises(UsageError, match="unknown bundle"):
            crony_commands.do_status(
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
        bundles = TomlConfig.load_all()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        h.apply("k", bundle="borgadm")
        crony_commands.do_status(
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job-or-uuid"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job-or-uuid"),
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
        monkeypatch.setattr(crony_platform, "current_host", lambda: "this-host")
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job-or-uuid"),
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
        bundles = TomlConfig.load_all()
        borgadm = bundles.by_name("borgadm")
        assert borgadm is not None
        h.apply("k", bundle="borgadm")
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job-or-uuid"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,kind"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,kind,config"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,unit-name"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,unit-name"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,groups"),
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,groups"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,groups"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,groups"),
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
        crony_commands.do_status(
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
        # the standalone runtime state isn't load-bearing for
        # day-to-day reading.
        assert "KIND" not in header
        # `UNIT` is a substring of `UNIT NAME`; check the bare header
        # label with surrounding whitespace.
        assert " UNIT " not in header
        assert not header.rstrip().endswith("UNIT")

    def test_status_help_epilog_lists_columns(self) -> None:
        parser = crony_cli._build_parser()
        # Locate the status subparser and pull its epilog text.
        subparsers_action = next(
            a
            for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        status_parser = subparsers_action.choices["status"]
        text = status_parser.format_help()
        # The generated reference wraps prose, so check whitespace-flat
        # for multi-word phrases.
        flat = re.sub(r"\s+", " ", text)
        # Every section header present (Columns / values / Aliases / Color),
        # rendered as a `Title:` header with the body indented beneath it.
        assert "Default Columns:\n" in text
        assert "Optional Columns:\n" in text
        assert "Column Aliases:\n" in text
        for col in [
            "job",
            "kind",
            "config",
            "schedule",
            "status",
            "last-ran",
            "masked-by",
            "unit-name",
            "uuid",
        ]:
            assert col in text
        # `default` alias enumerates its expansion so the block doubles as
        # the documentation of the default set.
        for col in crony_commands._DEFAULT_STATUS_COLS:
            assert col in text
        # The three aliases are each documented (each at the start of its
        # indented definition-list line).
        assert "default" in text
        assert "\n  all " in text
        assert "unit-files" in text
        # The `all` description notes the context-sensitive trimming.
        assert "Every column except the per-flag columns" in flat
        assert "shown only where a second unit is present" in flat
        # `unit-files` documents its optional second column.
        assert "plus the optional unit-config-2 where present" in flat

    def test_status_reference_sections_are_well_formed(self) -> None:
        # `status_reference_sections()` is the structured single source
        # behind both the `--help` epilog and the man page's STATUS
        # COLUMNS section. Assert the shape directly (the epilog test
        # only exercises it transitively).
        sections = crony_commands.status_reference_sections()
        titles = [s.title for s in sections]
        assert titles == [
            "Default Columns",
            "Optional Columns",
            "Column Aliases",
            "CONFIG values",
            "SCHEDULE values",
            "STATUS values",
            "FLAG values",
            "MASKED values",
            "Colors",
        ]
        # Every entry carries a non-empty label and description -- the
        # single-source guarantee that nothing renders blank.
        for section in sections:
            assert section.items, f"{section.title} has no items"
            for label, description in section.items:
                assert label, f"empty label in {section.title}"
                assert description, f"empty description for {label!r}"
        # Only the Colors section carries a lead paragraph.
        assert [s.title for s in sections if s.lead] == ["Colors"]
        # The CONFIG section covers every `ConfigStatus` verdict.
        config_labels = {
            label
            for s in sections
            if s.title == "CONFIG values"
            for label, _ in s.items
        }
        assert config_labels == {m.value for m in crony_model.ConfigStatus}

    def test_every_selectable_column_is_documented(self) -> None:
        # `_StatusCols` is the authoritative column set: the registry must
        # document every member exactly once (with a non-empty
        # description), and the selectable headers must be exactly the
        # members plus the per-flag tokens. Adding a `_StatusCols` member
        # without a registry entry, or vice versa, fails here.
        documented = {
            c.name
            for c in crony_commands._STATUS_COLUMNS
            if not c.name.startswith("<")
        }
        assert documented == set(crony_commands._StatusCols)
        flag_tokens = {f.token for f in JobFlags.members()}
        assert (
            set(crony_commands._STATUS_COL_HEADERS)
            == set(crony_commands._StatusCols) | flag_tokens
        )
        # Per-flag columns share the `<flag>` documentation entry.
        assert crony_commands._FLAG_COL_DOC in {
            c.name for c in crony_commands._STATUS_COLUMNS
        }
        for col in crony_commands._STATUS_COLUMNS:
            assert col.description, f"column {col.name!r} has no description"

    def test_every_alias_is_documented(self) -> None:
        # `_StatusAliases` is the authoritative alias set: `_STATUS_ALIASES`
        # documents every member, each with a description, and every
        # alias expands only to `_StatusCols` members.
        documented = {a.name for a in crony_commands._STATUS_ALIASES}
        assert documented == set(crony_commands._StatusAliases)
        assert set(crony_commands._STATUS_COL_ALIAS_NAMES) == set(
            crony_commands._StatusAliases
        )
        for alias in crony_commands._STATUS_ALIASES:
            assert alias.description, f"alias {alias.name!r} has no desc"
            for col in alias.cols:
                assert col in crony_commands._StatusCols, (
                    f"alias {alias.name!r} expands to non-column {col!r}"
                )

    def test_expand_status_alias_yields_only_columns(self) -> None:
        # Every alias expansion, under either masked / second-unit
        # context, is a subset of the selectable column set -- so an alias
        # can never select a column the renderer doesn't produce.
        selectable = set(crony_commands._StatusCols)
        for alias in crony_commands._StatusAliases:
            for second in (False, True):
                for masked in (False, True):
                    cols = crony_commands._expand_status_alias(
                        alias,
                        masked_present=masked,
                        second_unit_present=second,
                    )
                    assert set(cols) <= selectable

    def test_alias_expansion_trims_by_column_visibility(self) -> None:
        # The `all` alias lists every column but drops the conditional
        # ones whose `_ColVisibility` fails: `masked-by` only when a masked
        # row is shown, `unit-config-2` only when a shown row carries a
        # second unit. The trim is driven by the column property, so the
        # expansion tracks each context.
        _StatusCols = crony_commands._StatusCols

        def expand(masked: bool, second: bool) -> set[str]:
            return set(
                crony_commands._expand_status_alias(
                    crony_commands._StatusAliases.ALL,
                    masked_present=masked,
                    second_unit_present=second,
                )
            )

        assert _StatusCols.MASKED_BY in expand(True, False)
        assert _StatusCols.MASKED_BY not in expand(False, False)
        assert _StatusCols.UNIT_CONFIG_2 in expand(False, True)
        assert _StatusCols.UNIT_CONFIG_2 not in expand(False, False)
        # An unconditional column rides through every context.
        for masked in (False, True):
            for second in (False, True):
                assert _StatusCols.CONFIG in expand(masked, second)

    def test_every_column_visibility_value_is_used(self) -> None:
        # Each `_ColVisibility` value is exercised by at least one column,
        # so a value can't be added without a column that needs it (and
        # the corresponding `_column_in_context` branch stays covered).
        used = {col.visibility for col in crony_commands._STATUS_COLUMNS}
        assert used == set(crony_commands._ColVisibility)

    def test_status_renders_every_selectable_column(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Render with every selectable column named explicitly (`all`
        # omits the per-flag columns, so add each flag token too). Each
        # selected column does `row[col]`, so a column the registry lists
        # but `row_cells` doesn't build would KeyError here -- and the
        # `_build_row` assert that the two sets match fires first.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        flag_tokens = [f.token for f in JobFlags.members()]
        cols = ",".join(["all", *flag_tokens])
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg(cols),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "default.j" in out

    def test_color_section_lists_the_painted_values(self) -> None:
        # The Color section is generated from `_RED_CELLS` / `_YELLOW_CELLS`,
        # so every value those tables paint appears in the help -- guarding
        # against the docs drifting from `_status_value_color` (e.g. the
        # `disabled` SCHEDULE cell that renders red).
        parser = crony_cli._build_parser()
        subparsers_action = next(
            a
            for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        text = subparsers_action.choices["status"].format_help()
        color = text[text.index("Colors:") :]
        for table in (
            crony_commands._RED_CELLS,
            crony_commands._YELLOW_CELLS,
        ):
            for values in table.values():
                for value in values:
                    assert value in color, f"{value!r} missing from Color"

    def test_value_sections_list_every_enum_value(self) -> None:
        # The CONFIG / SCHEDULE / STATUS / FLAG value sections render
        # from the enums (and JobFlags), in the same `<value>
        # <description>` layout as the columns. Each value's label and
        # its description appear -- so a new value / flag can't be added
        # without it surfacing in the help.
        parser = crony_cli._build_parser()
        subparsers_action = next(
            a
            for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        text = subparsers_action.choices["status"].format_help()
        flat = re.sub(r"\s+", " ", text)
        described = (
            *crony_model.ConfigStatus,
            *crony_model.JobStatus,
            *crony_model.ScheduleValue,
        )
        for member in described:
            assert member.value in flat
            assert member.description.rstrip(".") in flat
        for flag in JobFlags.members():
            assert flag.token in flat
            assert flag.description.rstrip(".") in flat
        for reason in MaskReason:
            assert reason.value in flat
            assert reason.description.rstrip(".") in flat

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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,schedule"),
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
        # A disabled entry replaces the cron cell with `disabled` in
        # every view, including --config-pending: the pending config
        # carries the same disable (mirrored from the snapshot at load),
        # so the schedule it would compare against is `disabled` too --
        # showing the cron there would falsely imply the job will fire.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_disable(jobs=["j"], bundle=None)
        for pending in (False, True):
            crony_commands.do_status(
                jobs=[],
                cols=crony_commands.parse_cols_arg("job,schedule"),
                show_masked=False,
                bundle=None,
                config_current=False,
                config_pending=pending,
                exclude_healthy=False,
            )
            out = capsys.readouterr().out
            for line in out.splitlines():
                if "default.j " in line:
                    assert "disabled" in line
                    assert "*-*-* 03:00" not in line

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
        crony_commands.do_disable(jobs=["j"], bundle=None)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 09:00"}}},
            default_target_jobs=["j"],
        )
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,schedule"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,schedule"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,schedule"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,schedule"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=True,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "*-*-* 09:00^" in out
        assert "*-*-* 03:00" not in out

    def test_config_current_and_pending_mutually_exclusive(self) -> None:
        # The two source-pin flags form a parser mutex group, so the
        # combination exits 2 before dispatch.
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(
                ["status", "--config-current", "--config-pending"]
            )
        assert exc.value.code == 2

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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,config,masked-by"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "default.extra" not in out
        capsys.readouterr()
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,config,masked-by"),
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


class TestValidate:
    def test_clean_config(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony_commands.do_validate(bundle=None, file=None)
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
        crony_commands.do_validate(bundle=None, file=None)
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
            crony_commands.do_validate(bundle=None, file=None)
        assert exc.value.code == int(ExitCode.WARNING)
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
            crony_commands.do_validate(bundle=None, file=None)
        assert exc.value.code == int(ExitCode.WARNING)
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
        crony_commands.do_validate(bundle="borgadm", file=None)
        out = capsys.readouterr().out
        assert "ok" in out
        assert "orphans" not in out

    def test_bundle_unknown_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config({}, default_target_jobs=[])
        with pytest.raises(UsageError, match="unknown bundle"):
            crony_commands.do_validate(bundle="ghost", file=None)

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
            crony_commands.do_validate(bundle=None, file=None)
        assert exc.value.code == int(ExitCode.WARNING)
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
            crony_commands.do_validate(bundle=None, file=None)
        assert exc.value.code == int(ExitCode.WARNING)
        out = capsys.readouterr().out
        assert "[target.darwin]" in out
        assert "undefined name" in out

    def test_file_mode_valid(self, tmp_path: Path, capsys: Any) -> None:
        # Mirrors a borgadm-style drop-in: a non-default bundle whose
        # check job omits notify-channels (implicit inherit) and whose
        # noisy job silences itself, plus priority/keep-awake. The
        # bundle name comes from the filename stem.
        p = tmp_path / "borgadm.toml"
        p.write_text(
            "[job.create]\n"
            'uuid = "11111111-1111-5111-8111-111111111111"\n'
            'command = "wrapper create"\n'
            'interval = "1h"\n'
            'priority = "high"\n'
            "keep-awake = true\n"
            "notify-channels = []\n"
            "[job.check-age]\n"
            'uuid = "22222222-2222-5222-8222-222222222222"\n'
            'command = "borgadm check age"\n'
            'interval = "1d"\n'
            'priority = "high"\n'
            "keep-awake = true\n"
            "[target.darwin]\n"
            'jobs = ["create", "check-age"]\n',
            encoding="utf-8",
        )
        crony_commands.do_validate(bundle=None, file=str(p))
        out = capsys.readouterr().out
        assert "ok" in out
        assert "bundle 'borgadm'" in out

    def test_file_mode_warns_on_legacy_underscore_keys(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        # A file still using the legacy underscore spelling validates
        # (the key still parses) but draws a single deprecation warning
        # and exits WARNING.
        p = tmp_path / "borgadm.toml"
        p.write_text(
            "[job.create]\n"
            'uuid = "11111111-1111-5111-8111-111111111111"\n'
            'command = "wrapper create"\n'
            'interval = "1h"\n'
            "keep_awake = true\n"
            "[target.darwin]\n"
            'jobs = ["create"]\n',
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            crony_commands.do_validate(bundle=None, file=str(p))
        assert exc.value.code == int(ExitCode.WARNING)
        out = capsys.readouterr().out
        assert "ok" in out
        # One warning line naming the legacy key.
        assert out.count("legacy underscore-spelled") == 1
        assert "keep_awake" in out

    def test_file_mode_rejects_invalid_entry(self, tmp_path: Path) -> None:
        p = tmp_path / "borgadm.toml"
        p.write_text(
            "[job.create]\n"
            'uuid = "11111111-1111-5111-8111-111111111111"\n'
            'command = "true"\n'
            'interval = "1h"\n'
            'priority = "turbo"\n'
            "[target.darwin]\n"
            'jobs = ["create"]\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="invalid config"):
            crony_commands.do_validate(bundle=None, file=str(p))

    def test_file_mode_rejects_structural_error(self, tmp_path: Path) -> None:
        p = tmp_path / "borgadm.toml"
        p.write_text('[defaults]\nnotify_channels = "nope"\n', encoding="utf-8")
        with pytest.raises(ConfigError):
            crony_commands.do_validate(bundle=None, file=str(p))

    def test_file_mode_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="config not found"):
            crony_commands.do_validate(
                bundle=None, file=str(tmp_path / "nope.toml")
            )

    def test_file_mode_bad_bundle_name_from_stem(self, tmp_path: Path) -> None:
        # Stem "bad.name" carries a dot -> not a valid bundle name.
        p = tmp_path / "bad.name.toml"
        p.write_text(
            "[job.j]\n"
            'uuid = "11111111-1111-5111-8111-111111111111"\n'
            'command = "true"\n'
            'interval = "1h"\n'
            "[target.darwin]\n"
            'jobs = ["j"]\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="bundle name"):
            crony_commands.do_validate(bundle=None, file=str(p))

    def test_file_mode_default_config_uses_default_semantics(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # The installed config.toml validates as the 'default' bundle,
        # not bundle 'config' from its stem.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.cfg_file.write_text(
            _uuid_toml('[job.j]\ncommand = "true"\nschedule = "daily"\n'),
            encoding="utf-8",
        )
        crony_commands.do_validate(bundle=None, file=str(h.cfg_file))
        assert "bundle 'default'" in capsys.readouterr().out

    def test_file_mode_default_config_rejects_inherit_sentinel(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Validated as 'default', the notify-inherit sentinel is
        # rejected -- proving CONFIG_FILE gets default-bundle
        # semantics, not stem-based 'config' semantics (where the
        # sentinel would be allowed).
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.cfg_file.write_text(
            '[defaults]\nnotify_channels = ["default"]\n', encoding="utf-8"
        )
        with pytest.raises(ConfigError, match="cannot inherit its own"):
            crony_commands.do_validate(bundle=None, file=str(h.cfg_file))


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
        crony_commands.do_logs(
            job="j", n=5, since=None, tail=False, path=False, latest=False
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
        crony_commands.do_logs(
            job="j",
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

        def _interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(crony_commands.time, "sleep", _interrupt)
        # Mirrors the resolution `do_logs` performs when `n is None`
        # and `tail` is True.
        crony_commands._follow_log(log, n=10)
        out = capsys.readouterr().out
        printed = out.splitlines()
        assert printed == [f"line-{i}" for i in range(40, 50)]

    def test_missing_log_raises(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(UsageError, match="no log"):
            crony_commands.do_logs(
                job="ghost",
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
        crony_commands.do_logs(
            job="j", n=0, since=None, tail=False, path=True, latest=False
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
        crony_commands.do_logs(
            job="j", n=0, since=None, tail=False, path=True, latest=False
        )
        out = capsys.readouterr().out.strip()
        expected = h.state_dir("j") / "run.log"
        assert out == str(expected)

    def test_path_reports_alias_for_applied_entry(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_logs(
            job="j", n=0, since=None, tail=False, path=True, latest=False
        )
        out = capsys.readouterr().out.strip()
        # The applied entry has a short-name alias, so -p reports the
        # uuid-free path -- which resolves to the real run.log.
        alias_log = h.state / "default" / "j" / "run.log"
        assert out == str(alias_log)
        uuid_log = h.state_dir("j", ensure_snapshot=False) / "run.log"
        assert Path(out).resolve() == uuid_log.resolve()

    def test_path_falls_back_to_uuid_when_alias_missing(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # Remove the alias: the path must fall back to the uuid form so
        # it still resolves to a real location.
        (h.state / "default" / "j").unlink()
        crony_commands.do_logs(
            job="j", n=0, since=None, tail=False, path=True, latest=False
        )
        out = capsys.readouterr().out.strip()
        uuid_log = h.state_dir("j", ensure_snapshot=False) / "run.log"
        assert out == str(uuid_log)

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
            _dt.datetime.now(_dt.UTC).astimezone().isoformat(timespec="seconds")
        )
        log.write_text(
            f"=== {old_iso} j pid=1 ===\nold-line\n"
            f"=== {now_iso} j pid=2 ===\nnew-line\n",
            encoding="utf-8",
        )
        crony_commands.do_logs(
            job="j",
            n=0,
            since=crony_commands.parse_since_arg("1h"),
            tail=False,
            path=False,
            latest=False,
        )
        out = capsys.readouterr().out
        assert "new-line" in out
        assert "old-line" not in out

    def test_parse_since_unparseable(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="unparseable"):
            crony_commands.parse_since_arg("eventually")

    def test_parse_since_naive_iso_rejected(self) -> None:
        # Naive ISO would crash later when compared with tz-aware
        # run-header timestamps; surface at parse time instead.
        with pytest.raises(argparse.ArgumentTypeError, match="timezone offset"):
            crony_commands.parse_since_arg("2026-04-01T12:00:00")

    def test_since_unparseable_rejected_by_parser(self) -> None:
        # The --since type= converter routes a bad value through the
        # parser, so it exits 2 before any log lookup.
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(
                ["logs", "j", "--since", "eventually"]
            )
        assert exc.value.code == 2

    def test_follow_log_returns_cleanly_on_keyboard_interrupt(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Ctrl-C during `crony logs -t` should exit without a stack
        # trace. The follow loop sleeps on time.sleep(); raising
        # KeyboardInterrupt from there mimics the live signal.
        log = tmp_path / "run.log"
        log.write_text("existing line\n")

        def _interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(crony_commands.time, "sleep", _interrupt)
        # Returns cleanly rather than propagating the KeyboardInterrupt.
        crony_commands._follow_log(log)

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

        def _interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(crony_commands.time, "sleep", _interrupt)
        crony_commands._follow_log(log, n=5)
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

        def _interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(crony_commands.time, "sleep", _interrupt)
        crony_commands._follow_log(log, n=0)
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
        crony_commands.do_logs(
            job="j",
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
        crony_commands.do_logs(
            job="j",
            n=0,
            since=None,
            tail=False,
            path=False,
            latest=True,
        )
        out = capsys.readouterr().out
        assert "orphan content" in out

    def test_latest_and_tail_mutually_exclusive(self) -> None:
        # --tail and --latest form a parser mutex group, so the
        # combination exits 2 before any log lookup.
        with pytest.raises(SystemExit) as exc:
            crony_cli._build_parser().parse_command(
                ["logs", "j", "--tail", "--latest"]
            )
        assert exc.value.code == 2


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
        sd = h.state / DEFAULT_BUNDLE_NAME / ghost_uuid
        sd.mkdir(parents=True)
        (sd / "run.log").write_text("stale\n", encoding="utf-8")
        h.config({}, default_target_jobs=[])
        ref_input = f"{DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
        crony_commands.do_destroy(jobs=[ref_input], bundle=None, orphans=False)
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
        ref_input = f"{DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
        with pytest.raises(UsageError, match="unknown name"):
            crony_commands.do_destroy(
                jobs=[ref_input], bundle=None, orphans=False
            )

    def test_destroy_by_ref_rejects_path_traversal(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The ref parser rejects non-canonical uuid bodies;
        # destroy then treats the input as a normal full name and
        # rejects it as unknown. The would-be
        # `STATE_DIR/default/../../etc` target is never composed.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        attack = f"{DEFAULT_BUNDLE_NAME}:../../etc"
        with pytest.raises(UsageError, match="unknown name"):
            crony_commands.do_destroy(jobs=[attack], bundle=None, orphans=False)

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
        ref_input = f"{DEFAULT_BUNDLE_NAME}:{cfg.jobs['j'].uuid}"
        crony_commands.do_destroy(jobs=[ref_input], bundle=None, orphans=False)
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
        sd = h.state / DEFAULT_BUNDLE_NAME / ghost_uuid
        sd.mkdir(parents=True)
        lock = sd / "run.lock"
        held = open(lock, "w")
        _fcntl.flock(held, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        try:
            h.config({}, default_target_jobs=[])
            ref_input = f"{DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
            with pytest.raises(LockBusyError, match="run in progress"):
                crony_commands.do_destroy(
                    jobs=[ref_input], bundle=None, orphans=False
                )
        finally:
            _fcntl.flock(held, _fcntl.LOCK_UN)
            held.close()
        # State dir survived because the destroy refused.
        assert sd.exists()


class TestSnapshotlessDirOrphan:
    """A uuid dir with no snapshot.json is a nameless orphan: it
    renders as a ref-form `orphan` status row (so the operator can
    see and paste it) and is reclaimed by `destroy --orphans`.
    """

    def _plant(self, h: Any) -> tuple[str, Path]:
        ghost = "deadbeef-0000-0000-0000-deadbeef0000"
        sd = h.state / DEFAULT_BUNDLE_NAME / ghost
        sd.mkdir(parents=True)
        (sd / "run.log").write_text("stale\n", encoding="utf-8")
        return f"{DEFAULT_BUNDLE_NAME}:{ghost}", sd

    def test_renders_as_ref_form_orphan_row(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        ref_form, _ = self._plant(h)
        crony_commands.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert any(
            ref_form in line and "orphan" in line for line in out.splitlines()
        ), out

    def test_reclaimed_by_destroy_orphans(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        _ref_form, sd = self._plant(h)
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=True)
        assert not sd.exists()

    def test_explicit_destroy_reclaims_live_entrys_empty_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An interrupted apply can leave an empty dir at a configured
        # entry's uuid (no snapshot, no unit). `destroy <name>` resolves
        # to that uuid and reclaims the dir -- it isn't stranded just
        # because the entry is still in config.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        sd = h.state / DEFAULT_BUNDLE_NAME / cfg.jobs["j"].uuid
        sd.mkdir(parents=True)
        crony_commands.do_destroy(
            jobs=[h.full("j")], bundle=None, orphans=False
        )
        assert not sd.exists()


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
        sd = h.state / DEFAULT_BUNDLE_NAME / ghost_uuid
        sd.mkdir(parents=True)
        (sd / "run.log").write_text("hello\n", encoding="utf-8")
        ref_input = f"{DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
        crony_commands.do_logs(
            job=ref_input,
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
        ref_input = f"{DEFAULT_BUNDLE_NAME}:{ghost_uuid}"
        crony_commands.do_logs(
            job=ref_input,
            n=None,
            since=None,
            tail=False,
            path=True,
            latest=False,
        )
        out = capsys.readouterr().out.strip()
        assert out.endswith(f"{ghost_uuid}/run.log")


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
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony_platform, "current_host", lambda: "test-host")
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
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
        sd = crony_paths.STATE_DIR / "default" / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps({"schema": 999, "kind": "job", "name": "default.j"}),
            encoding="utf-8",
        )
        crony_commands.do_status(
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
        sd = crony_paths.STATE_DIR / "default" / uuid_value
        sd.mkdir(parents=True)
        # Corrupt JSON: the `name` field can't be recovered, so the
        # broken entry is addressable only by ref.
        (sd / "snapshot.json").write_text("{not valid json", encoding="utf-8")
        crony_commands.do_status(
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
        sd = crony_paths.STATE_DIR / "borgadm" / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps({"schema": 999, "kind": "job", "name": "borgadm.k"}),
            encoding="utf-8",
        )
        # borgadm's config file fails to parse: an errored bundle,
        # not a loaded one, yet it has on-disk state.
        (crony_paths.CONFIG_DROPIN_DIR / "borgadm.toml").write_text(
            "this is not [valid toml", encoding="utf-8"
        )
        crony_commands.do_status(
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
                    "schema": CURRENT_SNAPSHOT_SCHEMA,
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
                    "timeout": 600,
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
        config = crony_runtime.load_config()
        live_ref = EntityRef("default", live_uuid)
        stray_ref = EntityRef("default", stray_uuid)
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
        crony_commands.do_status(
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

    def test_shadowed_row_shows_ref_in_plain_job_column(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # The plain JOB column must not print the loser's phantom name
        # (it resolves to the winner), so the operator never sees two
        # rows reading `default.j` and can address the loser by ref.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        stray_uuid = "99999999-8888-7777-6666-555544443333"
        self._plant_residue(h, h.full("j"), stray_uuid)
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        loser = next(
            ln for ln in out.splitlines() if f"default:{stray_uuid}" in ln
        )
        assert h.full("j") not in loser
        # The winner's name appears exactly once across the table.
        assert sum(h.full("j") in ln for ln in out.splitlines()) == 1

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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=True)
        # Shadowed residue reclaimed; the live entry's dir + shared
        # unit are untouched (it's selected, not an orphan).
        assert not stray_dir.exists()
        assert (h.state / "default" / live_uuid / "snapshot.json").is_file()
        assert plist.exists()


class TestUnitOnlyOrphan:
    """A platform unit file with no corresponding state dir
    becomes a `Config.orphans` entry with a deterministic
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
        config = crony_runtime.load_config()
        ref = config.orphans_by_full_name.get("default.ghost")
        assert ref is not None
        # The synthetic uuid is deterministic: repeat loads
        # produce the same ref.
        config2 = crony_runtime.load_config()
        assert config2.orphans_by_full_name["default.ghost"] == ref
        # The platform unit path is captured in RuntimeState.
        rt = config.runtime[ref]
        assert rt.unit_paths[0] == plist
        # `cfg_status` reports orphan, not broken / synced /
        # stale.
        assert config.cfg_status(ref) == "orphan"

    def test_destroy_wipes_unit_only_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = self._setup(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        plist = h.agents / "org.crony.default.ghost.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("", encoding="utf-8")
        crony_commands.do_destroy(
            jobs=["default.ghost"], bundle=None, orphans=False
        )
        assert not plist.exists()

    def test_stray_timer_only_lands_in_unit_only(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A leftover `.timer` with no `.service` and no state dir (e.g.
        # a botched manual cleanup) must still surface as an orphan with
        # a synthetic ref the user can destroy -- the scheduler walks
        # both unit kinds for installed_names.
        h = self._setup(tmp_path, monkeypatch, platform="linux")
        h.config({}, default_target_jobs=[])
        timer = h.sysd / "crony-default.ghost.timer"
        timer.parent.mkdir(parents=True, exist_ok=True)
        timer.write_text("", encoding="utf-8")
        config = crony_runtime.load_config()
        ref = config.orphans_by_full_name.get("default.ghost")
        assert ref is not None
        assert config.cfg_status(ref) == "orphan"
        rt = config.runtime[ref]
        # The orphan is the timer (unit-config-2) with no config unit
        # (unit-config-1) on disk.
        assert rt.unit_paths[0] is None
        assert rt.unit_paths[1] == timer

    def test_destroy_wipes_stray_timer_by_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # status surfaces the orphan under its recoverable name, so the
        # operator destroys it by name; remove_files unlinks the timer
        # (and the absent service, tolerantly).
        h = self._setup(tmp_path, monkeypatch, platform="linux")
        h.config({}, default_target_jobs=[])
        timer = h.sysd / "crony-default.ghost.timer"
        timer.parent.mkdir(parents=True, exist_ok=True)
        timer.write_text("", encoding="utf-8")
        crony_commands.do_destroy(
            jobs=["default.ghost"], bundle=None, orphans=False
        )
        assert not timer.exists()


class TestAliasOrphan:
    """A short-name alias symlink whose name no live entry accounts for
    is a `Config.orphans` entry (has_symlink), cleaned by
    destroy --orphans / factory reset and by a targeted destroy. The
    alias is unlinked but its target dir -- which may belong to a live
    entry under another name -- is never touched.
    """

    def test_stray_alias_lands_in_orphans(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        # An alias pointing at a uuid dir nothing else references.
        (h.state / "default").mkdir(parents=True, exist_ok=True)
        alias = h.state / "default" / "ghost"
        alias.symlink_to("11111111-2222-3333-4444-555555555555")
        config = crony_runtime.load_config()
        ref = config.orphans_by_full_name.get("default.ghost")
        assert ref is not None
        orphan = config.orphans[ref]
        assert orphan.has_symlink
        assert config.cfg_status(ref) == "orphan"

    def test_destroy_orphans_unlinks_stray_alias(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        (h.state / "default").mkdir(parents=True, exist_ok=True)
        alias = h.state / "default" / "ghost"
        alias.symlink_to("11111111-2222-3333-4444-555555555555")
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=True)
        assert not alias.is_symlink()

    def test_rename_leftover_alias_is_orphan_target_untouched(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An alias whose name no entry claims but whose target is a
        # live entry's uuid dir (a stale rename leftover): the alias is
        # an orphan, but cleaning it must only unlink the alias -- the
        # live target dir stays intact.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        live_dir = h.state_dir("j", ensure_snapshot=False)
        stale_alias = h.state / "default" / "oldname"
        stale_alias.symlink_to(live_dir.name)
        config = crony_runtime.load_config()
        ref = config.orphans_by_full_name.get("default.oldname")
        assert ref is not None
        assert config.orphans[ref].has_symlink
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=True)
        assert not stale_alias.is_symlink()
        # The live entry and its alias are untouched.
        assert live_dir.is_dir()
        assert (h.state / "default" / "j").resolve() == live_dir.resolve()

    def test_dangling_alias_is_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        (h.state / "default").mkdir(parents=True, exist_ok=True)
        alias = h.state / "default" / "ghost"
        # Target dir does not exist -> dangling link.
        alias.symlink_to("deadbeef-0000-0000-0000-000000000000")
        config = crony_runtime.load_config()
        ref = config.orphans_by_full_name.get("default.ghost")
        assert ref is not None
        # A dangling alias (its target dir gone) still surfaces as a
        # symlink-bearing orphan that destroy can reclaim.
        assert config.orphans[ref].has_symlink
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=True)
        assert not alias.is_symlink()


class TestStatusUnitConfigColumn:
    """`crony status --cols ...,unit-config-1,unit-config-2` shows the on-disk
    paths of the platform config / timer units. The cell values come
    from `RuntimeState.unit_paths` (the platform's ordered per-unit view)
    so subcommands don't re-walk the unit dirs themselves.
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg(
                "job,unit-config-1,unit-config-2"
            ),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert "UNIT CONFIG 1" in out
        assert "org.crony.default.j.plist" in out
        # launchd has no separate timer unit: the column renders empty.
        assert "UNIT CONFIG 2" in out
        assert ".timer" not in out

    def test_unit_config_and_timer_render_on_linux(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg(
                "job,unit-config-1,unit-config-2"
            ),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        # The config unit is the .service; the schedule arm is the
        # separate .timer.
        assert "crony-default.j.service" in out
        assert "crony-default.j.timer" in out


class TestStatusLogFileColumn:
    """`crony status --cols log-file` shows the entry's reported log
    path (the form `crony logs` reads). Opt-in (not in the default
    columns); dual-source, so a not-yet-applied rename flags `^`.
    """

    def _status(self, cols: str, **kw: Any) -> None:
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg(cols),
            show_masked=False,
            bundle=None,
            config_current=kw.get("config_current", False),
            config_pending=kw.get("config_pending", False),
            exclude_healthy=False,
        )

    def test_log_file_not_in_default_columns(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_status(
            jobs=[],
            cols=None,
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        assert "LOG FILE" not in capsys.readouterr().out

    def test_log_file_renders_alias_path(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        self._status("job,log-file")
        out = capsys.readouterr().out
        assert "LOG FILE" in out
        alias_log = h.state / "default" / "j" / "run.log"
        assert str(alias_log) in out

    def test_log_file_flags_pending_rename(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"foo": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["foo"],
        )
        h.apply("foo")
        foo_uuid = cfg.jobs["foo"].uuid
        h.config(
            {
                "job": {
                    "bar": {
                        "uuid": foo_uuid,
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                    }
                }
            },
            default_target_jobs=["bar"],
        )
        # Default view shows the pending (config) path with a `^` since
        # the applied path still points at the old name.
        self._status("job,log-file")
        out = capsys.readouterr().out
        assert str(h.state / "default" / "bar" / "run.log") in out
        assert "^" in out
        # --config-current shows the applied (old-name) path.
        self._status("job,log-file", config_current=True)
        out = capsys.readouterr().out
        assert str(h.state / "default" / "foo" / "run.log") in out


class TestStatusFlagsColumns:
    """`crony status` exposes resolved capability flags: a `flags`
    summary column listing the enabled flags, plus one true / false
    column per `JobFlags` member. All opt-in (not in the default
    set); dual-source, so a not-yet-applied flag change flags `^` on
    the individual flag that diverged.
    """

    def _status(self, cols: str, **kw: Any) -> None:
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg(cols),
            show_masked=False,
            bundle=None,
            config_current=kw.get("config_current", False),
            config_pending=kw.get("config_pending", False),
            exclude_healthy=False,
        )

    def _row_for(self, out: str, needle: str) -> str:
        # The single data line mentioning `needle` (header excluded).
        lines = out.splitlines()
        return next(ln for ln in lines[1:] if ln.strip() and needle in ln)

    def test_flag_columns_not_in_default(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "flags": ["keep-awake"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        self._status("default")
        header = capsys.readouterr().out.splitlines()[0]
        assert "FLAGS" not in header
        assert "INTERACTIVE" not in header
        assert "KEEP-AWAKE" not in header

    def test_flags_summary_lists_enabled_flags(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "flags": ["interactive", "keep-awake"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        self._status("job,flags")
        out = capsys.readouterr().out
        assert "FLAGS" in out.splitlines()[0]
        # Members render in declaration order (interactive, keep-awake).
        assert "interactive,keep-awake" in self._row_for(out, h.full("j"))

    def test_flags_summary_empty_when_no_flag_enabled(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        self._status("job,flags")
        out = capsys.readouterr().out
        row = self._row_for(out, h.full("j"))
        assert "interactive" not in row
        assert "keep-awake" not in row

    def test_per_flag_columns_show_true_false(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "flags": ["keep-awake"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        self._status("job,interactive")
        out = capsys.readouterr().out
        assert "INTERACTIVE" in out.splitlines()[0]
        assert self._row_for(out, h.full("j")).split()[-1] == "false"
        self._status("job,keep-awake")
        out = capsys.readouterr().out
        assert "KEEP-AWAKE" in out.splitlines()[0]
        assert self._row_for(out, h.full("j")).split()[-1] == "true"

    def test_all_alias_omits_per_flag_columns_for_flags(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # `all` carries the compact FLAGS column but not the redundant
        # per-flag columns; those stay reachable by explicit name.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        self._status("all")
        header = capsys.readouterr().out.splitlines()[0]
        assert "FLAGS" in header
        assert "INTERACTIVE" not in header
        assert "KEEP-AWAKE" not in header
        # Explicitly named, a per-flag column still shows.
        self._status("interactive")
        assert "INTERACTIVE" in capsys.readouterr().out.splitlines()[0]

    def test_pending_flag_change_flags_caret(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Apply with the flag off, then turn it on in config without
        # re-applying: the pending node enables keep-awake while the
        # applied snapshot still has it off, so the individual flag
        # diverges. The shared uuid pin makes the second config an
        # edit of the same entry rather than a replacement.
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
                        "flags": ["keep-awake"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        # Default (pending-first): keep-awake reads true with a `^`, the
        # summary lists `keep-awake^`, and the stale footer prints.
        self._status("job,keep-awake,flags")
        out = capsys.readouterr().out
        row = self._row_for(out, h.full("j"))
        assert "true^" in row
        assert "keep-awake^" in row
        assert "One or more flagged cells are stale" in out
        # --config-current shows the applied (off) value, still `^`-
        # flagged since the other side differs.
        self._status("job,keep-awake", config_current=True)
        out = capsys.readouterr().out
        assert "false^" in self._row_for(out, h.full("j"))

    def test_summary_midstring_marker_prints_footer(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # The summary tags individual flags, so a diverged flag that
        # isn't the last one enabled leaves the `^` mid-cell (e.g.
        # `interactive^,keep-awake`). With only the `flags` column shown
        # that marker is the sole one on screen, so the footer gate must
        # detect it anywhere in the cell, not just at the end.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "flags": ["keep-awake"],
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
                        "flags": ["interactive", "keep-awake"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        self._status("flags")
        out = capsys.readouterr().out
        row = self._row_for(out, "interactive^,keep-awake")
        assert not row.rstrip().endswith("^")
        assert "One or more flagged cells are stale" in out

    def test_group_row_shows_resolved_flags(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A group shows its resolved cascade value the same way a job
        # does, so the inheritance it seeds into its children is visible
        # down the column: the group and its child both read keep-awake
        # true.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "job": {"a": {"command": "true"}},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "schedule": "*-*-* 03:00",
                        "flags": ["keep-awake"],
                    }
                },
            },
            default_target_jobs=["g"],
        )
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        self._status("job,keep-awake,flags")
        out = capsys.readouterr().out
        group_row = self._row_for(out, h.full("g"))
        child_row = self._row_for(out, h.full("a"))
        assert "true" in group_row
        assert "keep-awake" in group_row
        assert "true" in child_row
        assert "keep-awake" in child_row


class TestStatusFullDiskAccess:
    """`crony status` reads the shared Crony.app wrapper state onto a
    full-disk-access job's current node and folds it into the CONFIG
    verdict: a wrapper that can't serve the grant (not built, or
    ungranted) reads `error`; a stale wrapper reads `stale` (diverged
    `fda-wrapper`); an ok wrapper leaves the job synced. The wrapper
    probe is mocked so this runs on any platform."""

    def _status(self, cols: str) -> None:
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg(cols),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )

    def _row_for(self, out: str, needle: str) -> str:
        return next(
            ln for ln in out.splitlines()[1:] if ln.strip() and needle in ln
        )

    def _applied_fda(self, tmp_path: Path, monkeypatch: Any) -> Any:
        h = _ApplyHarness(tmp_path, monkeypatch)  # platform -> darwin
        # Apply writes the snapshot through apply_one; the wrapper build
        # is irrelevant to that path, so keep it inert.
        monkeypatch.setattr(crony_fda, "build_wrapper", lambda: None)
        h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "flags": ["full-disk-access"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        return h

    def test_missing_wrapper_reads_error(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = self._applied_fda(tmp_path, monkeypatch)
        monkeypatch.setattr(
            crony_fda, "wrapper_state", lambda: FDAWrapper.MISSING
        )
        self._status("job,config")
        out = capsys.readouterr().out
        assert "error" in self._row_for(out, h.full("j"))

    def test_ungranted_wrapper_reads_error(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = self._applied_fda(tmp_path, monkeypatch)
        monkeypatch.setattr(
            crony_fda, "wrapper_state", lambda: FDAWrapper.MISSING_FDA_GRANT
        )
        self._status("job,config")
        out = capsys.readouterr().out
        assert "error" in self._row_for(out, h.full("j"))

    def test_stale_wrapper_reads_stale_with_reason(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = self._applied_fda(tmp_path, monkeypatch)
        monkeypatch.setattr(
            crony_fda, "wrapper_state", lambda: FDAWrapper.STALE
        )
        self._status("job,config,stale")
        out = capsys.readouterr().out
        row = self._row_for(out, h.full("j"))
        assert "stale" in row
        assert "fda-wrapper" in row

    def test_ok_wrapper_leaves_job_synced(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = self._applied_fda(tmp_path, monkeypatch)
        monkeypatch.setattr(crony_fda, "wrapper_state", lambda: FDAWrapper.OK)
        self._status("job,config")
        out = capsys.readouterr().out
        assert "synced" in self._row_for(out, h.full("j"))

    def test_non_fda_job_unaffected_by_missing_wrapper(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(
            crony_fda, "wrapper_state", lambda: FDAWrapper.MISSING
        )
        self._status("job,config")
        out = capsys.readouterr().out
        assert "synced" in self._row_for(out, h.full("j"))


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
        crony_commands.do_status(
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
        crony_commands.do_status(
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
        crony_commands.do_status(
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
        crony_commands.do_disable(jobs=["paused"], bundle=None)
        crony_commands.do_status(
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
        assert snap["timeout"] == 600
        assert snap["schema"] == CURRENT_SNAPSHOT_SCHEMA

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
        assert snap["timeout"] == 315

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
        assert snap["timeout"] == 105

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
        assert snap["timeout"] == 105

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
        assert snap["timeout"] == 105

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

    def test_snapshot_env_merges_defaults(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # [defaults.env] is merged under each job's env; a job key wins.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "env": {"PATH": "$HOME/.local/bin:$PATH", "BASE": "1"}
                },
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "*-*-* 03:00",
                        "env": {"PATH": "/job:$PATH", "JOBVAR": "x"},
                    }
                },
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        snap = _cast_dict((h.state_dir("j") / "snapshot.json").read_text())
        assert snap["env"] == {
            "PATH": "/job:$PATH",  # job key wins over the default
            "BASE": "1",  # default-only key inherited
            "JOBVAR": "x",  # job-only key kept
        }

    def test_snapshot_env_inherits_defaults_when_job_has_none(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {"env": {"PATH": "$HOME/.local/bin:$PATH"}},
                "job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}},
            },
            default_target_jobs=["j"],
        )
        h.apply("j")
        snap = _cast_dict((h.state_dir("j") / "snapshot.json").read_text())
        assert snap["env"] == {"PATH": "$HOME/.local/bin:$PATH"}

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
        sd = h.state / DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        # schema=999 simulates a future version we don't support.
        (sd / "snapshot.json").write_text(
            '{"schema": 999, "kind": "job", "name": "default.j"}',
            encoding="utf-8",
        )
        with pytest.raises(PreconditionError, match="schema 999"):
            crony_runtime.load_snapshot(
                EntityRef(DEFAULT_BUNDLE_NAME, uuid_value)
            )

    def test_load_snapshot_refuses_missing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        _ = _RunnerHarness(tmp_path, monkeypatch)
        with pytest.raises(PreconditionError, match="no snapshot"):
            crony_runtime.load_snapshot(
                EntityRef(DEFAULT_BUNDLE_NAME, "never-applied-uuid")
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
        sd = h.state / DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps(
                {
                    "schema": CURRENT_SNAPSHOT_SCHEMA,
                    "kind": "job",
                    "name": "default.j",
                    "bogus_field": "unexpected",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(PreconditionError, match="malformed fields"):
            crony_runtime.load_snapshot(
                EntityRef(DEFAULT_BUNDLE_NAME, uuid_value)
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        snap_path = h.state_dir("g") / "snapshot.json"
        snap = _cast_dict(snap_path.read_text())
        # 1.05 * 100 = 105
        assert snap["timeout"] == 105

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
        crony_commands.do_apply(jobs=[h.full("g")], verbose=False, bundle=None)
        snap_after = _cast_dict(snap_path.read_text())
        assert snap_after["timeout"] == 210

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
        crony_runtime.destroy_one(h.full("j"), h.state_dir("j"))
        assert not snap_path.exists()

    def test_run_subcommand_hidden_from_top_level_help(self) -> None:
        # `run` is the platform unit's entry point, not user-facing.
        # It must not appear in the usage line's choices summary
        # nor in the subcommand description block. Free-form prose
        # in the epilog can still mention "run" as a verb -- this
        # test scopes to the structural surfaces only.
        parser = crony_cli._build_parser()
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
        crony_commands.do_init(force=False, bundle=None)
        assert h.cfg_file.exists()
        # Replace the template with a small real config so apply
        # has something concrete to install.
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony_commands.do_validate(bundle=None, file=None)
        # apply -> renders + activates
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        assert (h.agents / f"org.crony.{h.full('j')}.plist").exists()
        # status -> prints the synced/enabled tuple (sched stub)
        capsys.readouterr()  # drop earlier output
        crony_commands.do_status(
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
        crony_commands.do_destroy(jobs=[], bundle=None, orphans=False)
        assert not (h.agents / f"org.crony.{h.full('j')}.plist").exists()


class TestInteractiveHelpers:
    """The interactive wait / delay / dialog-mapping orchestration in
    bin/crony, driven against a stubbed HostPlatform. The backend idle /
    lock / dialog primitives are covered in
    test_crony_platform_host_darwin.py."""

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
        host = _idle_lock_host(
            idle=lambda: next(idle_values), locked=lambda: False
        )
        monkeypatch.setattr(crony_runtime, "host", lambda: host)
        now = [0.0]
        monkeypatch.setattr(crony_commands.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony_commands.time,
            "sleep",
            lambda s: now.__setitem__(0, now[0] + s),
        )
        crony_runner._wait_for_user_active(120, poll_sec=30, idle_break_sec=60)
        # Reached the return path; bound the elapsed time so a
        # broken loop would have hung the test.
        assert now[0] <= 300

    def test_wait_resets_on_idle_break(self, monkeypatch: Any) -> None:
        # Active for two polls, then a long idle gap resets the
        # accumulator, then active again until threshold met.
        idle_values = iter(
            [10.0, 10.0, 200.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
        )
        host = _idle_lock_host(
            idle=lambda: next(idle_values), locked=lambda: False
        )
        monkeypatch.setattr(crony_runtime, "host", lambda: host)
        now = [0.0]
        monkeypatch.setattr(crony_commands.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony_commands.time,
            "sleep",
            lambda s: now.__setitem__(0, now[0] + s),
        )
        crony_runner._wait_for_user_active(120, poll_sec=30, idle_break_sec=60)

    def test_wait_treats_locked_screen_as_idle(self, monkeypatch: Any) -> None:
        # Even with idle == 0, a locked screen prevents the active
        # accumulator from advancing.
        locked_values = iter([True, True, False, False, False, False, False])
        host = _idle_lock_host(
            idle=lambda: 0.0, locked=lambda: next(locked_values)
        )
        monkeypatch.setattr(crony_runtime, "host", lambda: host)
        now = [0.0]
        monkeypatch.setattr(crony_commands.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony_commands.time,
            "sleep",
            lambda s: now.__setitem__(0, now[0] + s),
        )
        crony_runner._wait_for_user_active(60, poll_sec=30, idle_break_sec=60)

    def test_wait_bypass_check_short_circuits(self, monkeypatch: Any) -> None:
        host = _idle_lock_host(idle=lambda: 0.0, locked=lambda: False)
        monkeypatch.setattr(crony_runtime, "host", lambda: host)
        # Trip the bypass on the first poll; idle / lock checks are
        # never consulted.
        assert (
            crony_runner._wait_for_user_active(
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
        host = _idle_lock_host(
            idle=lambda: next(idle_values), locked=lambda: False
        )
        monkeypatch.setattr(crony_runtime, "host", lambda: host)
        now = [0.0]
        monkeypatch.setattr(crony_commands.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony_commands.time,
            "sleep",
            lambda s: now.__setitem__(0, now[0] + s),
        )
        assert (
            crony_runner._wait_for_user_active(
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
        monkeypatch.setattr(crony_commands.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            crony_commands.time,
            "sleep",
            lambda s: now.__setitem__(0, now[0] + s),
        )
        assert (
            crony_runner._delay_or_bypass(
                120, bypass_check=lambda: False, poll_sec=30
            )
            is True
        )
        assert now[0] >= 120

    def test_delay_or_bypass_short_circuits_on_bypass(
        self, monkeypatch: Any
    ) -> None:
        # First two polls return False; third returns True. The
        # sleep should exit early without completing the full delay.
        bypass_values = iter([False, False, True])
        monkeypatch.setattr(crony_commands.time, "monotonic", lambda: 0.0)
        sleeps: list[float] = []
        monkeypatch.setattr(crony_commands.time, "sleep", sleeps.append)
        assert (
            crony_runner._delay_or_bypass(
                3600,
                bypass_check=lambda: next(bypass_values),
                poll_sec=30,
            )
            is False
        )
        # Two sleep chunks happened (one between each bypass check)
        # before the third check fired.
        assert sleeps == [30, 30]

    def _dialog_host(self, clicked: str) -> SimpleNamespace:
        captured: dict[str, Any] = {}

        def show_dialog(_title: str, _body: str, buttons: list[str]) -> str:
            captured["buttons"] = buttons
            return clicked

        return SimpleNamespace(show_dialog=show_dialog, captured=captured)

    def test_dialog_run_button(self, monkeypatch: Any) -> None:
        host = self._dialog_host("Run Job")
        monkeypatch.setattr(crony_runtime, "host", lambda: host)
        assert crony_runner._show_interactive_dialog("foo", "msg") == "run"

    def test_dialog_delay_button(self, monkeypatch: Any) -> None:
        host = self._dialog_host("Delay Job")
        monkeypatch.setattr(crony_runtime, "host", lambda: host)
        assert crony_runner._show_interactive_dialog("foo", "msg") == "delay"
        # The cancel button is first and the run button last, so the
        # backend uses them as the AppleScript cancel / default buttons.
        assert host.captured["buttons"] == [
            "Cancel Job",
            "Delay Job",
            "Run Job",
        ]

    def test_dialog_no_choice_maps_to_cancel(self, monkeypatch: Any) -> None:
        # The backend returns "" for a cancel-button click, a dismissed
        # dialog, or an unavailable osascript; all map to 'cancel'.
        host = self._dialog_host("")
        monkeypatch.setattr(crony_runtime, "host", lambda: host)
        assert crony_runner._show_interactive_dialog("foo", "msg") == "cancel"


class TestUserTriggerFlag:
    """The one-shot sentinel file written by `trigger_unit` when
    the user invokes `crony trigger` and consumed by `crony _run` to
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
        assert not crony_runtime.consume_user_trigger_flag(sd)
        crony_runtime.write_user_trigger_flag(sd)
        assert (sd / "user-trigger.flag").exists()
        assert crony_runtime.consume_user_trigger_flag(sd)
        assert not (sd / "user-trigger.flag").exists()
        # Second consume on absent flag returns False.
        assert not crony_runtime.consume_user_trigger_flag(sd)

    def test_trigger_unit_writes_flag_only_when_user_initiated(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("iv")
        sd = h.fabricate_orphan("iv")
        unit = tmp_path / "fake.plist"
        unit.write_text("")
        monkeypatch.setattr(
            crony_runtime, "dispatch_unit_path", lambda *_a, **_kw: unit
        )
        monkeypatch.setattr(
            crony_commands.subprocess,
            "run",
            lambda *a, **_kw: subprocess.CompletedProcess(a, 0),
        )
        crony_runner.trigger_unit(
            full, "darwin", triggered_by_user=True, state_dir=sd
        )
        flag = sd / "user-trigger.flag"
        assert flag.exists()
        flag.unlink()
        crony_runner.trigger_unit(full, "darwin")
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
        monkeypatch.setattr(
            crony_runtime, "dispatch_unit_path", lambda *_a, **_kw: unit
        )

        def fake_run(
            *a: Any, **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(1, a[0])

        monkeypatch.setattr(crony_commands.subprocess, "run", fake_run)
        with pytest.raises(subprocess.CalledProcessError):
            crony_runner.trigger_unit(
                full, "darwin", triggered_by_user=True, state_dir=sd
            )
        assert not (sd / "user-trigger.flag").exists()


class TestJobStatusInteractive:
    """`_job_status` reports `pending` for an interactive job
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
        with crony_runtime.acquire_lock(sd / "run.lock"):
            config = crony_runtime.load_config()
            assert crony_commands._job_status(config, full) == "pending"

    def test_running_when_lock_held_without_flag(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        full = h.full("iv")
        sd = h.fabricate_orphan("iv")
        with crony_runtime.acquire_lock(sd / "run.lock"):
            config = crony_runtime.load_config()
            assert crony_commands._job_status(config, full) == "running"

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
        config = crony_runtime.load_config()
        assert crony_commands._job_status(config, full) == "canceled"


class TestJobStatusCrashed:
    """When the scheduler's last launch ended without recording (killed,
    or exited nonzero before the runner wrote its record), the surviving
    last-run.json is stale: STATUS reads `crashed`. A status matching the
    recorded process exit is a normal result and is left alone."""

    def _setup(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        *,
        status: int,
        record: str,
    ) -> str:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        full = h.full("iv")
        sd = h.fabricate_orphan("iv")
        (sd / "last-run.json").write_text(record, encoding="utf-8")
        monkeypatch.setattr(
            launchd,
            "_launchctl_list",
            lambda: f"PID\tStatus\tLabel\n-\t{status}\torg.crony.{full}\n",
        )
        return full

    # A stale "ok" record (process_exit 0) from an earlier good launch.
    _STALE_OK = (
        '{"exit_class": "ok",'
        ' "started_at": "2099-01-01T00:00:00-08:00",'
        ' "host": "h", "platform": "darwin",'
        ' "ended_at": "2099-01-01T00:00:01-08:00",'
        ' "duration_sec": 1.0, "exit_code": 0, "signal": null,'
        ' "process_exit": 0, "gate": "none",'
        ' "log_path": "/tmp/run.log", "log_bytes_this_run": 0}'
    )
    # A recorded failure: the runner exited the process with code 1.
    _RECORDED_FAIL = (
        '{"exit_class": "fail",'
        ' "started_at": "2099-01-01T00:00:00-08:00",'
        ' "host": "h", "platform": "darwin",'
        ' "ended_at": "2099-01-01T00:00:01-08:00",'
        ' "duration_sec": 1.0, "exit_code": 1, "signal": null,'
        ' "process_exit": 1, "gate": "none",'
        ' "log_path": "/tmp/run.log", "log_bytes_this_run": 0}'
    )

    def test_signal_kill_is_crashed(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        full = self._setup(
            tmp_path, monkeypatch, status=-9, record=self._STALE_OK
        )
        config = crony_runtime.load_config()
        assert crony_commands._job_status(config, full) == "crashed"

    def test_nonzero_exit_without_record_is_crashed(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        full = self._setup(
            tmp_path, monkeypatch, status=127, record=self._STALE_OK
        )
        config = crony_runtime.load_config()
        assert crony_commands._job_status(config, full) == "crashed"

    def test_recorded_failure_is_not_crashed(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        full = self._setup(
            tmp_path, monkeypatch, status=1, record=self._RECORDED_FAIL
        )
        config = crony_runtime.load_config()
        assert crony_commands._job_status(config, full) == "fail"


class TestFormatElapsed:
    """`_format_elapsed` coarsens a second span to its largest whole
    unit, with no suffix (the caller adds "ago")."""

    @pytest.mark.parametrize(
        ("secs", "expected"),
        [
            (0, "0s"),
            (59, "59s"),
            (60, "1m"),
            (3599, "59m"),
            (3600, "1h"),
            (86399, "23h"),
            (86400, "1d"),
            (8 * 86400, "8d"),
        ],
    )
    def test_boundaries(self, secs: int, expected: str) -> None:
        assert crony_commands._format_elapsed(secs) == expected


class TestLastRanColumn:
    """LAST RAN renders the relative time since the last job start."""

    def test_reports_launch_start_from_run_pid_mtime(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        import datetime

        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        full = h.full("iv")
        sd = h.fabricate_orphan("iv")
        # A run.pid with no completion record: an in-flight / crashed
        # launch whose start is run.pid's mtime.
        pid_path = sd / "run.pid"
        pid_path.write_text("4242\n", encoding="utf-8")
        five_min_ago = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
        ).timestamp()
        os.utime(pid_path, (five_min_ago, five_min_ago))
        config = crony_runtime.load_config()
        assert crony_commands._last_ran_at(config, full) == "5m ago"

    def test_never_when_no_run(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        full = h.full("iv")
        h.fabricate_orphan("iv")  # snapshot only: no run.pid, no record
        config = crony_runtime.load_config()
        assert crony_commands._last_ran_at(config, full) == "never"


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
    ) -> tuple[_ApplyHarness, str, str]:
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        return h, group_uuid, member_uuid

    def test_default_view_shows_new_names_once_with_history(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h, group_uuid, member_uuid = self._renamed_config(tmp_path, monkeypatch)
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg(
                "job,uuid,schedule,status,last-ran"
            ),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,uuid,schedule"),
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job-or-uuid,uuid,unit-name"),
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
    when stdout is a color-capable TTY. On a color stream the `^` marker
    and its footer legend are dropped -- color is the staleness signal.
    """

    R = crony_commands._ANSI_RED
    Y = crony_commands._ANSI_YELLOW
    X = crony_commands._ANSI_RESET

    def _force_color(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(crony_commands, "_color_supported", lambda: True)

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
        crony_commands.do_status(
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
        # Divergence-flagged schedule -> yellow value; on a color stream
        # the `^` marker is dropped.
        assert f"{self.Y}*-*-* 09:00{self.X}" in out
        assert "*-*-* 09:00^" not in out
        # `missing` verdict -> red.
        assert f"{self.R}missing{self.X}" in out
        # The footer legend is a plain-stream signal only; not on color.
        assert "flagged cells are stale" not in out

    def test_status_fail_is_red(
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
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,status"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert f"{self.R}fail{self.X}" in out

    def test_disabled_schedule_is_red(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # A disabled entry's SCHEDULE cell reads `disabled` and renders
        # red -- it isn't firing, like the other red states.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        crony_commands.do_disable(jobs=["j"], bundle=None)
        self._force_color(monkeypatch)
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,schedule"),
            show_masked=False,
            bundle=None,
            config_current=False,
            config_pending=False,
            exclude_healthy=False,
        )
        out = capsys.readouterr().out
        assert f"{self.R}disabled{self.X}" in out

    def test_orphan_is_red(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.fabricate_orphan("ghost")
        h.config({}, default_target_jobs=[])
        self._force_color(monkeypatch)
        crony_commands.do_status(
            jobs=[],
            cols=crony_commands.parse_cols_arg("job,config"),
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
        crony_commands.do_status(
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
        # On a plain stream staleness shows as the `^` marker plus the
        # footer legend, not color.
        assert "*-*-* 09:00^" in out
        assert "flagged cells are stale" in out

    def test_color_supported_respects_no_color_and_tty(
        self, monkeypatch: Any
    ) -> None:
        class _Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(crony_commands.sys, "stdout", _Tty())
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert crony_commands._color_supported() is True
        monkeypatch.setenv("NO_COLOR", "1")
        assert crony_commands._color_supported() is False
        # Non-TTY stream never colors, regardless of NO_COLOR.
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(crony_commands.sys, "stdout", io.StringIO())
        assert crony_commands._color_supported() is False


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
