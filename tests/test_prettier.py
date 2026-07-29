#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""The repo's first-party JavaScript is prettier clean.

prettier runs with its own defaults, so the JavaScript reads the way the
language's tooling expects rather than in a house style borrowed from the
4-space Mach-O wrapper C beside it. No `.prettierrc.json` is shipped, and
not only because the defaults are wanted: a config at the repo root
applies to every type prettier handles, which would quietly make it an
authority over the markdown that `mdformat` and `markdownlint` own.

The version is pinned exactly, as the man-page gate pins pandoc: a
formatter's output varies between releases, so an unpinned one starts
failing on untouched code the first time somebody's `npx` resolves a
newer build. A linter is not the same risk, which is why the markdownlint
gate floats. Bump `PRETTIER` and reformat in one commit.

prettier is reached through `npx`, which the markdownlint gate already
requires; a missing one fails here too rather than skipping, since a
formatting gate that silently covered nothing is worse than a loud
missing tool.
"""

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_script_path = Path(__file__)

# Pinned; see the module docstring on why this does not float.
PRETTIER = "prettier@3.6.2"

_SUFFIXES = (".js", ".mjs", ".cjs")
# Vendored and mechanically synced from repo-shared, so not ours to format.
_EXCLUDE_TOP = ("_repo_shared",)


def _tracked_javascript() -> list[Path]:
    """Tracked first-party JavaScript, symlinks and absent files dropped.

    The symlink check earns its keep on exactly one path today: the
    repo-root `.markdownlint-rule-no-squashed-file-references.mjs`, which
    points into `_repo_shared/dotfiles/` and so is not ours to format,
    but whose first path component is the filename rather than
    `_repo_shared` -- meaning the exclusion below does not catch it.
    Without the check it reaches prettier, which hard-errors on a
    symlink named explicitly rather than following it.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    found = []
    for name in listed:
        if not name or not name.endswith(_SUFFIXES):
            continue
        rel = Path(name)
        if rel.parts[0] in _EXCLUDE_TOP:
            continue
        absolute = REPO_ROOT / rel
        if absolute.is_symlink() or not absolute.is_file():
            continue
        found.append(rel)
    return sorted(found)


_JS_SOURCES = _tracked_javascript()


def test_js_is_prettier_clean() -> None:
    assert shutil.which("npx") is not None, (
        "``npx`` is not on PATH; prettier cannot run. Install Node (e.g. "
        "``brew install node`` on macOS, ``apt install nodejs npm`` on "
        "Debian / Ubuntu) so the repo's JavaScript stays formatted."
    )
    paths = [str(p) for p in _JS_SOURCES]
    # prettier exits 0 when given no paths at all, so without this the
    # check would pass by covering nothing rather than failing loudly.
    assert paths, "no tracked first-party JavaScript to check"
    result = subprocess.run(
        # --ignore-path /dev/null: a `.prettierignore` added later must
        # not be able to empty this gate, which would then pass by
        # covering nothing while reporting success.
        [
            "npx",
            "--yes",
            PRETTIER,
            "--ignore-path",
            "/dev/null",
            "--check",
            *paths,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    # prettier exits 1 for "would reformat" and 2 for its own failure (a
    # syntax error, an unresolvable download). Only the first is a
    # formatting complaint, so the other must not advise --write.
    assert result.returncode in (0, 1), (
        f"prettier failed to run (exit {result.returncode}):\n"
        f"{result.stdout}{result.stderr}"
    )
    assert result.returncode == 0, (
        "not prettier clean; run from the repo root: npx --yes "
        f"{PRETTIER} --ignore-path /dev/null --write {' '.join(paths)}\n"
        f"{result.stdout}{result.stderr}"
    )


def test_js_sources_discovered() -> None:
    # Guard the discovery: a layout change that hid every JavaScript
    # source would make the gate silently pass by covering nothing.
    assert _JS_SOURCES, "no tracked first-party JavaScript found"


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
