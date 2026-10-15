"""Read-only filesystem case semantics for protected-path comparisons."""

from __future__ import annotations

import os
import re
import sys


def case_insensitive(path: str) -> bool:
    """Query the target volume on macOS, including for a file not yet created.

    Darwin's _PC_CASE_SENSITIVE is 11 (xnu/bsd/sys/unistd.h). Querying the
    nearest existing target ancestor follows symlinks and respects mounted
    case-sensitive volumes; the host platform alone cannot decide this.
    An unavailable answer on macOS conservatively protects case aliases.
    Linux paths retain their normal case-sensitive comparisons.
    """
    if sys.platform != "darwin":
        return False
    target = os.path.abspath(os.path.expanduser(path))
    while not os.path.exists(target):
        parent = os.path.dirname(target)
        if parent == target:
            return True
        target = parent
    try:
        return os.pathconf(target, os.pathconf_names.get("PC_CASE_SENSITIVE", 11)) != 1
    except (OSError, ValueError):
        return True


def pattern_matches(
    pattern: re.Pattern[str], path: str, insensitive: bool | None = None
) -> bool:
    if pattern.search(path):
        return True
    insensitive = case_insensitive(path) if insensitive is None else insensitive
    return insensitive and bool(
        re.search(pattern.pattern, path, pattern.flags | re.IGNORECASE)
    )


def is_inside(path: str, root: str) -> bool:
    if case_insensitive(path):
        path, root = path.casefold(), root.casefold()
    return path == root or path.startswith(root.rstrip("/") + "/")
