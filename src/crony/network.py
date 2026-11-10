# This is AI generated code

"""Live bundle settings and HTTPS probes for network-required jobs."""

import contextlib
import http.client
from typing import IO

import crony.config
import crony.errors


def check_bundle_network(bundle_name: str, log_file: IO[bytes]) -> bool:
    """True when any configured host returns an HTTPS response.

    Read only this bundle's defaults, independently of notification
    inheritance. A missing or unreadable bundle uses the built-in hosts
    so applied jobs can still run through a config error or removal.
    """
    hosts = list(crony.config.DEFAULT_NETWORK_CHECK_HTTPS)
    try:
        bundle = crony.config.TomlConfig.load_all().by_name(bundle_name)
    except OSError, crony.errors.ConfigError, crony.errors.UsageError:
        bundle = None
    if bundle is None:
        log_file.write(
            b"network: bundle config unavailable; using built-in hosts\n"
        )
    else:
        hosts = bundle.config.defaults.network_check_https

    for host in hosts:
        try:
            with contextlib.closing(
                http.client.HTTPSConnection(host, timeout=5)
            ) as connection:
                connection.request("HEAD", "/")
                with connection.getresponse() as response:
                    log_file.write(
                        f"network: https://{host}/ responded "
                        f"HTTP {response.status}\n".encode()
                    )
                    return True
        except (OSError, http.client.HTTPException) as exc:
            log_file.write(f"network: https://{host}/ failed: {exc}\n".encode())
    log_file.write(b"network: no HTTPS check host responded; canceling job\n")
    return False
