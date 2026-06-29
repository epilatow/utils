# This is AI generated code

"""crony's importable library code.

The package root defines `BASENAME`, the tool's identity string (its env
prefix and default config / state paths derive from it). `bin/crony` is
now a thin entry that puts this package on `sys.path` and calls
`crony.cli`; the whole implementation lives here as importable modules,
bottom-up: the path foundation (`crony.paths`), the exit codes and
exception hierarchy (`crony.errors`), the platform-neutral unit value
objects (`crony.unit`), the per-host scheduler / host backends
(`crony.platform`), the TOML configuration layer (`crony.config`), the
pure in-memory domain model (`crony.model`), the disk / lock /
scheduler-query runtime layer (`crony.runtime`), the notification layer
(`crony.notify`), the run pipeline (`crony.runner`), the command handlers
(`crony.commands`), and the argument parser plus entry point
(`crony.cli`).
"""

BASENAME: str = "crony"
