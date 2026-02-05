# Development Guidelines

- Before modifying any code or utilities, check for any associated tests
  (e.g., a `self-test` subcommand, test files in `tests/`, etc) and run
  those tests to get a baseline before making any changes. Flag any
  testing problems before implementing any planned changes.

- After modifying any code or utility, always run the associated tests
  before considering any changes complete.

- If a utility has tests and new functionality is being added, be sure
  to add tests for the new functionality.

- Always look at the contents of a script to see what kind of script it
  is, do not rely on file name extensions (or lack thereof). Scripts
  that have "uv run --script" in their shebang are python scripts, not
  shell scripts (regardless of the file extension).

- All code should be considered cross-platform and may execute on macOS
  or Linux. Platform specific code (say launchd or systemd support, or
  running commands specific to Linux or macOS), should be gated by
  platform checks.

- All utilities should have well defined return / exit values.

- For python code:
    - Strive to be consistent in form and layout with other python code
      in the repo.
    - Use "uv run --script" for their shebang interpreter.
    - Line wrap at 80 chars.
    - Be black, flake8, and mypy compliant (which should be enforced via
      tests).
    - Be strongly typed, avoiding the storage of structured data in a
      Dict with Any values.
    - By default, tests should use pytest, unless there is a good reason
      for using an alternative framework, in which case please suggest it.
    - When mocking functions always use `autospec=True` so that some
      argument verification occurs.
