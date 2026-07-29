#!/usr/bin/env python3
"""Tests for file_protection.py. Run: python3 hooks/file_protection_test.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from file_protection import check_path  # noqa: E402

_HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file_protection.py")

# (path, expected rule_id or None)
PATH_CASES = [
    # --- inherited from the original file_protection.py ---
    ("package-lock.json", "npm-lock"),
    ("Cargo.lock", "cargo-lock"),
    ("bun.lock", "bun-lock"),
    ("bun.lockb", "bun-lock"),
    ("Package.resolved", "swiftpm-resolved"),
    (".git/HEAD", "git-internals"),
    (".git/hooks/pre-commit", "git-internals"),
    (".git/config", "git-internals"),
    # --- newly added ecosystems ---
    ("uv.lock", "uv-lock"),
    ("poetry.lock", "poetry-lock"),
    ("Pipfile.lock", "pipenv-lock"),
    ("pnpm-lock.yaml", "pnpm-lock"),
    ("yarn.lock", "yarn-lock"),
    ("deno.lock", "deno-lock"),
    ("Gemfile.lock", "bundler-lock"),
    ("gems.locked", "bundler-lock"),
    ("npm-shrinkwrap.json", "npm-lock"),
    ("composer.lock", "composer-lock"),
    ("go.sum", "go-sum"),
    ("pubspec.lock", "dart-lock"),
    ("flake.lock", "nix-flake-lock"),
    ("Podfile.lock", "cocoapods-lock"),
    ("gradle.lockfile", "gradle-lock"),
    ("gradle/verification-metadata.xml", "gradle-lock"),
    ("node_modules/left-pad/index.js", "node-modules"),
    # --- nested paths resolve the same way ---
    ("/Users/x/proj/apps/web/package-lock.json", "npm-lock"),
    ("crates/core/Cargo.lock", "cargo-lock"),
    ("backend/uv.lock", "uv-lock"),
    # --- manifests are the user's to edit and must stay writable ---
    ("package.json", None),
    ("Cargo.toml", None),
    ("pyproject.toml", None),
    ("Pipfile", None),
    ("Gemfile", None),
    ("composer.json", None),
    ("go.mod", None),
    ("pubspec.yaml", None),
    ("Podfile", None),
    ("deno.json", None),
    ("deno.jsonc", None),
    ("flake.nix", None),
    ("Package.swift", None),
    ("pnpm-workspace.yaml", None),
    ("build.gradle.kts", None),
    # --- lookalikes must not trip ---
    ("docs/package-lock.md", None),
    ("src/lockfile.rs", None),
    ("my-node_modules-notes.md", None),
    ("scripts/update-cargo-lock.sh", None),
    # --- secrets are NOT this hook's business ---
    (".env", None),
    (".env.local", None),
    ("id_rsa", None),
    ("/Users/x/.aws/credentials", None),
    # --- ordinary source files ---
    ("src/main.rs", None),
    ("README.md", None),
    ("hooks/file_protection.py", None),
]


def _run(payload: dict, env: dict[str, str] | None = None) -> tuple[int, dict | None, str]:
    result = subprocess.run(
        [sys.executable, _HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )
    parsed = json.loads(result.stdout) if result.stdout.strip() else None
    return result.returncode, parsed, result.stderr


def _check(condition: bool, label: str, failures: list[str]) -> None:
    if condition:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}")
        failures.append(label)


def main() -> int:
    failures: list[str] = []

    print("Paths:")
    for path, expected in PATH_CASES:
        rule = check_path(path)
        actual = rule.rule_id if rule else None
        _check(actual == expected, f"{path:<44} -> {actual}", failures)

    print("\nHook protocol:")
    log_dir = os.path.join(os.environ.get("TMPDIR", "/tmp"), "file-protection-test-logs")
    base_env = {"FILE_PROTECTION_LOG_DIR": log_dir}

    for tool in ("Write", "Edit", "MultiEdit"):
        code, out, _ = _run({"tool_name": tool, "tool_input": {"file_path": "uv.lock"}}, base_env)
        _check(
            code == 0 and out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny",
            f"{tool} uv.lock -> deny",
            failures,
        )

    _, out, _ = _run({"tool_name": "Write", "tool_input": {"file_path": "poetry.lock"}}, base_env)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"] if out else ""
    _check("poetry lock" in reason, "denial names the right regeneration command", failures)
    _check(out is not None and out["hookSpecificOutput"]["hookEventName"] == "PreToolUse",
           "emits hookEventName: PreToolUse", failures)

    _, out, _ = _run({"tool_name": "Write", "tool_input": {"file_path": "deno.lock"}}, base_env)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"] if out else ""
    _check("deno install" in reason, "deno.lock -> `deno install`", failures)

    # Reads must pass through: inspecting a resolved version is normal.
    for payload, label in [
        ({"tool_name": "Read", "tool_input": {"file_path": "package-lock.json"}}, "Read package-lock.json"),
        ({"tool_name": "Read", "tool_input": {"file_path": "Cargo.lock"}}, "Read Cargo.lock"),
        ({"tool_name": "Grep", "tool_input": {"path": "yarn.lock"}}, "Grep yarn.lock"),
        ({"tool_name": "Bash", "tool_input": {"command": "cat go.sum"}}, "Bash cat go.sum"),
        ({"tool_name": "Write", "tool_input": {"file_path": "Cargo.toml"}}, "Write Cargo.toml"),
        ({"tool_name": "Write", "tool_input": {"file_path": ".env"}}, "Write .env (secrets hook's job)"),
    ]:
        code, out, _ = _run(payload, base_env)
        _check(code == 0 and out == {}, f"{label} -> defer (never 'allow')", failures)

    _, out, _ = _run(
        {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "node_modules/x/a.ipynb"}}, base_env
    )
    _check(
        out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny",
        "NotebookEdit notebook_path is checked",
        failures,
    )

    _, out, _ = _run(
        {"tool_name": "Write", "tool_input": {"file_path": "uv.lock"}},
        {**base_env, "FILE_PROTECTION_ASK": "true"},
    )
    _check(
        out is not None and out["hookSpecificOutput"]["permissionDecision"] == "ask",
        "FILE_PROTECTION_ASK=true -> ask",
        failures,
    )

    code, out, _ = _run(
        {"tool_name": "Write", "tool_input": {"file_path": "uv.lock"}},
        {**base_env, "FILE_PROTECTION_DISABLE": "1"},
    )
    _check(code == 0 and out == {}, "FILE_PROTECTION_DISABLE=1 -> defer", failures)

    result = subprocess.run(
        [sys.executable, _HOOK], input="not json", capture_output=True, text=True, check=False
    )
    _check(result.returncode == 1 and "invalid JSON" in result.stderr, "bad stdin -> exit 1", failures)

    logged = os.path.isdir(log_dir) and any(n.endswith(".jsonl") for n in os.listdir(log_dir))
    _check(logged, "writes a JSONL audit log", failures)

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"All tests passed ({len(PATH_CASES)} path cases + protocol checks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
