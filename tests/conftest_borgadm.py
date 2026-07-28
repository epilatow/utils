"""Shared fixtures and helpers for borgadm tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent

_REAL_UV_CACHE_DIR = os.environ.get(
    "UV_CACHE_DIR", os.path.expanduser("~/.cache/uv")
)

sys.path.insert(0, str(REPO_ROOT / "src"))

import borgadm_cli as ba  # noqa: E402


@pytest.fixture(name="_isolate_home", autouse=True)
def _isolate_home(tmp_path: Path) -> Iterator[Path]:
    """Redirect HOME and tempdir to empty temp directories.

    Prevents tests from accidentally accessing real user files
    (e.g., ~/.borgadm, ~/.borg_passphrase, ~/.ssh/id_borg.net)
    or writing to the real system temp directory.
    Any test that needs these files must create them explicitly.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    old_home = os.environ.get("HOME")
    old_tempdir = tempfile.tempdir
    basename: str = ba.BASENAME
    old_config = ba.CONFIG
    old_logfile = ba.LOGFILE

    os.environ["HOME"] = str(fake_home)
    tempfile.tempdir = str(tmp_path)
    # ba is a dynamically loaded module typed as ModuleType; a direct
    # attribute write fails mypy --strict (attr-defined), so setattr
    # stays.
    setattr(ba, "CONFIG", Path(fake_home / f".{basename}"))  # noqa: B010
    setattr(  # noqa: B010
        ba,
        "LOGFILE",
        Path(tempfile.gettempdir()) / f"{basename}.log",
    )

    try:
        yield fake_home
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            del os.environ["HOME"]
        tempfile.tempdir = old_tempdir
        setattr(ba, "CONFIG", old_config)  # noqa: B010
        setattr(ba, "LOGFILE", old_logfile)  # noqa: B010


_CONFIG_CONSUMED_KEYS = (
    "seconds",
    "keep_hourly",
    "keep_daily",
    "keep_weekly",
    "keep_monthly",
    "keep_yearly",
)


def mock_config_constructor(cfg: Any) -> Any:
    """Return a Config side_effect that pops args like the real one."""

    def constructor(
        _config_path: str,
        args: dict[str, Any],
        require_backup_sets: bool = False,  # noqa: ARG001 - keyword contract
    ) -> Any:
        # require_backup_sets mirrors the real Config signature so the
        # `config validate` path's keyword call binds; the mock ignores it.
        for key in _CONFIG_CONSUMED_KEYS:
            args.pop(key, None)
        return cfg

    return constructor


@pytest.fixture(name="mock_cfg")
def mock_cfg() -> Any:
    """Create a mock config for testing."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as config_file:
        config_file.write("""
        BORG_REPO = foobar
        BACKUP_SETS = { "set1": {"paths": ["foo"]} }
        """)
        config_file.flush()
        config_name = config_file.name

    cfg = ba.Config(config_name, {"command": "test"})
    original_cfg = ba.CFG
    # ba is a dynamically loaded module typed as ModuleType; a direct
    # attribute write fails mypy --strict (attr-defined), so setattr
    # stays.
    setattr(ba, "CFG", cfg)  # noqa: B010
    yield cfg
    setattr(ba, "CFG", original_cfg)  # noqa: B010


# -----------------------------------------------------------------------------
# E2E fixture: real borg repo + real subprocess invocations of borgadm.
# -----------------------------------------------------------------------------

# Backup-set layout used by borg_e2e. Two sets, each rooted at a single
# source directory under BACKUP_ROOT. Trailing slash marks the path as a
# directory (vs. a file) for borgadm's dir-vs-file classification in
# backup_set_paths().
#
# Iteration order is load-bearing: borgadm's list_backups() emits archives
# in cfg.BACKUP_SETS order within a timestamp, and several E2E tests
# assert on that exact ordering. Keep insertion order stable when editing.
_E2E_SETS: dict[str, list[str]] = {
    "set-a": ["set-a/"],
    "set-b": ["set-b/"],
}


@dataclass
class BorgE2EFixture:
    """Context object yielded by the borg_e2e fixture."""

    repo_path: Path
    backup_root: Path
    home: Path
    config_path: Path
    sets: dict[str, list[Path]] = field(default_factory=dict)

    @property
    def borgadm_bin(self) -> Path:
        return REPO_ROOT / "bin" / "borgadm"

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        borg_state = self.home / ".borg-test-state"
        env["BORG_BASE_DIR"] = str(borg_state / "base")
        env["BORG_CACHE_DIR"] = str(borg_state / "cache")
        env["BORG_CONFIG_DIR"] = str(borg_state / "config")
        env["BORG_KEYS_DIR"] = str(borg_state / "keys")
        env["BORG_SECURITY_DIR"] = str(borg_state / "security")
        # Borgadm is a `uv run --script` entrypoint, so each subprocess
        # call resolves its script venv via uv's cache. Without this, uv
        # falls back to $HOME/.cache/uv -- our fake-HOME -- which is
        # empty for every test and forces a fresh build per invocation.
        # Inherit the real user's uv cache (captured at module import
        # before the autouse `_isolate_home` fixture rewrote $HOME) so
        # subprocesses hit the warm parent cache. uv's cache is
        # content-addressed and lock-coordinated, so cross-process
        # sharing is safe.
        env.setdefault("UV_CACHE_DIR", _REAL_UV_CACHE_DIR)
        # Unencrypted repos prompt interactively on first access. The
        # opt-in env var silences the prompt for our local test repo.
        env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
        env.setdefault("BORG_PASSPHRASE", "")
        return env

    def run(
        self,
        *args: str,
        check: bool = True,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        """Invoke borgadm as a subprocess with HOME pointing at the fake
        home so the subprocess picks up our test config."""
        return subprocess.run(
            [str(self.borgadm_bin), *args],
            env=self._subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )

    def borg(
        self,
        *args: str,
        check: bool = True,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        """Invoke borg directly. Used by tests to set up state without
        going through borgadm, or to assert on raw repo state."""
        return subprocess.run(
            ["borg", *args],
            env=self._subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )

    def archives(self) -> list[str]:
        """Return the list of archive short-names currently in the repo."""
        result = self.borg("list", "--short", str(self.repo_path))
        return [line for line in result.stdout.splitlines() if line]

    def make_archive(self, name: str, content_path: Path | None = None) -> None:
        """Create a borg archive at `name`. Defaults to archiving the
        whole backup_root, since archive *contents* don't matter for
        name-based classification tests -- callers care about the
        archive name being present in the repo, not what's inside."""
        path = content_path if content_path is not None else self.backup_root
        self.borg("create", f"{self.repo_path}::{name}", str(path))


