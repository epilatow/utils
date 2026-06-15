#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "tomlkit"]
# ///
# This is AI generated code

"""Unit tests for crony.runtime."""

from __future__ import annotations

import dataclasses
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
from crony import model as crony_model  # noqa: E402
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
    CURRENT_SNAPSHOT_SCHEMA,
    ConfigStatus,
    Job,
    JobGroup,
)
from crony.platform import (  # noqa: E402
    fda as crony_fda,
)
from crony.platform import (  # noqa: E402
    launchd,
    systemd,
)
from crony.platform.fda import FDAWrapper  # noqa: E402
from crony.unit import (  # noqa: E402
    EntityName,
    EntityRef,
    Schedule,
)

_script_path = REPO_ROOT / "src" / "crony" / "runtime.py"


def _install_units(snap: Any) -> None:
    """Write `snap`'s rendered platform units to the scheduler's unit
    dir -- the on-disk side a hand-planted snapshot otherwise lacks, so
    its current node reads a healthy (not broken / missing) unit.

    Stamps the live uv / crony executables onto the node first, the way
    `load_config` does for a pending node, so rendering is self-contained
    (a hand-built `from_config` node carries no paths)."""
    snap = dataclasses.replace(
        snap,
        uv_path=crony_runtime._uv_executable(),
        crony_path=crony_runtime._crony_executable(),
    )
    sched = crony_runtime.scheduler()
    sched.unit_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in crony_runtime._render_units(snap).items():
        (sched.unit_dir / fname).write_text(content, encoding="utf-8")


class TestIsLoadedDarwin:
    # `is_loaded` reports only whether the scheduler has the unit; the
    # disabled overlay rides on the snapshot, not the scheduler.
    def test_loaded_label_is_true(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            launchd, "_launchctl_list", lambda: "-\t0\torg.crony.default.j\n"
        )
        assert crony_runtime.is_loaded("default.j", "darwin") is True

    def test_false_when_not_loaded(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(launchd, "_launchctl_list", lambda: "")
        assert crony_runtime.is_loaded("default.j", "darwin") is False


class TestIsLoadedLinux:
    def test_enabled_timer_is_loaded(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "enabled")
        assert crony_runtime.is_loaded("default.j", "linux") is True

    def test_static_service_is_loaded(self, monkeypatch: Any) -> None:
        # A schedule-less entry's static `.service` counts as loaded.
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "static")
        assert crony_runtime.is_loaded("default.j", "linux") is True

    def test_false_on_empty(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "")
        assert crony_runtime.is_loaded("default.j", "linux") is False


class TestUnitNameDelegatesTolerateRefForm:
    """A broken entity whose snapshot can't be read has no recoverable
    `<bundle>.<short>` name, so the status path probes it by its
    ref-form `<bundle>:<uuid>`. The scheduler keys on the unit name as
    a plain string, so the query delegates report not-installed for it
    rather than raising on a name that isn't a valid entity."""

    _REF_FORM = "default:11111111-1111-1111-1111-111111111111"

    def test_is_loaded_false_for_ref_form(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(launchd, "_launchctl_list", lambda: "")
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "")
        assert crony_runtime.is_loaded(self._REF_FORM, "darwin") is False
        assert crony_runtime.is_loaded(self._REF_FORM, "linux") is False

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
        verdict = config.config_state(self._ref(config, "default.j"))
        assert verdict == "synced"
        # The verdict is the typed enum, not a bare string, so a
        # regression back to a raw str return is caught.
        assert isinstance(verdict, ConfigStatus)

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


