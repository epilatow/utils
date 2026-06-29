# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "tomlkit", "pydantic>=2"]
# ///
# This is AI generated code

"""Shared helpers and fixtures for the crony test suite.

Imported explicitly (``from conftest_crony import ...``) by the
per-module crony test files. Named so pytest does not auto-load it as a
plugin; it carries no test classes and is never run as a test.
"""

import json
import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import tomlkit

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from crony import commands as crony_commands  # noqa: E402
from crony import paths as crony_paths  # noqa: E402
from crony import platform as crony_platform  # noqa: E402
from crony import runner as crony_runner  # noqa: E402
from crony import runtime as crony_runtime  # noqa: E402
from crony.config import (  # noqa: E402
    DEFAULT_BUNDLE_NAME,
    TomlBundle,
    TomlBundleConfig,
    TomlConfig,
)
from crony.model import (  # noqa: E402
    _resolve_snapshot_for,
)
from crony.platform import (  # noqa: E402
    launchd,
    systemd,
)
from crony.snapshot import CURRENT_SNAPSHOT_SCHEMA  # noqa: E402

# Scratch namespace published to the crony test files. The runner
# harness's `trigger_unit_sync` stub records each dispatched call on
# `crony._ledger` (installed via monkeypatch), and test_crony_runner
# reads it back to assert on what was dispatched.
crony = SimpleNamespace()


def _apply(
    short: str, *, bundle: str = DEFAULT_BUNDLE_NAME
) -> crony_runtime.ApplyResult:
    """Apply one entry through the production path: build the
    `Config` model (one disk pass) and call `apply_one` with the
    resolved ref -- mirroring what `do_apply` does per entry, so
    tests exercise the model-based code rather than a standalone
    path. For tests not built on `_ApplyHarness` (which exposes the
    same thing as `h.apply`).
    """
    config = crony_runtime.load_config()
    ref = config.pending.by_full_name.get(f"{bundle}.{short}")
    assert ref is not None, f"{bundle}.{short} not selected on this host"
    return crony_runtime.apply_one(config, ref)


