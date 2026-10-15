"""
Text a command reads that the parser can know: a file written earlier on the
line or on disk (`< file`), and what a `<(producer)` prints when the producer
only echoes literal text or `cat`s known files. Part of shell_parse.
"""

from __future__ import annotations

import os

from shell_expand import command_output
from shell_model import (
    SHELL_OPTION,
    ParseResult,
    _Context,
)

_MAX_INPUT_FILE = 256 * 1024


def _input_file_text(path: str, ctx: _Context) -> str | None:
    """A file a command reads on stdin: its text from this line's writes, or from disk."""
    if ctx.cwd is None or path.startswith("<("):
        return None
    absolute = os.path.normpath(os.path.join(ctx.cwd, os.path.expanduser(path)))
    entry = ctx.written_files.get(absolute)
    if entry is not None:
        return entry.text
    try:
        if os.path.isfile(absolute) and os.path.getsize(absolute) <= _MAX_INPUT_FILE:
            with open(absolute, encoding="utf-8", errors="replace") as handle:
                return handle.read()
    except OSError:
        pass
    return None


def _produced_text(operand: str, ctx: _Context) -> str | None:
    """What `<(producer)` prints, when every command in it prints known text."""
    if not (operand.startswith("<(") and operand.endswith(")")):
        return None
    return _producer_output(parse_producer(operand, ctx.cwd, ctx), ctx)


def parse_producer(operand: str, cwd: str | None, ctx: _Context) -> ParseResult:
    """The commands of `<(producer)`. They run in this shell's process
    substitution, so its `shopt -s xpg_echo` applies to their echo."""
    from shell_parse import parse  # re-entry: parsed as a command list
    xpg = "on" in ctx.variables.get(SHELL_OPTION + "xpg_echo", ())
    return parse(("shopt -s xpg_echo\n" if xpg else "") + operand[2:-1], cwd or "/")


def _producer_output(producer: ParseResult, ctx: _Context) -> str | None:
    """The exact bytes a producer list writes, one command's output straight
    after the other's (`printf r; printf m` gives `rm`): echo/printf of literal
    text and `cat` of files whose text is known. None if any part is unknown."""
    if not producer.commands or producer.unparseable or producer.truncated:
        return None
    parts: list[str] = []
    for command in producer.commands:
        if command.name == "shopt":
            continue  # the shell options the producer starts with
        if command.via or command.dynamic_head or command.redirects or any(
                "$" in a or "`" in a for a in command.args):
            return None
        if command.name in ("echo", "printf"):
            part = command_output(command)
        elif command.name == "cat" and command.args and not any(a.startswith("-") for a in command.args):
            texts = [_input_file_text(path, ctx) for path in command.args]
            part = None if any(t is None for t in texts) else "".join(texts)
        else:
            return None
        if part is None:
            return None
        parts.append(part)
    return "".join(parts)
