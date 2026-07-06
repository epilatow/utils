# This is AI generated code

"""crony's path foundation.

The CRONY_<KEY> env-override helper and the config / state / unit
directories every layer resolves against. This is the lowest module in
the package: it imports only the standard library and the package's own
`BASENAME`, so any crony module can import it without risking a cycle.
"""

import os
from pathlib import Path
from typing import overload

from crony import BASENAME

# Path overrides via env so platform-mediated invocations (the
# scheduler starting `crony _run <bundle>:<uuid>`) and tests can redirect
# config, state, and the platform unit dir without filesystem games.
# Names follow the convention CRONY_<KEY>.
_ENV_PREFIX: str = BASENAME.upper()


@overload
def _env_path(key: str, default: str) -> Path: ...
@overload
def _env_path(key: str, default: None = None) -> Path | None: ...
def _env_path(key: str, default: str | None = None) -> Path | None:
    """The CRONY_<KEY> override as a Path. With a string `default` the
    result is always a Path (the env value, else the default). With no
    default, an unset / empty env var yields None -- for an override
    whose fallback is not a fixed path but a value the caller derives
    (e.g. a per-OS scheduler location)."""
    value = os.environ.get(f"{_ENV_PREFIX}_{key}") or default
    return Path(value) if value else None


CONFIG_DIR: Path = _env_path(
    "CONFIG_DIR", os.path.expanduser(f"~/.config/{BASENAME}")
)
CONFIG_FILE: Path = _env_path("CONFIG_FILE", str(CONFIG_DIR / "config.toml"))
CONFIG_DROPIN_DIR: Path = _env_path(
    "CONFIG_DROPIN_DIR", str(CONFIG_DIR / "config")
)
STATE_DIR: Path = _env_path(
    "STATE_DIR", os.path.expanduser(f"~/.local/state/{BASENAME}")
)
# The platform unit dir (systemd `~/.config/systemd/user`, launchd
# `~/Library/LaunchAgents`) is resolved per-OS by each scheduler
# backend's `default_unit_dir()`; this override redirects it so a test
# can install units under a throwaway tree instead of the real one. None
# when unset, so the per-OS default stands.
UNIT_DIR: Path | None = _env_path("UNIT_DIR")
