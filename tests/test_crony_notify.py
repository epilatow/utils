#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest", "pytest-cov", "tomlkit", "pydantic>=2"]
# ///
# This is AI generated code

"""Unit tests for crony.notify."""

import logging
import sys
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_crony import (  # noqa: E402
    _email_block,
    _isolate_home,  # noqa: E402, F401
    _job,
    _parse,
    _RunnerHarness,
)

from crony import commands as crony_commands  # noqa: E402
from crony import notify as crony_notify  # noqa: E402
from crony import platform as crony_platform  # noqa: E402
from crony import runtime as crony_runtime  # noqa: E402
from crony.config import (  # noqa: E402
    Defaults,
    NotifyChannel,
    _validate_notify_channels,
)
from crony.errors import (  # noqa: E402
    ConfigError,
    CronyError,
    PreconditionError,
    UsageError,
)
from crony.model import (  # noqa: E402
    ExitClass,
    GateResult,
    JobRunResult,
    NotificationResult,
)

_script_path = REPO_ROOT / "src" / "crony" / "notify.py"


class TestSecretRetrieval:
    """retrieve_secret reads from the host keychain or a 0600 file."""

    def test_returns_none_when_no_source(self) -> None:
        assert (
            crony_notify.retrieve_secret(keychain_service=None, file_path=None)
            is None
        )

    def test_reads_from_file(self, tmp_path: Path) -> None:
        f = tmp_path / "secret"
        f.write_text("supersecret\n")
        f.chmod(0o600)
        assert (
            crony_notify.retrieve_secret(
                keychain_service=None, file_path=str(f)
            )
            == "supersecret"
        )

    def test_rejects_loose_mode(self, tmp_path: Path) -> None:
        f = tmp_path / "secret"
        f.write_text("supersecret")
        f.chmod(0o644)  # group/world readable
        with pytest.raises(PreconditionError, match="0600"):
            crony_notify.retrieve_secret(
                keychain_service=None, file_path=str(f)
            )

    def test_rejects_loose_parent_dir(self, tmp_path: Path) -> None:
        # File mode is fine but the directory is group/world
        # accessible; reject so file names / mtimes don't leak.
        d = tmp_path / "secrets"
        d.mkdir(mode=0o755)
        f = d / "smtp-pw"
        f.write_text("hunter2")
        f.chmod(0o600)
        with pytest.raises(PreconditionError, match="secret directory"):
            crony_notify.retrieve_secret(
                keychain_service=None, file_path=str(f)
            )

    def test_keychain_hit_returns_secret(self, monkeypatch: Any) -> None:
        # retrieve_secret returns the host keychain value, passing the
        # (service, account) pair through verbatim and not consulting
        # file_path. (The per-host keychain command is covered by the
        # backend tests in test_crony_platform_host_darwin.py.)
        seen: dict[str, Any] = {}

        class _FakeHost:
            def keychain_secret(
                self, service: str, account: str | None
            ) -> str | None:
                seen["args"] = (service, account)
                return "thesecret"

        monkeypatch.setattr(crony_runtime, "host", lambda: _FakeHost())
        secret = crony_notify.retrieve_secret(
            keychain_service="svc",
            keychain_account="acct",
            file_path=None,
        )
        assert secret == "thesecret"
        assert seen["args"] == ("svc", "acct")

    def test_keychain_falls_back_to_file_on_miss(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # When the host keychain yields nothing (no item, or a host with
        # no keychain), retrieve_secret falls back to file_path.
        f = tmp_path / "secret"
        f.write_text("from-file")
        f.chmod(0o600)

        class _NoKeychainHost:
            def keychain_secret(
                self, _service: str, _account: str | None
            ) -> str | None:
                return None

        monkeypatch.setattr(crony_runtime, "host", lambda: _NoKeychainHost())
        assert (
            crony_notify.retrieve_secret(
                keychain_service="missing-item", file_path=str(f)
            )
            == "from-file"
        )


class TestEmailNotify:
    """Email channel routing via smtplib (mocked)."""

    def _common_config(self, tmp_path: Path) -> Any:
        secret = tmp_path / "smtp-pw"
        secret.write_text("hunter2")
        secret.chmod(0o600)
        return _parse(
            {
                "defaults": {
                    "notify_channels": ["email"],
                    "notify_attach_log": True,
                    "notify_attach_max_kb": 1,
                    "notify": {
                        "email": {
                            "to": "you@example.com",
                            "from": "crony@example.com",
                            "smtp_host": "smtp.example.com",
                            "smtp_port": 587,
                            "smtp_user": "u@example.com",
                            "smtp_starttls": True,
                            "smtp_pass_file": str(secret),
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["email"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )

    def _make_failed_result(self, channels: list[str]) -> Any:
        return JobRunResult(
            host="h",
            platform="darwin",
            started_at="2026-05-02T10:00:00-07:00",
            ended_at="2026-05-02T10:00:01-07:00",
            duration_sec=1.0,
            exit_class=ExitClass.FAIL,
            exit_code=2,
            signal=None,
            process_exit=2,
            gate=GateResult.NONE,
            log_path="/tmp/run.log",
            notifications={
                ch: NotificationResult(sent=False) for ch in channels
            },
        )

    def test_sends_via_smtp(self, tmp_path: Path, monkeypatch: Any) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["email"])

        # autospec exercises the real SMTP signature; the resulting
        # mock instance plays the context-manager role with the same
        # return-value contract.
        smtp_cls = create_autospec(crony_notify.smtplib.SMTP)
        smtp_inst = smtp_cls.return_value
        smtp_inst.__enter__.return_value = smtp_inst
        smtp_inst.__exit__.return_value = None
        monkeypatch.setattr(crony_notify.smtplib, "SMTP", smtp_cls)

        crony_notify.dispatch_notify(
            result, "default.j", "log content here", cfg.defaults
        )

        assert result.notifications["email"].sent is True
        assert result.notifications["email"].error is None
        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=15)
        smtp_inst.starttls.assert_called_once()
        smtp_inst.login.assert_called_once_with("u@example.com", "hunter2")
        assert smtp_inst.send_message.call_count == 1
        sent = smtp_inst.send_message.call_args[0][0]
        assert sent["To"] == "you@example.com"
        assert sent["From"] == "crony@example.com"
        body = sent.get_content()
        assert "Job:        default.j" in body
        assert "fail" in body
        assert "--- log (latest run) ---" in body
        assert "log content here" in body

    def test_email_body_is_latest_run_entry_only(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Multi-run log: email and ntfy both include only the most
        # recent run's entry. Earlier history would be noise the
        # recipient already saw in prior notifications.
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["email"])

        smtp_cls = create_autospec(crony_notify.smtplib.SMTP)
        smtp_inst = smtp_cls.return_value
        smtp_inst.__enter__.return_value = smtp_inst
        smtp_inst.__exit__.return_value = None
        monkeypatch.setattr(crony_notify.smtplib, "SMTP", smtp_cls)

        log_text = (
            "=== 2026-05-01T03:00:00-08:00 j pid=1 ===\n"
            "older-run-detail\n"
            "=== 2026-05-02T03:00:00-08:00 j pid=2 ===\n"
            "newest-run-detail\n"
        )
        crony_notify.dispatch_notify(
            result, "default.j", log_text, cfg.defaults
        )
        sent = smtp_inst.send_message.call_args[0][0]
        body = sent.get_content()
        assert "newest-run-detail" in body
        assert "older-run-detail" not in body

    def test_records_smtp_failure_no_propagate(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["email"])

        smtp_cls = create_autospec(
            crony_notify.smtplib.SMTP, side_effect=ConnectionRefusedError("no")
        )
        monkeypatch.setattr(crony_notify.smtplib, "SMTP", smtp_cls)

        crony_notify.dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["email"].sent is False
        assert "ConnectionRefusedError" in (
            result.notifications["email"].error or ""
        )

    def test_missing_smtp_password_records_error(self) -> None:
        # Build a config that omits smtp_pass_*.
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["email"],
                    "notify": {
                        "email": {
                            "to": "y@e.com",
                            "smtp_host": "x",
                            "smtp_user": "u",
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["email"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )
        result = self._make_failed_result(["email"])
        crony_notify.dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["email"].sent is False
        assert "no SMTP password" in (result.notifications["email"].error or "")

    def test_user_headers_attached(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A `headers = { Reply-To = ... }` block on an email channel
        # should land as headers on the rendered EmailMessage.
        secret = tmp_path / "smtp-pw"
        secret.write_text("hunter2")
        secret.chmod(0o600)
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["email"],
                    "notify": {
                        "email": {
                            "to": "you@example.com",
                            "smtp_host": "smtp.example.com",
                            "smtp_user": "u@example.com",
                            "smtp_pass_file": str(secret),
                            "headers": {
                                "Reply-To": "support@example.com",
                                "X-Crony-Source": "automation",
                            },
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["email"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )
        result = self._make_failed_result(["email"])
        smtp_cls = create_autospec(crony_notify.smtplib.SMTP)
        smtp_inst = smtp_cls.return_value
        smtp_inst.__enter__.return_value = smtp_inst
        smtp_inst.__exit__.return_value = None
        monkeypatch.setattr(crony_notify.smtplib, "SMTP", smtp_cls)

        crony_notify.dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["email"].sent is True
        sent = smtp_inst.send_message.call_args[0][0]
        assert sent["Reply-To"] == "support@example.com"
        assert sent["X-Crony-Source"] == "automation"
        # crony-controlled headers still in place.
        assert sent["To"] == "you@example.com"


class TestNtfyNotify:
    """ntfy channel routing via urllib (mocked)."""

    def _common_config(self, tmp_path: Path) -> Any:
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        return _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify_attach_log": True,
                    "notify_attach_max_kb": 1,
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["ntfy"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )

    def _make_failed_result(self, channels: list[str]) -> Any:
        return JobRunResult(
            host="h",
            platform="darwin",
            started_at="2026-05-02T10:00:00-07:00",
            ended_at="2026-05-02T10:00:01-07:00",
            duration_sec=1.0,
            exit_class=ExitClass.FAIL,
            exit_code=2,
            signal=None,
            process_exit=2,
            gate=GateResult.NONE,
            log_path="/tmp/run.log",
            notifications={
                ch: NotificationResult(sent=False) for ch in channels
            },
        )

    def test_sends_via_urllib(self, tmp_path: Path, monkeypatch: Any) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["ntfy"])

        captured: dict[str, Any] = {}

        class _Resp:
            status = 200

            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake_urlopen(req: Any, **_kwargs: object) -> Any:
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["data"] = req.data
            captured["method"] = req.get_method()
            return _Resp()

        monkeypatch.setattr(
            crony_notify.urllib.request, "urlopen", _fake_urlopen
        )
        crony_notify.dispatch_notify(
            result, "default.j", "log content here", cfg.defaults
        )

        assert result.notifications["ntfy"].sent is True
        assert result.notifications["ntfy"].error is None
        assert captured["url"] == "https://ntfy.example.com/x"
        assert captured["method"] == "POST"
        # urllib.request.Request normalises header keys via
        # capitalize(); accept either form defensively.
        auth = captured["headers"].get("Authorization") or captured[
            "headers"
        ].get("authorization")
        assert auth == "Bearer tk_test"
        tags = captured["headers"].get("Tags") or captured["headers"].get(
            "tags"
        )
        assert tags == "warning,fail"
        # Body mirrors the email layout: human summary block,
        # separator, then the latest log entry. (No run-header in
        # this fixture, so latest-entry extraction passes the
        # text through unchanged.)
        body = captured["data"].decode("utf-8")
        assert "Job:" in body
        assert "Exit class:" in body
        assert "--- log (latest run) ---" in body
        assert "log content here" in body
        # No Filename header: the body is inline content, not an
        # ntfy attachment (which would be publicly addressable).
        for k in captured["headers"]:
            assert k.lower() != "filename", (
                f"Filename header leaked: {captured['headers']!r}"
            )

    def test_ntfy_body_is_latest_run_entry_only(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Multi-run log: the body should contain only the most
        # recent run's entry, not earlier history. ntfy's 4 KB
        # message ceiling means we can't ship the whole log.
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["ntfy"])
        captured: dict[str, Any] = {}

        class _Resp:
            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake(req: Any, **_kwargs: object) -> Any:
            captured["data"] = req.data
            return _Resp()

        monkeypatch.setattr(crony_notify.urllib.request, "urlopen", _fake)
        log_text = (
            "=== 2026-05-01T03:00:00-08:00 j pid=1 ===\n"
            "older-run-detail\n"
            "=== 2026-05-02T03:00:00-08:00 j pid=2 ===\n"
            "newest-run-detail\n"
        )
        crony_notify.dispatch_notify(
            result, "default.j", log_text, cfg.defaults
        )
        body = captured["data"].decode("utf-8")
        assert "newest-run-detail" in body
        assert "older-run-detail" not in body

    def test_ntfy_body_head_truncated_to_3kb(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Body must fit ntfy's per-message limit. The summary stays
        # intact at the top (its structured fields are more useful
        # than a truncated stub); the log section is head-truncated
        # so the most recent failure output stays visible at the
        # bottom.
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["ntfy"])
        captured: dict[str, Any] = {}

        class _Resp:
            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake(req: Any, **_kwargs: object) -> Any:
            captured["data"] = req.data
            return _Resp()

        monkeypatch.setattr(crony_notify.urllib.request, "urlopen", _fake)
        log_text = (
            "=== 2026-05-02T03:00:00-08:00 j pid=2 ===\n"
            + ("X" * 5000)
            + "MARKER-AT-TAIL\n"
        )
        crony_notify.dispatch_notify(
            result, "default.j", log_text, cfg.defaults
        )
        body_bytes = captured["data"]
        assert len(body_bytes) <= 3 * 1024
        body = body_bytes.decode("utf-8", errors="replace")
        # Summary block intact at the top.
        assert body.startswith("Job:")
        assert "Exit class:" in body
        # Log section follows the separator and shows the tail.
        assert "--- log (latest run) ---" in body
        assert "MARKER-AT-TAIL" in body
        # Truncation marker appears within the log section.
        assert "bytes truncated" in body

    def test_ntfy_body_is_summary_only_when_attach_log_disabled(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `notify_attach_log = false` means "no log content in
        # notifications"; the body is the structured summary
        # without the trailing log section.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify_attach_log": False,
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["ntfy"])},
            }
        )
        result = self._make_failed_result(["ntfy"])
        captured: dict[str, Any] = {}

        class _Resp:
            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake(req: Any, **_kwargs: object) -> Any:
            captured["data"] = req.data
            return _Resp()

        monkeypatch.setattr(crony_notify.urllib.request, "urlopen", _fake)
        crony_notify.dispatch_notify(
            result, "default.j", "log content not in body", cfg.defaults
        )
        body = captured["data"].decode("utf-8")
        # Human summary keys are present; log content is not.
        assert "Job:" in body
        assert "Exit class:" in body
        assert "log content not in body" not in body

    def test_http_error_recorded(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg = self._common_config(tmp_path)
        result = self._make_failed_result(["ntfy"])

        # urllib raises HTTPError for 4xx/5xx responses; mirror that
        # so the test reflects real-world failure.
        def _raise(req: Any, **_kwargs: object) -> Any:
            raise crony_notify.urllib.error.HTTPError(
                req.full_url, 503, "service unavailable", Message(), None
            )

        monkeypatch.setattr(crony_notify.urllib.request, "urlopen", _raise)
        crony_notify.dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["ntfy"].sent is False
        assert "503" in (result.notifications["ntfy"].error or "")

    def test_user_headers_attached(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # A custom-named ntfy channel with `headers = { Email = ... }`
        # should reach the HTTP POST.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["ntfy-email"],
                    "notify": {
                        "ntfy-email": {
                            "transport": "ntfy",
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                            "headers": {
                                "Email": "you@example.com",
                                "Priority": "urgent",
                            },
                        }
                    },
                },
                "job": {"j": _job(notify_channels=["ntfy-email"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )
        result = self._make_failed_result(["ntfy-email"])
        captured: dict[str, Any] = {}

        class _Resp:
            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *a: Any) -> None:
                return None

        def _fake_urlopen(req: Any, **_kwargs: object) -> Any:
            captured["headers"] = dict(req.header_items())
            return _Resp()

        monkeypatch.setattr(
            crony_notify.urllib.request, "urlopen", _fake_urlopen
        )
        crony_notify.dispatch_notify(result, "default.j", "log", cfg.defaults)
        assert result.notifications["ntfy-email"].sent is True
        # User headers reached the request. urllib normalizes header
        # keys via .capitalize().
        h = captured["headers"]
        email_h = h.get("Email") or h.get("email")
        prio_h = h.get("Priority") or h.get("priority")
        assert email_h == "you@example.com"
        assert prio_h == "urgent"
        # crony's controlled headers still set.
        assert h.get("Authorization") or h.get("authorization")
        assert h.get("Tags") or h.get("tags")


class TestDialogPopupNotify:
    """The zero-config `dialog-popup` built-in channel: valid without a
    `[defaults.notify.dialog-popup]` block, and on macOS spawns a
    detached osascript dialog carrying the failure summary + log.
    """

    def _make_failed_result(self, channels: list[str]) -> Any:
        return JobRunResult(
            host="h",
            platform="darwin",
            started_at="2026-05-02T10:00:00-07:00",
            ended_at="2026-05-02T10:00:01-07:00",
            duration_sec=1.0,
            exit_class=ExitClass.FAIL,
            exit_code=2,
            signal=None,
            process_exit=2,
            gate=GateResult.NONE,
            log_path="/tmp/run.log",
            notifications={
                ch: NotificationResult(sent=False) for ch in channels
            },
        )

    def test_validate_accepts_builtin_without_block(self) -> None:
        # No block, no other defined channels: the built-in name is
        # still a valid notify_channels entry.
        assert (
            _validate_notify_channels(
                ["dialog-popup"], set(), "[defaults]", is_default=False
            )
            is None
        )

    def test_bundle_with_builtin_validates_clean(self) -> None:
        cfg = _parse(
            {
                "defaults": {"notify_channels": ["dialog-popup"]},
                "job": {"j": _job(notify_channels=["dialog-popup"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )
        assert "j" not in cfg.errored_jobs
        assert "j" in cfg.jobs

    def test_explicit_block_parses(self) -> None:
        cfg = _parse(
            {
                "defaults": {
                    "notify_channels": ["dialog-popup"],
                    "notify": {"dialog-popup": {"transport": "dialog-popup"}},
                },
                "job": {"j": _job(notify_channels=["dialog-popup"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )
        ch = cfg.defaults.notify_channel_defs["dialog-popup"]
        assert ch.transport == "dialog-popup"
        assert ch.email is None and ch.ntfy is None

    def test_explicit_block_rejects_extra_keys(self) -> None:
        with pytest.raises(ConfigError, match="unknown key"):
            NotifyChannel._from_raw(
                "dialog-popup",
                {"transport": "dialog-popup", "headers": {"X": "y"}},
            )

    def test_dispatch_spawns_osascript_on_darwin(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        captured: dict[str, Any] = {}

        def _fake_popen(cmd: Any, **kwargs: Any) -> Any:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return None

        monkeypatch.setattr(crony_commands.subprocess, "Popen", _fake_popen)
        result = self._make_failed_result(["dialog-popup"])
        crony_notify.dispatch_notify(
            result, "borgadm.check-repo", "boom log line", Defaults()
        )
        assert result.notifications["dialog-popup"].sent is True
        assert captured["cmd"][0:2] == ["osascript", "-e"]
        script = captured["cmd"][2]
        assert "display dialog" in script
        assert "borgadm.check-repo" in script
        assert "(exit 2)" in script
        assert "boom log line" in script
        # Detached so the modal can't stall the runner.
        assert captured["kwargs"].get("start_new_session") is True

    def test_dispatch_records_failure_off_darwin(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(crony_platform, "current_platform", lambda: "linux")

        def _boom(*_args: object, **_kwargs: object) -> Any:
            raise AssertionError("osascript spawned on non-darwin")

        monkeypatch.setattr(crony_commands.subprocess, "Popen", _boom)
        result = self._make_failed_result(["dialog-popup"])
        crony_notify.dispatch_notify(result, "default.j", "log", Defaults())
        nr = result.notifications["dialog-popup"]
        assert nr.sent is False
        assert nr.error_class == "CronyError"

    def test_body_escapes_quotes_and_backslashes(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            crony_commands.subprocess,
            "Popen",
            lambda cmd, **_k: captured.setdefault("cmd", cmd),
        )
        result = self._make_failed_result(["dialog-popup"])
        crony_notify.dispatch_notify(
            result, "default.j", 'he said "hi" \\ bye', Defaults()
        )
        script = captured["cmd"][2]
        # Raw double-quotes / backslashes from the log would corrupt the
        # AppleScript string literal; they must arrive escaped.
        assert '\\"hi\\"' in script
        assert "\\\\ bye" in script

    def test_attach_log_false_omits_log(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            crony_platform, "current_platform", lambda: "darwin"
        )
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            crony_commands.subprocess,
            "Popen",
            lambda cmd, **_k: captured.setdefault("cmd", cmd),
        )
        result = self._make_failed_result(["dialog-popup"])
        crony_notify.dispatch_notify(
            result,
            "default.j",
            "secret log line",
            Defaults(notify_attach_log=False),
        )
        script = captured["cmd"][2]
        assert "secret log line" not in script
        assert crony_notify._LOG_SEPARATOR not in script


class TestMultiChannelDispatch:
    """`dispatch_notify` fans out across all configured channels and
    one channel's failure must not suppress the others. The
    single-channel tests in TestEmailNotify / TestNtfyNotify don't
    exercise this; this class pins the headline contract.
    """

    def _config(self, tmp_path: Path) -> Any:
        smtp_secret = tmp_path / "smtp-pw"
        smtp_secret.write_text("hunter2")
        smtp_secret.chmod(0o600)
        ntfy_secret = tmp_path / "ntfy-token"
        ntfy_secret.write_text("tk_test")
        ntfy_secret.chmod(0o600)
        return _parse(
            {
                "defaults": {
                    "notify_channels": ["email", "ntfy"],
                    "notify_attach_log": True,
                    "notify_attach_max_kb": 1,
                    "notify": {
                        "email": {
                            "to": "you@example.com",
                            "smtp_host": "smtp.example.com",
                            "smtp_user": "u@example.com",
                            "smtp_pass_file": str(smtp_secret),
                        },
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(ntfy_secret),
                        },
                    },
                },
                "job": {"j": _job(notify_channels=["email", "ntfy"])},
                "target": {"darwin": {"jobs": ["j"]}},
            }
        )

    def test_email_succeeds_ntfy_fails(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        cfg = self._config(tmp_path)
        result = JobRunResult(
            host="h",
            platform="darwin",
            started_at="2026-05-02T10:00:00-07:00",
            ended_at="2026-05-02T10:00:01-07:00",
            duration_sec=1.0,
            exit_class=ExitClass.FAIL,
            exit_code=2,
            signal=None,
            process_exit=2,
            gate=GateResult.NONE,
            log_path="/tmp/run.log",
            notifications={
                "email": NotificationResult(sent=False),
                "ntfy": NotificationResult(sent=False),
            },
        )
        smtp_cls = create_autospec(crony_notify.smtplib.SMTP)
        smtp_inst = smtp_cls.return_value
        smtp_inst.__enter__.return_value = smtp_inst
        smtp_inst.__exit__.return_value = None
        monkeypatch.setattr(crony_notify.smtplib, "SMTP", smtp_cls)

        def _fail_post(req: Any, **_kwargs: object) -> Any:
            raise crony_notify.urllib.error.HTTPError(
                req.full_url, 503, "service unavailable", Message(), None
            )

        monkeypatch.setattr(crony_notify.urllib.request, "urlopen", _fail_post)
        crony_notify.dispatch_notify(
            result, "default.j", "log content", cfg.defaults
        )

        # email succeeded
        assert result.notifications["email"].sent is True
        assert result.notifications["email"].error is None
        # ntfy failed independently
        assert result.notifications["ntfy"].sent is False
        assert "503" in (result.notifications["ntfy"].error or "")
        # And both still appear (one transport failure didn't suppress
        # the other channel).
        assert set(result.notifications.keys()) == {"email", "ntfy"}


class TestNotifyTestSubcommand:
    """`crony notify-test` synth event invocation."""

    def test_no_channels_is_quiet(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        # No channels configured: should not raise.
        crony_commands.do_notify_test(channel=None, bundle=None)

    def test_unresolvable_secret_raises_config_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # email channel is fully defined but the SMTP password
        # source can't be resolved -- this is a config-shaped
        # failure (the user can fix it), so notify-test surfaces
        # it as CONFIG (3) rather than ERROR (4).
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["email"],
                    "notify": {"email": _email_block()},
                },
            },
            default_target_jobs=[],
        )
        with pytest.raises(ConfigError, match="notify-test failed"):
            crony_commands.do_notify_test(channel=None, bundle=None)

    def test_transport_failure_raises_crony_error(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Properly configured ntfy but the transport itself fails
        # (HTTP 503). The classifier should surface CronyError ->
        # ERROR (4), not ConfigError -- this is not a config issue
        # the user can fix in the toml.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                },
            },
            default_target_jobs=[],
        )

        def _raise(req: Any, **_kwargs: object) -> Any:
            raise crony_notify.urllib.error.HTTPError(
                req.full_url, 503, "service unavailable", Message(), None
            )

        monkeypatch.setattr(crony_notify.urllib.request, "urlopen", _raise)
        with pytest.raises(CronyError) as exc:
            crony_commands.do_notify_test(channel="ntfy", bundle=None)
        # Distinguishing from ConfigError matters: CronyError exits
        # with ERROR (4), ConfigError with CONFIG (3).
        assert not isinstance(exc.value, ConfigError)
        assert "notify-test failed" in str(exc.value)

    def test_no_bundle_means_default_only(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # With multiple bundles present and --bundle omitted,
        # notify-test exercises only the default bundle (matches
        # crony's bare-input rule). The borgadm bundle's broken
        # ntfy config must not be touched.
        h = _RunnerHarness(tmp_path, monkeypatch)
        # default bundle: no channels, so no attempt -> quiet exit.
        h.config({}, default_target_jobs=[])
        # second bundle: lists ntfy but has no [defaults.notify.ntfy]
        # block -- would raise ConfigError if reached.
        (h.cfg_dropin / "borgadm.toml").write_text(
            '[defaults]\nnotify_channels = ["ntfy"]\n',
            encoding="utf-8",
        )
        # Should not raise: only default is exercised.
        crony_commands.do_notify_test(channel=None, bundle=None)

    def test_namespaced_channel_picks_named_bundle(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `--channel borgadm.ntfy` should target borgadm's ntfy
        # config, not the default bundle's.
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        # borgadm has no ntfy block. Asking for borgadm.ntfy should
        # fail because no channel of that name is defined there,
        # which proves we routed into borgadm and not default.
        (h.cfg_dropin / "borgadm.toml").write_text(
            "[defaults]\nnotify_channels = []\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="unknown notify channel"):
            crony_commands.do_notify_test(channel="borgadm.ntfy", bundle=None)

    def test_unknown_bundle_rejected(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        with pytest.raises(UsageError, match="unknown bundle"):
            crony_commands.do_notify_test(channel=None, bundle="ghost")

    def test_inheriting_bundle_dispatches_default_channels(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # An inheriting bundle's notify-test sends through the default
        # bundle's channels. A 503 from the (only, inherited) ntfy
        # channel proves it was resolved and attempted.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                }
            },
            default_target_jobs=[],
        )
        (h.cfg_dropin / "borgadm.toml").write_text(
            "[defaults]\n", encoding="utf-8"
        )
        calls: list[str] = []

        def _raise(req: Any, **_kwargs: object) -> Any:
            calls.append(req.full_url)
            raise crony_notify.urllib.error.HTTPError(
                req.full_url, 503, "service unavailable", Message(), None
            )

        monkeypatch.setattr(crony_notify.urllib.request, "urlopen", _raise)
        with pytest.raises(CronyError, match="notify-test failed") as exc:
            crony_commands.do_notify_test(channel=None, bundle="borgadm")
        assert calls, "inherited ntfy channel was not attempted"
        # The failure detail attributes the channel to where it is
        # defined (the default bundle), not the inheriting bundle.
        assert "borgadm.ntfy (inherited from default)" in str(exc.value)

    def test_inheriting_bundle_success_attributes_default(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                }
            },
            default_target_jobs=[],
        )
        (h.cfg_dropin / "private.toml").write_text(
            "[defaults]\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            crony_notify.urllib.request,
            "urlopen",
            lambda *_a, **_k: MagicMock(),
        )
        with caplog.at_level(logging.INFO, logger="crony"):
            crony_commands.do_notify_test(channel=None, bundle="private")
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "notification sent via private.ntfy (inherited from default)" in m
            for m in messages
        )

    def test_explicit_channel_on_inheriting_bundle_attributes_default(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # An explicit --channel against an inheriting bundle resolves
        # through (and is attributed to) the default bundle too.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                }
            },
            default_target_jobs=[],
        )
        (h.cfg_dropin / "private.toml").write_text(
            "[defaults]\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            crony_notify.urllib.request,
            "urlopen",
            lambda *_a, **_k: MagicMock(),
        )
        with caplog.at_level(logging.INFO, logger="crony"):
            crony_commands.do_notify_test(channel="ntfy", bundle="private")
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "notification sent via private.ntfy (inherited from default)" in m
            for m in messages
        )

    def test_self_defined_channel_has_no_inherited_suffix(
        self, tmp_path: Path, monkeypatch: Any, caplog: Any
    ) -> None:
        # The default bundle sends through its own channel, so the
        # message carries no inherited-from suffix.
        secret = tmp_path / "ntfy-token"
        secret.write_text("tk_test")
        secret.chmod(0o600)
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {
                "defaults": {
                    "notify_channels": ["ntfy"],
                    "notify": {
                        "ntfy": {
                            "url": "https://ntfy.example.com/x",
                            "token_file": str(secret),
                        }
                    },
                }
            },
            default_target_jobs=[],
        )
        monkeypatch.setattr(
            crony_notify.urllib.request,
            "urlopen",
            lambda *_a, **_k: MagicMock(),
        )
        with caplog.at_level(logging.INFO, logger="crony"):
            crony_commands.do_notify_test(channel=None, bundle=None)
        messages = [r.getMessage() for r in caplog.records]
        assert any("notification sent via default.ntfy" in m for m in messages)
        assert not any("inherited from" in m for m in messages)


class TestLogHelpers:
    """Direct unit tests for `extract_latest_log_entry` and
    `_head_truncate_to_kb`. Exercised end-to-end via TestLogs and
    TestNtfyNotify; this class isolates the boundary conditions
    so a regression in either helper surfaces here first.
    """

    def test_extract_returns_from_last_header(self) -> None:
        text = (
            "=== 2026-05-01T03:00:00-08:00 j pid=1 ===\n"
            "older\n"
            "=== 2026-05-02T03:00:00-08:00 j pid=2 ===\n"
            "newest\n"
        )
        out = crony_notify.extract_latest_log_entry(text)
        assert out.startswith("=== 2026-05-02T03:00:00-08:00")
        assert "newest" in out
        assert "older" not in out

    def test_extract_returns_full_text_when_no_header(self) -> None:
        text = "no header here, just content\n"
        assert crony_notify.extract_latest_log_entry(text) == text

    def test_extract_returns_empty_for_empty_input(self) -> None:
        assert crony_notify.extract_latest_log_entry("") == ""

    def test_head_truncate_under_cap_passes_through(self) -> None:
        text = "small body\n"
        out, truncated = crony_notify._head_truncate_to_kb(text, 1)
        assert out == text
        assert truncated is False

    def test_head_truncate_over_cap_keeps_tail_with_marker(self) -> None:
        # 1 KB cap; build a text that's ~3KB so head-truncation drops
        # the start. The output must be <= 1024 bytes and start with
        # the truncation marker.
        text = "X" * 3000 + "TAIL"
        out, truncated = crony_notify._head_truncate_to_kb(text, 1)
        assert truncated is True
        assert len(out.encode("utf-8")) <= 1024
        assert out.startswith("[... ")
        assert "bytes truncated" in out
        assert out.endswith("TAIL")


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
