#!/usr/bin/env python3
"""
Claude Code Hook: Secrets Guard
===============================
PreToolUse hook for Read, Edit, MultiEdit, Write, NotebookEdit, Grep, Glob and
Bash. Blocks access to credential files and to shell commands that would read,
mutate, or exfiltrate secrets.

Consolidates three earlier hooks:
  env_file_protection_hook.py  .env access patterns in Bash
  read_env_protection_hook.py  .env paths in Read
  protect-secrets.js           tiered secret files + Bash exfiltration (ported)

Generated files that must not be hand-edited (lockfiles, .git/ internals) are a
separate category and live in file_protection.py.

Policy lives in secrets_guard_rules.py, tiered so coverage can be dialled:
  critical  SSH keys, cloud credentials, .env files
  high      + secrets files, env dumps, exfiltration attempts   <- default
  strict    + database configs and anything that might hold a secret

Decision protocol: https://code.claude.com/docs/en/hooks#pretooluse-decision-control
A match emits `permissionDecision: "deny"` (or `"ask"`, per level). A non-match
emits `{}` (defer) — never `"allow"`, which would auto-approve every unrelated
tool call and silently defeat the user's own permission rules.

Configuration (environment variables, set in the hook command):
  SECRETS_GUARD_LEVEL=critical|high|strict   enforcement tier (default: high)
  SECRETS_GUARD_DISABLE=1                    turn the hook off entirely
  SECRETS_GUARD_LOG_DIR=<dir>                default ~/.claude/hooks-logs
  HOOK_ASK_CRITICAL / HOOK_ASK_HIGH / HOOK_ASK_STRICT = true
      prompt the user for that tier instead of denying outright

Wire-up (~/.claude/settings.json):
  {
    "matcher": "Read|Edit|MultiEdit|Write|NotebookEdit|Grep|Glob|Bash",
    "hooks": [{ "type": "command",
                "command": "python3 ~/.claude/hooks/secrets_guard.py",
                "timeout": 10 }]
  }
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from hook_protocol import (  # noqa: E402
    READ_TOOLS,
    WRITE_TOOLS,
    Decision,
    action_verb,
    emit,
    env_flag,
    log_event,
    paths_from,
    read_input,
    tool_input,
    truncate,
)
from secrets_guard_rules import (  # noqa: E402
    ALLOWLIST,
    ALLOWLIST_IN_COMMAND,
    CRITICAL,
    EXEMPT_COMMAND_HEADS,
    HIGH,
    SECRET_COMMANDS,
    SECRET_FILES,
    STRICT,
    CommandRule,
    FileRule,
)

HOOK_NAME = "secrets-guard"

LEVEL_ORDER = {CRITICAL: 1, HIGH: 2, STRICT: 3}
LEVEL_MARKS = {CRITICAL: "🔐", HIGH: "🛡️", STRICT: "⚠️"}
DEFAULT_LEVEL = HIGH

HANDLED_TOOLS = READ_TOOLS | WRITE_TOOLS | {"Bash"}

# Statement separators only. A single `|` is deliberately NOT a separator: a
# pipeline is one data flow, and several rules (`cat .env | pbcopy`,
# `ls .env* | xargs cat`) must be able to see across it. Rules keep themselves
# inside a simple command via their own `[^;|&\n]*` fragments instead.
_COMMAND_SPLIT = re.compile(r"(?:\|\||&&|[;&\n])")

# A heredoc body is an argument, not a statement — it is what the command reads,
# not what the shell runs. Commit messages, docs and scripts routinely quote
# secret-shaped examples in one, and splitting the raw string on `;` mistakes
# those lines for commands.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1(.*?)^\s*\2\s*$", re.DOTALL | re.MULTILINE)

# ...except when the body is piped to something that executes it, where the
# heredoc really is a command list.
_INTERPRETER = re.compile(r"\b(?:bash|sh|zsh|ksh|dash|fish|eval|source|ssh)\b")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def safety_level() -> str:
    requested = os.environ.get("SECRETS_GUARD_LEVEL", "").strip().lower()
    return requested if requested in LEVEL_ORDER else DEFAULT_LEVEL


def asks_for(level: str) -> bool:
    """Whether a tier should prompt the user rather than deny outright."""
    return env_flag(f"HOOK_ASK_{level.upper()}")


def _in_scope(level: str, threshold: str) -> bool:
    return LEVEL_ORDER[level] <= LEVEL_ORDER[threshold]


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def is_allowlisted(path: str) -> bool:
    """True for templates and examples that never hold real secrets."""
    return bool(path) and bool(ALLOWLIST.search(path))


def check_path(path: str, threshold: str) -> FileRule | None:
    """Return the first rule that forbids touching `path`."""
    if not path or is_allowlisted(path):
        return None
    normalized = path.replace(os.sep, "/")
    for rule in SECRET_FILES:
        if _in_scope(rule.level, threshold) and rule.pattern.search(normalized):
            return rule
    return None


def _strip_heredocs(command: str) -> str:
    """Replace heredoc bodies with a placeholder, unless a shell would run them."""
    if _INTERPRETER.search(command):
        return command
    return _HEREDOC.sub("<<heredoc", command)


def _neutralize(command: str) -> str:
    """Strip the parts of a command that cannot possibly leak a secret.

    Allowlisted filenames are replaced in place rather than used to wave the
    whole command through, so `cat .env.example; cat .env` still trips on the
    second half. Heredoc bodies are dropped as data, and segments headed by a
    text-only command (`git commit -m "…"`) are dropped entirely.

    The result is one statement per line, so rules anchored with `^`/`$` see
    statement boundaries (the rule patterns are compiled MULTILINE).
    """
    scrubbed = ALLOWLIST_IN_COMMAND.sub("<allowlisted>", _strip_heredocs(command))
    kept = [
        stripped
        for segment in _COMMAND_SPLIT.split(scrubbed)
        if (stripped := segment.strip()) and not EXEMPT_COMMAND_HEADS.match(stripped)
    ]
    return "\n".join(kept)


def check_command(command: str, threshold: str) -> CommandRule | None:
    """Return the first rule that forbids running `command`."""
    if not command:
        return None
    candidate = _neutralize(command)
    if not candidate.strip():
        return None
    for rule in SECRET_COMMANDS:
        if _in_scope(rule.level, threshold) and rule.pattern.search(candidate):
            return rule
    return None


def evaluate(
    tool_name: str, inputs: dict, threshold: str
) -> tuple[FileRule | CommandRule, str] | None:
    """Match a tool call against the rules. Returns (rule, target) or None."""
    if tool_name == "Bash":
        command = inputs.get("command", "")
        rule = check_command(command, threshold)
        return (rule, command) if rule else None

    for path in paths_from(inputs):
        rule = check_path(path, threshold)
        if rule:
            return rule, path
    return None


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def build_reason(rule: FileRule | CommandRule, tool_name: str, target: str) -> str:
    lines = [
        f"{LEVEL_MARKS[rule.level]} BLOCKED by secrets-guard [{rule.rule_id}] — "
        f"cannot {action_verb(tool_name)}: {rule.reason}",
        f"    target: {truncate(target)}",
        "",
    ]
    lines.append(
        rule.remediation
        or (
            "Do not work around this by another route (a different tool, a shell "
            "redirect, a Python one-liner). If you genuinely need this, explain why "
            "and let the user run it themselves with `! <command>`."
        )
    )
    return "\n".join(lines)


def run(data: dict) -> Decision | None:
    """Return the secrets policy decision for one tool call."""
    if env_flag("SECRETS_GUARD_DISABLE"):
        return None

    tool_name = data.get("tool_name", "")
    if tool_name not in HANDLED_TOOLS:
        return None

    threshold = safety_level()
    finding = evaluate(tool_name, tool_input(data), threshold)
    if not finding:
        return None

    rule, target = finding
    decision = "ask" if asks_for(rule.level) else "deny"
    log_event(
        HOOK_NAME,
        {
            "outcome": decision,
            "rule": rule.rule_id,
            "level": rule.level,
            "tool": tool_name,
            "target": target[:200],
            "threshold": threshold,
            "session_id": data.get("session_id"),
            "cwd": data.get("cwd"),
            "permission_mode": data.get("permission_mode"),
        },
        log_dir_env=("SECRETS_GUARD_LOG_DIR",),
    )
    return Decision(
        decision,
        build_reason(rule, tool_name, target),
        f"secrets-guard [{rule.rule_id}]: {rule.reason}",
        HOOK_NAME,
    )


def main() -> None:
    data = read_input(HOOK_NAME)
    try:
        emit(run(data))
    except re.error as error:  # a malformed rule must not block real work
        log_event(
            HOOK_NAME,
            {
                "outcome": "rule-error",
                "error": str(error),
                "tool": data.get("tool_name", ""),
            },
        )
        print(f"secrets_guard: rule error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
