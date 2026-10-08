"""
Context for the model judge: what the user asked for, what the agent just
ran, and what the command will execute from disk.

A command is only "off task" or "suspicious" relative to the task. shellkeeper's
model card shows the difference: red-team AUC 0.953 with the user's task and
recent commands in the prompt, 0.618 without. This module assembles that
context from what a hook can see:

  * the latest prompt the user typed, from the session transcript
    (Claude Code `transcript_path`) or a `user_prompt` field a host passes
    directly (the omp/pi extensions do);
  * the last few shell commands the agent ran in this session;
  * program text the command runs from disk (scripts, package.json scripts,
    Makefile recipes), resolved by shell_parse/shell_sources.

All of it is redacted with the same secret patterns as the command and
bounded in size. Reading never raises; anything missing is simply left out.
Set BASH_RISK_JUDGE_CONTEXT=0 to send the command alone, as before.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field

TRANSCRIPT_TAIL_BYTES = 512 * 1024
PROMPT_LIMIT = 1500
RECENT_COMMANDS = 5
RECENT_COMMAND_LIMIT = 300
SOURCE_LIMIT = 8000

_NOT_TYPED = ("[Request interrupted", "<command-", "<local-command", "<system-reminder", "Caveat:")


@dataclass
class JudgeContext:
    task: str | None = None
    recent_commands: list[str] = field(default_factory=list)
    # (label, text) for program text the command runs from files.
    sources: list[tuple[str, str]] = field(default_factory=list)
    # True when some program text was cut to fit; the model saw only part of it.
    truncated: bool = False


def _text_of(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(t for t in texts if t) or None
    return None


def _tail_lines(path: str) -> list[str]:
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
        data = handle.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[1:] if size > TRANSCRIPT_TAIL_BYTES else lines


def read_transcript(path: str) -> tuple[str | None, list[str]]:
    """(latest typed user prompt, recent Bash commands) from a Claude Code transcript."""
    prompt: str | None = None
    last_prompt_record: str | None = None
    commands: list[str] = []
    try:
        lines = _tail_lines(os.path.expanduser(path))
    except OSError:
        return None, []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        kind = record.get("type")
        if kind == "last-prompt" and isinstance(record.get("lastPrompt"), str):
            last_prompt_record = record["lastPrompt"]
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        if kind == "user" and not record.get("isMeta") and not record.get("isSidechain"):
            text = _text_of(message.get("content"))
            if text and not text.lstrip().startswith(_NOT_TYPED):
                prompt = text
        elif kind == "assistant" and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                    command = (block.get("input") or {}).get("command")
                    if isinstance(command, str):
                        commands.append(command)
    return prompt or last_prompt_record, commands[-RECENT_COMMANDS:]


def _clip(text: str, limit: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit] + "\n[... truncated]", True)


def gather(
    data: dict,
    command: str,
    indirect_sources: list[tuple[str, str]],
    redact: Callable[[str], str],
) -> JudgeContext:
    """Assemble redacted, size-bounded context for one Bash call."""
    context = JudgeContext()
    if os.environ.get("BASH_RISK_JUDGE_CONTEXT", "1").strip().lower() in ("0", "false", "no", "off"):
        return context

    prompt = data.get("user_prompt") if isinstance(data.get("user_prompt"), str) else None
    recent: list[str] = []
    transcript = data.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        from_transcript, recent = read_transcript(transcript)
        prompt = prompt or from_transcript
    if prompt:
        context.task = redact(_clip(prompt.strip(), PROMPT_LIMIT)[0])
    # The command being judged is usually the newest tool_use already; skip it.
    recent = [c for c in recent if c.strip() != command.strip()]
    context.recent_commands = [redact(_clip(c, RECENT_COMMAND_LIMIT)[0]) for c in recent]

    budget = SOURCE_LIMIT
    for label, text in indirect_sources:
        if budget <= 0:
            context.truncated = True
            break
        clipped, cut = _clip(text, budget)
        context.truncated = context.truncated or cut
        context.sources.append((label, redact(clipped)))
        budget -= len(clipped)
    return context


def render(context: JudgeContext, cwd: str, command: str) -> str:
    """The state text sent to the model."""
    parts: list[str] = []
    if context.task:
        parts.append(f"user's latest request:\n{context.task}\n")
    if context.recent_commands:
        parts.append("commands the agent ran just before this one:\n"
                     + "\n".join(f"$ {c}" for c in context.recent_commands) + "\n")
    parts.append(f"cwd: {cwd}\ncommand:\n{command}\n")
    for label, text in context.sources:
        parts.append(f"this command executes ({label}):\n{text}\n")
    return "\n".join(parts)
