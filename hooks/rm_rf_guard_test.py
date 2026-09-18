#!/usr/bin/env python3
"""Tests for rm_rf_guard.py. Run: python3 hooks/rm_rf_guard_test.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rm_rf_guard import analyze_target, find_recursive_force_invocations  # noqa: E402

_GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rm_rf_guard.py")

SHOULD_BLOCK = [
    "rm -rf build",
    "rm -fr build",
    "rm -Rf build",
    "rm -rvf build",
    "rm -r -f build",
    "rm -f -R build",
    "rm --recursive --force build",
    "rm --force --recursive build",
    "rm -r --force build",
    "sudo rm -rf /var/cache/foo",
    "npm ci && rm -rf node_modules",
    "cd /tmp; rm -rf scratch",
    "find . -name '*.tmp' -exec rm -rf {} +",
    "ls | xargs rm -rf",
    "/bin/rm -rf build",
    "rm -rf -- --weird-dir",
    "rm -rf $PROJECT/dist",
    "rm -rf ~/Library/Caches/foo",
    "test -d out && rm -rf out || echo skipped",
]

SHOULD_ALLOW = [
    "rm build.log",
    "rm -r build",
    "rm -R build",
    "rm --recursive build",
    "rm -f build.log",
    "rm --force build.log",
    "rm -i -r build",
    'echo "rm -rf /"',
    "grep -rf patterns.txt src/",
    "echo rm -rf | wc -c",
    "git rm -r --cached build",
    "rsync -rf src/ dst/",
    "rm -rd build",
    "rm -- -rf",
    "printf '%s' 'rm -rf build'",
]


def _run_hook(command: str, cwd: str = "/Users/jeesearmand/Develop/claude-code") -> dict | None:
    """Invoke the hook end-to-end; return its parsed JSON payload, or None if it deferred."""
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": cwd,
        }
    )
    result = subprocess.run(
        [sys.executable, _GUARD], input=payload, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"unexpected exit {result.returncode}: {result.stderr}"
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _check(condition: bool, label: str, failures: list[str]) -> None:
    if condition:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}")
        failures.append(label)


def main() -> int:
    failures: list[str] = []

    print("Detection — must block:")
    for command in SHOULD_BLOCK:
        invocations, _ = find_recursive_force_invocations(command)
        _check(bool(invocations), command, failures)

    print("\nDetection — must not block:")
    for command in SHOULD_ALLOW:
        invocations, _ = find_recursive_force_invocations(command)
        _check(not invocations, command, failures)

    print("\nTarget analysis:")
    with tempfile.TemporaryDirectory() as tmp:
        directory = os.path.join(tmp, "adir")
        os.mkdir(directory)
        open(os.path.join(directory, "child"), "w").close()
        file_path = os.path.join(tmp, "afile")
        open(file_path, "w").close()
        link = os.path.join(tmp, "alink")
        os.symlink(file_path, link)

        cases = [
            (file_path, "file", f"rm {file_path}"),
            (directory, "directory", f"rm -r {directory}"),
            (link, "symlink", f"rm {link}"),
            (os.path.join(tmp, "nope"), "missing", None),
            ("$UNSET/dist", "unresolved", None),
            ("build/*", "glob", None),
        ]
        for target, expected_kind, expected_safer in cases:
            analysis = analyze_target(target, tmp)
            _check(
                analysis.kind == expected_kind and analysis.safer_command == expected_safer,
                f"{target} -> kind={analysis.kind} safer={analysis.safer_command}",
                failures,
            )

        _check(analyze_target("/", tmp).severity == "critical", "/ is critical", failures)
        _check(
            analyze_target(os.path.expanduser("~"), tmp).severity == "critical",
            "~ is critical",
            failures,
        )
        _check(analyze_target("/usr", tmp).severity == "critical", "/usr is critical", failures)
        _check(
            analyze_target("..", os.path.join(os.path.expanduser("~"), "Develop", "claude-code")).severity
            == "critical",
            ".. from cwd is critical (ancestor of cwd)",
            failures,
        )

    print("\nEnd-to-end hook protocol:")
    denied = _run_hook("rm -rf build")
    _check(
        denied is not None
        and denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        and denied["hookSpecificOutput"]["hookEventName"] == "PreToolUse",
        "rm -rf build -> permissionDecision: deny",
        failures,
    )
    _check(
        denied is not None and "AskUserQuestion" in denied["hookSpecificOutput"]["permissionDecisionReason"],
        "denial reason instructs AskUserQuestion",
        failures,
    )
    _check(
        denied is not None and "rm -r " in denied["hookSpecificOutput"]["permissionDecisionReason"],
        "denial reason suggests rm / rm -r",
        failures,
    )
    _check(_run_hook("rm -r build") is None, "rm -r build -> defer (no output)", failures)
    _check(_run_hook("ls -la") is None, "ls -la -> defer (no output)", failures)

    result = subprocess.run(
        [sys.executable, _GUARD],
        input=json.dumps({"tool_name": "Edit", "tool_input": {"new_text": "rm -rf /"}}),
        capture_output=True,
        text=True,
        check=False,
    )
    _check(
        result.returncode == 0 and not result.stdout.strip(),
        "non-Bash tool -> defer",
        failures,
    )

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
