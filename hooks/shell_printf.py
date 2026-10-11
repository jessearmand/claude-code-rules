"""
The exact text `echo` and `printf` print, for the parser's literal-output cases.

`printf '\\162\\155'` prints `rm`, `printf %b '\\x72m'` prints `rm`, and
`echo -n r` prints `r` with no newline; files built from such output by
`>`/`>>` and the substitutions `$(printf ...)` hold exactly these bytes.
Formats the parser does not model (`%5.2f`, `%q`) give None: unknown.
"""

from __future__ import annotations

import re

_CONVERSION = re.compile(r"%[-+ #0]*\d*(?:\.\d+)?([sbdiuxXoc])")
_SIMPLE_ESCAPES = {"\\": "\\", "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f",
                   "v": "\v", '"': '"', "'": "'", "e": "\x1b", "E": "\x1b"}


def printed_text(name: str, args: tuple[str, ...] | list[str], xpg_echo: bool = False) -> str | None:
    """What `name args...` writes to stdout, newlines included; None if unknown.
    `xpg_echo` (bash's `shopt -s xpg_echo`, and sh/dash): echo expands escapes
    unless told `-E`."""
    if name == "echo":
        return _echo(list(args), xpg_echo)
    if name == "printf":
        return _printf(list(args))
    return None


def _echo(args: list[str], xpg_echo: bool = False) -> str:
    newline, escapes = True, xpg_echo
    while args and re.fullmatch(r"-[neE]+", args[0]):
        newline = newline and "n" not in args[0]
        escapes = ("e" in args[0]) if ("e" in args[0] or "E" in args[0]) else escapes
        args = args[1:]
    text = " ".join(args)
    if escapes:
        text, stop = _unescape(text, mode="echo")
        if stop:
            return text  # `\c`: print nothing more, not even the newline
    return text + ("\n" if newline else "")


def _printf(args: list[str]) -> str | None:
    if not args:
        return ""
    fmt, values = args[0], list(args[1:])
    out: list[str] = []
    while True:
        consumed = False
        index = 0
        while index < len(fmt):
            char = fmt[index]
            if char == "\\":
                text, index = _escape(fmt, index, mode="format")
                out.append(text)
            elif fmt.startswith("%%", index):
                out.append("%")
                index += 2
            elif char == "%":
                match = _CONVERSION.match(fmt, index)
                if not match:
                    return None
                value = values.pop(0) if values else ""
                consumed = True
                if match.group(1) == "b":
                    value, stop = _unescape(value, mode="b")
                    out.append(value)
                    if stop:
                        return "".join(out)
                else:
                    out.append(value)
                index = match.end()
            else:
                out.append(char)
                index += 1
        # The format is reused while arguments remain, as in bash.
        if not values or not consumed:
            return "".join(out)


def _unescape(text: str, mode: str) -> tuple[str, bool]:
    """Expand backslash escapes in `text`; also whether `\\c` stopped the output."""
    out: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("\\c", index):
            return "".join(out), True
        if text[index] == "\\":
            piece, index = _escape(text, index, mode)
            out.append(piece)
        else:
            out.append(text[index])
            index += 1
    return "".join(out), False


_OCTAL = {"format": re.compile(r"()([0-7]{1,3})"), "echo": re.compile(r"(0)([0-7]{0,3})"),
          "b": re.compile(r"(0)([0-7]{0,3})|()([0-7]{1,3})")}


def _escape(text: str, index: int, mode: str) -> tuple[str, int]:
    """The character the escape at text[index] stands for, and where it ends.
    printf's format takes `\\NNN`, `echo -e` takes `\\0NNN`, and `%b` takes both."""
    following = text[index + 1:index + 2]
    if following in _SIMPLE_ESCAPES:
        return _SIMPLE_ESCAPES[following], index + 2
    if following == "x":
        digits = re.match(r"[0-9a-fA-F]{1,2}", text[index + 2:])
        if digits:
            return chr(int(digits.group(0), 16)), index + 2 + len(digits.group(0))
    octal = _OCTAL[mode].match(text, index + 1)
    if octal:
        digits = next((g for g in octal.groups()[1::2] if g is not None), "")
        return chr(int(digits or "0", 8) & 0xFF), octal.end()
    return "\\" + following, index + 1 + len(following)  # not an escape: kept as written
