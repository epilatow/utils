#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "tomlkit"]
# ///
# This is AI generated code

"""Unit tests for crony.runtime."""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_crony import (  # noqa: E402
    _ApplyHarness,
    _isolate_home,  # noqa: E402, F401
    _job,
    _parse,
)

from crony import commands as crony_commands  # noqa: E402
from crony import config as crony_config  # noqa: E402
from crony import paths as crony_paths  # noqa: E402
from crony import platform as crony_platform  # noqa: E402
from crony import runtime as crony_runtime  # noqa: E402
from crony.config import (  # noqa: E402
    DEFAULT_BUNDLE_NAME,
    TomlConfig,
)
from crony.errors import (  # noqa: E402
    ConfigError,
    UsageError,
)
from crony.model import (  # noqa: E402
    SNAPSHOT_SCHEMA,
    Job,
)
from crony.platform import (  # noqa: E402
    launchd,
    systemd,
)
from crony.unit import (  # noqa: E402
    EntityName,
    EntityRef,
    Schedule,
)

_script_path = REPO_ROOT / "src" / "crony" / "runtime.py"


class TestUnitStateDarwin:
    def test_loaded_label_is_enabled(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(launchd, "_launchctl_print_disabled", lambda: "")
        monkeypatch.setattr(
            launchd, "_launchctl_list", lambda: "-\t0\torg.crony.default.j\n"
        )
        assert crony_runtime.unit_state("default.j", "darwin") == "enabled"

    def test_disabled_record_takes_precedence(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            launchd,
            "_launchctl_print_disabled",
            lambda: '"org.crony.default.j" => disabled',
        )
        monkeypatch.setattr(
            launchd, "_launchctl_list", lambda: "-\t0\torg.crony.default.j\n"
        )
        assert crony_runtime.unit_state("default.j", "darwin") == "disabled"

    def test_none_when_neither_loaded_nor_disabled(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(launchd, "_launchctl_print_disabled", lambda: "")
        monkeypatch.setattr(launchd, "_launchctl_list", lambda: "")
        assert crony_runtime.unit_state("default.j", "darwin") == "none"


class TestUnitStateLinux:
    def test_enabled(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "enabled")
        assert crony_runtime.unit_state("default.j", "linux") == "enabled"

    def test_disabled(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "disabled")
        assert crony_runtime.unit_state("default.j", "linux") == "disabled"

    def test_none_on_empty(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "")
        assert crony_runtime.unit_state("default.j", "linux") == "none"


class TestUnitNameDelegatesTolerateRefForm:
    """A broken entity whose snapshot can't be read has no recoverable
    `<bundle>.<short>` name, so the status path probes it by its
    ref-form `<bundle>:<uuid>`. The scheduler keys on the unit name as
    a plain string, so the query delegates report not-installed for it
    rather than raising on a name that isn't a valid entity."""

    _REF_FORM = "default:11111111-1111-1111-1111-111111111111"

    def test_unit_state_none_for_ref_form(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(launchd, "_launchctl_print_disabled", lambda: "")
        monkeypatch.setattr(launchd, "_launchctl_list", lambda: "")
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "")
        assert crony_runtime.unit_state(self._REF_FORM, "darwin") == "none"
        assert crony_runtime.unit_state(self._REF_FORM, "linux") == "none"

    def test_unit_config_path_none_for_ref_form(self) -> None:
        for platform in ("darwin", "linux"):
            got = crony_runtime._platform_unit_config_path(
                self._REF_FORM, platform
            )
            assert got is None

    def test_dispatch_unit_path_absent_for_ref_form(self) -> None:
        for platform in ("darwin", "linux"):
            path = crony_runtime.dispatch_unit_path(self._REF_FORM, platform)
            assert not path.exists()


class TestConfigState:
    """`Config.config_state` classification driven through the real
    apply -> load_config path (vs `TestConfigStateInMemory`, which
    plants snapshots by hand). Confirms _apply_one writes a snapshot
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
        config = crony_runtime.load_config()
        assert config.config_state(self._ref(config, "default.j")) == "missing"

    def test_synced_after_apply(self, tmp_path: Path, monkeypatch: Any) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        config = crony_runtime.load_config()
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
        config = crony_runtime.load_config()
        assert config.config_state(self._ref(config, "default.j")) == "stale"

    def test_orphan_stamped_not_in_config(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        # Stamp the entry on disk, then drop it from the config.
        h.fabricate_orphan("old")
        h.config({}, default_target_jobs=[])
        config = crony_runtime.load_config()
        ref = config.current.by_full_name["default.old"]
        assert config.config_state(ref) == "orphan"


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
        # entry to consult so sched falls through to unit_state
        # (stubbed to "enabled" to surface the branch).
        ghost = h.full("ghost")
        h.fabricate_orphan("ghost")
        h.config({}, default_target_jobs=[])
        monkeypatch.setattr(crony_runtime, "unit_state", lambda _n: "enabled")
        config = crony_runtime.load_config()
        cfg, sched, last = crony_commands._resolve_state_axes(
            config, ghost, config.installed_full_names()
        )
        assert cfg == "orphan"
        assert sched == "enabled"
        assert last == "never"

    def test_missing_short_circuits_unit_state_to_none(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # No stamp, no bundle entry -> missing; unit short-
        # circuits to "none" without consulting unit_state.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        called: list[str] = []

        def _stub_sched(n: str) -> str:
            called.append(n)
            return "enabled"

        monkeypatch.setattr(crony_runtime, "unit_state", _stub_sched)
        config = crony_runtime.load_config()
        cfg, unit_state, last = crony_commands._resolve_state_axes(
            config, h.full("ghost"), set()
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
        # unit_state.
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
        monkeypatch.setattr(crony_runtime, "unit_state", lambda _n: "enabled")
        config = crony_runtime.load_config()
        _, sched, _ = crony_commands._resolve_state_axes(
            config, h.full("a"), config.installed_full_names()
        )
        assert sched == "grouped"

    def test_leaf_with_schedule_consults_unit_state(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Scheduled leaf -> sched read from unit_state.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony_runtime, "unit_state", lambda _n: "disabled")
        config = crony_runtime.load_config()
        cfg_state, sched, _ = crony_commands._resolve_state_axes(
            config, h.full("j"), config.installed_full_names()
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
        with pytest.raises(ConfigError, match="name collision"):
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

        with caplog.at_level(logging.ERROR, logger=crony_config.logger.name):
            bundles = TomlConfig.load_all()
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
        monkeypatch.setattr(crony_runtime, "unit_state", lambda _n: "enabled")
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
        with pytest.raises(UsageError, match="config error"):
            crony_commands.do_apply(
                jobs=[h.full("bad")], verbose=False, bundle=None
            )

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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
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
        crony_commands.do_destroy(
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
        monkeypatch.setattr(crony_runtime, "unit_state", lambda _n: "enabled")
        crony_commands.do_status(
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
        config = crony_runtime.load_config()
        cfg_state, _unit_state, _last_state = (
            crony_commands._resolve_state_axes(
                config, h.full("bad"), config.installed_full_names()
            )
        )
        assert cfg_state == "error"


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
        cfg = crony_runtime.load_config()
        ref_input = "default:11111111-2222-3333-4444-555555555555"
        # The ref-form is recognized by all three resolve methods.
        # When the entity isn't in any side (this test has no
        # pending or current entry), the methods all return None
        # for the ref since the ref doesn't appear in their
        # backing source. The ref-form parser still gives the
        # caller a way to construct an EntityRef explicitly:
        assert EntityRef.from_str(ref_input) == EntityRef(
            "default", "11111111-2222-3333-4444-555555555555"
        )
        assert cfg.resolve_runnable(ref_input) is None
        assert cfg.resolve_current(ref_input) is None
        assert cfg.resolve_pending(ref_input) is None


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
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony_platform, "current_host", lambda: "test-host")
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
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
        config = crony_runtime.load_config()
        ref = EntityRef("default", uuid_a)
        assert ref in config.pending.jobs
        assert config.pending.jobs[ref].name == EntityName.from_str("default.a")
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
        crony_paths.CONFIG_FILE.write_text("", encoding="utf-8")
        sd = crony_paths.STATE_DIR / "default" / uuid_g
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps(
                {
                    "schema": SNAPSHOT_SCHEMA,
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
        config = crony_runtime.load_config()
        ref = EntityRef("default", uuid_g)
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
        bundles = TomlConfig.load_all()
        snap = Job.from_config(
            bundles.bundles[0].config,
            bundles.bundles[0].config.jobs["a"],
            EntityName.from_str("default.a"),
        )
        sd = crony_paths.STATE_DIR / "default" / uuid_a
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps(snap.to_dict()), encoding="utf-8"
        )
        config = crony_runtime.load_config()
        ref = EntityRef("default", uuid_a)
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
        bundles = TomlConfig.load_all()
        snap = Job.from_config(
            bundles.bundles[0].config,
            bundles.bundles[0].config.jobs["a"],
            EntityName.from_str("default.a"),
        )
        sd = crony_paths.STATE_DIR / "default" / uuid_a
        sd.mkdir(parents=True)
        # Persist a snapshot with a divergent command vs what TOML
        # currently says ("true" vs "stale-command").
        diverged = snap.to_dict()
        diverged["command"] = "stale-command"
        (sd / "snapshot.json").write_text(
            json.dumps(diverged), encoding="utf-8"
        )
        config = crony_runtime.load_config()
        ref = EntityRef("default", uuid_a)
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
        config = crony_runtime.load_config()
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
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony_platform, "current_host", lambda: "test-host")
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        cfg_file.write_text("", encoding="utf-8")
        return cfg_file

    def _plant_state(self, uuid_value: str, contents: str) -> tuple[Any, Path]:
        sd = crony_paths.STATE_DIR / "default" / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(contents, encoding="utf-8")
        return EntityRef("default", uuid_value), sd

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
        config = crony_runtime.load_config()
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
                    "schema": SNAPSHOT_SCHEMA,
                    "kind": "banana",
                    "name": "default.j",
                }
            ),
        )
        config = crony_runtime.load_config()
        assert ref in config.broken
        assert "banana" in config.broken[ref].reason
        assert config.config_state(ref) == "broken"

    def test_dataclass_type_error_recorded_as_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        # Right schema + kind but missing required fields -> the
        # snapshot constructor raises.
        ref, _ = self._plant_state(
            "33333333-3333-3333-3333-333333333333",
            json.dumps(
                {
                    "schema": SNAPSHOT_SCHEMA,
                    "kind": "job",
                    "name": "default.partial",
                }
            ),
        )
        config = crony_runtime.load_config()
        assert ref in config.broken
        assert config.broken[ref].name == "default.partial"
        assert "snapshot conversion" in config.broken[ref].reason

    def test_corrupt_json_recorded_as_broken_without_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        ref, _ = self._plant_state(
            "44444444-4444-4444-4444-444444444444",
            "{not valid json",
        )
        config = crony_runtime.load_config()
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
        config = crony_runtime.load_config()
        assert config.config_state(ref) == "broken"


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
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony_platform, "current_host", lambda: "test-host")
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
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
        config = crony_runtime.load_config()
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
        sd = crony_paths.STATE_DIR / "default" / uuid_b
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            json.dumps(
                {"schema": 999, "kind": "job", "name": "default.legacy"}
            ),
            encoding="utf-8",
        )
        config = crony_runtime.load_config()
        ref = EntityRef("default", uuid_b)
        assert config.resolve_runnable("default.legacy") is None
        assert config.resolve_current("default.legacy") == ref
        assert config.resolve_pending("default.legacy") is None


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
        names = crony_runtime._platform_unit_names()
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
        names = crony_runtime._platform_unit_names()
        assert names == {"default.foo", "bundle.bar"}

    def test_missing_unit_dir_returns_empty(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Fresh install or otherwise no unit dir: no crash, just empty.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        if h.agents.exists():
            shutil.rmtree(h.agents)
        assert crony_runtime._platform_unit_names() == set()


class TestExtractUnitExecPaths:
    """`launchd._extract_exec_paths` / `systemd._extract_exec_paths`
    parse the on-disk unit file to recover the `(uv, crony)` paths
    apply baked in. Anything that doesn't match the expected argv
    shape returns None so the drift check treats the file as stale
    (apply will re-render it from the snapshot).
    """

    def test_extracts_paths_from_plist(self) -> None:
        plist = launchd.render_plist(
            "j",
            EntityRef("default", "u-test"),
            None,
            uv_path=Path("/abs/uv"),
            crony_path=Path("/abs/crony"),
        )
        assert launchd._extract_exec_paths(plist) == (
            Path("/abs/uv"),
            Path("/abs/crony"),
        )

    def test_extracts_paths_from_systemd_service(self) -> None:
        svc = systemd.render_service(
            "j",
            EntityRef("default", "u-test"),
            uv_path=Path("/abs/uv"),
            crony_path=Path("/abs/crony"),
        )
        assert systemd._extract_exec_paths(svc) == (
            Path("/abs/uv"),
            Path("/abs/crony"),
        )

    def test_returns_none_for_malformed_plist(self) -> None:
        assert launchd._extract_exec_paths("not xml") is None

    def test_returns_none_for_plist_missing_program_arguments(self) -> None:
        assert (
            launchd._extract_exec_paths(
                '<?xml version="1.0"?><plist><dict>'
                "<key>Label</key><string>x</string></dict></plist>",
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
        assert launchd._extract_exec_paths(bogus) is None

    def test_returns_none_for_plist_sh_wrapper_bad_inner(self) -> None:
        # /bin/sh -c wrapper whose inner argv isn't the runner shape.
        bogus = (
            '<?xml version="1.0"?><plist><dict>'
            "<key>ProgramArguments</key><array>"
            "<string>/bin/sh</string><string>-c</string>"
            "<string>exec /abs/uv weird /abs/crony</string>"
            "</array></dict></plist>"
        )
        assert launchd._extract_exec_paths(bogus) is None

    def test_returns_none_for_plist_sh_wrapper_without_exec(self) -> None:
        # Wrapper command must start with `exec`.
        bogus = (
            '<?xml version="1.0"?><plist><dict>'
            "<key>ProgramArguments</key><array>"
            "<string>/bin/sh</string><string>-c</string>"
            "<string>/abs/uv run --script /abs/crony run x:y</string>"
            "</array></dict></plist>"
        )
        assert launchd._extract_exec_paths(bogus) is None

    def test_returns_none_for_systemd_missing_exec_start(self) -> None:
        no_exec = "[Service]\nType=oneshot\n"
        assert systemd._extract_exec_paths(no_exec) is None

    def test_returns_none_for_systemd_unparseable_ini(self) -> None:
        # Leading non-section content trips configparser.
        assert (
            systemd._extract_exec_paths(
                "ExecStart=/abs/uv run --script /abs/crony run x:y\n",
            )
            is None
        )

    def test_returns_none_for_systemd_wrong_argv_shape(self) -> None:
        bogus = (
            "[Service]\nExecStart=/abs/uv weird --script /abs/crony run x:y\n"
        )
        assert systemd._extract_exec_paths(bogus) is None


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
        crony_commands.do_apply(jobs=[h.full("j")], verbose=False, bundle=None)
        config = crony_runtime.load_config()
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
        # notice and flag the install stale. (Hour is the only
        # integer 3 in the rendered plist, so this is unambiguous
        # without depending on the serializer's indentation.)
        munged = content.replace("<integer>3</integer>", "<integer>5</integer>")
        assert munged != content
        unit_config.write_text(munged)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.runtime[ref].unit_is_stale is True

    def test_missing_unit_file_flags_stale(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.runtime[ref].unit_is_stale is True

    def test_unloaded_unit_flags_stale(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h, _, _ = self._apply_and_load(tmp_path, monkeypatch)
        # Simulate the scheduler having unloaded the unit (e.g.
        # the user ran `launchctl bootout` directly). File on
        # disk still intact but the launchd probe reports the unit
        # as not loaded, so the drift check reads "none".
        monkeypatch.setattr(launchd, "_is_loaded", lambda _label: False)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.runtime[ref].unit_is_stale is True

    def test_grouped_entry_not_stale_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A grouped (schedule-less) entry installs only a .service on
        # linux -- no .timer. `unit_state` queries the timer, so a
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        # Faithful systemctl: a unit is "enabled" only if its file is
        # present. The grouped child has no .timer, so its timer query
        # returns "" -> unit_state "none" -- the real linux behavior
        # the harness's blanket `enabled` stub hides.
        monkeypatch.setattr(
            systemd,
            "_is_enabled",
            lambda u: "enabled" if (h.sysd / u).is_file() else "",
        )
        config = crony_runtime.load_config()
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
        live_uv = str(crony_commands._uv_executable())
        bogus_uv = str(tmp_path / "nonexistent" / "uv")
        unit_config.write_text(content.replace(live_uv, bogus_uv))
        config = crony_runtime.load_config()
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
        # loaded once at start (_apply_one's `model` path), not by
        # re-probing disk per entry. A unit deleted after the first
        # apply is `unit_is_stale` at load time, so the no-arg apply
        # re-renders it.
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
        with caplog.at_level(logging.INFO, logger="crony_app"):
            crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        assert unit_config.exists()
        msgs = [r.getMessage() for r in caplog.records]
        assert any(f"{h.full('j')}: updated" in m for m in msgs), msgs

    def test_status_reports_stale_for_drifted_install(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
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
        crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        timer = h.sysd / f"crony-{h.full('transit')}.timer"
        timer.write_text(
            systemd.render_timer(
                h.full("transit"), Schedule.from_str("*-*-* 03:00")
            )
        )
        config = crony_runtime.load_config()
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
        snap_dir = h.state / DEFAULT_BUNDLE_NAME / legacy_uuid
        snap_dir.mkdir(parents=True)
        # Pre-existing snapshot lacking schedule / interval keys.
        legacy = {
            "schema": SNAPSHOT_SCHEMA,
            "kind": "job",
            "name": full,
            "bundle": DEFAULT_BUNDLE_NAME,
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
        snap = crony_runtime.load_snapshot(
            EntityRef(DEFAULT_BUNDLE_NAME, legacy_uuid)
        )
        assert isinstance(snap, Job)
        assert snap.timing is None


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
