#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "pytest-cov", "tomlkit", "pydantic>=2"]
# ///
# This is AI generated code

"""Unit tests for crony.model."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_crony import (  # noqa: E402
    _assert_errored_job,
    _assert_errored_job_group,
    _isolate_home,  # noqa: E402, F401
    _job,
    _parse,
)

from crony.config import (  # noqa: E402
    DEFAULT_BUNDLE_NAME,
    JobFlags,
    parse_full_name,
    resolve_cli_name,
)
from crony.errors import (  # noqa: E402
    ConfigError,
    ExitCode,
    UsageError,
)
from crony.model import (  # noqa: E402
    RUN_LOG_NAME,
    ConfigStatus,
    ExitClass,
    Graph,
    Job,
    JobGroup,
    JobOrphan,
    JobStatus,
    LastRun,
    RuntimeState,
    ScheduleValue,
    _JobCommon,
    _resolve_snapshot_for,
    snapshot_from_dict,
)
from crony.platform import UnitLastExit  # noqa: E402
from crony.platform.fda import FDAWrapper  # noqa: E402
from crony.snapshot import (  # noqa: E402
    _COMPAT_FLOOR_SCHEMA,
    COMPAT_SNAPSHOT_SCHEMA,
    CURRENT_SNAPSHOT_SCHEMA,
    GroupSnapshot,
    JobSnapshot,
    _Since,
    parse,
)
from crony.unit import (  # noqa: E402
    EntityKind,
    EntityName,
    EntityRef,
    PriorityClass,
)

_script_path = REPO_ROOT / "src" / "crony" / "model.py"


class TestTypeStrictness:
    """Booleans must not silently pass for int-typed fields, and
    int-typed defaults must be positive.
    """

    def test_bool_rejected_for_int_field(self) -> None:
        with pytest.raises(ConfigError, match="bool"):
            _parse({"defaults": {"job_timeout_sec": True}})

    def test_negative_default_timeout_rejected(self) -> None:
        with pytest.raises(ConfigError, match=">= 0"):
            _parse({"defaults": {"job_timeout_sec": -5}})

    def test_zero_default_timeout_means_no_cap(self) -> None:
        # 0 is the "no wallclock cap" sentinel, valid at the defaults
        # level so a bundle can disable the cap for all its jobs.
        cfg = _parse({"defaults": {"job_timeout_sec": 0}})
        assert cfg.defaults.job_timeout_sec == 0

    def test_negative_default_attach_max_rejected(self) -> None:
        with pytest.raises(ConfigError, match="positive"):
            _parse({"defaults": {"notify_attach_max_kb": -1}})

    def test_negative_default_log_keep_rejected(self) -> None:
        with pytest.raises(ConfigError, match="positive"):
            _parse({"defaults": {"log_keep_runs": 0}})

    def test_invalid_default_priority_rejected(self) -> None:
        with pytest.raises(ConfigError, match="invalid priority"):
            _parse({"defaults": {"priority": "turbo"}})

    def test_non_bool_default_keep_awake_rejected(self) -> None:
        with pytest.raises(ConfigError, match="keep-awake' must be bool"):
            _parse({"defaults": {"keep_awake": "yes"}})


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
        with pytest.raises(ConfigError, match="must match"):
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
        assert resolve_cli_name("foo", None) == "default.foo"

    def test_qualified_round_trips_without_scope(self) -> None:
        assert resolve_cli_name("borgadm.k", None) == "borgadm.k"

    def test_bare_resolves_in_scope_bundle(self) -> None:
        assert resolve_cli_name("foo", "borgadm") == "borgadm.foo"

    def test_qualified_in_scope_round_trips(self) -> None:
        assert resolve_cli_name("borgadm.k", "borgadm") == "borgadm.k"

    def test_qualified_other_bundle_rejected(self) -> None:
        with pytest.raises(UsageError, match="default"):
            resolve_cli_name("default.k", "borgadm")


class TestParseFullName:
    """`parse_full_name` turns CLI input into (bundle, short)."""

    def test_bare_name_is_default_bundle(self) -> None:
        assert parse_full_name("foo") == (
            DEFAULT_BUNDLE_NAME,
            "foo",
        )

    def test_namespaced_form(self) -> None:
        assert parse_full_name("borgadm.foo") == ("borgadm", "foo")

    def test_multi_dot_short_name(self) -> None:
        # Splits on the FIRST dot; remaining dots stay in the short.
        assert parse_full_name("default.foo.bar") == (
            "default",
            "foo.bar",
        )

    def test_empty_bundle_rejected(self) -> None:
        with pytest.raises(UsageError):
            parse_full_name(".foo")

    def test_empty_short_rejected(self) -> None:
        with pytest.raises(UsageError):
            parse_full_name("default.")


class TestEntityRefInput:
    """The `<bundle>:<UUID>` input form lets an operator address
    an entity that has no recoverable config-side name (corrupt
    snapshot.json, broken entity, unit-only orphan) by pasting the
    JOB cell from a status row back into a subcommand. The same
    form is what platform units pass to `crony _run`. The parser
    validates both pieces so the resulting `EntityRef` is safe
    to compose into a state-dir path.
    """

    _CANONICAL_UUID = "11111111-2222-3333-4444-555555555555"

    def test_parse_round_trips(self) -> None:
        ref = EntityRef("default", self._CANONICAL_UUID)
        rendered = str(ref)
        assert rendered == f"default:{self._CANONICAL_UUID}"
        assert EntityRef.from_str(rendered) == ref

    def test_parse_with_non_default_bundle(self) -> None:
        ref = EntityRef("borgadm", self._CANONICAL_UUID)
        rendered = str(ref)
        assert EntityRef.from_str(rendered) == ref

    def test_parse_non_ref_returns_none(self) -> None:
        # Dot-separated names aren't entity refs.
        assert EntityRef.from_str("default.foo") is None
        # Bare names aren't entity refs either.
        assert EntityRef.from_str("foo") is None
        # Bundle-only (no uuid body).
        assert EntityRef.from_str("default:") is None
        # No bundle.
        assert EntityRef.from_str(f":{self._CANONICAL_UUID}") is None

    def test_parse_rejects_non_canonical_uuid(self) -> None:
        # Validation runs because the parsed ref flows into a
        # path that `shutil.rmtree` later trusts -- a malformed
        # uuid must fail at parse time, not at filesystem time.
        assert EntityRef.from_str("default:not-a-uuid") is None
        assert EntityRef.from_str("default:abc123") is None
        # Path-traversal-shaped uuid bodies must be rejected so
        # `crony destroy` can't be tricked into `rmtree`-ing
        # `STATE_DIR/default/../../etc`.
        assert EntityRef.from_str("default:../../etc") is None

    def test_parse_rejects_invalid_bundle_name(self) -> None:
        # Bundle names are constrained by `_BUNDLE_NAME_RE`; an
        # invalid bundle prevents the path composition from
        # walking outside `STATE_DIR`.
        bad = f"../etc:{self._CANONICAL_UUID}"
        assert EntityRef.from_str(bad) is None


class TestLogPath:
    """`log_path` reports the short-name alias when the node's recorded
    `state_dir_symlink` pair resolves to its own uuid, else the uuid-keyed path.
    """

    def _snap(self) -> Any:
        cfg = _parse({"job": {"j": _job()}})
        return _resolve_snapshot_for(cfg, "j")

    def test_expected_pair_reports_alias(self) -> None:
        snap = self._snap()
        # A config-built node carries the expected pair (alias -> uuid).
        assert snap.state_dir_symlink == (
            snap.state_dir_symlink_path,
            snap.uuid,
        )
        assert snap.log_path == snap.state_dir_symlink_path / RUN_LOG_NAME

    def test_missing_pair_reports_uuid_path(self) -> None:
        snap = dataclasses.replace(self._snap(), state_dir_symlink=None)
        assert snap.log_path == snap.log_path_resolved

    def test_mismatched_target_reports_uuid_path(self) -> None:
        base = self._snap()
        # A link that points at some other uuid is not this node's
        # alias, so the reported path falls back to the uuid dir.
        snap = dataclasses.replace(
            base, state_dir_symlink=(base.state_dir_symlink_path, "other-uuid")
        )
        assert snap.log_path == snap.log_path_resolved


class TestJobFlagsBacking:
    """`interactive` and `keep_awake` are stored as a single `flags`
    bitmask; the per-flag booleans are derived properties, and the
    snapshot round-trips through those booleans (the bitmask itself is
    never serialized)."""

    def _snap(self, **over: Any) -> Any:
        return _resolve_snapshot_for(_parse({"job": {"j": _job(**over)}}), "j")

    def test_flags_reflect_interactive_and_keep_awake(self) -> None:
        snap = self._snap(interactive=True, keep_awake=True)
        assert snap.flags == JobFlags.INTERACTIVE | JobFlags.KEEP_AWAKE
        assert snap.interactive is True
        assert snap.keep_awake is True

    def test_no_flags_by_default(self) -> None:
        snap = self._snap()
        assert snap.flags == JobFlags(0)
        assert snap.interactive is False
        assert snap.keep_awake is False
        assert snap.full_disk_access is False

    def test_full_disk_access_backs_property_and_round_trips(self) -> None:
        snap = self._snap(flags=["full-disk-access"])
        assert snap.full_disk_access is True
        assert JobFlags.FULL_DISK_ACCESS in snap.flags
        d = snap.to_dict()
        assert d["full-disk-access"] is True
        assert snapshot_from_dict(d).flags == snap.flags

    def test_snapshot_serializes_flags_as_booleans(self) -> None:
        snap = self._snap(keep_awake=True)
        d = snap.to_dict()
        assert "flags" not in d
        # Keyed by the dash token, matching the config spelling.
        assert d["keep-awake"] is True
        assert d["interactive"] is False
        assert "keep_awake" not in d
        # The booleans fold back into the bitmask on load.
        assert snapshot_from_dict(d).flags == snap.flags

    def test_snapshot_loads_legacy_underscore_flag_key(self) -> None:
        # Snapshots written before the dash rename keyed keep-awake as
        # `keep_awake`; the loader still folds that legacy spelling in.
        d = self._snap(keep_awake=True).to_dict()
        d["keep_awake"] = d.pop("keep-awake")
        assert snapshot_from_dict(d).flags == JobFlags.KEEP_AWAKE

    def test_group_carries_resolved_flags_and_round_trips(self) -> None:
        # A group carries its resolved cascade value (here keep-awake
        # inherited from the defaults level) and serializes it like a
        # job, so its pending snapshot equals its reloaded one -- no
        # spurious config=stale drift from a flag that round-trips off.
        cfg = _parse(
            {
                "defaults": {"flags": ["keep-awake"]},
                "job": {"a": _job()},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
            }
        )
        group = _resolve_snapshot_for(cfg, "g")
        assert group.flags == JobFlags.KEEP_AWAKE
        # The alias pair and the normalized units are derived state, never
        # serialized, so a bare reload (no disk inputs) carries neither.
        assert snapshot_from_dict(group.to_dict()) == dataclasses.replace(
            group,
            state_dir_symlink=None,
            unit_config_normalized=None,
            unit_timer_normalized=None,
        )


class TestFdaWrapperField:
    """`Job.fda_wrapper` carries the Crony.app wrapper state as a graph
    knows it -- OK (expected) on a config-built node, the probed live
    state on a current node, None for a non-FDA job. It is compared in
    `==` (so wrapper drift is caught by the snapshot comparison) but
    never serialized. It is a job-only field: a group never carries
    it."""

    def _snap(self, **over: Any) -> Any:
        return _resolve_snapshot_for(_parse({"job": {"j": _job(**over)}}), "j")

    def test_pending_fda_job_expects_ok(self) -> None:
        snap = self._snap(flags=["full-disk-access"])
        assert snap.fda_wrapper is FDAWrapper.OK

    def test_non_fda_job_has_no_wrapper(self) -> None:
        assert self._snap().fda_wrapper is None

    def test_to_dict_excludes_fda_wrapper(self) -> None:
        d = self._snap(flags=["full-disk-access"]).to_dict()
        assert "fda_wrapper" not in d
        assert "fda-wrapper" not in d

    def test_load_stamps_probed_state_for_fda_job(self) -> None:
        d = self._snap(flags=["full-disk-access"]).to_dict()
        loaded = snapshot_from_dict(d, fda_wrapper=FDAWrapper.STALE)
        assert isinstance(loaded, Job)
        assert loaded.fda_wrapper is FDAWrapper.STALE

    def test_load_ignores_probed_state_for_non_fda_job(self) -> None:
        # A probed value passed for a non-FDA job is dropped -- the two
        # graphs must agree on None so a non-FDA job never drifts.
        d = self._snap().to_dict()
        loaded = snapshot_from_dict(d, fda_wrapper=FDAWrapper.STALE)
        assert isinstance(loaded, Job)
        assert loaded.fda_wrapper is None

    def test_wrapper_drift_breaks_equality(self) -> None:
        ok = self._snap(flags=["full-disk-access"])
        stale = dataclasses.replace(ok, fda_wrapper=FDAWrapper.STALE)
        assert ok != stale

    def test_round_trip_off_probe_matches_expected_node(self) -> None:
        # Loaded with no probe (current-graph-less load), the node reads
        # back with fda_wrapper None; the pending node's expected OK is
        # the only difference, so equality holds once both derived
        # fields are normalized.
        snap = self._snap(flags=["full-disk-access"])
        loaded = snapshot_from_dict(snap.to_dict())
        assert loaded == dataclasses.replace(
            snap,
            state_dir_symlink=None,
            fda_wrapper=None,
            unit_config_normalized=None,
            unit_timer_normalized=None,
        )

    def test_group_has_no_fda_wrapper_field(self) -> None:
        group = _resolve_snapshot_for(
            _parse(
                {
                    "job": {"a": _job()},
                    "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
                }
            ),
            "g",
        )
        assert isinstance(group, JobGroup)
        assert not hasattr(group, "fda_wrapper")


class TestSharedSnapshotSurface:
    """`timing`, `unit_spec`, and `to_dict` are declared once on the
    `_JobCommon` base and shared by both `Job` and `JobGroup`; only the
    unit's priority differs (a job exposes its resolved class, a group
    renders without one).
    """

    def _job_snap(self) -> Any:
        cfg = _parse({"job": {"j": _job(priority="high")}})
        return _resolve_snapshot_for(cfg, "j")

    def _group_snap(self) -> Any:
        raw = {
            "job": {"a": _job()},
            "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
        }
        return _resolve_snapshot_for(_parse(raw), "g")

    def test_timing_is_a_shared_base_field(self) -> None:
        base_fields = {f.name for f in dataclasses.fields(_JobCommon)}
        assert "timing" in base_fields
        # Both kinds carry the pinned schedule through the same field.
        assert self._job_snap().timing is not None
        assert self._group_snap().timing is not None

    def test_job_unit_spec_carries_its_priority(self) -> None:
        # `unit_spec` builds the command itself from the node's
        # executables, so the node has to carry them.
        snap = dataclasses.replace(
            self._job_snap(),
            uv_path=Path("/uv"),
            crony_path=Path("/crony"),
        )
        spec = snap.unit_spec()
        assert spec.priority == snap.priority
        assert snap.priority is PriorityClass.HIGH
        assert spec.timing == snap.timing
        assert spec.cmd[0] == "/uv"

    def test_group_unit_spec_is_normal(self) -> None:
        # Groups request no special scheduling, which resolves to the
        # neutral NORMAL class (zero platform directives) rather than a
        # nullable priority.
        group = dataclasses.replace(
            self._group_snap(),
            uv_path=Path("/uv"),
            crony_path=Path("/crony"),
        )
        spec = group.unit_spec()
        assert spec.priority is PriorityClass.NORMAL
        assert spec.timing == group.timing

    def test_group_to_dict_round_trips_without_priority(self) -> None:
        group = self._group_snap()
        d = group.to_dict()
        assert "priority" not in d
        # The alias pair and the normalized units are derived state, never
        # serialized, so a reloaded node carries neither until the
        # current-graph scan supplies them.
        assert snapshot_from_dict(d) == dataclasses.replace(
            group,
            state_dir_symlink=None,
            unit_config_normalized=None,
            unit_timer_normalized=None,
        )

    def test_timeout_is_a_shared_base_field(self) -> None:
        base_fields = {f.name for f in dataclasses.fields(_JobCommon)}
        assert "timeout" in base_fields
        # A job pins its resolved job-timeout-sec; a group pins its
        # cumulative child budget -- both through the same field.
        assert self._job_snap().timeout == 1800
        assert self._group_snap().timeout > 0

    def test_snapshot_writes_unified_timeout_key(self) -> None:
        d = self._job_snap().to_dict()
        assert d["timeout"] == 1800
        assert "job_timeout_sec" not in d
        assert "group_budget_sec" not in d

    def test_v4_compat_maps_job_timeout_sec(self) -> None:
        # Schema 4 keyed a job's deadline as `job_timeout_sec`; the v4
        # compat in snapshot_from_dict maps it onto `timeout`.
        d = self._job_snap().to_dict()
        d["job_timeout_sec"] = d.pop("timeout")
        assert snapshot_from_dict(d).timeout == 1800

    def test_v4_compat_maps_group_budget_sec(self) -> None:
        # Schema 4 keyed a group's deadline as `group_budget_sec`; same
        # forward-map.
        group = self._group_snap()
        d = group.to_dict()
        d["group_budget_sec"] = d.pop("timeout")
        assert snapshot_from_dict(d).timeout == group.timeout


class TestUnitDisabled:
    """`unit_disabled` is the operator-disable overlay: it drops the
    schedule from `unit_spec` (so the unit loads dormant) while leaving
    the `timing` field intact, and it round-trips through snapshot.json
    (it is real applied state, not derived)."""

    def _snap(self) -> Any:
        cfg = _parse({"job": {"j": _job(schedule="daily")}})
        # `unit_spec` builds the command from the node's executables, so
        # the node has to carry them.
        return dataclasses.replace(
            _resolve_snapshot_for(cfg, "j"),
            uv_path=Path("/uv"),
            crony_path=Path("/crony"),
        )

    def test_default_is_enabled(self) -> None:
        assert self._snap().unit_disabled is False

    def test_disabled_strips_schedule_but_keeps_timing(self) -> None:
        snap = dataclasses.replace(self._snap(), unit_disabled=True)
        assert snap.timing is not None  # pinned, so `enable` restores it
        assert snap.unit_spec().timing is None  # rendered dormant

    def test_enabled_renders_with_schedule(self) -> None:
        snap = self._snap()
        assert snap.unit_spec().timing == snap.timing

    def test_round_trips_through_snapshot(self) -> None:
        snap = dataclasses.replace(self._snap(), unit_disabled=True)
        d = snap.to_dict()
        assert d["unit_disabled"] is True
        assert snapshot_from_dict(d).unit_disabled is True

    def test_absent_in_old_snapshot_loads_false(self) -> None:
        d = self._snap().to_dict()
        d.pop("unit_disabled", None)  # a snapshot written before the field
        assert snapshot_from_dict(d).unit_disabled is False


class TestJobFromRefAndFullName:
    """`Graph.job_from_ref` is the single-source ref->node lookup the
    reconciliation paths compose to make their source order explicit;
    `full_name` reads the name uniformly across a node and an orphan.
    """

    def _snap(self) -> Any:
        return _resolve_snapshot_for(_parse({"job": {"j": _job()}}), "j")

    def test_job_from_ref_is_single_source(self) -> None:
        snap = self._snap()
        ref = snap.entity_ref
        graph = Graph()
        graph.jobs[ref] = snap
        assert graph.job_from_ref(ref) == snap
        # Single-source: a different (empty) graph never returns it --
        # callers compose the cross-source order they want.
        assert Graph().job_from_ref(ref) is None
        # A ref the graph doesn't carry resolves to None.
        missing = EntityRef(snap.bundle, "11111111-2222-3333-4444-555555555555")
        assert graph.job_from_ref(missing) is None

    def test_nodes_returns_jobs_then_groups(self) -> None:
        raw = {
            "job": {"a": _job()},
            "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
        }
        cfg = _parse(raw)
        job = _resolve_snapshot_for(cfg, "a")
        group = _resolve_snapshot_for(cfg, "g")
        assert isinstance(job, Job)
        assert isinstance(group, JobGroup)
        graph = Graph()
        graph.jobs[job.entity_ref] = job
        graph.groups[group.entity_ref] = group
        # The node-level analogue of refs(): jobs first, then groups.
        assert graph.nodes() == [job, group]
        assert Graph().nodes() == []

    def test_full_name_uniform_across_node_and_orphan(self) -> None:
        snap = self._snap()
        assert snap.full_name == "default.j"
        named = JobOrphan(bundle="default", uuid=snap.uuid, name="default.j")
        assert named.full_name == "default.j"
        # A too-corrupt remnant carries no name -- the only shape
        # difference from the node's always-set `str`.
        nameless = JobOrphan(bundle="default", uuid=snap.uuid, name=None)
        assert nameless.full_name is None


class TestRuntimeStateCrashed:
    """`crashed` flags a launch that ended without recording its result,
    via two independent signals: a surviving run.pid naming a different
    pid than the last record wrote (the launch never reached cleanup),
    or the scheduler's last-launch status disagreeing with the recorded
    process exit. A status matching the recorded exit, or an in-flight
    run, is not a crash."""

    def _rs(
        self,
        unit_last_exit: UnitLastExit | None = None,
        *,
        last_run: LastRun | None = None,
        run_pid: int | None = None,
        is_running: bool = False,
    ) -> RuntimeState:
        return RuntimeState(
            state_dir=Path("/x"),
            last_run=last_run,
            is_running=is_running,
            is_pending=False,
            has_user_trigger_flag=False,
            unit_last_exit=unit_last_exit,
            run_pid=run_pid,
        )

    def _last(
        self,
        exit_class: str,
        process_exit: int | None,
        *,
        pid: int | None = None,
    ) -> LastRun:
        return LastRun(
            exit_class=ExitClass(exit_class),
            started_at="2026-01-01T00:00",
            process_exit=process_exit,
            pid=pid,
        )

    def test_lingering_run_pid_unrecorded_is_crashed(self) -> None:
        # A launch wrote run.pid (7397) then died; the surviving record
        # is from an earlier launch (pid 100), so the two pids differ.
        rs = self._rs(
            last_run=self._last("ok", 0, pid=100),
            run_pid=7397,
        )
        assert rs.crashed is True

    def test_run_pid_without_any_record_is_crashed(self) -> None:
        # First-ever launch died before writing any record.
        rs = self._rs(run_pid=7397)
        assert rs.crashed is True

    def test_run_pid_matching_record_is_not_crashed(self) -> None:
        # The record carries the same pid as the surviving run.pid, so
        # that launch did record (run.pid just wasn't unlinked yet).
        rs = self._rs(
            last_run=self._last("ok", 0, pid=7397),
            run_pid=7397,
        )
        assert rs.crashed is False

    def test_run_pid_while_running_is_not_crashed(self) -> None:
        # An in-flight run holds the lock; its run.pid is expected.
        rs = self._rs(
            last_run=self._last("ok", 0, pid=100),
            run_pid=7397,
            is_running=True,
        )
        assert rs.crashed is False

    def test_signal_kill_over_stale_ok_is_crashed(self) -> None:
        # Stale "ok" (process_exit 0) survives a launch the scheduler
        # killed (negative status); the two don't match.
        rs = self._rs(
            UnitLastExit(exit_status=-9),
            last_run=self._last("ok", 0),
        )
        assert rs.crashed is True

    def test_nonzero_exit_without_matching_record_is_crashed(self) -> None:
        # uv-not-found (127) before the runner recorded; stale "ok".
        rs = self._rs(
            UnitLastExit(exit_status=127),
            last_run=self._last("ok", 0),
        )
        assert rs.crashed is True

    def test_abnormal_without_record_is_crashed(self) -> None:
        rs = self._rs(UnitLastExit(exit_status=-11))
        assert rs.crashed is True

    def test_recorded_failure_matching_status_is_not_crashed(self) -> None:
        # A normal job failure: the runner recorded it and exited the
        # process with the same code the scheduler reports.
        rs = self._rs(
            UnitLastExit(exit_status=1),
            last_run=self._last("fail", 1),
        )
        assert rs.crashed is False

    def test_recorded_cancel_matching_status_is_not_crashed(self) -> None:
        # A snapshot-load-failure cancel exits PRECONDITION and records
        # the same process_exit, so it stays `canceled`, not `crashed`.
        code = int(ExitCode.PRECONDITION)
        rs = self._rs(
            UnitLastExit(exit_status=code),
            last_run=self._last("canceled", code),
        )
        assert rs.crashed is False

    def test_clean_exit_is_not_crashed(self) -> None:
        rs = self._rs(
            UnitLastExit(exit_status=0),
            last_run=self._last("ok", 0),
        )
        assert rs.crashed is False

    def test_no_scheduler_record_is_not_crashed(self) -> None:
        # Also the in-flight case: a running unit is omitted from the
        # map, so its RuntimeState carries no unit_last_exit.
        rs = self._rs(None)
        assert rs.crashed is False


class TestStatusEnums:
    """ExitClass / JobStatus are StrEnums: they serialize as their plain
    values (on-disk records round-trip) and JobStatus reuses ExitClass's
    values for the outcomes it carries."""

    def test_exitclass_serializes_as_plain_value(self) -> None:
        assert (
            json.dumps({"exit_class": ExitClass.OK}) == '{"exit_class": "ok"}'
        )

    def test_parse_known_value(self) -> None:
        assert ExitClass.parse("timeout") is ExitClass.TIMEOUT

    def test_parse_is_tolerant_of_bad_input(self) -> None:
        assert ExitClass.parse("bogus") is None
        assert ExitClass.parse(None) is None
        assert ExitClass.parse(123) is None

    def test_jobstatus_shares_exitclass_values(self) -> None:
        # The shared members are defined as `= ExitClass.X`, so their
        # string values stay in lockstep.
        assert str(JobStatus.OK) == str(ExitClass.OK) == "ok"
        assert str(JobStatus.CANCELED) == str(ExitClass.CANCELED)

    def test_jobstatus_omits_folded_and_dropped_outcomes(self) -> None:
        # signal folds to fail, dispatched shows as unknown -- neither
        # reaches the LAST cell, so JobStatus carries no member for them.
        values = {s.value for s in JobStatus}
        assert "signal" not in values
        assert "dispatched" not in values
        assert "crashed" in values

    def test_every_status_value_is_documented(self) -> None:
        # The `status` --help CONFIG / STATUS value reference renders
        # these descriptions; a member without one would surface as a
        # blank entry. Adding a value without documenting it fails here.
        for member in (*ConfigStatus, *JobStatus, *ScheduleValue):
            assert member.description, f"{member!r} has no description"


class TestSnapshotSchemaVersioning:
    """The snapshot schema constants derive from the model's per-field
    version tags, and the strict model stays in lockstep with the
    runtime flag set."""

    def _job_snapshot_dict(self) -> dict[str, Any]:
        cfg = _parse({"job": {"j": _job()}})
        return _resolve_snapshot_for(cfg, "j").to_dict()

    def test_current_schema_is_the_newest_field_version(self) -> None:
        versions = [
            meta.version
            for model in (JobSnapshot, GroupSnapshot)
            for info in model.model_fields.values()
            for meta in info.metadata
            if isinstance(meta, _Since)
        ]
        assert CURRENT_SNAPSHOT_SCHEMA == max(versions)

    def test_every_field_carries_exactly_one_version(self) -> None:
        # A persisted field with no version tag would leave
        # CURRENT_SNAPSHOT_SCHEMA unable to see it.
        for model in (JobSnapshot, GroupSnapshot):
            for name, info in model.model_fields.items():
                tags = [m for m in info.metadata if isinstance(m, _Since)]
                assert len(tags) == 1, f"{model.__name__}.{name}: {tags}"

    def test_compat_is_floor_through_current(self) -> None:
        assert COMPAT_SNAPSHOT_SCHEMA == frozenset(
            range(_COMPAT_FLOOR_SCHEMA, CURRENT_SNAPSHOT_SCHEMA + 1)
        )

    def test_model_has_a_field_for_every_flag(self) -> None:
        # Adding a JobFlags member without a matching model field (aliased
        # to its token) would silently drop the flag from the snapshot.
        keys = {
            info.serialization_alias or name
            for name, info in JobSnapshot.model_fields.items()
        }
        for flag in JobFlags.members():
            assert flag.token in keys, f"no model field for {flag.token}"

    def test_unknown_key_is_a_broken_snapshot(self) -> None:
        # extra="forbid": an unrecognized key fails validation, which the
        # loaders treat as a broken snapshot (ValidationError is a
        # ValueError).
        d = self._job_snapshot_dict()
        d["bogus_key"] = 1
        with pytest.raises(ValueError):
            snapshot_from_dict(d)


class TestSnapshotFieldSync:
    """Every persisted snapshot field must be wired into both
    `<node>.to_snapshot` and `<node>.from_snapshot`. The mapping is not
    mechanical -- a source string becomes a value object, the per-flag
    booleans become a bitmask -- so there is no `**`-splat keeping the two
    directions in lockstep; this round-trip is the guard instead. A
    snapshot dict with every field at a distinctive non-default value must
    survive a node load and re-dump unchanged: a field that
    `from_snapshot` drops (or `to_snapshot` fails to re-emit) shows up as a
    diff. The companion census tests assert the maximal dict still covers
    the full field set, so adding a field forces this coverage (and thus
    the round-trip) to be extended for it."""

    def _persisted_keys(self, model: Any) -> set[str]:
        return {
            info.serialization_alias or name
            for name, info in model.model_fields.items()
        }

    def _maximal_job_dict(self) -> dict[str, Any]:
        cfg = _parse(
            {
                "job": {
                    "j": _job(
                        priority="high",
                        flags=["interactive", "keep-awake"],
                        **{"success-exit-codes": [3, 7]},
                    )
                }
            }
        )
        d = _resolve_snapshot_for(cfg, "j").to_dict()
        # Push every remaining persisted field off its default so a
        # dropped one surfaces as a round-trip diff. `interval` stays the
        # schedule's mutually-exclusive null twin.
        d.update(
            {
                "args": ["a", "b"],
                "env": {"K": "V"},
                "gate": "true",
                "gate_script": "/g",
                "gate_args": ["x"],
                "script": "/s",
                "interactive_active_sec": 11,
                "interactive_delay_sec": 22,
                "full-disk-access": True,
                "unit_disabled": True,
            }
        )
        return d

    def _maximal_group_dict(self) -> dict[str, Any]:
        cfg = _parse(
            {
                "job": {"a": _job()},
                "job-group": {
                    "g": {
                        "jobs": ["a"],
                        "interval": "1h",
                        "flags": ["keep-awake"],
                    }
                },
            }
        )
        d = _resolve_snapshot_for(cfg, "g").to_dict()
        d["unit_disabled"] = True
        return d

    def test_job_dict_round_trips_unchanged(self) -> None:
        d = self._maximal_job_dict()
        assert snapshot_from_dict(d).to_dict() == d

    def test_group_dict_round_trips_unchanged(self) -> None:
        d = self._maximal_group_dict()
        assert snapshot_from_dict(d).to_dict() == d

    def test_job_dict_covers_every_persisted_field(self) -> None:
        assert set(self._maximal_job_dict()) == self._persisted_keys(
            JobSnapshot
        )

    def test_group_dict_covers_every_persisted_field(self) -> None:
        assert set(self._maximal_group_dict()) == self._persisted_keys(
            GroupSnapshot
        )


class TestSnapshotIdentityRehydration:
    """`kind` is a typed `EntityKind` field (pydantic decodes the on-disk
    string at validation), and `entity_name()` / `entity_ref()` rehydrate
    the stored name / uuid into their value objects -- so `crony.model`
    consumes typed identity without re-parsing the on-disk encoding. The
    on-disk `kind` stays the plain string so an older crony reads it
    unchanged."""

    def _job_dict(self) -> dict[str, Any]:
        cfg = _parse({"job": {"j": _job()}})
        return _resolve_snapshot_for(cfg, "j").to_dict()

    def _group_dict(self) -> dict[str, Any]:
        cfg = _parse(
            {
                "job": {"a": _job()},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
            }
        )
        return _resolve_snapshot_for(cfg, "g").to_dict()

    def test_kind_decodes_to_entity_kind(self) -> None:
        assert parse(self._job_dict()).kind is EntityKind.JOB
        assert parse(self._group_dict()).kind is EntityKind.GROUP

    def test_kind_serializes_as_plain_string(self) -> None:
        assert self._job_dict()["kind"] == "job"
        assert self._group_dict()["kind"] == "group"

    def test_entity_name_and_ref_rehydrate(self) -> None:
        snap = parse(self._job_dict())
        en = EntityName.from_str(snap.name)
        assert snap.entity_name() == en
        assert snap.entity_ref() == EntityRef(en.bundle, snap.uuid)


class TestGroupChildRefs:
    """A JobGroup carries its children as bundle-scoped `EntityRef`s (the
    parent's bundle paired with each child uuid). On disk they persist as
    the bare uuids, and `GroupSnapshot.child_refs` rebuilds the refs on
    load so a child rename never flips the parent."""

    _CHILD_UUID = "11111111-1111-1111-1111-111111111111"

    def _group_dict(self) -> dict[str, Any]:
        cfg = _parse(
            {
                "job": {"a": _job()},
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
            }
        )
        d = _resolve_snapshot_for(cfg, "g").to_dict()
        d["children"] = [self._CHILD_UUID]
        return d

    def test_child_uuids_load_as_bundle_scoped_refs(self) -> None:
        loaded = snapshot_from_dict(self._group_dict())
        assert isinstance(loaded, JobGroup)
        assert loaded.children == [EntityRef(loaded.bundle, self._CHILD_UUID)]

    def test_children_persist_back_as_uuids(self) -> None:
        # The node holds refs but the on-disk edge stays the bare uuid,
        # so an older crony reads it unchanged.
        loaded = snapshot_from_dict(self._group_dict())
        assert loaded.to_dict()["children"] == [self._CHILD_UUID]

    def test_child_refs_decode_drives_the_node(self) -> None:
        d = self._group_dict()
        snap = parse(d)
        assert isinstance(snap, GroupSnapshot)
        assert snap.child_refs() == [
            EntityRef(snap.entity_name().bundle, self._CHILD_UUID)
        ]
        loaded = snapshot_from_dict(d)
        assert isinstance(loaded, JobGroup)
        assert loaded.children == snap.child_refs()


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
