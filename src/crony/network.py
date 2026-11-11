# This is AI generated code

"""Live bundle settings and HTTPS probes for network-required jobs."""

import contextlib
import http.client
from pathlib import Path
from typing import IO

import crony.config
import crony.errors
import crony.log


def check_bundle_network(
    bundle_name: str, log_file: IO[bytes], *, log_path: Path | None = None
) -> bool:
    """True when any configured host returns HTTP 200 over verified HTTPS.

    Read only this bundle's defaults, independently of notification
    inheritance. A missing or unreadable bundle uses the built-in hosts
    so applied jobs can still run through a config error or removal.
    """

    def log(text: str) -> None:
        crony.log.write_line(log_file, text, log_path=log_path)

    hosts = list(crony.config.DEFAULT_NETWORK_CHECK_HTTPS)
    try:
        bundle = crony.config.TomlConfig.load_all().by_name(bundle_name)
    except OSError, crony.errors.ConfigError, crony.errors.UsageError:
        bundle = None
    if bundle is None:
        log("network: bundle config unavailable; using built-in hosts")
    else:
        hosts = bundle.config.defaults.network_check_https

    for host in hosts:
        try:
            with contextlib.closing(
                http.client.HTTPSConnection(host, timeout=5)
            ) as connection:
                connection.request("HEAD", "/")
                with connection.getresponse() as response:
                    log(
                        f"network: https://{host}/ responded "
                        f"HTTP {response.status}",
                    )
                    if response.status == 200:
                        return True
        except (OSError, http.client.HTTPException) as exc:
            log(f"network: https://{host}/ failed: {exc}")
    log("network: no HTTPS check host returned HTTP 200; canceling job")
    return False
