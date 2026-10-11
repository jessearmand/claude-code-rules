"""Bash pattern matching, as in `${X#pattern}`, `${X/pattern/s}` and `${X^^pattern}`.

Python's fnmatch differs from bash: it has no POSIX character classes
(`[[:upper:]]`), treats `^` as a literal at the start of a bracket expression,
and does not honour backslash escapes. This converts a bash pattern to a
regular expression instead: `*`, `?`, bracket expressions with `!`/`^`
negation, ranges, `[:class:]` names, and `\\x` escapes. extglob is off in
non-interactive bash, so `+(...)` and the like are literal text.
"""

from __future__ import annotations

import re
from functools import lru_cache

_CLASSES = {
    "alnum": "a-zA-Z0-9", "alpha": "a-zA-Z", "ascii": "\\x00-\\x7f", "blank": " \\t",
    "cntrl": "\\x00-\\x1f\\x7f", "digit": "0-9", "graph": "!-~", "lower": "a-z",
    "print": " -~", "punct": "!-/:-@\\[-`{-~", "space": " \\t\\n\\r\\f\\v", "upper": "A-Z",
    "word": "a-zA-Z0-9_", "xdigit": "0-9A-Fa-f",
}


def match(text: str, pattern: str) -> bool:
    """Whether all of `text` matches the bash pattern `pattern`."""
    return _compiled(pattern).fullmatch(text) is not None


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
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
    return re.compile("".join(out), re.DOTALL)


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
