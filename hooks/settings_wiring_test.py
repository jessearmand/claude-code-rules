#!/usr/bin/env python3
"""Tests that .claude/settings.json routes each hook every tool it handles.

Run: python3 hooks/settings_wiring_test.py

The per-hook suites call the scripts directly, so they pass even when
settings.json never sends a tool to the hook. That gap is how file_protection
handled NotebookEdit in its tests while the matcher omitted it in practice.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hook_protocol import READ_TOOLS, WRITE_TOOLS  # noqa: E402

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS = os.path.join(os.path.dirname(_HOOKS_DIR), ".claude", "settings.json")

# The tools each hook acts on. A hook's matcher must admit all of them.
HANDLED_TOOLS = {
    "rm_rf_guard.py": {"Bash"},
    "bash_command_validator.py": {"Bash"},
    "bash_risk_judge.py": {"Bash"},
    "secrets_guard.py": READ_TOOLS | WRITE_TOOLS | {"Bash"},
    "file_protection.py": set(WRITE_TOOLS),
}


def routed_tools(settings: dict) -> dict[str, set[str]]:
    """Map hook script name -> every tool name some matcher routes to it."""
    routed: dict[str, set[str]] = {}
    candidates = set().union(*HANDLED_TOOLS.values())
    for group in settings.get("hooks", {}).get("PreToolUse", []):
        pattern = re.compile(group.get("matcher") or ".*")
        admitted = {tool for tool in candidates if pattern.fullmatch(tool)}
        for hook in group.get("hooks", []):
            script = os.path.basename(hook.get("command", "").split()[-1])
            routed.setdefault(script, set()).update(admitted)
    return routed


def main() -> int:
    with open(_SETTINGS, encoding="utf-8") as handle:
        routed = routed_tools(json.load(handle))

    failures: list[str] = []
    for script, handled in HANDLED_TOOLS.items():
        if not os.path.isfile(os.path.join(_HOOKS_DIR, script)):
            failures.append(f"{script}: listed here but missing from hooks/")
        if script not in routed:
            failures.append(f"{script}: not wired into settings.json")
            continue
        missing = handled - routed[script]
        label = f"{script:<28} routed {sorted(routed[script])}"
        if missing:
            failures.append(f"{script}: handles {sorted(missing)} but no matcher routes them")
            print(f"  FAIL  {label}")
        else:
            print(f"  pass  {label}")

    for script in routed:
        if script.endswith(".py") and script not in HANDLED_TOOLS:
            failures.append(f"{script}: wired in settings.json but has no entry in HANDLED_TOOLS")

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All hooks are routed every tool they handle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
