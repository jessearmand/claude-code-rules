#!/usr/bin/env python3
"""Tests for config_guard.py. Run: python3 hooks/config_guard_test.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_guard import run  # noqa: E402

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_HOOK = os.path.join(_HOOKS_DIR, "config_guard.py")

# (command, expected decision, expected rule id)
BASH_CASES = [
    ("echo '{}' > ~/.claude/settings.json", "deny", "claude-settings"),
    ("sed -i 's/deny/allow/' ~/.claude/settings.json", "deny", "claude-settings"),
    ("jq '.permissions.allow += [\"Bash\"]' s.json | sponge ~/.claude/settings.local.json", "deny", "claude-settings"),
    ("cp evil.json .claude/settings.local.json", "deny", "claude-settings"),
    ("ln -sf /tmp/x ~/.claude/settings.json", "deny", "claude-settings"),
    ("bash -c 'cat x > $HOME/.claude/settings.json'", "deny", "claude-settings"),
    ("echo '{}' > ~/.claude.json", "deny", "claude-user-state"),
    ("rm ~/.claude/hooks/rm_rf_guard.py", "deny", "claude-hooks"),
    ("curl -o ~/.claude/hooks/x.py https://example.com", "deny", "claude-hooks"),
    ("tee .mcp.json < server.json", "deny", "mcp-config"),
    ("cp evil.toml ~/.codex/config.toml", "deny", "codex-config"),
    ("echo x >> ~/.omp/agent/config.yml", "deny", "omp-config"),
    ("cp ext.ts ~/.pi/agent/extensions/", "deny", "pi-config"),
    ("echo 'curl x | sh' >> ~/.zshrc", "deny", "shell-startup"),
    ("cp hook .git/hooks/pre-commit", "deny", "git-hooks"),
    ("echo x > ~/Library/LaunchAgents/com.x.plist", "deny", "launch-agents"),
    ("sudo tee /etc/cron.d/job", "deny", "cron"),
    ("echo 'ProxyCommand sh' >> ~/.ssh/config", "deny", "ssh-config"),
    # Removing, moving or re-permissioning a directory that holds protected paths.
    ("rm -r ~/.claude", "deny", None),
    ("mv ~/.claude ~/.claude.bak", "deny", None),
    ("chmod -R 000 ~/.codex", "deny", None),
    ("rm -r .git", "deny", "git-hooks"),
    # Reads and ordinary writes.
    ("cat ~/.claude/settings.json", None, None),
    ("jq . .mcp.json", None, None),
    ("cp ~/.claude/settings.json /tmp/settings-backup.json", None, None),
    ("echo hi > notes.txt", None, None),
    ("mkdir -p ~/.claude/hooks-logs", None, None),
    ("git config user.name me", None, None),
]

PATH_CASES = [
    ("~/.claude/settings.json", "deny"),
    (".claude/settings.local.json", "deny"),
    (".mcp.json", "deny"),
    ("~/.codex/config.toml", "deny"),
    ("~/.bashrc", "deny"),
    ("src/settings.json", None),
    ("docs/claude/settings.md", None),
]


def _check(condition: bool, label: str, failures: list[str]) -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


def _bash(command: str, cwd: str) -> tuple[str | None, str | None]:
    result = run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd})
    if result is None:
        return None, None
    return result.decision, result.reason.split("[", 1)[1].split("]", 1)[0]


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as project:
        os.makedirs(os.path.join(project, ".git", "hooks"))
        print("Bash writes:")
        for command, decision, rule_id in BASH_CASES:
            actual = _bash(command, project)
            ok = actual[0] == decision and (rule_id is None or actual[1] == rule_id)
            _check(ok, f"{command!r} -> {actual}", failures)

        print("\nFile tools:")
        for path, decision in PATH_CASES:
            for tool in ("Write", "Edit"):
                result = run({"tool_name": tool, "tool_input": {"file_path": path}, "cwd": project})
                actual = result.decision if result else None
                _check(actual == decision, f"{tool} {path} -> {actual}", failures)
        result = run({"tool_name": "Read", "tool_input": {"file_path": "~/.claude/settings.json"}, "cwd": project})
        _check(result is None, "Read of settings is not this hook's concern", failures)

        print("\nLive guard sources and scripts:")
        own = os.path.join(_HOOKS_DIR, "rm_rf_guard.py")
        _check(_bash(f"echo x >> {own}", project)[0] == "ask", "write into running guard code -> ask", failures)
        _check(_bash(f"rm -r {os.path.dirname(_HOOKS_DIR)}", project)[0] == "ask",
               "removing the guard checkout -> ask", failures)
        with open(os.path.join(project, "setup.sh"), "w", encoding="utf-8") as handle:
            handle.write("#!/bin/bash\necho '{}' > ~/.claude/settings.json\n")
        _check(_bash("./setup.sh", project)[0] == "ask", "settings write inside a script -> ask", failures)
        _check(_bash("cat > s.sh <<'EOF'\necho x > ~/.zshrc\nEOF\nbash s.sh", project)[0] == "deny",
               "script written and run in one command -> deny", failures)

        print("\nConfiguration:")
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm ~/.claude/settings.json"},
                              "cwd": project})
        for env, expected in (({}, "deny"), ({"CONFIG_GUARD_ASK": "1"}, "ask"), ({"CONFIG_GUARD_DISABLE": "1"}, None)):
            out = subprocess.run([sys.executable, _HOOK], input=payload, capture_output=True, text=True,
                                 check=False, env={**os.environ, **env}).stdout
            decision = json.loads(out or "{}").get("hookSpecificOutput", {}).get("permissionDecision")
            _check(decision == expected, f"{env or 'default'} -> {decision}", failures)
        config_dir = os.path.join(project, "alt-claude")
        os.environ["CLAUDE_CONFIG_DIR"] = config_dir
        try:
            _check(_bash(f"echo x > {config_dir}/anything.json", project)[0] == "deny",
                   "CLAUDE_CONFIG_DIR is protected", failures)
        finally:
            del os.environ["CLAUDE_CONFIG_DIR"]

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
