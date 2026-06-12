# This is AI generated code

"""crony's path foundation.

The CRONY_<KEY> env-override helper and the config / state directories
every layer resolves against. This is the lowest module in the package:
it imports only the standard library and the package's own `BASENAME`,
so any crony module can import it without risking a cycle.
"""

from __future__ import annotations

import os
from pathlib import Path

from crony import BASENAME

# Path overrides via env so platform-mediated invocations (the
# scheduler starting `crony _run <bundle>:<uuid>`) and tests can redirect
# config and state without filesystem games. Names follow the
# convention CRONY_<KEY>.
_ENV_PREFIX: str = BASENAME.upper()


def _env_path(key: str, default: str) -> Path:
    return Path(os.environ.get(f"{_ENV_PREFIX}_{key}") or default)


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