class TestCurrentGraphFdaWrapper:
    """`_build_current_graph` stamps each full-disk-access current node
    with the live Crony.app wrapper state (probed once per load), and
    never probes when no full-disk-access job is on disk."""

    def _ref(self, config: Any, full: str) -> Any:
        return config.current.by_full_name.get(full)

    def test_fda_current_node_carries_probed_state(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
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
        monkeypatch.setattr(
            crony_fda, "wrapper_state", lambda: FDAWrapper.STALE
        )
        config = crony_runtime.load_config()
        node = config.current.job_from_ref(self._ref(config, "default.j"))
        assert isinstance(node, Job)
        assert node.fda_wrapper is FDAWrapper.STALE
        # The drift rides through the snapshot comparison.
        assert config.config_state(node.entity_ref) == "stale"

    def test_non_fda_load_never_probes(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        probed: list[bool] = []

        def _tracked() -> FDAWrapper:
            probed.append(True)
            return FDAWrapper.OK

        monkeypatch.setattr(crony_fda, "wrapper_state", _tracked)
        config = crony_runtime.load_config()
        node = config.current.job_from_ref(self._ref(config, "default.j"))
        assert isinstance(node, Job)
        assert node.fda_wrapper is None
        assert probed == [], "wrapper probed for a non-FDA load"


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
        # entry to consult so the unit axis falls through to the
        # scheduler (stubbed loaded to surface the `enabled` branch).
        ghost = h.full("ghost")
        h.fabricate_orphan("ghost")
        h.config({}, default_target_jobs=[])
        monkeypatch.setattr(crony_runtime, "is_loaded", lambda _n: True)
        config = crony_runtime.load_config()
        cfg, sched, last = crony_commands._resolve_state_axes(
            config, ghost, config.installed_full_names()
        )
        assert cfg == "orphan"
        assert sched == "enabled"
        assert last == "never"

    def test_missing_short_circuits_unit_to_none(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # No stamp, no bundle entry -> missing; unit short-
        # circuits to "none" without consulting the scheduler.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        called: list[str] = []

        def _stub_sched(n: str) -> bool:
            called.append(n)
            return True

        monkeypatch.setattr(crony_runtime, "is_loaded", _stub_sched)
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
        # parent group) -> sched = "grouped" without consulting the
        # scheduler.
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
        monkeypatch.setattr(crony_runtime, "is_loaded", lambda _n: True)
        config = crony_runtime.load_config()
        _, sched, _ = crony_commands._resolve_state_axes(
            config, h.full("a"), config.installed_full_names()
        )
        assert sched == "grouped"

    def test_leaf_with_schedule_consults_is_loaded(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Scheduled, enabled leaf -> the unit-axis display reads the
        # scheduler's live load fact via `runtime.is_loaded`. Stub it
        # False (distinct from the harness's loaded-at-bake stub, which
        # keeps CONFIG synced) so the display alone flips to `none`.
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(crony_runtime, "is_loaded", lambda _n: False)
        config = crony_runtime.load_config()
        cfg_state, sched, _ = crony_commands._resolve_state_axes(
            config, h.full("j"), config.installed_full_names()
        )
        assert cfg_state == "synced"
        assert sched == "none"


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
        assert "notify-channels" in cfg.errored_jobs["bad"]

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
        monkeypatch.setattr(crony_runtime, "is_loaded", lambda _n: True)
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
        monkeypatch.setattr(crony_runtime, "is_loaded", lambda _n: True)
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
        assert config.pending.jobs[ref].bundle == "default"
        assert config.pending.jobs[ref].name == "a"
        assert config.pending.jobs[ref].entity_name == EntityName.from_str(
            "default.a"
        )
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
                    "schema": CURRENT_SNAPSHOT_SCHEMA,
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
                    "timeout": 600,
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
        # apply also plants the short-name alias; without it the
        # current node's recorded link diverges from the expected one.
        (crony_paths.STATE_DIR / "default" / "a").symlink_to(uuid_a)
        # Install the rendered unit and report it loaded: a current node
        # with no / unloaded unit reads broken / missing, so isolating
        # the snapshot-field comparison needs a healthy on-disk unit.
        _install_units(snap)
        monkeypatch.setattr(launchd, "_is_loaded", lambda _label: True)
        config = crony_runtime.load_config()
        ref = EntityRef("default", uuid_a)
        assert config.config_state(ref) == "synced"

    def test_scan_skips_alias_symlink(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """The short-name alias symlink that sits beside the uuid dirs
        is skipped by the state scan: `is_dir()` follows the link, so
        without the guard the alias would be re-scanned under its short
        name -- here surfacing a phantom broken entry keyed by that
        name instead of only the real uuid-keyed one."""
        self._setup(tmp_path, monkeypatch)
        crony_paths.CONFIG_FILE.write_text("", encoding="utf-8")
        uuid_a = "33333333-4444-5555-6666-aaaaaaaaaaaa"
        sd = crony_paths.STATE_DIR / "default" / uuid_a
        sd.mkdir(parents=True)
        # A schema-mismatched snapshot makes the real uuid dir a broken
        # entry; reaching it again through the alias would mint a second
        # broken entry keyed by the alias's short name.
        (sd / "snapshot.json").write_text(
            json.dumps(
                {
                    "schema": 999,
                    "kind": "job",
                    "name": "default.a",
                    "uuid": uuid_a,
                }
            ),
            encoding="utf-8",
        )
        (crony_paths.STATE_DIR / "default" / "a").symlink_to(uuid_a)
        config = crony_runtime.load_config()
        assert set(config.orphans) == {EntityRef("default", uuid_a)}
        assert EntityRef("default", "a") not in config.orphans

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
        # Install a healthy, loaded unit (the command isn't baked into
        # it, so the render matches) -- the divergence is the snapshot
        # `command` field, which must read stale through the comparison.
        _install_units(snap)
        monkeypatch.setattr(launchd, "_is_loaded", lambda _label: True)
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
    """`Config.orphans` carries the entities whose on-disk snapshot
    can't be loaded by this crony binary, flagged `is_broken`.
    `_build_current_graph` records them instead of silently dropping
    the state dir; `Config.config_state` returns `"broken"` for refs
    that land there, beating the synced / stale / orphan / missing
    axes.
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
        assert ref in config.orphans
        assert ref not in config.current.jobs
        # Broken entries get a runtime entry so the unit-config /
        # last / last-ran columns can read the same state-dir
        # files normal current entries read.
        assert ref in config.runtime
        assert config.orphans[ref].name == "default.legacy"
        assert "schema 999" in (config.orphans[ref].reason or "")
        assert config.config_state(ref) == "broken"
        # Name-recovery let it land in orphans_by_full_name.
        assert config.orphans_by_full_name.get("default.legacy") == ref

    def test_unrecognized_kind_recorded_as_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        ref, _ = self._plant_state(
            "22222222-2222-2222-2222-222222222222",
            json.dumps(
                {
                    "schema": CURRENT_SNAPSHOT_SCHEMA,
                    "kind": "banana",
                    "name": "default.j",
                }
            ),
        )
        config = crony_runtime.load_config()
        assert ref in config.orphans
        assert "banana" in (config.orphans[ref].reason or "")
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
                    "schema": CURRENT_SNAPSHOT_SCHEMA,
                    "kind": "job",
                    "name": "default.partial",
                }
            ),
        )
        config = crony_runtime.load_config()
        assert ref in config.orphans
        assert config.orphans[ref].name == "default.partial"
        assert "snapshot conversion" in (config.orphans[ref].reason or "")

    def test_corrupt_json_recorded_as_broken_without_name(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        ref, _ = self._plant_state(
            "44444444-4444-4444-4444-444444444444",
            "{not valid json",
        )
        config = crony_runtime.load_config()
        assert ref in config.orphans
        # No recoverable name from corrupt JSON; the entry is
        # reachable only by ref (or the synthetic input form).
        assert config.orphans[ref].name is None
        assert "unreadable" in (config.orphans[ref].reason or "")
        assert ref not in config.orphans_by_full_name.values()

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


class TestConfigSnapshotlessDir:
    """A uuid dir with no snapshot.json at all is modeled as a
    nameless, non-broken `JobOrphan` -- leftover junk a sweep or a
    ref-form destroy reclaims, unless its uuid is a live config entry
    (then it is that entry's wiped state, surfaced as `stale`).
    """

    def _plant(self, h: Any, ghost: str) -> Path:
        sd: Path = h.state / DEFAULT_BUNDLE_NAME / ghost
        sd.mkdir(parents=True)
        (sd / "run.log").write_text("stale\n", encoding="utf-8")
        return sd

    def test_unmodeled_dir_is_nameless_non_broken_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        ghost = "deadbeef-0000-0000-0000-deadbeef0000"
        self._plant(h, ghost)
        config = crony_runtime.load_config()
        ref = EntityRef(DEFAULT_BUNDLE_NAME, ghost)
        assert ref in config.orphans
        orphan = config.orphans[ref]
        assert orphan.name is None
        assert not orphan.is_broken
        assert config.config_state(ref) == "orphan"
        assert ref not in config.current.jobs
        assert ref not in config.current.groups

    def test_dir_for_live_config_uuid_reads_stale_not_orphan(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The wiped state dir of an applied entry that is still in
        # config keeps that entry's uuid. It stays a (nameless)
        # orphan so destroy / apply can reclaim it, but `config_state`
        # reads it `stale` (re-apply) -- not `orphan` -- because the
        # ref is still a live pending entry.
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        (h.state_dir("j", cfg=cfg) / "snapshot.json").unlink()
        config = crony_runtime.load_config()
        ref = EntityRef(DEFAULT_BUNDLE_NAME, cfg.jobs["j"].uuid)
        assert ref in config.orphans
        assert config.orphans[ref].name is None
        assert config.config_state(ref) == "stale"


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


class TestExecPathStrings:
    """`model.exec_path_strings` recovers the absolute uv / crony
    executable path strings baked into a unit's argv -- by name, not
    position, and regardless of whether they still exist on disk
    (rendering the normalized unit and checking the paths' existence on
    disk are separate concerns).
    """

    def test_recovers_paths(self) -> None:
        argv = crony_model._run_argv(
            Path("/abs/uv"), Path("/abs/crony"), EntityRef("d", "u-test")
        )
        assert crony_model.exec_path_strings(list(argv)) == (
            "/abs/uv",
            "/abs/crony",
        )

    def test_finds_paths_regardless_of_position(self) -> None:
        # The scan keys on the path name, not the argv position, so a
        # wrapper that repeats uv / crony elsewhere still recovers them.
        argv = ["/a/uv", "x", "/b/crony", "y", "z", "/a/uv"]
        assert crony_model.exec_path_strings(argv) == ("/a/uv", "/b/crony")

    def test_recovers_even_when_absent_on_disk(self) -> None:
        # The strings are returned even for a baked path that's since been
        # removed; the filesystem check that decides whether the unit can
        # be reproduced happens elsewhere (the current-graph scan).
        argv = ["/gone/uv", "run", "--script", "/gone/crony", "_run", "x:y"]
        assert crony_model.exec_path_strings(argv) == (
            "/gone/uv",
            "/gone/crony",
        )

    def test_none_for_missing_element(self) -> None:
        assert crony_model.exec_path_strings(["/abs/uv", "run", "x:y"]) == (
            "/abs/uv",
            None,
        )
        assert crony_model.exec_path_strings(["run", "x:y"]) == (None, None)


class TestGuardedArgv:
    """`model._guarded_argv` wraps the base run in the hard-timeout
    guard for a capped entry; the path scan recovers uv / crony from the
    guarded shape just as it does from the bare one."""

    _UV = Path("/abs/uv")
    _CRONY = Path("/abs/crony")
    _REF = EntityRef("default", "u-test")

    def test_uncapped_is_bare_run(self) -> None:
        assert crony_model._guarded_argv(
            self._UV, self._CRONY, self._REF, 0
        ) == crony_model._run_argv(self._UV, self._CRONY, self._REF)

    def test_capped_wraps_with_padded_cap(self) -> None:
        argv = crony_model._guarded_argv(self._UV, self._CRONY, self._REF, 120)
        cap = 120 + crony_model._HARD_TIMEOUT_PADDING_SEC
        assert argv == (
            "/abs/uv",
            "run",
            "--script",
            "/abs/crony",
            crony_model.GUARD_SUBCOMMAND,
            str(cap),
            *crony_model._run_argv(self._UV, self._CRONY, self._REF),
        )

    def test_paths_recover_from_guarded_shape(self) -> None:
        argv = crony_model._guarded_argv(self._UV, self._CRONY, self._REF, 600)
        assert crony_model.exec_path_strings(list(argv)) == (
            "/abs/uv",
            "/abs/crony",
        )


class TestInstalledCmdParsing:
    """The backends parse an on-disk unit back to its run argv
    (`launchd._plist_argv` / `systemd._service_argv`), or None when the
    file isn't the shape `render` produces. Interpreting the argv is the
    runtime layer's job (see `TestExecPathsFromArgv`).
    """

    _CMD = ("/abs/uv", "run", "--script", "/abs/crony", "run", "x:y")

    def test_plist_round_trips(self) -> None:
        plist = launchd.render_plist("j", self._CMD, None)
        assert launchd._plist_argv(plist) == list(self._CMD)

    def test_service_round_trips(self) -> None:
        svc = systemd.render_service("j", self._CMD)
        assert systemd._service_argv(svc) == list(self._CMD)

    def test_none_for_malformed_plist(self) -> None:
        assert launchd._plist_argv("not xml") is None

    def test_none_for_plist_missing_program_arguments(self) -> None:
        assert (
            launchd._plist_argv(
                '<?xml version="1.0"?><plist><dict>'
                "<key>Label</key><string>x</string></dict></plist>",
            )
            is None
        )

    def test_none_for_plist_without_sh_wrapper(self) -> None:
        # render wraps the argv in `/bin/sh -c 'exec ...'`; a bare argv
        # array isn't the shape installed_cmd recognizes.
        bogus = (
            '<?xml version="1.0"?><plist><dict>'
            "<key>ProgramArguments</key><array>"
            "<string>/abs/uv</string><string>run</string>"
            "</array></dict></plist>"
        )
        assert launchd._plist_argv(bogus) is None

    def test_none_for_plist_sh_wrapper_without_exec(self) -> None:
        bogus = (
            '<?xml version="1.0"?><plist><dict>'
            "<key>ProgramArguments</key><array>"
            "<string>/bin/sh</string><string>-c</string>"
            "<string>/abs/uv run --script /abs/crony _run x:y</string>"
            "</array></dict></plist>"
        )
        assert launchd._plist_argv(bogus) is None

    def test_none_for_systemd_missing_exec_start(self) -> None:
        assert systemd._service_argv("[Service]\nType=oneshot\n") is None

    def test_none_for_systemd_unparseable_ini(self) -> None:
        # Leading non-section content trips configparser.
        assert (
            systemd._service_argv(
                "ExecStart=/abs/uv run --script /abs/crony _run x:y\n",
            )
            is None
        )


class TestUnitDriftDetection:
    """`load_config` bakes each node's normalized config / timer units, so
    a divergence between the rendered pending node and the on-disk current
    node surfaces as `config=stale` through the plain node `==`. A
    hand-edited unit reads `stale`; a gone baked uv / crony binary or a
    deleted-but-loaded unit reads `broken`; a deleted-and-unloaded unit
    reads `missing`. The uv / crony paths render blank, so a
    moved-but-present binary reads `synced`. The next apply re-renders /
    re-installs a drifted unit even if the snapshot is unchanged.
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
            unit_config = unit_dir / f"crony-{h.full('j')}.service"
        return h, config, unit_config

    def test_clean_apply_is_synced(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        _, config, _ = self._apply_and_load(tmp_path, monkeypatch)
        ref = config.current.by_full_name["default.j"]
        assert config.config_state(ref) == "synced"

    def test_hand_edited_plist_flags_stale(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch, platform="darwin")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony_commands.do_apply(jobs=[h.full("j")], verbose=False, bundle=None)
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        content = plist.read_text()
        # Flip Hour 3 -> Hour 5: the snapshot still says 03:00, so a
        # render with the unit's own paths no longer matches the on-disk
        # plist -> normalized config None -> stale. (Hour is the only
        # integer 3 in the rendered plist.)
        munged = content.replace("<integer>3</integer>", "<integer>5</integer>")
        assert munged != content
        plist.write_text(munged)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.config_state(ref) == "stale"
        assert "unit-config" in crony_commands._stale_fields(
            config.pending.job_from_ref(ref),
            config.current.job_from_ref(ref),
        )

    def test_deleted_unit_loaded_flags_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Unit file deleted while the scheduler still has it loaded (the
        # harness stubs `_is_loaded` True): works now, dies on reboot ->
        # broken.
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.config_state(ref) == "broken"

    def test_deleted_unit_unloaded_flags_missing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Unit file deleted and the scheduler no longer has it loaded:
        # the apply artifacts are gone -> missing (re-apply).
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
        monkeypatch.setattr(launchd, "_is_loaded", lambda _label: False)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.config_state(ref) == "missing"

    def test_unloaded_scheduled_flags_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The plist is intact but the scheduler has it unloaded (e.g. a
        # hand `launchctl bootout`): an entry the scheduler can't trigger
        # reads broken (re-apply reloads it), not synced.
        h, _, _ = self._apply_and_load(tmp_path, monkeypatch)
        monkeypatch.setattr(launchd, "_is_loaded", lambda _label: False)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.config_state(ref) == "broken"

    def _linux_stale_reasons(
        self, tmp_path: Path, monkeypatch: Any, edit: str
    ) -> str:
        # Apply a scheduled job on linux (a .service + .timer), edit the
        # named unit file, and return the comma-joined stale reasons
        # `load_config` records for it.
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony_commands.do_apply(jobs=[h.full("j")], verbose=False, bundle=None)
        path = h.sysd / f"crony-{h.full('j')}.{edit}"
        path.write_text(path.read_text() + "\n# edited\n")
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        return crony_commands._stale_fields(
            config.pending.job_from_ref(ref),
            config.current.job_from_ref(ref),
        )

    def test_linux_service_edit_is_config_drift(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        assert (
            self._linux_stale_reasons(tmp_path, monkeypatch, "service")
            == "unit-config"
        )

    def test_linux_timer_edit_is_timer_drift(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        assert (
            self._linux_stale_reasons(tmp_path, monkeypatch, "timer")
            == "unit-timer"
        )

    def test_linux_deleted_timer_flags_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A scheduled entry's `.timer` arms its schedule; deleted, the
        # job never fires even though the `.service` is still loaded ->
        # broken, not just stale (re-apply re-renders the timer).
        h = _ApplyHarness(tmp_path, monkeypatch, platform="linux")
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        crony_commands.do_apply(jobs=[h.full("j")], verbose=False, bundle=None)
        (h.sysd / f"crony-{h.full('j')}.timer").unlink()
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.config_state(ref) == "broken"

    def test_grouped_entry_is_synced_on_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A grouped (schedule-less) entry installs only a .service on
        # linux -- no .timer. Its config unit matches and it expects no
        # timer, so a clean apply reads `synced`.
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
        config = crony_runtime.load_config()
        a_ref = config.current.by_full_name[h.full("a")]
        g_ref = config.current.by_full_name[h.full("g")]
        assert config.config_state(g_ref) == "synced"
        assert config.config_state(a_ref) == "synced"

    def test_missing_baked_uv_path_flags_broken(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        # Replace the baked uv path with one pointing at a nonexistent
        # file. The extracted path no longer resolves, so `uv_path` reads
        # None -- the unit can't run as installed, which is `broken`.
        content = unit_config.read_text()
        live_uv = str(crony_runtime._uv_executable())
        bogus_uv = str(tmp_path / "nonexistent" / "uv")
        unit_config.write_text(content.replace(live_uv, bogus_uv))
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.config_state(ref) == "broken"
        node = config.current.job_from_ref(ref)
        assert node is not None and node.uv_path is None

    def test_moved_but_present_binary_is_synced(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The baked uv / crony paths differ from the live ones but still
        # exist on disk (a binary that moved). The normalized unit renders
        # with blank paths, so the install reads synced -- no needless
        # re-apply for a cosmetic path change.
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        alt = tmp_path / "moved"
        alt.mkdir()
        alt_uv = alt / "uv"
        alt_uv.write_text("")
        alt_crony = alt / "crony"
        alt_crony.write_text("")
        content = unit_config.read_text()
        content = content.replace(
            str(crony_runtime._uv_executable()), str(alt_uv)
        )
        content = content.replace(
            str(crony_runtime._crony_executable()), str(alt_crony)
        )
        unit_config.write_text(content)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.config_state(ref) == "synced"

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
        # `do_apply` reads the drift verdict from the Config it loaded
        # once at start (apply_one's `model` path), not by re-probing disk
        # per entry. A unit deleted after the first apply diverges from
        # its pending node at load time, so the no-arg apply re-renders it.
        h, _, unit_config = self._apply_and_load(tmp_path, monkeypatch)
        unit_config.unlink()
        with caplog.at_level(logging.INFO, logger="crony"):
            crony_commands.do_apply(jobs=[], verbose=False, bundle=None)
        assert unit_config.exists()
        msgs = [r.getMessage() for r in caplog.records]
        assert any(f"{h.full('j')}: updated" in m for m in msgs), msgs

    def test_status_reports_stale_for_drifted_install(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        h, _, _ = self._apply_and_load(tmp_path, monkeypatch)
        # Hand-edit the plist (Hour 3 -> 5) so it diverges from what the
        # snapshot would render -> config=stale (a deleted unit would
        # read broken / missing instead).
        plist = h.agents / f"org.crony.{h.full('j')}.plist"
        plist.write_text(
            plist.read_text().replace(
                "<integer>3</integer>", "<integer>5</integer>"
            )
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
        # The transit group renders no timer (pending timer None), but
        # one is on disk -- the timer comparison catches it as drift.
        assert config.config_state(ref) == "stale"


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
            "schema": CURRENT_SNAPSHOT_SCHEMA,
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
            "timeout": 600,
        }
        (snap_dir / "snapshot.json").write_text(json.dumps(legacy))
        _ = full
        snap = crony_runtime.load_snapshot(
            EntityRef(DEFAULT_BUNDLE_NAME, legacy_uuid)
        )
        assert isinstance(snap, Job)
        assert snap.timing is None

    def test_v4_snapshot_loads_via_timeout_compat(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Schema 4 keyed the deadline as `job_timeout_sec`; 4 is still
        # in COMPAT_SNAPSHOT_SCHEMA, so the gate accepts it and the v4
        # compat maps the key onto the unified `timeout` field.
        h = _ApplyHarness(tmp_path, monkeypatch)
        full = h.full("j")
        v4_uuid = "aaaa1111-2222-3333-4444-555566667777"
        snap_dir = h.state / DEFAULT_BUNDLE_NAME / v4_uuid
        snap_dir.mkdir(parents=True)
        v4 = {
            "schema": 4,
            "kind": "job",
            "name": full,
            "bundle": DEFAULT_BUNDLE_NAME,
            "uuid": v4_uuid,
            "command": "true",
            "script": None,
            "args": [],
            "gate": None,
            "gate_script": None,
            "gate_args": [],
            "env": {},
            "job_timeout_sec": 600,
        }
        (snap_dir / "snapshot.json").write_text(json.dumps(v4))
        snap = crony_runtime.load_snapshot(
            EntityRef(DEFAULT_BUNDLE_NAME, v4_uuid)
        )
        assert isinstance(snap, Job)
        assert snap.timeout == 600

    def test_v4_group_snapshot_loads_via_timeout_compat(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The v4 group key `group_budget_sec` maps onto `timeout`
        # through the same gate path as a job.
        h = _ApplyHarness(tmp_path, monkeypatch)
        full = h.full("g")
        v4_uuid = "bbbb1111-2222-3333-4444-555566667777"
        snap_dir = h.state / DEFAULT_BUNDLE_NAME / v4_uuid
        snap_dir.mkdir(parents=True)
        v4 = {
            "schema": 4,
            "kind": "group",
            "name": full,
            "bundle": DEFAULT_BUNDLE_NAME,
            "uuid": v4_uuid,
            "children": [],
            "group_budget_sec": 900,
            "trigger_timeout_sec": 15,
        }
        (snap_dir / "snapshot.json").write_text(json.dumps(v4))
        snap = crony_runtime.load_snapshot(
            EntityRef(DEFAULT_BUNDLE_NAME, v4_uuid)
        )
        assert isinstance(snap, JobGroup)
        assert snap.timeout == 900


class TestRuntimeUnitLastExit:
    """load_config captures each entry's scheduler last-launch outcome
    on its RuntimeState via one bulk query."""

    def _ref(self, config: Any, full: str) -> Any:
        return config.pending.by_full_name.get(
            full
        ) or config.current.by_full_name.get(full)

    def test_signal_kill_lands_on_runtime_state(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        # The applied unit's last launch was killed by SIGKILL.
        monkeypatch.setattr(
            launchd,
            "_launchctl_list",
            lambda: "PID\tStatus\tLabel\n-\t-9\torg.crony.default.j\n",
        )
        config = crony_runtime.load_config()
        rt = config.runtime[self._ref(config, "default.j")]
        assert rt.unit_last_exit is not None
        assert rt.unit_last_exit.exit_status == -9
        assert rt.crashed is True

    def test_clean_launch_leaves_runtime_state_clean(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        monkeypatch.setattr(
            launchd,
            "_launchctl_list",
            lambda: "PID\tStatus\tLabel\n-\t0\torg.crony.default.j\n",
        )
        config = crony_runtime.load_config()
        rt = config.runtime[self._ref(config, "default.j")]
        assert rt.unit_last_exit == crony_platform.UnitLastExit(exit_status=0)
        assert rt.crashed is False


class TestRuntimePidCrashSignal:
    """A surviving run.pid naming a different pid than the recorded run
    flags `crashed` even when the scheduler kept no exit record (e.g. the
    unit was unloaded), which the launchctl-list reconciliation misses."""

    def _ref(self, config: Any, full: str) -> Any:
        return config.pending.by_full_name.get(
            full
        ) or config.current.by_full_name.get(full)

    def test_lingering_run_pid_flags_crashed(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _ApplyHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {"job": {"j": {"command": "true", "schedule": "*-*-* 03:00"}}},
            default_target_jobs=["j"],
        )
        h.apply("j")
        sd = h.state_dir("j", cfg=cfg)
        # An earlier launch recorded (pid 100); a later one wrote run.pid
        # and died without recording. The scheduler has no record.
        (sd / "last-run.json").write_text(
            json.dumps(
                {
                    "exit_class": "ok",
                    "started_at": "2026-01-01T00:00:00-08:00",
                    "process_exit": 0,
                    "pid": 100,
                }
            ),
            encoding="utf-8",
        )
        (sd / "run.pid").write_text("999999\n", encoding="utf-8")
        monkeypatch.setattr(launchd, "_launchctl_list", lambda: "")
        config = crony_runtime.load_config()
        rt = config.runtime[self._ref(config, "default.j")]
        assert rt.run_pid == 999999
        assert rt.unit_last_exit is None
        assert rt.crashed is True


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
