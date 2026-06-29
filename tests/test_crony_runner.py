#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "pytest-cov", "tomlkit", "pydantic>=2"]
# ///
# This is AI generated code

"""Unit tests for crony.runner."""

import json
import math
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_crony import (  # noqa: E402
    _ApplyHarness,
    _assert_errored_job,
    _email_block,
    _isolate_home,  # noqa: E402, F401
    _job,
    _ntfy_block,
    _parse,
    _RunnerHarness,
    _stub_trigger_sync,
    crony,
)

from crony import commands as crony_commands  # noqa: E402
from crony import notify as crony_notify  # noqa: E402
from crony import platform as crony_platform  # noqa: E402
from crony import runner as crony_runner  # noqa: E402
from crony import runtime as crony_runtime  # noqa: E402
from crony.config import (  # noqa: E402
    DEFAULT_BUNDLE_NAME,
    TomlBundleConfig,
    TomlJob,
)
from crony.errors import (  # noqa: E402
    ConfigError,
    ExitCode,
    JobTimeoutError,
    PreconditionError,
    TriggerStartTimeout,
    UnitNotInstalledError,
)
from crony.model import (  # noqa: E402
    ExitClass,
    GroupChildResult,
    Job,
    _resolve_script,
    _resolve_snapshot_for,
)
from crony.platform import (  # noqa: E402
    PidWait,
)
from crony.platform import fda as crony_fda  # noqa: E402
from crony.platform.fda import FDAWrapper  # noqa: E402
from crony.snapshot import CURRENT_SNAPSHOT_SCHEMA  # noqa: E402
from crony.unit import (  # noqa: E402
    EntityName,
    PriorityClass,
)

_script_path = REPO_ROOT / "src" / "crony" / "runner.py"


