# This is AI generated code

"""Shared contract + mechanics tests for a utility's `automate`
subcommand (the crony(1)-backed apply / status / destroy lifecycle built
on ``common.crony_automate.CronyAutomation``).

Lives outside ``conftest.py`` on purpose: only the utilities that
schedule themselves through crony (borgadm, secure-archiver) subclass
this, so it stays out of the conftest every test file loads.
``conftest.py`` registers it for pytest assertion rewriting so failures
here read as richly as in a test module.
"""

import contextlib
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest


class CronyAutomateBase:
    """Shared contract + mechanics tests for a utility's `automate`
    subcommand.

    Every utility that schedules itself through crony drives the same
    generic lifecycle -- write the bundle, run `crony apply`, report
    currency + `crony status`, tear down with `crony destroy` -- and must
    satisfy the same two contract gates: the generated bundle validates
    against the real crony, and every crony argv it emits parses against
    crony's own CLI. Those tests live here once and run for each utility;
    the per-utility render content (job set, platform gating, selection
    markers) stays in the utility's own suite.

    The generic crony subprocess (the driver's own ``subprocess.run``) is
    mocked so crony is never really invoked -- each call surfaces as a
    ``subprocess.run([crony, *argv])`` on the yielded mock. A utility
    whose own ``run_cmd`` also shells out to crony (e.g. borgadm's
    ``do_logs``, which must capture crony's stdout) has that seam mocked
    too; ``_crony_calls`` aggregates both, filtering to crony by basename.
    The bundle-validate gate does NOT use those mocks -- it runs the real
    ``crony config validate`` against the rendered file.

    Subclasses set:
      MODULE: the utility module under test (its ``do_automate_*``
        handlers, ``_render_crony_bundle``, ``_CRONY`` driver instance,
        ``platform`` re-export, ``run_cmd``, and ``_warning_occurred``).
      BUNDLE: the crony bundle name (drop-in file ``<BUNDLE>.toml``).
      ERROR: the utility's base exception type (raised on crony failure).
      CRONY_PARSER: callable returning crony's CLI parser (imported in
        the utility's own test module so this file stays free of crony's
        heavy transitive deps).
      EXPECTED_VERBS: the crony verbs the utility emits across a full
        apply / status / destroy drive (plus any extra handler).

    Subclasses implement ``apply`` / ``status`` / ``destroy`` (call the
    utility's handler with its own defaults), and may override
    ``render_cases`` (bundle texts the validate gate checks) and
    ``drive_extra_crony_handlers`` (additional crony shell-outs the argv
    gate must cover).
    """

    MODULE: ClassVar[Any]
    BUNDLE: ClassVar[str]
    ERROR: ClassVar[type[Exception]]
    CRONY_PARSER: ClassVar[Any]
    EXPECTED_VERBS: ClassVar[set[str]]

    def apply(self, *, config_only: bool) -> None:
        raise NotImplementedError

    def status(self, *, config_only: bool) -> None:
        raise NotImplementedError

    def destroy(self, *, config_only: bool) -> None:
        raise NotImplementedError

    def render_cases(self, _monkeypatch: Any) -> list[tuple[str, str]]:
        """(label, bundle-text) cases the validate gate checks. Default
        is the single host-platform render; a utility with platform- or
        selection-dependent bundles overrides to cover each variant."""
        return [("default", type(self).MODULE._render_crony_bundle())]

    def drive_extra_crony_handlers(self) -> None:
        """Drive any additional handler that shells out to crony (beyond
        apply / status / destroy) so the argv gate covers it. Default
        none; the bundle is present when this runs (destroy comes last)."""
        return None

    @contextlib.contextmanager
    def _automate_ctx(
        self, system: str, tmp_path: Path, monkeypatch: Any
    ) -> Iterator[tuple[Path, Any]]:
        mod = type(self).MODULE
        dropin = tmp_path / "crony-config"
        monkeypatch.setenv("CRONY_CONFIG_DROPIN_DIR", str(dropin))
        monkeypatch.setattr(mod, "_warning_occurred", False)
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with (
            patch.object(mod.platform, "system", return_value=system),
            patch(
                "common.crony_automate.subprocess.run", autospec=True
            ) as mock_run,
            patch.object(mod, "run_cmd", autospec=True) as mock_run_cmd,
        ):
            mock_run.return_value = completed
            mock_run_cmd.return_value = completed
            yield dropin, mock_run

    @pytest.fixture
    def automate_env(
        self, tmp_path: Path, monkeypatch: Any
    ) -> Iterator[tuple[Path, Any]]:
        """Darwin automate environment (see _automate_ctx)."""
        with self._automate_ctx("Darwin", tmp_path, monkeypatch) as env:
            yield env

    @pytest.fixture
    def automate_env_linux(
        self, tmp_path: Path, monkeypatch: Any
    ) -> Iterator[tuple[Path, Any]]:
        """Linux automate environment (see _automate_ctx)."""
        with self._automate_ctx("Linux", tmp_path, monkeypatch) as env:
            yield env

    def _crony_calls(self, mock_run: Any) -> list[list[str]]:
        """The crony argv (after the crony path) of every crony
        invocation, aggregated across the driver's subprocess seam
        (``mock_run``) and the utility's own ``run_cmd``. Non-crony
        ``run_cmd`` calls are filtered out by basename."""
        mod = type(self).MODULE
        calls = list(mock_run.call_args_list) + list(mod.run_cmd.call_args_list)
        argvs: list[list[str]] = []
        for call in calls:
            argv = call.args[0]
            if argv and Path(argv[0]).name == "crony":
                argvs.append(list(argv[1:]))
        return argvs

    def test_apply_writes_bundle_and_runs_crony(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        dropin, mock_run = automate_env
        self.apply(config_only=False)
        bundle = dropin / f"{type(self).BUNDLE}.toml"
        assert bundle.exists()
        assert "[job." in bundle.read_text()
        assert ["apply", "-b", type(self).BUNDLE] in self._crony_calls(mock_run)

    def test_apply_config_only_skips_crony(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        # config-only must neither require nor invoke crony -- it works
        # even where crony is missing entirely.
        dropin, mock_run = automate_env
        with patch.object(
            type(self).MODULE._CRONY,
            "crony_path",
            autospec=True,
            side_effect=type(self).ERROR("crony not found"),
        ):
            self.apply(config_only=True)
        assert (dropin / f"{type(self).BUNDLE}.toml").exists()
        mock_run.assert_not_called()

    def test_apply_leaves_no_bundle_when_crony_missing(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        # crony resolves before the bundle is written, so a missing crony
        # fails cleanly instead of leaving an orphan bundle behind.
        dropin, mock_run = automate_env
        with (
            patch.object(
                type(self).MODULE._CRONY,
                "crony_path",
                autospec=True,
                side_effect=type(self).ERROR("crony not found"),
            ),
            pytest.raises(type(self).ERROR, match="crony not found"),
        ):
            self.apply(config_only=False)
        assert not (dropin / f"{type(self).BUNDLE}.toml").exists()
        mock_run.assert_not_called()

    def test_destroy_removes_units_and_bundle(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        dropin, mock_run = automate_env
        bundle = dropin / f"{type(self).BUNDLE}.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        self.destroy(config_only=False)
        assert [
            "destroy",
            "-b",
            type(self).BUNDLE,
            "--all",
        ] in self._crony_calls(mock_run)
        assert not bundle.exists()

    def test_destroy_is_noop_when_not_applied(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        # No bundle file: crony has nothing addressable and exits nonzero,
        # but destroy must treat that as a clean no-op, not an error.
        dropin, mock_run = automate_env
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="unknown bundle"
        )
        self.destroy(config_only=False)
        assert not (dropin / f"{type(self).BUNDLE}.toml").exists()

    def test_destroy_surfaces_failure_when_bundle_present(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        # The bundle is installed (file present) but destroy fails for a
        # real reason (a running job holds the lock); that must surface
        # rather than silently leaving the file in place.
        dropin, mock_run = automate_env
        bundle = dropin / f"{type(self).BUNDLE}.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=7, stdout="", stderr="lock held"
        )
        with pytest.raises(type(self).ERROR, match="crony destroy failed"):
            self.destroy(config_only=False)
        assert bundle.exists()

    def test_destroy_config_only_unlinks_bundle_only(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        dropin, mock_run = automate_env
        bundle = dropin / f"{type(self).BUNDLE}.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        with patch.object(
            type(self).MODULE._CRONY,
            "crony_path",
            autospec=True,
            side_effect=type(self).ERROR("crony not found"),
        ):
            self.destroy(config_only=True)
        assert not bundle.exists()
        mock_run.assert_not_called()

    def test_status_up_to_date(self, automate_env: tuple[Path, Any]) -> None:
        dropin, mock_run = automate_env
        mod = type(self).MODULE
        bundle = dropin / f"{type(self).BUNDLE}.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(mod._render_crony_bundle())
        self.status(config_only=False)
        assert not mod._warning_occurred
        assert ["status", "-b", type(self).BUNDLE] in self._crony_calls(
            mock_run
        )

    def test_status_out_of_date_sets_warning(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        dropin, mock_run = automate_env
        mod = type(self).MODULE
        bundle = dropin / f"{type(self).BUNDLE}.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("# stub\n")
        self.status(config_only=False)
        assert mod._warning_occurred
        # The drift report does not preempt crony's deployed-state table.
        assert ["status", "-b", type(self).BUNDLE] in self._crony_calls(
            mock_run
        )

    def test_status_not_written_sets_warning(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        _dropin, _mock_run = automate_env
        mod = type(self).MODULE
        self.status(config_only=False)
        assert mod._warning_occurred

    def test_status_config_only_skips_crony(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        dropin, mock_run = automate_env
        mod = type(self).MODULE
        bundle = dropin / f"{type(self).BUNDLE}.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(mod._render_crony_bundle())
        with patch.object(
            mod._CRONY,
            "crony_path",
            autospec=True,
            side_effect=type(self).ERROR("crony not found"),
        ):
            self.status(config_only=True)
        assert not mod._warning_occurred
        mock_run.assert_not_called()

    def test_generated_bundle_validates_against_crony(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Every bundle the utility generates must be accepted by the real
        # crony validator, so a future crony schema change that breaks it
        # fails here rather than at install time.
        crony = type(self).MODULE._CRONY.crony_path()
        f = tmp_path / f"{type(self).BUNDLE}.toml"
        for label, text in self.render_cases(monkeypatch):
            f.write_text(text)
            proc = subprocess.run(
                [crony, "config", "validate", "--file", str(f)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            assert proc.returncode == 0, f"{label}: {proc.stdout}{proc.stderr}"

    def test_emitted_crony_argv_parses_against_crony(
        self, automate_env: tuple[Path, Any]
    ) -> None:
        # Contract test: every crony invocation the utility emits must be
        # accepted by crony's own parser (including validate callbacks,
        # e.g. destroy's required target). The utility shells out to
        # crony, so a change to crony's CLI contract otherwise breaks
        # automate silently at runtime with no failing test.
        dropin, mock_run = automate_env
        mod = type(self).MODULE
        bundle = dropin / f"{type(self).BUNDLE}.toml"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(mod._render_crony_bundle())
        # do_logs (borgadm's extra handler) needs the bundle present, so
        # destroy (which unlinks it) runs last.
        self.apply(config_only=False)
        self.status(config_only=False)
        self.drive_extra_crony_handlers()
        self.destroy(config_only=False)
        argvs = self._crony_calls(mock_run)
        assert {argv[0] for argv in argvs} == type(self).EXPECTED_VERBS
        for argv in argvs:
            # parse_command runs the verb's validate callback; a broken
            # contract exits 2 (SystemExit), failing the test.
            parsed = type(self).CRONY_PARSER().parse_command(argv)
            assert parsed.command == argv[0]
