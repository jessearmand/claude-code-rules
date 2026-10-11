"""Bash integer arithmetic, as in array subscripts and `$((...))`.

Evaluates the operators bash's arithmetic supports, with its precedence and
64-bit wrapping: `X[1&1]`, `X[i<<1]`, `X[n?1:0]`. A variable named in the
expression is itself evaluated as arithmetic, as bash does. Anything not
modelled (an assignment, an unknown variable, a malformed expression) gives
None, so callers treat the value as unknown.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<number>0[xX][0-9a-fA-F]+|\d+#[0-9A-Za-z@_]+|\d+)"
    r"|(?P<name>\$?\{?[A-Za-z_][A-Za-z0-9_]*\}?)"
    r"|(?P<op>\*\*|<<|>>|<=|>=|==|!=|&&|\|\||\+\+|--|[-+*/%<>&|^!~?:(),=])"
    r")"
)
# Binary operators by precedence, loosest first (`?:` and `,` are handled apart).
_BINARY = (
    ("||",), ("&&",), ("|",), ("^",), ("&",), ("==", "!="), ("<", "<=", ">", ">="),
    ("<<", ">>"), ("+", "-"), ("*", "/", "%"),
)
_MAX_DEPTH = 8


class _Unsupported(Exception):
    pass


def evaluate(expression: str, lookup: Callable[[str], str | None], depth: int = 0) -> int | None:
    """The value of `expression`, or None when it is not known."""
    try:
        tokens = _tokens(expression)
        parser = _Parser(tokens, lookup, depth)
        value = parser.comma()
        if parser.index != len(tokens):
            return None
        return value
    except (_Unsupported, ZeroDivisionError, RecursionError, ValueError):
        return None


def _tokens(expression: str) -> list[tuple[str, str]]:
    text = expression.replace("$((", "((")  # `$((n))` inside a subscript: parentheses
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        if text[index:].strip() == "":
            break
        match = _TOKEN.match(text, index)
        if not match or match.end() == index:
            raise _Unsupported(text[index:])
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(kind)))
        index = match.end()
    return tokens


def _wrap(value: int) -> int:
    """Bash arithmetic is 64-bit signed."""
    value &= (1 << 64) - 1
    return value - (1 << 64) if value >= 1 << 63 else value


def _number(text: str) -> int:
    if text.lower().startswith("0x"):
        return int(text, 16)
    if "#" in text:
        base_text, digits = text.split("#", 1)
        base = int(base_text)
        if not 2 <= base <= 64:
            raise _Unsupported(text)
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ@_"
        if base <= 36:
            digits = digits.lower()
        value = 0
        for char in digits:
            digit = alphabet.index(char)
            if digit >= base:
                raise _Unsupported(text)
            value = value * base + digit
        return value
    if len(text) > 1 and text.startswith("0"):
        return int(text, 8)
    return int(text)


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], lookup: Callable[[str], str | None], depth: int) -> None:
        self.tokens, self.lookup, self.depth, self.index = tokens, lookup, depth, 0

    def _peek(self) -> str | None:
        return self.tokens[self.index][1] if self.index < len(self.tokens) and self.tokens[self.index][0] == "op" \
            else None

    def _take(self, op: str) -> None:
        if self._peek() != op:
            raise _Unsupported(op)
        self.index += 1

    def comma(self) -> int:
        value = self.ternary()
        while self._peek() == ",":
            self.index += 1
            value = self.ternary()
        return value

    def ternary(self) -> int:
        condition = self.binary(0)
        if self._peek() != "?":
            if self._peek() == "=":
                raise _Unsupported("assignment")
            return condition
        self.index += 1
        yes = self.comma()
        self._take(":")
        no = self.ternary()
        return yes if condition else no

    def binary(self, level: int) -> int:
        if level == len(_BINARY):
            return self.power()
        left = self.binary(level + 1)
        while self._peek() in _BINARY[level]:
            op = self._peek()
            self.index += 1
            right = self.binary(level + 1)
            left = _apply(op or "", left, right)
        return left

    def power(self) -> int:
        base = self.unary()
        if self._peek() == "**":
            self.index += 1
            exponent = self.power()
            if exponent < 0:
                raise _Unsupported("negative exponent")
            return _wrap(pow(base, exponent, 1 << 64))
        return base

    def unary(self) -> int:
        op = self._peek()
        if op in ("++", "--"):
            raise _Unsupported("increment")
        if op in ("-", "+", "!", "~"):
            self.index += 1
            value = self.unary()
            return {"-": _wrap(-value), "+": value, "!": int(not value), "~": _wrap(~value)}[op]
        return self.primary()

    def primary(self) -> int:
        if self.index >= len(self.tokens):
            raise _Unsupported("end")
        kind, text = self.tokens[self.index]
        self.index += 1
        if kind == "number":
            return _wrap(_number(text))
        if kind == "name":
            if self._peek() in ("++", "--"):
                raise _Unsupported("increment")
            return self._variable(text.lstrip("$").strip("{}"))
        if text == "(":
            value = self.comma()
            self._take(")")
            return value
        raise _Unsupported(text)

    def _variable(self, name: str) -> int:
        value = self.lookup(name)
        if value is None:
            raise _Unsupported(name)
        if not value.strip():
            return 0  # an empty or unset variable is 0
        if self.depth >= _MAX_DEPTH:
            raise _Unsupported("depth")
        result = evaluate(value, self.lookup, self.depth + 1)
        if result is None:
            raise _Unsupported(name)
        return result


def _apply(op: str, left: int, right: int) -> int:
    if op in ("/", "%"):
        if right == 0:
            raise ZeroDivisionError
        quotient = abs(left) // abs(right) * (1 if (left >= 0) == (right >= 0) else -1)
        return _wrap(quotient if op == "/" else left - right * quotient)
    if op in ("<<", ">>"):
        right &= 63
        return _wrap(left << right) if op == "<<" else left >> right
    results = {
        "||": lambda: int(bool(left) or bool(right)), "&&": lambda: int(bool(left) and bool(right)),
        "|": lambda: left | right, "^": lambda: left ^ right, "&": lambda: left & right,
        "==": lambda: int(left == right), "!=": lambda: int(left != right),
        "<": lambda: int(left < right), "<=": lambda: int(left <= right),
        ">": lambda: int(left > right), ">=": lambda: int(left >= right),
        "+": lambda: left + right, "-": lambda: left - right, "*": lambda: left * right,
    }
    return _wrap(results[op]())