def _archive_name(
    set_name: str,
    ts: str,
    n: int,
    m: int,
    backup_name: str = "test",
) -> str:
    """Build an archive name string for the test fixture's BACKUP_NAME.

    Delegates to the real ba._ArchiveName so the tests exercise the
    production renderer rather than a parallel copy of the format.
    """
    return str(
        ba._ArchiveName(
            backup_name=backup_name,
            set_name=set_name,
            timestamp=ts,
            n=n,
            m=m,
        )
    )


def _have_borg() -> bool:
    return shutil.which("borg") is not None


def _require_borg_or_fail() -> None:
    if not _have_borg():
        pytest.fail(
            "borg must be installed to run the E2E suite (--e2e was "
            "requested but `borg` is not on PATH)."
        )


def _initialize_borg_repo_template(repo: Path, state: Path) -> None:
    """Initialize a repository without using the invoking user's Borg state."""
    home = state / "home"
    home.mkdir(parents=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "BORG_BASE_DIR": str(state / "base"),
        "BORG_CACHE_DIR": str(state / "cache"),
        "BORG_CONFIG_DIR": str(state / "config"),
        "BORG_KEYS_DIR": str(state / "keys"),
        "BORG_SECURITY_DIR": str(state / "security"),
    }
    subprocess.run(
        ["borg", "init", "--encryption=none", str(repo)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture(name="_borg_repo_template", scope="session")
def _borg_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create one clean repository for this worker's E2E copies.

    Each test receives a real copy rather than hardlinks, so repository
    mutations remain isolated. Copies intentionally share the template's
    repository ID; the function-scoped fake HOME keeps their Borg cache and
    security state separate.
    """
    _require_borg_or_fail()
    root = tmp_path_factory.mktemp("borg-template")
    template = root / "repo"
    _initialize_borg_repo_template(
        template,
        root / "state",
    )
    return template


@pytest.fixture(name="borg_e2e")
def borg_e2e(
    _isolate_home: Path,
    _borg_repo_template: Path,
    tmp_path: Path,
) -> Iterator[BorgE2EFixture]:
    """Spin up a real local borg repo with a minimal borgadm config so
    tests can drive `borgadm` via subprocess against actual archives.

    Layout:
      <_isolate_home>/             # HOME for the subprocess
        .borgadm                   # config pointing at the local repo
        .borg_passphrase           # dummy, repo uses --encryption=none
      <tmp_path>/repo/             # the borg repo (encryption=none)
      <tmp_path>/src/<set>/...     # source dirs, one per backup set
    """
    _require_borg_or_fail()
    home = _isolate_home
    repo_path = tmp_path / "repo"
    backup_root = tmp_path / "src"
    backup_root.mkdir()

    sets: dict[str, list[Path]] = {}
    for set_name, paths in _E2E_SETS.items():
        absolute_paths: list[Path] = []
        for rel in paths:
            full = backup_root / rel.rstrip("/")
            full.mkdir(parents=True)
            (full / f"{set_name}-file.txt").write_text(f"{set_name} content\n")
            absolute_paths.append(full)
        sets[set_name] = absolute_paths

    passphrase_file = home / ".borg_passphrase"
    passphrase_file.write_text("e2e-test-passphrase\n")
    passphrase_file.chmod(0o600)

    shutil.copytree(_borg_repo_template, repo_path)

    backup_sets_cfg = {name: {"paths": _E2E_SETS[name]} for name in _E2E_SETS}
    config_path = home / ".borgadm"
    config_path.write_text(
        f"BORG_REPO = {repo_path}\n"
        f"BACKUP_NAME = test\n"
        f"BACKUP_ROOT = {backup_root}\n"
        f"BORG_PASSPHRASE_FILE = {passphrase_file}\n"
        f"BACKUP_SETS = {json.dumps(backup_sets_cfg)}\n"
    )

    yield BorgE2EFixture(
        repo_path=repo_path,
        backup_root=backup_root,
        home=home,
        config_path=config_path,
        sets=sets,
    )