class TestPathFieldExpansion:
    """`script`, `args`, `gate_script`, and `gate_args` accept `~` and
    `$VAR` / `${VAR}`, mirroring how shell-string `command` fields are
    expanded by `/bin/sh`. Without this, configs that use `$HOME` in
    a script path fail with a misleading "script not found" error
    (the literal `$HOME` gets concatenated under CONFIG_DIR).
    """

    def test_resolve_script_expands_tilde(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("HOME", "/home/user")
        p = _resolve_script("~/bin/foo.sh")
        assert str(p) == "/home/user/bin/foo.sh"

    def test_resolve_script_expands_dollar_var(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("HOME", "/home/user")
        p = _resolve_script("$HOME/bin/foo.sh")
        assert str(p) == "/home/user/bin/foo.sh"

    def test_resolve_script_expands_braced_var(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("HOME", "/home/user")
        p = _resolve_script("${HOME}/bin/foo.sh")
        assert str(p) == "/home/user/bin/foo.sh"

    def test_resolve_script_unresolved_var_stays_literal(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("CRONY_NO_SUCH_VAR", raising=False)
        # When no expansion applies, the value falls under CONFIG_DIR
        # as a relative path. The literal `$VAR` is preserved.
        p = _resolve_script("$CRONY_NO_SUCH_VAR/foo.sh")
        assert "$CRONY_NO_SUCH_VAR" in str(p)

    def test_snapshot_resolves_expanded_args(self, monkeypatch: Any) -> None:
        # Path-field expansion is applied at snapshot-resolve time
        # (apply); the runner then pulls already-expanded argv from
        # the snapshot.
        monkeypatch.setenv("HOME", "/home/user")
        job = TomlJob(
            name="j",
            uuid=str(uuid.uuid4()),
            script="/abs/path.sh",
            args=["~/data", "$HOME/cache", "--flag"],
        )
        snap = Job.from_config(
            TomlBundleConfig(),
            job,
            EntityName.from_str("default.j"),
        )
        assert snap.script == "/abs/path.sh"
        assert snap.args == [
            "/home/user/data",
            "/home/user/cache",
            "--flag",
        ]
        assert crony_runner._command_argv(snap) == [
            "/abs/path.sh",
            "/home/user/data",
            "/home/user/cache",
            "--flag",
        ]

    def test_snapshot_resolves_expanded_gate_args(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("HOME", "/home/user")
        job = TomlJob(
            name="j",
            uuid=str(uuid.uuid4()),
            command="true",
            gate_script="/abs/gate.sh",
            gate_args=["$HOME/state"],
        )
        snap = Job.from_config(
            TomlBundleConfig(),
            job,
            EntityName.from_str("default.j"),
        )
        assert snap.gate_script == "/abs/gate.sh"
        assert snap.gate_args == ["/home/user/state"]
        assert crony_runner._gate_argv(snap) == [
            "/abs/gate.sh",
            "/home/user/state",
        ]


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
        env = crony_runner._runtime_env({})
        assert env["PATH"] == "/usr/bin:/bin"

    def test_session_bus_vars_forwarded(self, monkeypatch: Any) -> None:
        # The linux session-bus locators must reach the job so a
        # command like `crony apply` can drive `systemctl --user`.
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        monkeypatch.setenv(
            "DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus"
        )
        env = crony_runner._runtime_env({})
        assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"
        assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"

    def test_arbitrary_inherited_var_forwarded(self, monkeypatch: Any) -> None:
        # The inherited env passes through wholesale: any var in the
        # runner's environment reaches the job. SSH_AUTH_SOCK rides
        # this path so jobs can reach the user's ssh-agent.
        monkeypatch.setenv("CRONY_INHERIT_PROBE", "passed-through")
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
        env = crony_runner._runtime_env({})
        assert env["CRONY_INHERIT_PROBE"] == "passed-through"
        assert env["SSH_AUTH_SOCK"] == "/tmp/agent.sock"

    def test_unset_var_absent(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("CRONY_INHERIT_PROBE", raising=False)
        env = crony_runner._runtime_env({})
        assert "CRONY_INHERIT_PROBE" not in env

    def test_dollar_var_resolves_against_inherited(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        env = crony_runner._runtime_env({"PATH": "/extra:$PATH"})
        assert env["PATH"] == "/extra:/usr/bin:/bin"

    def test_brace_form_resolves(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("HOME", "/Users/edp")
        env = crony_runner._runtime_env({"TMPDIR": "${HOME}/.local/tmp"})
        assert env["TMPDIR"] == "/Users/edp/.local/tmp"

    def test_expansion_resolves_against_any_inherited_var(
        self, monkeypatch: Any
    ) -> None:
        # Expansion sees the whole inherited env, so a value can
        # reference any inherited var (here the session runtime dir).
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        env = crony_runner._runtime_env({"BUS": "$XDG_RUNTIME_DIR/bus"})
        assert env["BUS"] == "/run/user/1000/bus"

    def test_unknown_var_stays_literal(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("CRONY_NOPE", raising=False)
        env = crony_runner._runtime_env({"FOO": "$CRONY_NOPE"})
        assert env["FOO"] == "$CRONY_NOPE"

    def test_double_dollar_escapes_to_literal(self) -> None:
        env = crony_runner._runtime_env({"MSG": "cost: $$5"})
        assert env["MSG"] == "cost: $5"

    def test_iteration_order_lets_later_keys_see_earlier(
        self, monkeypatch: Any
    ) -> None:
        # Python dicts preserve insertion order; toml parsers do too.
        # An earlier job.env key should be visible to a later one.
        monkeypatch.setenv("PATH", "/usr/bin")
        env = crony_runner._runtime_env(
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
        env = crony_runner._runtime_env({"FOO": "bar"})
        assert env["HOME"] == "/Users/edp"
        assert env["LANG"] == "en_US.UTF-8"
        assert env["FOO"] == "bar"

    def test_malformed_references_stay_literal(self) -> None:
        # safe_substitute leaves bad-shape references untouched
        # rather than raising. $1 isn't a valid identifier; a
        # trailing bare $ has nothing to consume; ${UNCLOSED has
        # no closing brace.
        env = crony_runner._runtime_env(
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
        rc = crony_runner._run_job(h.snap(cfg, "ok"))
        assert rc == 0
        rec = h.last_run("ok")
        assert rec["exit_class"] == "ok"
        assert rec["exit_code"] == 0
        assert rec["gate"] == "none"
        # The record carries the runner's pid (the run executed in this
        # process), the basis for the run.pid crash signal.
        assert rec["pid"] == os.getpid()

    def test_log_header_reports_timeout_and_pid(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The run.log header reports `timeout=<n>s pid=<pid>` -- the
        # same shape a group header uses.
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "ok": {
                        "command": "true",
                        "schedule": "daily",
                        "job-timeout-sec": 300,
                    }
                }
            },
            default_target_jobs=["ok"],
        )
        crony_runner._run_job(h.snap(cfg, "ok"))
        log = (h.state_dir("ok") / "run.log").read_text(encoding="utf-8")
        assert f"{h.full('ok')} timeout=300s pid=" in log

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
        rc = crony_runner._run_job(h.snap(cfg, "fail"))
        assert rc == 17
        rec = h.last_run("fail")
        assert rec["exit_class"] == "fail"
        assert rec["exit_code"] == 17

    def test_unknown_name_raises_precondition_at_resolve(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config({}, default_target_jobs=[])
        with pytest.raises(PreconditionError, match="unknown"):
            _resolve_snapshot_for(cfg, "ghost")

    def test_run_without_snapshot_raises_precondition(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(PreconditionError, match="no snapshot"):
            crony_runner.do_run(
                ref="default:11111111-2222-3333-4444-999999999999",
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
        sd = h.state / DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        (sd / "snapshot.json").write_text(
            '{"schema": 999, "kind": "job", "name": "default.j"}',
            encoding="utf-8",
        )
        with pytest.raises(PreconditionError, match="schema 999"):
            crony_runner.do_run(
                ref=f"default:{uuid_value}",
            )
        rec = json.loads((sd / "last-run.json").read_text(encoding="utf-8"))
        assert rec["exit_class"] == "canceled"
        assert rec["exit_code"] == int(ExitCode.PRECONDITION)
        # process_exit matches the code the process exits with, so status
        # reconciles this against the scheduler as `canceled`, not
        # `crashed`.
        assert rec["process_exit"] == int(ExitCode.PRECONDITION)
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
        sd = h.state / DEFAULT_BUNDLE_NAME / uuid_value
        assert not sd.exists()
        with pytest.raises(PreconditionError, match="no snapshot"):
            crony_runner.do_run(
                ref=f"default:{uuid_value}",
            )
        assert not sd.exists()

    def test_run_records_last_run_on_unreadable_snapshot(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        uuid_value = "11112222-3333-4444-5555-aaaabbbbcccc"
        sd = h.state / DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        # Corrupt JSON: parser bails before schema / kind checks.
        (sd / "snapshot.json").write_text("{not valid json", encoding="utf-8")
        with pytest.raises(PreconditionError, match="unreadable"):
            crony_runner.do_run(
                ref=f"default:{uuid_value}",
            )
        rec = json.loads((sd / "last-run.json").read_text(encoding="utf-8"))
        assert rec["exit_class"] == "canceled"

    def test_run_records_last_run_on_unknown_kind(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        uuid_value = "11112222-3333-4444-5555-bbbbccccdddd"
        sd = h.state / DEFAULT_BUNDLE_NAME / uuid_value
        sd.mkdir(parents=True)
        # Schema matches, but `kind` is neither "job" nor "group".
        (sd / "snapshot.json").write_text(
            f'{{"schema": {CURRENT_SNAPSHOT_SCHEMA}, '
            f'"kind": "banana", "name": "default.j"}}',
            encoding="utf-8",
        )
        # The discriminated-union validator rejects the unknown tag, so
        # the entry loads as a malformed (broken) snapshot.
        with pytest.raises(PreconditionError, match="malformed fields"):
            crony_runner.do_run(
                ref=f"default:{uuid_value}",
            )
        rec = json.loads((sd / "last-run.json").read_text(encoding="utf-8"))
        assert rec["exit_class"] == "canceled"

    def test_run_records_canceled_on_dispatch_precondition(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A precondition that fails inside _run_job (here a missing
        # script), after the snapshot loads, records `canceled` the
        # same way a snapshot-load failure does -- otherwise the fire
        # leaves no record and surfaces only as an unexplained
        # `crashed` launch.
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "script": "/nonexistent/script",
                        "schedule": "daily",
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.write_snap(cfg, "j")
        uuid_value = cfg.jobs["j"].uuid
        sd = h.state_dir("j", cfg=cfg)
        with pytest.raises(PreconditionError, match="script not found"):
            crony_runner.do_run(
                ref=f"default:{uuid_value}",
            )
        rec = json.loads((sd / "last-run.json").read_text(encoding="utf-8"))
        assert rec["exit_class"] == "canceled"
        assert rec["exit_code"] == int(ExitCode.PRECONDITION)
        assert rec["process_exit"] == int(ExitCode.PRECONDITION)
        assert "script not found" in rec["reason"]
        assert "CANCELED" in (sd / "run.log").read_text(encoding="utf-8")

    def test_canceled_surfaces_in_status_column(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # The whole point of writing last-run.json: `crony status`
        # shows the canceled label in the STATUS column on the next
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
        # STATUS column carries the canceled label; not silently
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
        assert h.full("j") in out
        assert "canceled" in out


class TestSuccessExitCodes:
    """Per-job `success_exit_codes`: configured non-zero codes classify
    as "ok" (surfacing 0 to the scheduler, no notification); everything
    else still fails.
    """

    def test_parse_accepts_int_list(self) -> None:
        cfg = _parse({"job": {"j": _job(success_exit_codes=[0, 1])}})
        assert cfg.jobs["j"].success_exit_codes == [0, 1]

    def test_parse_default_empty(self) -> None:
        cfg = _parse({"job": {"j": _job()}})
        assert cfg.jobs["j"].success_exit_codes == []

    def test_parse_rejects_non_int(self) -> None:
        _assert_errored_job(
            {"job": {"j": _job(success_exit_codes=["x"])}},
            "j",
            "must be a list of integers",
        )

    def test_parse_rejects_bool(self) -> None:
        _assert_errored_job(
            {"job": {"j": _job(success_exit_codes=[True])}},
            "j",
            "must be a list of integers",
        )

    def test_parse_rejects_out_of_range(self) -> None:
        _assert_errored_job(
            {"job": {"j": _job(success_exit_codes=[300])}},
            "j",
            "out of the valid 0-255",
        )

    def test_snapshot_carries_codes(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "warn": _job(success_exit_codes=[1]),
                }
            },
            default_target_jobs=["warn"],
        )
        assert h.snap(cfg, "warn").success_exit_codes == [1]

    def test_listed_code_classified_ok(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "warn": {
                        "command": "exit 1",
                        "schedule": "daily",
                        "success_exit_codes": [1],
                    }
                }
            },
            default_target_jobs=["warn"],
        )
        # Surfaces 0 to the scheduler even though the command exited 1.
        rc = crony_runner._run_job(h.snap(cfg, "warn"))
        assert rc == 0
        rec = h.last_run("warn")
        assert rec["exit_class"] == "ok"
        # The real exit code is still recorded.
        assert rec["exit_code"] == 1

    def test_unlisted_code_still_fails(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "warn": {
                        "command": "exit 2",
                        "schedule": "daily",
                        "success_exit_codes": [1],
                    }
                }
            },
            default_target_jobs=["warn"],
        )
        rc = crony_runner._run_job(h.snap(cfg, "warn"))
        assert rc == 2
        rec = h.last_run("warn")
        assert rec["exit_class"] == "fail"
        assert rec["exit_code"] == 2

    def test_ok_classification_suppresses_notify(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "warn": {
                        "command": "exit 1",
                        "schedule": "daily",
                        "success_exit_codes": [1],
                        "notify_channels": ["dialog-popup"],
                    }
                }
            },
            default_target_jobs=["warn"],
        )
        called: list[int] = []
        monkeypatch.setattr(
            crony_notify, "dispatch_notify", lambda *_a, **_k: called.append(1)
        )
        crony_runner._run_job(h.snap(cfg, "warn"))
        assert not called  # ok -> dispatch skipped, no dialog


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
        rc = crony_runner._run_job(h.snap(cfg, "g"))
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
        rc = crony_runner._run_job(h.snap(cfg, "g"))
        assert rc == 0  # gated exits 0
        rec = h.last_run("g")
        assert rec["exit_class"] == "gated"
        assert rec["gate"] == "failed"
        # Main command never ran -> exit_code recorded as 0 placeholder
        assert rec["exit_code"] == 0
        log = (h.state_dir("g") / "run.log").read_text()
        assert "skipping job" in log


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
            rc = crony_runner._run_job(h.snap(cfg, "j"))
        finally:
            _fcntl.flock(held, _fcntl.LOCK_UN)
            held.close()
        assert rc == int(ExitCode.LOCK_BUSY)
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
        crony_runner._run_job(h.snap(cfg, "fail"))
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
        with pytest.raises(ConfigError, match="not defined"):
            h.config(
                {
                    "defaults": {"notify_channels": ["ntfy"]},
                    "job": {
                        "fail": {"command": "exit 1", "schedule": "daily"},
                    },
                },
                default_target_jobs=["fail"],
            )


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
        rc = crony_runner._run_group(h.snap(cfg, "g"))
        assert rc == 0
        rec = h.last_run("g")
        names = [c["name"] for c in rec["jobs_run"]]
        assert names == [h.full("a"), h.full("b")]
        # Children fire in declared order through the platform stub.
        led = crony._ledger
        assert [e["full_name"] for e in led] == [h.full("a"), h.full("b")]
        # The group run.log header reports `timeout=<n>s pid=<pid>`,
        # the same shape a job header uses.
        log = (h.state_dir("g") / "run.log").read_text(encoding="utf-8")
        assert "timeout=" in log
        assert "pid=" in log
        assert f"group {h.full('g')} " in log

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
        rc = crony_runner._run_group(h.snap(cfg, "g"))
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
        crony_runner._run_group(h.snap(cfg, "g"))
        rec = h.last_run("g")
        assert rec["exit_class"] == "ok"

    def test_group_skips_disabled_child(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A child whose snapshot is operator-disabled is not dispatched;
        # it records `gated` (which rolls up `ok`) and the group fires
        # only the enabled children.
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
        h.write_snap(cfg, "a", disabled=True)
        h.write_snap(cfg, "b")
        _stub_trigger_sync(
            monkeypatch,
            {h.full("b"): {"exit_code": 0, "exit_class": "ok"}},
        )
        rc = crony_runner._run_group(h.snap(cfg, "g"))
        assert rc == 0
        rec = h.last_run("g")
        rows = {c["name"]: c for c in rec["jobs_run"]}
        assert rows[h.full("a")]["exit_class"] == "gated"
        assert rows[h.full("b")]["exit_class"] == "ok"
        # Only the enabled child actually fired through the platform.
        assert [e["full_name"] for e in crony._ledger] == [h.full("b")]
        assert rec["exit_class"] == "ok"
        log = (h.state_dir("g") / "run.log").read_text(encoding="utf-8")
        assert f"{h.full('a')}: skipped (disabled)" in log

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

        def _slow(*_args: object, **_kwargs: object) -> dict[str, Any]:
            # Burn 11 seconds of monotonic time using a fake clock;
            # we monkeypatch time.monotonic to make this fast.
            return {"exit_code": 0, "exit_class": "ok"}

        # Simulate elapsed time by returning a moving monotonic value.
        clock = {"now": 0.0}
        real_monotonic = crony_commands.time.monotonic

        def fake_monotonic() -> float:
            return float(real_monotonic()) + clock["now"]

        monkeypatch.setattr(crony_commands.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(crony_runner, "trigger_unit_sync", _slow)

        # Advance the fake clock forward in the stub so the second
        # iteration sees no remaining budget.
        called: list[str] = []

        def _stub_advance(full_name: str, **_kwargs: object) -> dict[str, Any]:
            called.append(full_name)
            clock["now"] += 11.0  # past 1.05*(5+5) budget
            return {"exit_code": 0, "exit_class": "ok"}

        monkeypatch.setattr(crony_runner, "trigger_unit_sync", _stub_advance)

        h.write_snap(cfg, "a")
        h.write_snap(cfg, "b")
        rc = crony_runner._run_group(h.snap(cfg, "g"))
        assert rc == 0
        rec = h.last_run("g")
        # Only `a` actually fired; `b` was budget-skipped.
        assert called == [h.full("a")]
        assert rec["jobs_run"][0]["name"] == h.full("a")
        assert rec["jobs_run"][0]["exit_class"] == "ok"
        assert rec["jobs_run"][1]["name"] == h.full("b")
        assert rec["jobs_run"][1]["exit_class"] == "timeout"

    def test_group_uncapped_child_dispatched_with_no_cap(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An uncapped child (job_timeout_sec=0) makes the group budget
        # infinite, so the child is dispatched with no wallclock cap
        # (job_timeout=inf) instead of being budget-skipped.
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "a": {
                        "command": "true",
                        "schedule": "daily",
                        "job_timeout_sec": 0,
                    },
                },
                "job-group": {"g": {"jobs": ["a"], "schedule": "daily"}},
            },
            default_target_jobs=["g"],
        )
        captured: dict[str, float] = {}

        def _capture(
            full_name: str, *, job_timeout: float, **_kwargs: object
        ) -> dict[str, Any]:
            captured[full_name] = job_timeout
            return {"exit_code": 0, "exit_class": "ok"}

        monkeypatch.setattr(crony_runner, "trigger_unit_sync", _capture)
        h.write_snap(cfg, "a")
        assert crony_runner._run_group(h.snap(cfg, "g")) == 0
        assert math.isinf(captured[h.full("a")])
        rec = h.last_run("g")
        assert rec["jobs_run"][0]["exit_class"] == "ok"

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

        def _stub(full_name: str, **_kwargs: object) -> dict[str, Any]:
            if full_name == h.full("missing"):
                raise UnitNotInstalledError(
                    f"unit for {full_name!r} is not installed on this host"
                )
            return {"exit_code": 0, "exit_class": "ok"}

        monkeypatch.setattr(crony_runner, "trigger_unit_sync", _stub)
        h.write_snap(cfg, "missing")
        h.write_snap(cfg, "ok")
        rc = crony_runner._run_group(h.snap(cfg, "g"))
        # Group orchestration succeeds (rc 0); the child failure
        # surfaces in the rolled-up exit_class and per-child
        # records so the runner's notification path fires.
        assert rc == 0
        rec = h.last_run("g")
        missing_rec = rec["jobs_run"][0]
        assert missing_rec["name"] == h.full("missing")
        assert missing_rec["exit_class"] == "fail"
        assert missing_rec["exit_code"] == int(ExitCode.PRECONDITION)
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

        def _stub(*_args: object, **_kwargs: object) -> dict[str, Any]:
            return {"exit_code": 0, "exit_class": "ok"}

        monkeypatch.setattr(crony_runner, "trigger_unit_sync", _stub)
        # Only "ok" gets a snapshot; "gone" stays unresolvable.
        h.write_snap(cfg, "ok")
        rc = crony_runner._run_group(h.snap(cfg, "g"))
        assert rc == 0
        rec = h.last_run("g")
        # Resolved child ran first; the synthetic fail row trails.
        assert rec["jobs_run"][0]["name"] == h.full("ok")
        assert rec["jobs_run"][0]["exit_class"] == "ok"
        gone_uuid = cfg.jobs["gone"].uuid
        synthetic = rec["jobs_run"][1]
        assert synthetic["name"] == f"default:{gone_uuid}"
        assert synthetic["exit_class"] == "fail"
        assert synthetic["exit_code"] == int(ExitCode.PRECONDITION)
        assert rec["exit_class"] == "fail"


class TestRunGroupInteractive:
    """A group that contains an interactive child fires that child
    async (via `trigger_unit`, not `trigger_unit_sync`) and moves
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

        def _stub_sync(full_name: str, **_kwargs: object) -> dict[str, Any]:
            sync_calls.append(full_name)
            return {"exit_code": 0, "exit_class": "ok"}

        def _stub_async(name: str, *_args: object, **_kwargs: object) -> None:
            async_calls.append(name)

        monkeypatch.setattr(crony_runner, "trigger_unit_sync", _stub_sync)
        monkeypatch.setattr(crony_runner, "trigger_unit", _stub_async)

        rc = crony_runner._run_group(h.snap(cfg, "g"))
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
        target = cfg.resolve_target("test-host", "darwin")
        budget = cfg.resolved_group_timeout_sec(target, "g")
        # Only the non-interactive child contributes:
        # 1.05 * 100 == 105.
        assert budget == 105

    def test_group_budget_excludes_inherited_interactive_children(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A child interactive only via an inherited flag (here from
        # [defaults]) is excluded too -- the budget uses the resolved
        # flags, not the child's own delta.
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "defaults": {"flags": ["interactive"]},
                "job": {
                    "iv": {
                        "command": "true",
                        "schedule": "daily",
                        "job_timeout_sec": 10_000,
                    },
                    "regular": {
                        "command": "true",
                        "job_timeout_sec": 100,
                        "flags": ["interactive=false"],
                    },
                },
                "job-group": {
                    "g": {"jobs": ["iv", "regular"], "schedule": "daily"},
                },
            },
            default_target_jobs=["g"],
        )
        target = cfg.resolve_target("test-host", "darwin")
        # iv is interactive via the defaults flag -> excluded; only
        # regular (which overrides it off, 100) contributes: 1.05 * 100.
        assert cfg.resolved_group_timeout_sec(target, "g") == 105

    def test_group_budget_zero_when_child_uncapped(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "uncapped": {"command": "true", "job_timeout_sec": 0},
                    "regular": {"command": "true", "job_timeout_sec": 100},
                },
                "job-group": {
                    "g": {
                        "jobs": ["uncapped", "regular"],
                        "schedule": "daily",
                    },
                },
            },
            default_target_jobs=["g"],
        )
        target = cfg.resolve_target("test-host", "darwin")
        # An uncapped child makes the whole group uncapped (0 = no cap).
        assert cfg.resolved_group_timeout_sec(target, "g") == 0

    def test_group_budget_zero_propagates_through_subgroup(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        cfg = h.config(
            {
                "job": {
                    "uncapped": {"command": "true", "job_timeout_sec": 0},
                },
                "job-group": {
                    "leaf": {"jobs": ["uncapped"]},
                    "root": {"jobs": ["leaf"], "schedule": "daily"},
                },
            },
            default_target_jobs=["root"],
        )
        target = cfg.resolve_target("test-host", "darwin")
        # 0 propagates up the group chain.
        assert cfg.resolved_group_timeout_sec(target, "leaf") == 0
        assert cfg.resolved_group_timeout_sec(target, "root") == 0

    def test_dispatched_does_not_poison_rollup(self) -> None:
        # `dispatched` has precedence 0 so it ties with ok / gated
        # in the rollup; a group with only dispatched children
        # rolls up as "ok".
        rollup = crony_runner._rollup_group_exit_class(
            [
                GroupChildResult(
                    name="a", exit_class=ExitClass.DISPATCHED, exit_code=0
                ),
                GroupChildResult(
                    name="b", exit_class=ExitClass.OK, exit_code=0
                ),
            ]
        )
        assert rollup == "ok"

    def test_dispatched_rolls_up_under_fail(self) -> None:
        rollup = crony_runner._rollup_group_exit_class(
            [
                GroupChildResult(
                    name="a", exit_class=ExitClass.DISPATCHED, exit_code=0
                ),
                GroupChildResult(
                    name="b", exit_class=ExitClass.FAIL, exit_code=1
                ),
            ]
        )
        assert rollup == "fail"


class TestTriggerUnitNotInstalled:
    """`trigger_unit` refuses early when the platform unit file
    doesn't exist, and `trigger_unit_sync` doesn't side-effect a
    state dir for a never-installed name.
    """

    # The autouse home-isolation fixture points Path.home() at a
    # never-created sentinel, so the scheduler's default unit dir
    # doesn't exist and a unit lookup deterministically reads "absent"
    # -- no per-test unit-dir setup needed.

    def test_trigger_unit_raises_when_unit_file_missing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("ghost")
        platform = crony_platform.current_platform()
        with pytest.raises(UnitNotInstalledError, match="not installed"):
            crony_runner.trigger_unit(full, platform)
        # No state dir leaked: the bundle subdir for default
        # should not have any uuid-keyed entries.
        bundle_dir = h.state / DEFAULT_BUNDLE_NAME
        assert not bundle_dir.exists() or not any(bundle_dir.iterdir())

    def test_trigger_unit_sync_does_not_create_state_dir(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The waiter takes a read-only stance on the state dir
        # until the runner actually starts. Refusing a missing
        # unit must not leave a phantom state-dir-only remnant
        # behind (which `crony status` would then surface).
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("ghost")
        ghost_sd = h.state / DEFAULT_BUNDLE_NAME / "u-ghost"
        with pytest.raises(UnitNotInstalledError):
            crony_runner.trigger_unit_sync(
                full,
                state_dir=ghost_sd,
                job_timeout=5.0,
                trigger_timeout=5.0,
            )
        # The waiter took read-only stance; refusing didn't create
        # the state dir we pointed it at.
        assert not ghost_sd.exists()


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
        crony_runner._run_job(h.snap(cfg, "fail"))
        rec = h.last_run("fail")
        assert list(rec["notifications"].keys()) == order


class TestJobPriority:
    """`priority` enum rendered into the platform unit (and tracked
    by the snapshot + unit-drift check)."""

    def test_parse_valid(self) -> None:
        cfg = _parse({"job": {"a": _job(priority="high")}})
        assert cfg.jobs["a"].priority is PriorityClass.HIGH

    def test_parse_omitted_is_none(self) -> None:
        cfg = _parse({"job": {"a": _job()}})
        assert cfg.jobs["a"].priority is None

    def test_parse_invalid_rejected(self) -> None:
        _assert_errored_job(
            {"job": {"a": _job(priority="turbo")}},
            "a",
            "invalid priority",
        )

    def test_snapshot_carries_priority(self) -> None:
        cfg = _parse({"job": {"a": _job(priority="high")}})
        snap = Job.from_config(
            cfg, cfg.jobs["a"], EntityName.from_str("default.a")
        )
        assert snap.priority is PriorityClass.HIGH

    def test_default_cascades_to_unset_job(self) -> None:
        cfg = _parse({"defaults": {"priority": "high"}, "job": {"a": _job()}})
        snap = Job.from_config(
            cfg, cfg.jobs["a"], EntityName.from_str("default.a")
        )
        assert snap.priority == PriorityClass.HIGH

    def test_snapshot_unset_priority_is_normal(self) -> None:
        # An unset config priority resolves to the concrete NORMAL class
        # in the snapshot (never None), and an explicit `normal` yields
        # the same value -- so the two never spuriously diverge.
        unset = _parse({"job": {"a": _job()}})
        explicit = _parse({"job": {"a": _job(priority="normal")}})
        for cfg in (unset, explicit):
            snap = Job.from_config(
                cfg, cfg.jobs["a"], EntityName.from_str("default.a")
            )
            assert snap.priority is PriorityClass.NORMAL

    def test_job_overrides_default(self) -> None:
        cfg = _parse(
            {
                "defaults": {"priority": "high"},
                "job": {"a": _job(priority="low")},
            }
        )
        snap = Job.from_config(
            cfg, cfg.jobs["a"], EntityName.from_str("default.a")
        )
        assert snap.priority == PriorityClass.LOW

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
        crony_commands.do_apply(jobs=[h.full("j")], verbose=False, bundle=None)
        unit = h.agents / f"org.crony.{h.full('j')}.plist"
        content = unit.read_text()
        munged = content.replace(
            "<string>Interactive</string>", "<string>Standard</string>"
        )
        assert munged != content
        unit.write_text(munged)
        config = crony_runtime.load_config()
        ref = config.current.by_full_name[h.full("j")]
        assert config.cfg_status(ref) == "stale"


class TestKeepAwake:
    """`keep_awake` wraps the command in a power assertion at fire
    time (caffeinate / systemd-inhibit)."""

    def _snap(self, keep_awake: bool) -> Any:
        cfg = _parse({"job": {"a": _job(keep_awake=keep_awake)}})
        return Job.from_config(
            cfg, cfg.jobs["a"], EntityName.from_str("default.a")
        )

    def test_parse_true(self) -> None:
        cfg = _parse({"job": {"a": _job(keep_awake=True)}})
        assert cfg.jobs["a"].keep_awake is True

    def test_parse_default_none(self) -> None:
        # Omitting keep_awake leaves it None (inherit [defaults]); it
        # resolves to the default's False when no default is set.
        cfg = _parse({"job": {"a": _job()}})
        assert cfg.jobs["a"].keep_awake is None

    def test_default_cascades_to_unset_job(self) -> None:
        cfg = _parse({"defaults": {"keep_awake": True}, "job": {"a": _job()}})
        snap = Job.from_config(
            cfg, cfg.jobs["a"], EntityName.from_str("default.a")
        )
        assert snap.keep_awake is True

    def test_job_false_overrides_true_default(self) -> None:
        cfg = _parse(
            {
                "defaults": {"keep_awake": True},
                "job": {"a": _job(keep_awake=False)},
            }
        )
        snap = Job.from_config(
            cfg, cfg.jobs["a"], EntityName.from_str("default.a")
        )
        assert snap.keep_awake is False

    def test_parse_non_bool_rejected(self) -> None:
        _assert_errored_job(
            {"job": {"a": _job(keep_awake="yes")}},
            "a",
            "keep-awake' must be bool",
        )

    def test_snapshot_carries_keep_awake(self) -> None:
        assert self._snap(True).keep_awake is True

    def test_disabled_passthrough(self) -> None:
        argv, note = crony_runner._keep_awake_argv(["true"], self._snap(False))
        assert argv == ["true"]
        assert note is None

    def test_enabled_delegates_to_host(self, monkeypatch: Any) -> None:
        # When keep_awake is set, _keep_awake_argv hands the command and
        # the job label to the host wrapper and returns its result. The
        # per-host inhibitor command is covered by the backend tests.
        seen: dict[str, Any] = {}

        class _FakeHost:
            def keep_awake_argv(
                self, argv: list[str], label: str
            ) -> tuple[list[str], str | None]:
                seen["args"] = (argv, label)
                return ["wrap", *argv], None

        monkeypatch.setattr(crony_runtime, "host", lambda: _FakeHost())
        argv, note = crony_runner._keep_awake_argv(["true"], self._snap(True))
        assert argv == ["wrap", "true"]
        assert note is None
        # The job's full name is passed through as the label.
        assert seen["args"] == (["true"], "default.a")

    def test_run_job_wraps_command(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)  # platform -> darwin
        monkeypatch.setattr(
            crony_runtime.shutil,
            "which",
            lambda n: "/x/caffeinate" if n == "caffeinate" else None,
        )
        captured: dict[str, Any] = {}

        def fake_exec(argv: list[str], **_kwargs: object) -> Any:
            captured["argv"] = argv
            return crony_runner._ExitOutcome(rc=0, signal=None)

        monkeypatch.setattr(crony_runner, "_exec_with_timeout", fake_exec)
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "keep_awake": True,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert crony_runner._run_job(h.snap(cfg, "j")) == 0
        assert captured["argv"] == [
            "/x/caffeinate",
            "-i",
            "-s",
            "/bin/sh",
            "-c",
            "true",
        ]

    def test_run_job_uncapped_passes_none_timeout(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        captured: dict[str, Any] = {}

        def fake_exec(*_args: object, timeout: Any, **_kwargs: object) -> Any:
            captured["timeout"] = timeout
            return crony_runner._ExitOutcome(rc=0, signal=None)

        monkeypatch.setattr(crony_runner, "_exec_with_timeout", fake_exec)
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "job_timeout_sec": 0,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert crony_runner._run_job(h.snap(cfg, "j")) == 0
        # 0 (no cap) reaches the runner as None so proc.wait never caps.
        assert captured["timeout"] is None

    def test_run_job_logs_note_when_tool_missing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)  # platform -> darwin
        monkeypatch.setattr(crony_runtime.shutil, "which", lambda _n: None)
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "keep_awake": True,
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert crony_runner._run_job(h.snap(cfg, "j")) == 0
        log = (h.state_dir("j") / "run.log").read_text(encoding="utf-8")
        assert "caffeinate not found" in log


class TestFullDiskAccess:
    """A full-disk-access job's command is routed through the host FDA
    wrapper at fire time (Crony.app on darwin), inside any keep-awake
    wrap, and a missing wrapper / absent grant cancels the run via
    PreconditionError. The wrapper state / binary are mocked so this
    runs on any platform."""

    def _snap(self, **over: Any) -> Any:
        cfg = _parse({"job": {"a": _job(**over)}})
        return Job.from_config(
            cfg, cfg.jobs["a"], EntityName.from_str("default.a")
        )

    def test_disabled_passthrough(self) -> None:
        argv = crony_runner._full_disk_access_argv(["true"], self._snap())
        assert argv == ["true"]

    def test_enabled_delegates_to_host(self, monkeypatch: Any) -> None:
        seen: dict[str, Any] = {}

        class _FakeHost:
            def full_disk_access_argv(self, argv: list[str]) -> list[str]:
                seen["argv"] = argv
                return ["WRAP", *argv]

        monkeypatch.setattr(crony_runtime, "host", lambda: _FakeHost())
        argv = crony_runner._full_disk_access_argv(
            ["true"], self._snap(flags=["full-disk-access"])
        )
        assert argv == ["WRAP", "true"]
        assert seen["argv"] == ["true"]

    def test_run_job_wraps_command_inside_keep_awake(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # On darwin the real DarwinHost routes through Crony.app (state
        # / binary mocked) and caffeinate; the exec argv must be
        # caffeinate (outermost) -> Crony.app -> command.
        h = _RunnerHarness(tmp_path, monkeypatch)  # platform -> darwin
        monkeypatch.setattr(crony_fda, "wrapper_state", lambda: FDAWrapper.OK)
        monkeypatch.setattr(
            crony_fda, "wrapper_binary", lambda: Path("/x/Crony")
        )
        monkeypatch.setattr(
            crony_runtime.shutil,
            "which",
            lambda n: "/x/caffeinate" if n == "caffeinate" else None,
        )
        captured: dict[str, Any] = {}

        def fake_exec(argv: list[str], **_kwargs: object) -> Any:
            captured["argv"] = argv
            return crony_runner._ExitOutcome(rc=0, signal=None)

        monkeypatch.setattr(crony_runner, "_exec_with_timeout", fake_exec)
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "keep_awake": True,
                        "flags": ["full-disk-access"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        assert crony_runner._run_job(h.snap(cfg, "j")) == 0
        assert captured["argv"] == [
            "/x/caffeinate",
            "-i",
            "-s",
            "/x/Crony",
            "/bin/sh",
            "-c",
            "true",
        ]

    def test_run_job_raises_when_grant_denied(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)  # platform -> darwin
        monkeypatch.setattr(
            crony_fda, "wrapper_state", lambda: FDAWrapper.MISSING_FDA_GRANT
        )
        monkeypatch.setattr(
            crony_fda, "grant_instructions", lambda: "grant me FDA"
        )
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "flags": ["full-disk-access"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        with pytest.raises(PreconditionError, match="grant me FDA"):
            crony_runner._run_job(h.snap(cfg, "j"))

    def test_do_run_records_canceled_on_denied_grant(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)  # platform -> darwin
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "flags": ["full-disk-access"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        h.write_snap(cfg, "j")
        ref = str(h.snap(cfg, "j").entity_ref)
        monkeypatch.setattr(
            crony_fda, "wrapper_state", lambda: FDAWrapper.MISSING
        )
        with pytest.raises(PreconditionError, match="not built"):
            crony_runner.do_run(ref=ref)
        sd = h.state_dir("j", cfg=cfg)
        rec = json.loads((sd / "last-run.json").read_text(encoding="utf-8"))
        assert rec["exit_class"] == "canceled"
        assert "CANCELED" in (sd / "run.log").read_text(encoding="utf-8")

    def test_denied_grant_cancels_before_interactive_prompt(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The FDA precondition is checked before the interactive prompt,
        # so an interactive full-disk-access job with a denied grant is
        # canceled without ever asking the user to approve a run that
        # can't proceed.
        h = _RunnerHarness(tmp_path, monkeypatch)  # platform -> darwin
        prompted: list[bool] = []

        def _fake_prompt(*_a: object, **_k: object) -> str:
            prompted.append(True)
            return "run"

        monkeypatch.setattr(
            crony_runner, "_interactive_wait_and_prompt", _fake_prompt
        )
        monkeypatch.setattr(
            crony_fda, "wrapper_state", lambda: FDAWrapper.MISSING_FDA_GRANT
        )
        monkeypatch.setattr(
            crony_fda, "grant_instructions", lambda: "grant me FDA"
        )
        cfg = h.config(
            {
                "job": {
                    "j": {
                        "command": "true",
                        "schedule": "daily",
                        "interactive": True,
                        "flags": ["full-disk-access"],
                    }
                }
            },
            default_target_jobs=["j"],
        )
        with pytest.raises(PreconditionError, match="grant me FDA"):
            crony_runner._run_job(h.snap(cfg, "j"))
        assert prompted == [], "user was prompted before the FDA cancel"


class TestGroupExitClassRollup:
    """Direct unit tests for `_rollup_group_exit_class`. The
    STATUS column reads this rolled-up value from the group's
    last-run.json instead of re-deriving it; coverage
    here keeps the precedence ladder honest as new exit_class
    values get introduced.
    """

    def _children(self, *classes: str) -> list[Any]:
        return [
            GroupChildResult(
                name=f"default.c{i}",
                exit_class=ExitClass(cls),
                exit_code=0,
            )
            for i, cls in enumerate(classes)
        ]

    def test_empty_rolls_up_to_ok(self) -> None:
        assert crony_runner._rollup_group_exit_class([]) == "ok"

    def test_all_ok_rolls_up_to_ok(self) -> None:
        assert (
            crony_runner._rollup_group_exit_class(self._children("ok", "ok"))
            == "ok"
        )

    def test_gated_treated_as_success(self) -> None:
        # Gating is per-child intent ("don't run today"), not a
        # group-level outcome.
        assert (
            crony_runner._rollup_group_exit_class(self._children("ok", "gated"))
            == "ok"
        )
        assert (
            crony_runner._rollup_group_exit_class(
                self._children("gated", "gated")
            )
            == "ok"
        )

    def test_any_fail_rolls_up_to_fail(self) -> None:
        assert (
            crony_runner._rollup_group_exit_class(self._children("ok", "fail"))
            == "fail"
        )

    def test_signal_at_fail_grade(self) -> None:
        # A signaled child surfaces its own exit_class so a
        # downstream reader can distinguish abort signals from
        # plain non-zero exits if it cares.
        assert (
            crony_runner._rollup_group_exit_class(
                self._children("ok", "signal")
            )
            == "signal"
        )

    def test_timeout_outranks_fail(self) -> None:
        # Group with both a fail and a timeout: timeout wins so
        # the STATUS column surfaces the more severe condition.
        assert (
            crony_runner._rollup_group_exit_class(
                self._children("fail", "timeout", "ok")
            )
            == "timeout"
        )

    def test_gated_does_not_mask_fail(self) -> None:
        # gated ties with ok at the bottom; a fail child must
        # still surface, not be masked by sibling gating.
        assert (
            crony_runner._rollup_group_exit_class(
                self._children("gated", "fail")
            )
            == "fail"
        )
        assert (
            crony_runner._rollup_group_exit_class(
                self._children("fail", "gated")
            )
            == "fail"
        )

    def test_signal_and_fail_are_equally_severe(self) -> None:
        # signal and fail share severity 1; the first child of
        # that tier wins, so the readout reflects the
        # encountered-order outcome rather than swapping based on
        # iteration. This pins the tie-break for either case.
        assert (
            crony_runner._rollup_group_exit_class(
                self._children("signal", "fail")
            )
            == "signal"
        )
        assert (
            crony_runner._rollup_group_exit_class(
                self._children("fail", "signal")
            )
            == "fail"
        )


class TestTriggerUnitSync:
    """`trigger_unit_sync` wraps the kickstart + pid-watch +
    last-run.json cross-check. Stub the platform trigger and
    write a synthetic last-run.json to exercise the waiter loop
    without requiring real launchd / systemd."""

    def test_returns_recent_completion(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")

        def _stub_trigger(*_args: object, **_kwargs: object) -> None:
            # Pretend the runner ran and wrote a fresh result.
            (sd / "last-run.json").write_text(
                '{"ended_at": "2099-01-01T00:00:00-08:00",'
                ' "exit_code": 0, "exit_class": "ok"}',
                encoding="utf-8",
            )

        monkeypatch.setattr(crony_runner, "trigger_unit", _stub_trigger)
        rec = crony_runner.trigger_unit_sync(
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
        monkeypatch.setattr(
            crony_runner, "trigger_unit", lambda *_a, **_kw: None
        )
        with pytest.raises(TriggerStartTimeout, match="never produced"):
            crony_runner.trigger_unit_sync(
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
        monkeypatch.setattr(
            crony_runner, "trigger_unit", lambda *_a, **_kw: None
        )
        with pytest.raises(TriggerStartTimeout):
            crony_runner.trigger_unit_sync(
                full, state_dir=sd, job_timeout=5.0, trigger_timeout=1.0
            )

    def test_subsecond_run_is_recognized_as_fresh(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `pre_trigger` and `ended_at` must compare at the same
        # precision: the runner's `now_iso()` truncates to whole
        # seconds, so a run that completes within the same second
        # as the trigger needs `pre_trigger` truncated too --
        # otherwise the waiter sees `ended_at < pre_trigger` and
        # spins until `trigger_timeout`.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")

        def _stub_trigger(*_args: object, **_kwargs: object) -> None:
            # Write a last-run.json whose ended_at is the same
            # whole-second timestamp `now_iso()` would produce
            # right now -- modeling a sub-second run.
            (sd / "last-run.json").write_text(
                f'{{"ended_at": "{crony_runtime.now_iso()}", '
                f'"exit_code": 4, "exit_class": "fail"}}',
                encoding="utf-8",
            )

        monkeypatch.setattr(crony_runner, "trigger_unit", _stub_trigger)
        rec = crony_runner.trigger_unit_sync(
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

        def _stub_wait(_pid: int, timeout: float) -> PidWait:
            wait_calls.append(timeout)
            # Simulate the runner completing now: write a fresh
            # last-run.json and unlink the pid.
            (sd / "last-run.json").write_text(
                f'{{"ended_at": "{crony_runtime.now_iso()}",'
                ' "exit_code": 0, "exit_class": "ok"}',
                encoding="utf-8",
            )
            (sd / "run.pid").unlink(missing_ok=True)
            return PidWait.EXITED

        monkeypatch.setattr(
            crony_runner, "trigger_unit", lambda *_a, **_kw: None
        )
        monkeypatch.setattr(crony_runner, "_wait_for_pid_exit", _stub_wait)
        rec = crony_runner.trigger_unit_sync(
            full, state_dir=sd, job_timeout=120.0, trigger_timeout=1.0
        )
        # The runner completed, even though trigger_timeout (1s)
        # was tighter than what a real-world startup might take.
        assert rec["exit_class"] == "ok"
        # The wait was bounded by the larger job_timeout, not
        # trigger_timeout: confirms the deadline switch happened
        # once the pid was observed.
        assert wait_calls and wait_calls[0] > 1.0

    def test_dead_pid_does_not_spin_raises_start_timeout(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A run.pid that names a dead process (a launch that wrote
        # the pid then died without recording a result) must not be
        # treated as a live runner: re-attaching to a corpse whose
        # kernel pid-exit notification returns instantly is what
        # busy-spun the group dispatch. The waiter checks liveness
        # and falls to the bounded poll path, so even with an
        # uncapped job_timeout it surfaces TriggerStartTimeout within
        # trigger_timeout instead of looping forever.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")
        # PID_MAX on macOS is 99999; this is guaranteed non-existent.
        (sd / "run.pid").write_text("999999\n", encoding="utf-8")
        waited: list[int] = []

        def _no_wait(
            pid: int,
            timeout: float | None,  # noqa: ARG001
        ) -> PidWait:
            waited.append(pid)
            return PidWait.EXITED

        monkeypatch.setattr(
            crony_runner, "trigger_unit", lambda *_a, **_kw: None
        )
        monkeypatch.setattr(crony_runner, "_wait_for_pid_exit", _no_wait)
        with pytest.raises(TriggerStartTimeout, match="never produced"):
            crony_runner.trigger_unit_sync(
                full,
                state_dir=sd,
                job_timeout=math.inf,
                trigger_timeout=1.0,
            )
        # The dead pid was never handed to the pid-exit wait: liveness
        # gates the wait, so the corpse never re-armed the loop.
        assert waited == []

    def test_wedged_live_pid_raises_job_timeout(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A live child that never completes must not hold the group
        # forever: the hard job_timeout cap stops the wait and raises
        # JobTimeoutError. The pid points at the test process
        # (live for the test's duration); the pid-exit wait is stubbed
        # to model a bounded wait that times out without the pid
        # exiting, shortened so the test stays fast.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")
        (sd / "run.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

        def _timeout_wait(
            _pid: int,
            timeout: float | None,  # noqa: ARG001
        ) -> PidWait:
            time.sleep(0.2)
            return PidWait.TIMED_OUT

        monkeypatch.setattr(
            crony_runner, "trigger_unit", lambda *_a, **_kw: None
        )
        monkeypatch.setattr(crony_runner, "_wait_for_pid_exit", _timeout_wait)
        with pytest.raises(JobTimeoutError, match="did not complete"):
            crony_runner.trigger_unit_sync(
                full,
                state_dir=sd,
                job_timeout=0.1,
                trigger_timeout=30.0,
            )

    def test_completed_run_at_exhausted_budget_returns(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A child that completed is honored even when the budget is
        # already spent: the fresh-result read precedes the hard cap,
        # so a run finishing right at the boundary returns its real
        # record instead of being reported a spurious timeout.
        # job_timeout=0.0 models an already-exhausted finite budget.
        h = _RunnerHarness(tmp_path, monkeypatch)
        full = h.full("foo")
        sd = h.fabricate_orphan("foo")

        def _stub_trigger(*_args: object, **_kwargs: object) -> None:
            (sd / "last-run.json").write_text(
                f'{{"ended_at": "{crony_runtime.now_iso()}",'
                ' "exit_code": 0, "exit_class": "ok"}',
                encoding="utf-8",
            )

        monkeypatch.setattr(crony_runner, "trigger_unit", _stub_trigger)
        rec = crony_runner.trigger_unit_sync(
            full, state_dir=sd, job_timeout=0.0, trigger_timeout=5.0
        )
        assert rec["exit_class"] == "ok"
        assert rec["exit_code"] == 0


class TestRunJobInteractive:
    """End-to-end _run_job behavior for interactive jobs. The wait /
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
            crony_runner,
            "_interactive_wait_and_prompt",
            lambda _snap, _log_file: "run",
        )
        rc = crony_runner._run_job(h.snap(cfg, "iv"))
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
            crony_runner,
            "_interactive_wait_and_prompt",
            lambda _snap, _log_file: "cancel",
        )
        rc = crony_runner._run_job(h.snap(cfg, "iv"))
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
        # that: write the flag before _run_job, into the uuid-keyed
        # state dir.
        sd = h.state_dir("iv", cfg=cfg)
        crony_runtime.write_user_trigger_flag(sd)

        called: list[bool] = []

        def _no_wait(_snap: Any, _log_file: Any) -> str:
            called.append(True)
            return "run"

        monkeypatch.setattr(
            crony_runner, "_interactive_wait_and_prompt", _no_wait
        )
        rc = crony_runner._run_job(h.snap(cfg, "iv"))
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
        host = SimpleNamespace(
            hid_idle_seconds=lambda: 999.0, screen_locked=lambda: True
        )
        monkeypatch.setattr(crony_runtime, "host", lambda: host)
        sleeps = [0]

        def fake_sleep(_s: float) -> None:
            sleeps[0] += 1
            if sleeps[0] == 1:
                crony_runtime.write_user_trigger_flag(sd)

        monkeypatch.setattr(crony_commands.time, "sleep", fake_sleep)

        rc = crony_runner._run_job(h.snap(cfg, "iv"))
        assert rc == 0
        rec = h.last_run("iv")
        assert rec["exit_class"] == "ok"
        # The flag was consumed during the wait.
        assert not (sd / "user-trigger.flag").exists()


def _run_guard_in_child(cap: int, argv: list[str]) -> int:
    """Run `do_run_guard` in a forked child and return its exit code.

    Forking isolates the guard's signal-handler installation from the
    pytest process, which would otherwise inherit the SIGTERM/SIGINT/
    SIGHUP forwarders.
    """
    pid = os.fork()
    if pid == 0:
        try:
            crony_runner.do_run_guard(cap, argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
            os._exit(code)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


class TestDoRunGuard:
    """The hard-timeout backstop: propagate a normal exit, and kill the
    whole run group (not just the direct child) on overrun."""

    def test_propagates_success(self) -> None:
        assert _run_guard_in_child(10, ["/bin/sh", "-c", "exit 0"]) == 0

    def test_propagates_nonzero_exit(self) -> None:
        assert _run_guard_in_child(10, ["/bin/sh", "-c", "exit 7"]) == 7

    def test_overrun_is_killed_and_exits_timeout(self) -> None:
        start = time.monotonic()
        code = _run_guard_in_child(1, ["/bin/sh", "-c", "sleep 30"])
        elapsed = time.monotonic() - start
        assert code == int(ExitCode.TIMEOUT)
        # Killed near the cap, not after the full 30s sleep.
        assert elapsed < 20

    def test_overrun_kills_whole_process_group(self, tmp_path: Path) -> None:
        # The run's descendants must die too, not just the direct child:
        # the guard runs the child in its own session and kills the
        # group. A backgrounded grandchild records its pid; after the
        # overrun kill it must be gone.
        pidfile = tmp_path / "grandchild.pid"
        argv = [
            "/bin/sh",
            "-c",
            f"sleep 30 & echo $! > {pidfile}; wait",
        ]
        code = _run_guard_in_child(1, argv)
        assert code == int(ExitCode.TIMEOUT)
        gc_pid = int(pidfile.read_text().strip())
        for _ in range(50):
            try:
                os.kill(gc_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"grandchild {gc_pid} survived the group kill")

    def test_forwards_sigterm_to_the_run(self, tmp_path: Path) -> None:
        # The guard is the scheduler-tracked process; a stop signal it
        # receives must reach the run that escaped into its own session.
        pidfile = tmp_path / "grandchild.pid"
        ready = tmp_path / "ready"
        argv = [
            "/bin/sh",
            "-c",
            f"sleep 30 & echo $! > {pidfile}; touch {ready}; wait",
        ]
        pid = os.fork()
        if pid == 0:
            try:
                crony_runner.do_run_guard(300, argv)
            except SystemExit as exc:
                os._exit(exc.code if isinstance(exc.code, int) else 0)
            os._exit(0)
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.1)
        gc_pid = int(pidfile.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        os.waitpid(pid, 0)
        for _ in range(50):
            try:
                os.kill(gc_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"grandchild {gc_pid} survived SIGTERM forwarding")


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
