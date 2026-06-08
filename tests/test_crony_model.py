#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "tomlkit"]
# ///
# This is AI generated code

"""Unit tests for crony.model."""

from __future__ import annotations

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
    parse_full_name,
    resolve_cli_name,
)
from crony.errors import (  # noqa: E402
    ConfigError,
    UsageError,
)
from crony.model import (  # noqa: E402
    RUN_LOG_NAME,
    _resolve_snapshot_for,
)
from crony.unit import (  # noqa: E402
    EntityRef,
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
        with pytest.raises(ConfigError, match="keep_awake' must be bool"):
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
    form is what platform units pass to `crony run`. The parser
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
    `symlink` pair resolves to its own uuid, else the uuid-keyed path.
    """

    def _snap(self) -> Any:
        cfg = _parse({"job": {"j": _job()}})
        return _resolve_snapshot_for(cfg, "j")

    def test_expected_pair_reports_alias(self) -> None:
        snap = self._snap()
        # A config-built node carries the expected pair (alias -> uuid).
        assert snap.symlink == (snap.symlink_state_dir, snap.uuid)
        assert snap.log_path == snap.symlink_state_dir / RUN_LOG_NAME

    def test_missing_pair_reports_uuid_path(self) -> None:
        snap = self._snap()
        snap.symlink = None
        assert snap.log_path == snap.log_path_resolved

    def test_mismatched_target_reports_uuid_path(self) -> None:
        snap = self._snap()
        # A link that points at some other uuid is not this node's
        # alias, so the reported path falls back to the uuid dir.
        snap.symlink = (snap.symlink_state_dir, "other-uuid")
        assert snap.log_path == snap.log_path_resolved


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
