"""
Bash parameter-expansion operators applied to a value the parser knows.

`X=rm; ${X:0:2} -rf v` runs `rm`; `P=~/.claude/x.bak; > "${P%.bak}"` writes
`~/.claude/x`. shell_parse resolves the variable; this module applies the
operator: substring, length, prefix/suffix removal, replacement and case
modification. Patterns are shell globs, matched as bash does (shell_glob).
"""

from __future__ import annotations

import re

import shell_glob
from shell_lexer import lex

_SUBSTRING = re.compile(r":\s*(-?\d+)\s*(?::\s*(-?\d+)\s*)?")


def apply(value: str, operator: str, extglob: bool = False) -> str | None:
    """`${NAME<operator>}` when NAME holds `value`; None if `operator` is not one
    whose result can be computed (indirection, arrays, `@P`/`@A`/`@K`/`@a`, an
    extglob `!(...)` pattern). `extglob`: `shopt -s extglob` is on."""
    with shell_glob.extglob(extglob):
        try:
            return _apply(value, operator)
        except shell_glob.Unsupported:
            return None


def _apply(value: str, operator: str) -> str | None:
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
        return next((value[end:] for end in ends if shell_glob.match(value[:end], pattern)), value)
    return remove


def _remove_suffix(longest: bool):
    def remove(value: str, pattern: str) -> str:
        starts = range(len(value) + 1) if longest else range(len(value), -1, -1)
        return next((value[:start] for start in starts if shell_glob.match(value[start:], pattern)), value)
    return remove


def _first_match(value: str, pattern: str, start: int) -> tuple[int, int] | None:
    """Leftmost, then longest, match of `pattern` at or after `start`."""
    for left in range(start, len(value) + 1):
        for right in range(len(value), left - 1, -1):
            if right > left and shell_glob.match(value[left:right], pattern):
                return left, right
    return None


def _substituted(replacement: str, matched: str) -> str:
    """bash 5.2's `patsub_replacement` (on by default): an unescaped, unquoted `&`
    in the replacement stands for the matched text; `\\&` and `'&'` are a literal `&`."""
    out, index = [], 0
    while index < len(replacement):
        char = replacement[index]
        if char in "'\"":
            # Quoted text is literal, `&` included; the quotes are removed.
            close = replacement.find(char, index + 1)
            if close < 0:
                close = len(replacement)
            out.append(replacement[index + 1:close])
            index = close + 1
        elif replacement.startswith("\\&", index):
            out.append("&")
            index += 2
        elif char == "&":
            out.append(matched)
            index += 1
        else:
            out.append(replacement[index])
            index += 1
    return "".join(out)


def _replace(every: bool):
    def replace(value: str, spec: str) -> str:
        pattern, _, replacement = spec.partition("/")
        if pattern.startswith("#"):
            matched = _anchored(value, pattern[1:], at_start=True)
            return value if matched is None else _substituted(replacement, matched) + value[len(matched):]
        if pattern.startswith("%"):
            matched = _anchored(value, pattern[1:], at_start=False)
            return value if matched is None else value[:len(value) - len(matched)] + _substituted(replacement, matched)
        out, index = [], 0
        while (found := _first_match(value, pattern, index)) is not None:
            left, right = found
            out += [value[index:left], _substituted(replacement, value[left:right])]
            index = right
            if not every:
                break
        return "".join(out) + value[index:]
    return replace


def _anchored(value: str, pattern: str, at_start: bool) -> str | None:
    """The longest part of `value` matching `pattern` at its start (or end)."""
    for size in range(len(value), -1, -1):
        part = value[:size] if at_start else value[len(value) - size:]
        if shell_glob.match(part, pattern):
            return part
    return None


def _case(upper: bool, every: bool):
    """`${X^^P}` / `${X,,P}` change the case of each character matching the
    pattern P (any character when P is empty); `${X^P}` / `${X,P}` only the first."""
    def change(value: str, pattern: str) -> str:
        pattern = pattern or "?"
        convert = str.upper if upper else str.lower
        return "".join(convert(char) if (every or index == 0) and shell_glob.match(char, pattern) else char
                       for index, char in enumerate(value))
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
