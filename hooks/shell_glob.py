"""Bash pattern matching, as in `${X#pattern}`, `${X/pattern/s}` and `${X^^pattern}`.

Python's fnmatch differs from bash: it has no POSIX character classes
(`[[:upper:]]`), treats `^` as a literal at the start of a bracket expression,
and does not honour backslash escapes. This converts a bash pattern to a
regular expression instead: `*`, `?`, bracket expressions with `!`/`^`
negation, ranges, `[:class:]` names, and `\\x` escapes. extglob is off in
non-interactive bash, so `+(...)` and the like are literal text unless the
caller says `shopt -s extglob` is on; then `?( )`, `*( )`, `+( )` and `@( )`
match, and `!( )` raises Unsupported.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

_CLASSES = {
    "alnum": "a-zA-Z0-9", "alpha": "a-zA-Z", "ascii": "\\x00-\\x7f", "blank": " \\t",
    "cntrl": "\\x00-\\x1f\\x7f", "digit": "0-9", "graph": "!-~", "lower": "a-z",
    "print": " -~", "punct": "!-/:-@\\[-`{-~", "space": " \\t\\n\\r\\f\\v", "upper": "A-Z",
    "word": "a-zA-Z0-9_", "xdigit": "0-9A-Fa-f",
}


class Unsupported(ValueError):
    """A pattern this module cannot match the way bash would (`!(...)`)."""


_EXTGLOB_ON: ContextVar[bool] = ContextVar("extglob", default=False)


@contextmanager
def extglob(enabled: bool) -> Iterator[None]:
    """Match with `shopt -s extglob` on (or off) inside this block."""
    token = _EXTGLOB_ON.set(enabled)
    try:
        yield
    finally:
        _EXTGLOB_ON.reset(token)


def match(text: str, pattern: str) -> bool:
    """Whether all of `text` matches the bash pattern `pattern`."""
    return _compiled(pattern, _EXTGLOB_ON.get()).fullmatch(text) is not None


@lru_cache(maxsize=512)
def _compiled(pattern: str, extglob: bool) -> re.Pattern[str]:
    return re.compile(_translate(pattern, extglob), re.DOTALL)


# `?(a|b)` zero or one, `*( )` any number, `+( )` one or more, `@( )` exactly one.
_EXTGLOB = {"?": "?", "*": "*", "+": "+", "@": ""}


def _translate(pattern: str, extglob: bool) -> str:
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if extglob and char in "?*+@!" and pattern.startswith("(", index + 1):
            close = _group_end(pattern, index + 1)
            if close is not None:
                if char == "!":
                    raise Unsupported(pattern)
                alternatives = _split_alternatives(pattern[index + 2:close])
                body = "|".join(_translate(alt, extglob) for alt in alternatives)
                out.append(f"(?:{body}){_EXTGLOB[char]}")
                index = close + 1
                continue
        if char == "\\" and index + 1 < len(pattern):
            out.append(re.escape(pattern[index + 1]))
            index += 2
        elif char == "*":
            out.append(".*")
            index += 1
        elif char == "?":
            out.append(".")
            index += 1
        elif char == "[":
            bracket = _bracket(pattern, index)
            if bracket is None:
                out.append("\\[")  # no closing `]`: a literal bracket
                index += 1
            else:
                expression, index = bracket
                out.append(expression)
        else:
            out.append(re.escape(char))
            index += 1
    return "".join(out)


def _group_end(pattern: str, open_index: int) -> int | None:
    """The index of the `)` closing the group that opens at `open_index`."""
    depth, index = 0, open_index
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0:
            return index
        index += 1
    return None


def _split_alternatives(body: str) -> list[str]:
    """`a|b(c|d)` -> [`a`, `b(c|d)`]: split on `|` outside nested groups."""
    parts, depth, start, index = [], 0, 0, 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            index += 2
            continue
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "|" and depth == 0:
            parts.append(body[start:index])
            start = index + 1
        index += 1
    return [*parts, body[start:]]


def _bracket(pattern: str, start: int) -> tuple[str, int] | None:
    """The regex for the bracket expression at `start`, and where it ends."""
    index = start + 1
    negate = index < len(pattern) and pattern[index] in "!^"
    if negate:
        index += 1
    parts: list[str] = []
    first = True
    while index < len(pattern):
        char = pattern[index]
        if char == "]" and not first:
            body = "".join(parts)
            return (f"[^{body}]" if negate else f"[{body}]"), index + 1
        first = False
        if pattern.startswith("[:", index):
            close = pattern.find(":]", index + 2)
            name = pattern[index + 2:close] if close > 0 else ""
            if name in _CLASSES:
                parts.append(_CLASSES[name])
                index = close + 2
                continue
        if char == "\\" and index + 1 < len(pattern):
            parts.append(re.escape(pattern[index + 1]))
            index += 2
            continue
        if char == "-" and parts and index + 1 < len(pattern) and pattern[index + 1] != "]":
            parts.append("-")
            index += 1
            continue
        parts.append(re.escape(char) if char not in "-" else "\\-")
        index += 1
    return None
