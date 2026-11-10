#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15,<3.16"
# dependencies = ["pytest", "pytest-cov", "tomlkit", "pydantic>=2.14"]
# ///
# This is AI generated code

"""Live bundle network settings and verified HTTPS request behavior."""

import http.client
import io
import socket
import ssl
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_crony import _RunnerHarness  # noqa: E402

from crony import network  # noqa: E402
from crony.config import TomlConfig  # noqa: E402
from crony.errors import ConfigError  # noqa: E402

_script_path = REPO_ROOT / "src" / "crony" / "network.py"


class TestNetworkCheck:
    @pytest.mark.parametrize("status", [200, 301, 403, 405, 503])
    def test_any_verified_http_response_is_enough(
        self, status: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config({}, default_target_jobs=[])
        log = io.BytesIO()
        with mock.patch.object(
            http.client, "HTTPSConnection", autospec=True
        ) as connection:
            conn = connection.return_value
            conn.getresponse.return_value.__enter__.return_value.status = status
            assert network.check_bundle_network("default", log)
            # The default HTTPSConnection context verifies the certificate
            # and hostname. No context override or insecure fallback is used.
            connection.assert_called_once_with("cloudflare.com", timeout=5)
            conn.request.assert_called_once_with("HEAD", "/")
            conn.close.assert_called_once_with()
            conn.getresponse.return_value.__exit__.assert_called_once()
        assert f"HTTP {status}".encode() in log.getvalue()

    @pytest.mark.parametrize(
        "error",
        [
            socket.gaierror("DNS failed"),
            TimeoutError("timed out"),
            ConnectionRefusedError("refused"),
            ssl.SSLCertVerificationError("untrusted certificate"),
            http.client.BadStatusLine("bad response"),
        ],
    )
    @pytest.mark.parametrize("stage", ["request", "getresponse"])
    def test_all_hosts_fail(
        self,
        error: Exception,
        stage: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _RunnerHarness(tmp_path, monkeypatch)
        log = io.BytesIO()
        with mock.patch.object(
            http.client, "HTTPSConnection", autospec=True
        ) as connection:
            conn = connection.return_value
            getattr(conn, stage).side_effect = error
            assert not network.check_bundle_network("missing", log)
            assert connection.call_args_list == [
                mock.call("cloudflare.com", timeout=5),
                mock.call("google.com", timeout=5),
            ]
            assert conn.close.call_count == 2
        assert b"canceling job" in log.getvalue()

    def test_second_host_can_succeed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _RunnerHarness(tmp_path, monkeypatch)
        with mock.patch.object(
            http.client, "HTTPSConnection", autospec=True
        ) as connection:
            conn = connection.return_value
            conn.request.side_effect = [OSError("offline"), None]
            conn.getresponse.return_value.__enter__.return_value.status = 200
            assert network.check_bundle_network("default", io.BytesIO())
            assert connection.call_count == 2

    def test_live_bundle_hosts_without_default_bundle_inheritance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _RunnerHarness(tmp_path, monkeypatch)
        h.config(
            {"defaults": {"network-check-https": ["default.example"]}},
            default_target_jobs=[],
        )
        bundle = h.cfg_dropin / "backup.toml"
        bundle.write_text("", encoding="utf-8")
        with mock.patch.object(
            http.client, "HTTPSConnection", autospec=True
        ) as connection:
            assert network.check_bundle_network("backup", io.BytesIO())
            connection.assert_called_once_with("cloudflare.com", timeout=5)
            connection.reset_mock()
            bundle.write_text(
                '[defaults]\nnetwork-check-https = ["backup.example."]\n',
                encoding="utf-8",
            )
            assert network.check_bundle_network("backup", io.BytesIO())
            connection.assert_called_once_with("backup.example", timeout=5)

    def test_config_error_uses_builtin_hosts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _RunnerHarness(tmp_path, monkeypatch)
        with (
            mock.patch.object(
                TomlConfig,
                "load_all",
                autospec=True,
                side_effect=ConfigError("unreadable config"),
            ),
            mock.patch.object(
                http.client, "HTTPSConnection", autospec=True
            ) as connection,
        ):
            assert network.check_bundle_network("backup", io.BytesIO())
            connection.assert_called_once_with("cloudflare.com", timeout=5)


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
