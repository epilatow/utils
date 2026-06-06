# This is AI generated code

"""crony's importable library code.

The package root defines `BASENAME`, the tool's identity string (its env
prefix and default config / state paths derive from it). The reusable
pieces `bin/crony` composes live here as importable modules -- the path
foundation (`crony.paths`), the exit codes and exception hierarchy
(`crony.errors`), the platform-neutral unit value objects (`crony.unit`),
the per-host scheduler / host backends (`crony.platform`), the TOML
configuration layer (`crony.config`), the pure in-memory domain model
(`crony.model`), and the disk / lock / scheduler-query runtime layer
(`crony.runtime`).
"""

from __future__ import annotations

BASENAME: str = "crony"
