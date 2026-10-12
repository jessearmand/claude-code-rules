"""
GNU Make expansion, as far as the guards need it: which shell commands a
Makefile runs besides the literal recipe text.

  * Variables. A recipe line `$(CLEAN)` runs whatever CLEAN holds, so recipes
    are expanded through the Makefile's variable table (`=`, `:=`, `::=`,
    `:::=`, `?=`, `+=`, with `override`/`export`) before they are parsed.
  * `$(shell CMD)` / `${shell CMD}`. Make runs CMD itself, at parse time for
    immediate assignments and top-level text, or when a recipe is expanded.
    Inside a recipe it becomes the shell substitution `$(CMD)`, so the parser
    sees CMD run. Anywhere else it is returned on its own by `parse_time_commands`.
  * `VAR != CMD`. A shell assignment: CMD runs when the Makefile is read.

Everything here is text in, text out: no file access. It errs toward running
too much: every `$(shell …)` outside a recipe counts, even in a variable that
is never used, because the guards only ask about Makefile text.
"""

from __future__ import annotations

import re

MAX_DEPTH = 8

_ASSIGNMENT = re.compile(
    r"^(?:(?:override|export|private)\s+)*([A-Za-z_][\w.-]*)\s*(:::=|::=|:=|\?=|\+=|!=|=)\s*(.*)$"
)
_SHELL_CALL = re.compile(r"\$([({])shell\s")
_CLOSE = {"(": ")", "{": "}"}


def _logical_lines(text: str) -> list[str]:
    return text.replace("\\\n", " ").split("\n")


def variables(text: str) -> dict[str, str]:
    """name -> value for the Makefile's global variable assignments.

    Values stay unexpanded (as for `=`); `expand` resolves references when the
    value is used. A `!=` value is command output, which is unknown here.
    """
    table: dict[str, str] = {}
    for line in _logical_lines(text):
        if line.startswith("\t"):
            continue
        match = _ASSIGNMENT.match(line.strip())
        if not match:
            continue
        name, operator, value = match.groups()
        value = re.split(r"(?<!\\)#", value, maxsplit=1)[0].strip()
        if operator == "?=":
            table.setdefault(name, value)
        elif operator == "+=":
            table[name] = f"{table[name]} {value}".strip() if name in table else value
        elif operator == "!=":
            table[name] = ""
        else:
            table[name] = value
    return table


def _reference_end(text: str, start: int) -> int:
    """Index just past the `$(…)`/`${…}` reference whose `$` is at `start`, or -1."""
    opening = text[start + 1]
    closing, depth = _CLOSE[opening], 0
    for index in range(start + 1, len(text)):
        char = text[index]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def expand(text: str, table: dict[str, str], depth: int = 0) -> str:
    """`text` with `$(NAME)`/`${NAME}` replaced from `table` and `$(shell CMD)`
    turned into the shell substitution `$(CMD)`. `$$`, automatic variables,
    unknown names and other Make functions are left as written."""
    if depth > MAX_DEPTH or "$" not in text:
        return text
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "$" or index + 1 >= len(text):
            out.append(char)
            index += 1
            continue
        following = text[index + 1]
        if following == "$":
            out.append("$$")
            index += 2
            continue
        if following not in _CLOSE:
            out.append(char)
            index += 1
            continue
        end = _reference_end(text, index)
        if end < 0:
            out.append(text[index:])
            break
        inner = text[index + 2:end - 1]
        if _SHELL_CALL.match(text, index):
            command = expand(inner[len("shell"):].strip(), table, depth + 1)
            out.append(f"$({command})")
        elif inner in table:
            out.append(expand(table[inner], table, depth + 1))
        else:
            out.append(text[index:end])
        index = end
    return "".join(out)


def unescape(text: str) -> str:
    """What Make hands the shell for already-expanded text: `$$` becomes `$`."""
    return text.replace("$$", "$")


def parse_time_commands(text: str) -> list[str]:
    """Shell commands Make runs while reading the Makefile, outside any recipe:
    `$(shell …)` calls in assignments, conditionals or top-level functions, and
    the right-hand side of `VAR != …`. Each is expanded and unescaped."""
    table = variables(text)
    commands: list[str] = []
    for line in _logical_lines(text):
        if line.startswith("\t") or line.lstrip().startswith("#"):
            continue
        assignment = _ASSIGNMENT.match(line.strip())
        if assignment and assignment.group(2) == "!=":
            commands.append(unescape(expand(assignment.group(3), table)))
        for match in _SHELL_CALL.finditer(line):
            if _escaped(line, match.start()):
                continue  # `$$(shell …)` is a literal for the shell, not a Make call
            end = _reference_end(line, match.start())
            inner = line[match.end():end - 1 if end > 0 else len(line)]
            commands.append(unescape(expand(inner.strip(), table)))
    return [command for command in commands if command.strip()]


def _escaped(line: str, dollar: int) -> bool:
    """True when the `$` at `dollar` is the second half of a `$$` escape."""
    run = 0
    while dollar - run - 1 >= 0 and line[dollar - run - 1] == "$":
        run += 1
    return run % 2 == 1
