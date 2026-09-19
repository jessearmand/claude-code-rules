#!/usr/bin/env python3
"""
Shared PreToolUse plumbing for the hooks in this directory.

Encodes the decision protocol once so each hook only has to express its policy:
https://code.claude.com/docs/en/hooks#pretooluse-decision-control

The one rule worth stating explicitly: a hook that has no opinion must emit `{}`
(defer), never `permissionDecision: "allow"`. "allow" approves the call without
prompting, so a hook that returns it for non-matching input silently
auto-approves every unrelated tool call and defeats the user's own permission
settings.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

DEFAULT_LOG_DIR = os.path.join(os.path.expanduser("~"), ".claude", "hooks-logs")

# Tool inputs that can carry a path, across every tool these hooks match.
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

WRITE_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
READ_TOOLS = frozenset({"Read", "Grep", "Glob"})

ACTION_VERBS = {
    "Read": "read",
    "Grep": "search",
    "Glob": "list",
    "Edit": "modify",
    "MultiEdit": "modify",
    "Write": "write",
    "NotebookEdit": "modify",
    "Bash": "run",
}


@dataclass(frozen=True)
class Decision:
    """A hook verdict. None from run() means defer."""

    decision: str
    reason: str
    system_message: str | None = None
    hook: str = ""


def env_flag(name: str) -> bool:
    """True for 1/true/yes/on, case-insensitive."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def read_input(hook: str) -> dict:
    """Parse the hook payload from stdin. Exits 1 (non-blocking) on bad JSON."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        print(f"{hook}: invalid JSON input: {error}", file=sys.stderr)
        sys.exit(1)
    return data if isinstance(data, dict) else {}


def tool_input(data: dict) -> dict:
    value = data.get("tool_input")
    return value if isinstance(value, dict) else {}


def paths_from(inputs: dict) -> list[str]:
    """Every path-like value in a tool input, deduplicated, in key order."""
    found: list[str] = []
    for key in PATH_KEYS:
        value = inputs.get(key)
        if isinstance(value, str) and value and value not in found:
            found.append(value)
    return found


def action_verb(tool_name: str) -> str:
    return ACTION_VERBS.get(tool_name, "use")


def defer() -> None:
    """Emit no decision: the normal permission flow applies."""
    sys.stdout.write("{}\n")


def decide(decision: str, reason: str, system_message: str | None = None) -> None:
    """Emit a permission decision ("deny" or "ask") for a PreToolUse call."""
    payload: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    if system_message:
        payload["systemMessage"] = system_message
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def emit(result: Decision | None) -> None:
    """Emit a hook result using the Claude Code decision protocol."""
    if result is None:
        defer()
        return
    decide(result.decision, result.reason, result.system_message)


def log_event(hook: str, payload: dict, log_dir_env: tuple[str, ...] = ()) -> None:
    """Append one JSONL record. Never raises — logging must not break a hook."""
    directory = next(
        (
            os.environ[name]
            for name in (*log_dir_env, "HOOKS_LOG_DIR")
            if os.environ.get(name)
        ),
        DEFAULT_LOG_DIR,
    )
    try:
        os.makedirs(directory, exist_ok=True)
        now = datetime.now(timezone.utc)
        record = {"ts": now.isoformat(), "hook": hook, **payload}
        with open(
            os.path.join(directory, f"{now:%Y-%m-%d}.jsonl"), "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def truncate(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."
