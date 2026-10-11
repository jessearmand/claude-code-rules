#!/usr/bin/env python3
"""
Claude Code Hook: Dangerous Commands
====================================
PreToolUse hook for Bash. Denies (or asks about) commands that destroy data,
rewrite history, run downloaded code, weaken security, publish packages or
set up persistence. It complements rm_rf_guard (recursive forced rm),
secrets_guard (credentials) and config_guard (the agent's own config).

Commands are parsed structurally (shell_parse), so `sudo -u x`, `bash -c`,
`eval`, `ssh host`, `$(...)`, `python -c "os.system(...)"`, heredocs fed
to a shell and scripts the command runs are all seen through. Rules live in
dangerous_system.py, dangerous_git.py and dangerous_infra.py.

Decision policy, in line with the other deterministic guards:
  * a rule's default is deny, or ask for commands that are often legitimate;
  * a match found only inside a file the command runs (script, package.json
    script, Makefile recipe) is asked about, never denied;
  * configuration can soften a deny to ask, never an ask to nothing.

Configuration (environment variables, set in the hook command):
  DANGEROUS_COMMANDS_LEVEL=critical|high|strict   tier (default: high)
  DANGEROUS_COMMANDS_DISABLE=1                    turn the hook off
  DANGEROUS_COMMANDS_ASK=1                        ask instead of deny, every tier
  HOOK_ASK_CRITICAL / HOOK_ASK_HIGH / HOOK_ASK_STRICT = true
      ask instead of deny for that tier (shared with secrets_guard)
  DANGEROUS_COMMANDS_LOG_DIR=<dir>                default ~/.claude/hooks-logs

Wire-up (~/.claude/settings.json):
  { "matcher": "Bash",
    "hooks": [{ "type": "command",
                "command": "python3 ~/.claude/hooks/dangerous_commands.py",
                "timeout": 10 }] }
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import shell_sources  # noqa: E402
from danger_rule import LEVEL_ORDER, DangerRule, RuleContext  # noqa: E402
from dangerous_git import GIT_RULES  # noqa: E402
from dangerous_infra import INFRA_RULES  # noqa: E402
from dangerous_system import SYSTEM_RULES  # noqa: E402
from hook_protocol import (  # noqa: E402
    Decision,
    emit,
    env_flag,
    for_permission_mode,
    log_event,
    read_input,
    tool_input,
    truncate,
)
from shell_parse import SimpleCommand, parse  # noqa: E402

HOOK_NAME = "dangerous-commands"
DEFAULT_LEVEL = "high"
MARKS = {"critical": "☠️", "high": "⛔", "strict": "⚠️"}

# Order is reporting order: the first deny found wins over any ask.
RULES: tuple[DangerRule, ...] = SYSTEM_RULES + GIT_RULES + INFRA_RULES


@dataclass(frozen=True)
class Finding:
    rule: DangerRule
    command: SimpleCommand
    detail: str
    decision: str


def safety_level() -> str:
    requested = os.environ.get("DANGEROUS_COMMANDS_LEVEL", "").strip().lower()
    return requested if requested in LEVEL_ORDER else DEFAULT_LEVEL


def _decision_for(rule: DangerRule, command: SimpleCommand) -> str:
    if rule.decision == "ask" or command.indirect:
        return "ask"
    if env_flag("DANGEROUS_COMMANDS_ASK") or env_flag(f"HOOK_ASK_{rule.level.upper()}"):
        return "ask"
    return "deny"


def find_dangers(command: str, cwd: str, level: str = DEFAULT_LEVEL) -> list[Finding]:
    """Every rule hit in `command`, denies first."""
    parsed = parse(command, cwd, shell_sources.resolve)
    findings: list[Finding] = []
    for simple in parsed.commands:
        # Rules resolve relative paths where the command might run: after any
        # `cd`, or as if it failed. An unknown directory is judged as `/`.
        contexts = [RuleContext(parsed, directory or os.sep)
                    for directory in dict.fromkeys(simple.possible_cwds)]
        for rule, context in ((rule, context) for rule in RULES for context in contexts):
            if LEVEL_ORDER[rule.level] > LEVEL_ORDER[level]:
                continue
            try:
                detail = rule.match(simple, context)
            except (IndexError, ValueError, AttributeError):
                detail = None  # a rule bug must not break the hook
            if detail:
                findings.append(Finding(rule, simple, detail, _decision_for(rule, simple)))
    findings.sort(key=lambda f: (f.decision != "deny", LEVEL_ORDER[f.rule.level]))
    return findings


def build_reason(finding: Finding) -> str:
    rule, command = finding.rule, finding.command
    verb = "BLOCKED" if finding.decision == "deny" else "Confirm with the user"
    lines = [
        f"{MARKS[rule.level]} {verb} — dangerous-commands [{rule.rule_id}]: {rule.reason}.",
        f"    matched: {truncate(finding.detail)}",
        f"    command: {truncate(command.rendered())}",
    ]
    if command.via:
        lines.append(f"    reached via: {command.origin()}")
    if command.indirect:
        lines.append("    (inside a file this command runs, not typed in this call)")
    lines += ["", rule.remediation]
    if finding.decision == "deny":
        lines += [
            "",
            "Do not retry it another way (a wrapper, a script, an interpreter one-liner). If the "
            "task really needs it, explain why and let the user run it with `! <command>`.",
        ]
    return "\n".join(lines)


def run(data: dict) -> Decision | None:
    if env_flag("DANGEROUS_COMMANDS_DISABLE") or data.get("tool_name") != "Bash":
        return None
    command = tool_input(data).get("command", "")
    if not isinstance(command, str) or not command.strip():
        return None

    cwd = os.path.normpath(data.get("cwd") or os.getcwd())
    level = safety_level()
    findings = find_dangers(command, cwd, level)
    if not findings:
        return None

    top = findings[0]
    log_event(
        HOOK_NAME,
        {
            "outcome": top.decision,
            "rule": top.rule.rule_id,
            "level": top.rule.level,
            "detail": top.detail[:200],
            "indirect": top.command.indirect,
            "via": list(top.command.via),
            "also": sorted({f.rule.rule_id for f in findings[1:]}),
            "command": command[:300],
            "threshold": level,
            "session_id": data.get("session_id"),
            "cwd": data.get("cwd"),
            "permission_mode": data.get("permission_mode"),
        },
        log_dir_env=("DANGEROUS_COMMANDS_LOG_DIR",),
    )
    return Decision(
        top.decision,
        build_reason(top),
        f"dangerous-commands [{top.rule.rule_id}]: {top.rule.reason}",
        HOOK_NAME,
    )


def main() -> None:
    data = read_input(HOOK_NAME)
    emit(for_permission_mode(run(data), data))


if __name__ == "__main__":
    main()
