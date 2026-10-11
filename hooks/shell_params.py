"""
Bash parameter-expansion operators applied to a value the parser knows.

`X=rm; ${X:0:2} -rf v` runs `rm`; `P=~/.claude/x.bak; > "${P%.bak}"` writes
`~/.claude/x`. shell_parse resolves the variable; this module applies the
operator: substring, length, prefix/suffix removal, replacement and case
modification. Patterns are shell globs, matched with fnmatch.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase

from shell_lexer import lex

_SUBSTRING = re.compile(r":\s*(-?\d+)\s*(?::\s*(-?\d+)\s*)?")


def apply(value: str, operator: str) -> str | None:
    """`${NAME<operator>}` when NAME holds `value`; None if `operator` is not one
    whose result can be computed (indirection, arrays, `@P`/`@A`/`@K`/`@a`)."""
    if operator.startswith("@"):
        return _transform(value, operator[1:])
    substring = _SUBSTRING.fullmatch(operator)
    if substring:
        return _substring(value, int(substring.group(1)), substring.group(2))
    for prefix, function in _OPERATORS:
        if operator.startswith(prefix):
            return function(value, operator[len(prefix):])
    return None


def _transform(value: str, letter: str) -> str | None:
    """`${X@E}` expands backslash escapes as `$'...'` does; `@U`, `@u`, `@L`
    change case; `@Q` quotes. The others depend on state not modelled."""
    if letter == "E":
        decoded = lex("$'" + value.replace("'", "\\'") + "'").tokens
        return decoded[0].text if len(decoded) == 1 else None
    if letter == "Q":
        return "'" + value.replace("'", "'\\''") + "'"
    return {"U": value.upper(), "u": value[:1].upper() + value[1:], "L": value.lower()}.get(letter)


def _substring(value: str, offset: int, length: str | None) -> str:
    start = max(len(value) + offset, 0) if offset < 0 else offset
    if length is None:
        return value[start:]
    count = int(length)
    end = len(value) + count if count < 0 else start + count
    return value[start:max(end, start)]


def _remove_prefix(longest: bool):
    def remove(value: str, pattern: str) -> str:
        ends = range(len(value), -1, -1) if longest else range(len(value) + 1)
        return next((value[end:] for end in ends if fnmatchcase(value[:end], pattern)), value)
    return remove


def _remove_suffix(longest: bool):
    def remove(value: str, pattern: str) -> str:
        starts = range(len(value) + 1) if longest else range(len(value), -1, -1)
        return next((value[:start] for start in starts if fnmatchcase(value[start:], pattern)), value)
    return remove


def _first_match(value: str, pattern: str, start: int) -> tuple[int, int] | None:
    """Leftmost, then longest, match of `pattern` at or after `start`."""
    for left in range(start, len(value) + 1):
        for right in range(len(value), left - 1, -1):
            if right > left and fnmatchcase(value[left:right], pattern):
                return left, right
    return None


def _replace(every: bool):
    def replace(value: str, spec: str) -> str:
        pattern, _, replacement = spec.partition("/")
        if pattern.startswith("#"):
            matched = _anchored(value, pattern[1:], at_start=True)
            return value if matched is None else replacement + value[len(matched):]
        if pattern.startswith("%"):
            matched = _anchored(value, pattern[1:], at_start=False)
            return value if matched is None else value[:len(value) - len(matched)] + replacement
        out, index = [], 0
        while (found := _first_match(value, pattern, index)) is not None:
            left, right = found
            out += [value[index:left], replacement]
            index = right
            if not every:
                break
        return "".join(out) + value[index:]
    return replace


def _anchored(value: str, pattern: str, at_start: bool) -> str | None:
    """The longest part of `value` matching `pattern` at its start (or end)."""
    for size in range(len(value), -1, -1):
        part = value[:size] if at_start else value[len(value) - size:]
        if fnmatchcase(part, pattern):
            return part
    return None


def _case(upper: bool, every: bool):
    def change(value: str, pattern: str) -> str:
        if pattern:
            return value  # only the pattern-free forms are modelled
        if not value:
            return value
        if every:
            return value.upper() if upper else value.lower()
        head = value[0].upper() if upper else value[0].lower()
        return head + value[1:]
    return change


# Longer prefixes first: `##` before `#`.
_OPERATORS = (
    ("##", _remove_prefix(longest=True)),
    ("#", _remove_prefix(longest=False)),
    ("%%", _remove_suffix(longest=True)),
    ("%", _remove_suffix(longest=False)),
    ("//", _replace(every=True)),
    ("/", _replace(every=False)),
    ("^^", _case(upper=True, every=True)),
    ("^", _case(upper=True, every=False)),
    (",,", _case(upper=False, every=True)),
    (",", _case(upper=False, every=False)),
)