def isolate_crony_home(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Redirect every host-touching crony default to a non-existent
    sentinel under tmp_path.

    crony resolves its config/state/launchd/systemd paths from $HOME
    at import time. A test that forgets to monkeypatch the in-process
    module attributes (CONFIG_FILE, STATE_DIR, etc.) -- or that spawns
    a subprocess that re-reads the CRONY_* env vars -- would land on
    the user's real config and state dirs. This fixture wires every
    attribute and matching env var to a per-test sentinel; the
    sentinel itself is never created, so a stray write fails loudly
    rather than silently mutating the user's dotfiles.

    Tests that need to actually write files override these on top
    via monkeypatch.setattr / setenv; pytest's monkeypatch stack
    layers cleanly atop this fixture.
    """
    sentinel = tmp_path / "_home_sentinel_unwritten"
    # The scheduler backends resolve their unit directory under
    # Path.home(), so patching it sandboxes those too -- no separate
    # redirect needed.
    monkeypatch.setattr(Path, "home", lambda: sentinel)
    # Every path constant is read through crony.paths, so redirect each
    # on that module, plus its matching CRONY_* env var.
    cfg = sentinel / ".config" / "crony"
    state = sentinel / ".local" / "state" / "crony"
    dirs = {
        "CONFIG_DIR": cfg,
        "CONFIG_FILE": cfg / "config.toml",
        "CONFIG_DROPIN_DIR": cfg / "config",
        "STATE_DIR": state,
    }
    for attr, path in dirs.items():
        monkeypatch.setattr(crony_paths, attr, path)
        monkeypatch.setenv(f"CRONY_{attr}", str(path))


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: Any) -> None:
    isolate_crony_home(tmp_path, monkeypatch)


def _job(**overrides: Any) -> dict[str, Any]:
    """Build a minimal job body with overrides."""
    base: dict[str, Any] = {"command": "true", "schedule": "daily"}
    base.update(overrides)
    return base


def _inject_uuids(raw: dict[str, Any]) -> dict[str, Any]:
    """Stamp a fresh UUID on every job/group in a test config that
    lacks one. Returns `raw` for chaining.

    UUIDs are required on every parsed entry, but most tests
    exercise unrelated parser behavior and would otherwise have
    to repeat boilerplate `"uuid": str(uuid.uuid4())` lines on
    each fixture. Tests that specifically exercise missing or
    duplicate UUID validation bypass this helper.
    """
    for section in ("job", "job-group"):
        entries = raw.get(section)
        if not isinstance(entries, dict):
            continue
        for body in entries.values():
            if isinstance(body, dict) and "uuid" not in body:
                body["uuid"] = str(uuid.uuid4())
    return raw


def _parse(raw: dict[str, Any]) -> Any:
    """Auto-stamp missing uuids and parse. The dominant test path."""
    return TomlBundleConfig.from_raw(_inject_uuids(raw))


def _uuid_toml(text: str) -> str:
    """Stamp missing uuids on every `[job.*]` / `[job-group.*]`
    table in a TOML string. Mirrors what `crony config update`
    does on a real bundle file; lets fixtures that write raw TOML
    to disk stay focused on the surface they exercise rather than
    repeating `uuid = "..."` lines.
    """
    doc = tomlkit.parse(text)
    crony_commands._insert_missing_uuids_in_section(doc, "job")
    crony_commands._insert_missing_uuids_in_section(doc, "job-group")
    return tomlkit.dumps(doc)


def _assert_errored_job(raw: dict[str, Any], short: str, match: str) -> None:
    """Assert from_raw records a per-entity error for job `short`.

    Per-entity parse failures land in `TomlBundleConfig.errored_jobs` instead
    of raising, so tests of bad-shape inputs check the recorded
    message rather than wrapping the call in `pytest.raises`.
    """
    cfg = _parse(raw)
    assert short in cfg.errored_jobs, (
        f"expected {short!r} in errored_jobs, got {list(cfg.errored_jobs)}"
    )
    assert re.search(match, cfg.errored_jobs[short]), (
        f"errored_jobs[{short!r}]={cfg.errored_jobs[short]!r} "
        f"did not match {match!r}"
    )
    assert short not in cfg.jobs


def _assert_errored_job_group(
    raw: dict[str, Any], short: str, match: str
) -> None:
    """As `_assert_errored_job` but for `[job-group.*]` entries."""
    cfg = _parse(raw)
    assert short in cfg.errored_job_groups, (
        f"expected {short!r} in errored_job_groups, got "
        f"{list(cfg.errored_job_groups)}"
    )
    assert re.search(match, cfg.errored_job_groups[short]), (
        f"errored_job_groups[{short!r}]="
        f"{cfg.errored_job_groups[short]!r} "
        f"did not match {match!r}"
    )
    assert short not in cfg.job_groups


def _assert_errored_platform_target(
    raw: dict[str, Any], platform: str, match: str
) -> None:
    """As `_assert_errored_job` but for `[target.<platform>]` entries."""
    cfg = _parse(raw)
    assert platform in cfg.errored_platform_targets, (
        f"expected {platform!r} in errored_platform_targets, got "
        f"{list(cfg.errored_platform_targets)}"
    )
    assert re.search(match, cfg.errored_platform_targets[platform]), (
        f"errored_platform_targets[{platform!r}]="
        f"{cfg.errored_platform_targets[platform]!r} "
        f"did not match {match!r}"
    )
    assert platform not in cfg.platform_targets


def _assert_errored_host_target(
    raw: dict[str, Any], host: str, match: str
) -> None:
    """As `_assert_errored_job` but for `[target.host.<name>]`."""
    cfg = _parse(raw)
    assert host in cfg.errored_host_targets, (
        f"expected {host!r} in errored_host_targets, got "
        f"{list(cfg.errored_host_targets)}"
    )
    assert re.search(match, cfg.errored_host_targets[host]), (
        f"errored_host_targets[{host!r}]="
        f"{cfg.errored_host_targets[host]!r} "
        f"did not match {match!r}"
    )
    assert host not in cfg.host_targets


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


def _bundle_set(*pairs: tuple[str, Any]) -> Any:
    """Wrap (name, TomlBundleConfig) pairs into a TomlConfig."""
    tc = TomlConfig()
    for name, cfg in pairs:
        tc.bundles.append(
            TomlBundle(name=name, source=Path(f"/test/{name}.toml"), config=cfg)
        )
    return tc


class _RunnerHarness:
    """Isolated state + config so runner tests don't touch the real
    ~/.local/state/crony. Sets the in-process module attributes
    (for direct calls into _run_job/_run_group) and the matching
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
        monkeypatch.setattr(crony_paths, "STATE_DIR", state)
        monkeypatch.setattr(crony_paths, "CONFIG_DIR", cfg_dir)
        monkeypatch.setattr(crony_paths, "CONFIG_FILE", cfg_file)
        monkeypatch.setattr(crony_paths, "CONFIG_DROPIN_DIR", cfg_dropin)
        monkeypatch.setattr(crony_platform, "current_host", lambda: "test-host")
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        self.state = state
        self.cfg_file = cfg_file
        self.cfg_dropin = cfg_dropin
        self._last_cfg: Any | None = None
        # Stable short -> uuid mapping across successive `config()`
        # calls so a test that builds a fresh config to simulate a
        # drift / edit keeps the same identity for an entry, not a
        # fresh one (which would look like a delete + add to apply).
        self._uuid_pins: dict[tuple[str, str], dict[str, str]] = {}

    def full(self, short: str) -> str:
        """The full namespaced name for a short job/group name in the
        default bundle, used for unit-label assertions and CLI
        argument construction.
        """
        return f"{DEFAULT_BUNDLE_NAME}.{short}"

    def ref(
        self,
        short: str,
        cfg: Any | None = None,
        *,
        bundle: str = DEFAULT_BUNDLE_NAME,
    ) -> str:
        """The `<bundle>:<uuid>` entity ref for a short name, drawn from
        the most-recent (or passed) parsed config. Mirrors the runner's
        `str(snap.entity_ref)` form used for the CRONY_RUNNING_REF env.
        """
        cfg = cfg or self._last_cfg
        assert cfg is not None, "no config built yet for ref lookup"
        entity_uuid: str = (
            cfg.jobs[short].uuid
            if short in cfg.jobs
            else cfg.job_groups[short].uuid
        )
        return f"{bundle}:{entity_uuid}"

    def fabricate_orphan(
        self,
        short: str,
        *,
        bundle: str = DEFAULT_BUNDLE_NAME,
        kind: str = "job",
    ) -> Path:
        """Plant a state dir whose snapshot records a name that no
        live config selects -- i.e. a remnant of a previously-applied
        entry that's since been removed. Used by orphan-detection
        tests that need to verify the apply / destroy paths see and
        clean up such remnants.
        """
        # Use a deterministic uuid so re-stamps in the same test
        # collide on the same dir rather than fanning out. The uuid
        # format here is arbitrary -- the directory name itself is
        # the storage key (no canonical-uuid4 validation), and
        # production lookups go through `Config.current.by_full_name`
        # built by `_build_current_graph`'s tree walk.
        orphan_uuid = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"orphan/{bundle}.{short}")
        )
        sd = self.state / bundle / orphan_uuid
        sd.mkdir(parents=True, exist_ok=True)
        # Fully populated snapshot matching what apply would have
        # written. load_config skips snapshots missing required
        # fields (TypeError from Job(**raw)); incomplete fixtures
        # would silently fall out of the current graph.
        snapshot: dict[str, Any] = {
            "schema": CURRENT_SNAPSHOT_SCHEMA,
            "kind": kind,
            "name": f"{bundle}.{short}",
            "bundle": bundle,
            "uuid": orphan_uuid,
        }
        if kind == "job":
            snapshot.update(
                {
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
            )
        else:
            snapshot.update(
                {
                    "children": [],
                    "timeout": 600,
                    "trigger_timeout_sec": 15,
                    "schedule": "daily",
                    "interval": None,
                }
            )
        (sd / "snapshot.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        return sd

    def last_run(self, short: str, cfg: Any | None = None) -> dict[str, Any]:
        """Read last-run.json for `<default>.<short>` and return its
        parsed dict.
        """
        return _cast_dict(
            (self.state_dir(short, cfg=cfg) / "last-run.json").read_text()
        )

    def state_dir(
        self,
        short: str,
        cfg: Any | None = None,
        *,
        bundle: str = DEFAULT_BUNDLE_NAME,
        ensure_snapshot: bool = True,
    ) -> Path:
        """uuid-keyed state directory for `<bundle>.<short>`.

        `cfg` is the parsed TomlBundleConfig for the bundle, used to look up the
        entry's uuid. Pass it explicitly when the harness's `_last_cfg`
        is stale (e.g. mid-test config rewrite that doesn't go through
        the harness). Most callers omit it and rely on the most-recent
        `h.config(...)` result.

        `ensure_snapshot=True` materializes a minimal snapshot.json so
        the runtime helpers that resolve a state dir by walking
        `snapshot.json` (logs, trigger, status) can find it. Useful
        for tests that fabricate a state dir without actually running
        apply.
        """
        cfg = cfg or self._last_cfg
        assert cfg is not None, "no config built yet for state_dir lookup"
        entity_uuid: str = (
            cfg.jobs[short].uuid
            if short in cfg.jobs
            else cfg.job_groups[short].uuid
        )
        sd = self.state / bundle / entity_uuid
        if ensure_snapshot:
            sd.mkdir(parents=True, exist_ok=True)
            snap_p = sd / "snapshot.json"
            if not snap_p.exists():
                kind = "job" if short in cfg.jobs else "group"
                # Fully populated snapshot so load_config can build
                # the entity's runtime state; a partial dict would
                # be skipped by Job(**raw) / JobGroup(**raw).
                payload: dict[str, Any] = {
                    "schema": CURRENT_SNAPSHOT_SCHEMA,
                    "kind": kind,
                    "name": f"{bundle}.{short}",
                    "bundle": bundle,
                    "uuid": entity_uuid,
                }
                if kind == "job":
                    payload.update(
                        {
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
                    )
                else:
                    payload.update(
                        {
                            "children": [],
                            "timeout": 600,
                            "trigger_timeout_sec": 15,
                            "schedule": "daily",
                            "interval": None,
                        }
                    )
                snap_p.write_text(json.dumps(payload), encoding="utf-8")
        return sd

    def snap(self, cfg: Any, short: str) -> Any:
        """Resolve a snapshot for a default-bundle entry. Convenience
        for runner tests that build a TomlBundleConfig and call _run_job /
        _run_group directly without going through full apply.
        """
        return _resolve_snapshot_for(cfg, short)

    def write_snap(
        self, cfg: Any, short: str, *, disabled: bool = False
    ) -> None:
        """Write a snapshot to disk so `load_snapshot` finds it.
        Used by group runner tests where children are loaded from
        their own snapshot files (not from the parent's config).
        With `disabled=True`, marks the snapshot operator-disabled so
        a parent group's dispatch skips it."""
        snap = self.snap(cfg, short)
        if disabled:
            snap = snap.with_unit_disabled(True)
        sd = self.state_dir(short, cfg=cfg)
        p = sd / "snapshot.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        import json as _json

        p.write_text(
            _json.dumps(snap.to_dict(), sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def config(
        self, raw: dict[str, Any], *, default_target_jobs: list[str]
    ) -> Any:
        """Build a TomlBundleConfig with a target (for the platform
        this harness simulates) selecting these jobs.

        Persists the raw config to the on-disk file so subprocess
        re-invocations of `crony _run <child>` (group dispatch) load
        the same config we hand to _run_group. The target keys on the
        simulated platform so the entries are actually selected when
        a later `load_config()` resolves the host's target.
        """
        plat = crony_platform.current_platform()
        full = dict(raw)
        full.setdefault("target", {})
        target_section = full["target"]
        if isinstance(target_section, dict):
            target_section.setdefault(plat, {})
            assert isinstance(target_section[plat], dict)
            target_section[plat].setdefault("jobs", default_target_jobs)
        # Re-use uuids previously assigned to the same short name
        # so a successive `h.config(...)` call simulates an edit
        # to the same entity rather than a delete + replace.
        for section in ("job", "job-group"):
            entries = full.get(section)
            if not isinstance(entries, dict):
                continue
            for short, body in entries.items():
                if not isinstance(body, dict) or "uuid" in body:
                    continue
                pinned = self._uuid_pins.get((section, short))
                if pinned is not None:
                    body["uuid"] = pinned
        _inject_uuids(full)
        # Record the (section, short) -> uuid pins for next time.
        for section in ("job", "job-group"):
            entries = full.get(section)
            if not isinstance(entries, dict):
                continue
            for short, body in entries.items():
                if isinstance(body, dict) and isinstance(body.get("uuid"), str):
                    self._uuid_pins[(section, short)] = body["uuid"]
        self.cfg_file.write_text(tomlkit.dumps(full), encoding="utf-8")
        cfg = TomlBundleConfig.from_raw(full)
        self._last_cfg = cfg
        return cfg


def _last_run(state: Path, name: str) -> dict[str, Any]:
    """Read last-run.json by job name.

    A bare short name resolves against the default bundle so call
    sites stay terse. A full namespaced name (containing a dot)
    looks up that exact path.
    """
    if "." not in name:
        name = f"{DEFAULT_BUNDLE_NAME}.{name}"
    # State dirs are uuid-keyed under the bundle subdir; resolve
    # the path via the snapshot file rather than reconstructing
    # from the full name (which is no longer the on-disk key).
    bundle, _, _ = name.partition(".")
    bundle_dir = state / bundle
    last_run_path: Path | None = None
    if bundle_dir.is_dir():
        for uuid_dir in bundle_dir.iterdir():
            snap = uuid_dir / "snapshot.json"
            if not snap.is_file():
                continue
            try:
                raw = json.loads(snap.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError:
                continue
            if raw.get("name") == name:
                last_run_path = uuid_dir / "last-run.json"
                break
    assert last_run_path is not None, (
        f"no state dir for {name!r} under {bundle_dir}"
    )
    text = last_run_path.read_text()
    return _cast_dict(text)


def _cast_dict(text: str) -> dict[str, Any]:
    """Read JSON into a typed dict for test assertions."""
    import json as _json

    out = _json.loads(text)
    assert isinstance(out, dict)
    return out


def _stub_trigger_sync(
    monkeypatch: Any, results: dict[str, dict[str, Any]]
) -> None:
    """Replace `trigger_unit_sync` with a deterministic stub.

    `results` maps full child names -> the dict each call should
    return (mimicking last-run.json). The stub records each call's
    args (job_timeout, trigger_timeout) on a ledger we can assert
    against.
    """
    ledger: list[dict[str, Any]] = []

    def _stub(
        full_name: str,
        *,
        state_dir: Path,
        job_timeout: float,
        trigger_timeout: float,
    ) -> dict[str, Any]:
        ledger.append(
            {
                "full_name": full_name,
                "state_dir": state_dir,
                "job_timeout": job_timeout,
                "trigger_timeout": trigger_timeout,
            }
        )
        return results.get(full_name, {"exit_code": 0, "exit_class": "ok"})

    monkeypatch.setattr(crony_runner, "trigger_unit_sync", _stub)
    monkeypatch.setattr(crony, "_ledger", ledger, raising=False)


class _ApplyHarness(_RunnerHarness):
    """RunnerHarness extension that also redirects platform unit dirs
    and stubs subprocess so launchctl/systemctl never run for real.
    """

    def __init__(
        self, tmp_path: Path, monkeypatch: Any, *, platform: str = "darwin"
    ) -> None:
        super().__init__(tmp_path, monkeypatch)
        # The scheduler backends resolve their unit dir under
        # Path.home(); point Path.home() at a writable tmp home
        # (overriding the autouse non-writable sentinel) and pre-create
        # the per-backend unit dirs so apply / destroy can write there.
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)
        agents = home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        sysd = home / ".config" / "systemd" / "user"
        sysd.mkdir(parents=True)
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: platform
        )
        # Capture subprocess.run calls so apply/destroy don't actually
        # invoke launchctl or systemctl.
        self.calls: list[list[str]] = []

        def fake_run(*args: Any, **kwargs: Any) -> Any:
            argv: list[str] = list(args[0] if args else kwargs.get("args", []))
            self.calls.append(argv)
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(crony_commands.subprocess, "run", fake_run)
        # The default empty-subprocess path resolves `unit_state` to
        # "none". Stub the underlying primitives so a freshly-applied
        # unit reads back as `enabled` (loaded). Tests that assert a
        # specific scheduler state override these at the same level (e.g.
        # `systemd._is_enabled` -> "").
        monkeypatch.setattr(systemd, "_is_enabled", lambda _u: "enabled")
        monkeypatch.setattr(launchd, "_is_loaded", lambda _label: True)
        # activate's reload waits for the booted-out label to clear by
        # polling `_is_loaded` -- which is stubbed True above, so the
        # bounded wait would otherwise spin to its timeout every apply.
        # The settle's real behavior is covered in the launchd backend
        # tests; here it is a no-op so apply/destroy stay fast.
        monkeypatch.setattr(
            launchd.LaunchdScheduler, "_await_unloaded", lambda _self, _n: None
        )
        # load_config issues one bulk scheduler query for last-launch
        # outcomes; stub its primitives so it stays off the captured
        # subprocess path (tests asserting a no-start override these).
        monkeypatch.setattr(launchd, "_launchctl_list", lambda: "")
        monkeypatch.setattr(systemd, "_show_services", lambda _u: [])
        self.platform = platform
        self.agents = agents
        self.sysd = sysd

    def apply(
        self, short: str, *, bundle: str = DEFAULT_BUNDLE_NAME
    ) -> crony_runtime.ApplyResult:
        """Apply one entry through the production path (see the
        module-level `_apply`)."""
        return _apply(short, bundle=bundle)


def _idle_lock_host(
    *,
    idle: Callable[[], float],
    locked: Callable[[], bool],
) -> SimpleNamespace:
    """A stand-in HostPlatform exposing just the idle / lock probes the
    interactive wait reads."""
    return SimpleNamespace(hid_idle_seconds=idle, screen_locked=locked)
