"""
A small lexer for the subset of bash syntax that the guards need to see through.

`shlex` is not enough for a security check. It treats a newline as plain
whitespace, so `echo hi<newline>rm -rf ~` looks like one `echo` command; it
starts a comment at a `#` in the middle of a word, so `echo a#b; rm -rf ~`
hides everything after `a`; and it has no idea that `$(...)`, backticks or
`<(...)` run a command list of their own.

This lexer produces words with quotes removed, operator tokens (newline
included), every command substitution body, and every heredoc body. It never
raises: input it cannot finish (an unterminated quote or substitution) sets
`unterminated` so a caller can fall back to a conservative check.

It is deliberately not a full shell. It does no expansion beyond quote
removal; a word that contains a parameter expansion or substitution is
flagged `dynamic` so a caller knows its runtime value is unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Longest first, so `&&` wins over `&` and `<<-` over `<<`.
_OPERATORS = (
    ";;&", "<<-", "<<<", "&>>",
    "&&", "||", ";;", ";&", "|&", "&>", "<<", ">>", ">|", "<&", ">&", "<>",
    "(", ")", ";", "|", "&", "<", ">", "\n",
)
REDIRECT_OPERATORS = frozenset({"<<-", "<<<", "&>>", "&>", "<<", ">>", ">|", "<&", ">&", "<>", "<", ">"})
HEREDOC_OPERATORS = frozenset({"<<", "<<-"})
# Only space and tab are blanks to the shell; a carriage return is part of a word.
_METACHARACTERS = frozenset(" \t\n|&;()<>")
# The text before `(` in a compound array assignment: `X=`, `X+=`.
_COMPOUND_TARGET = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_ANSI_C_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b",
                   "f": "\f", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


_BARE_NAME_TAIL = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)$")


@dataclass
class Word:
    """A shell word after quote removal. `dynamic` means its runtime value is unknown."""

    text: str
    dynamic: bool = False
    quoted: bool = False
    heredoc_body: str | None = None
    # Command lists this word runs while it is expanded ($(...), backticks,
    # <(...), and $(...) inside ${...}, $((...)) or an unquoted heredoc), in
    # order. They run when the command holding the word runs, not before.
    substitutions: list[str] = field(default_factory=list)
    # An unquoted expansion (`$X`, not `"$X"`): its value is split into words.
    splits: bool = False
    # Offsets in `text` of the unquoted `{`, `,` and `}`: only those take part in
    # brace expansion (`{rm,-rf,x}` is three words; `'{a,b}'` is one).
    braces: tuple[int, ...] = ()
    # `X=(a "b c")`: the elements of a compound array assignment; None otherwise.
    elements: list[Word] | None = None


@dataclass(frozen=True)
class Operator:
    op: str


@dataclass
class LexResult:
    tokens: list[Word | Operator] = field(default_factory=list)
    # Bodies of $(...), `...`, <(...) and >(...): each runs as its own command list.
    substitutions: list[str] = field(default_factory=list)
    unterminated: bool = False


class _Lexer:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.n = len(text)
        self.result = LexResult()
        self._pending_heredocs: list[tuple[Word, bool]] = []

    # ------------------------------------------------------------------ #
    # Driver
    # ------------------------------------------------------------------ #

    def run(self) -> LexResult:
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t":
                self.i += 1
            elif c == "\\" and self._peek(1) == "\n":
                self.i += 2
            elif c == "#":
                self._skip_comment()
            elif c in "<>" and self._peek(1) == "(":
                self._process_substitution()
            elif c == "\n":
                self.result.tokens.append(Operator("\n"))
                self.i += 1
                self._read_heredoc_bodies()
            else:
                op = self._match_operator()
                if op:
                    self.i += len(op)
                    self.result.tokens.append(Operator(op))
                    if op in HEREDOC_OPERATORS:
                        self._heredoc_delimiter(strip_tabs=op == "<<-")
                else:
                    self._word()
        return self.result

    def _peek(self, offset: int) -> str:
        index = self.i + offset
        return self.s[index] if index < self.n else ""

    def _match_operator(self) -> str | None:
        for op in _OPERATORS:
            if self.s.startswith(op, self.i):
                return op
        return None

    def _skip_comment(self) -> None:
        end = self.s.find("\n", self.i)
        self.i = self.n if end < 0 else end

    # ------------------------------------------------------------------ #
    # Words
    # ------------------------------------------------------------------ #

    def _word(self) -> None:
        word = self._read_word()
        # `2>file`: digits glued to a redirect are a file descriptor, not an argument.
        if word.text.isdigit() and not word.quoted and self.i < self.n and self.s[self.i] in "<>":
            return
        self.result.tokens.append(word)

    def _read_word(self) -> Word:
        parts: list[str] = []
        # Whether each part can end in a bare `$NAME` (an expansion, or text in
        # double quotes), so a following part must not extend the name.
        expands: list[bool] = []
        word = Word("")

        def add(text: str, expansion: bool = False) -> None:
            if parts and expands[-1] and text[:1] and (text[0].isalnum() or text[0] == "_"):
                # `$X'm'` is $X then m: write it `${X}m` so the name stays X.
                parts[-1] = _BARE_NAME_TAIL.sub(r"${\1}", parts[-1])
            parts.append(text)
            expands.append(expansion)

        while self.i < self.n:
            c = self.s[self.i]
            if c in _METACHARACTERS:
                break
            if c == "\\":
                nxt = self._peek(1)
                if nxt == "\n":
                    self.i += 2
                    continue
                add(nxt)
                word.quoted = True
                self.i += 2
            elif c == "'":
                word.quoted = True
                add(self._single_quoted())
            elif c == "$" and self._peek(1) == "'":
                word.quoted = True
                add(self._ansi_c_quoted())
            elif c == '"':
                word.quoted = True
                add(self._double_quoted(word), expansion=True)
            elif c in "$`":
                word.splits = True
                add(self._expansion(word), expansion=True)
            else:
                if c in "{,}":
                    word.braces += (sum(map(len, parts)),)
                add(c)
                self.i += 1
        word.text = "".join(parts)
        if self.i < self.n and self.s[self.i] == "(" and not word.quoted and _COMPOUND_TARGET.fullmatch(word.text):
            self._compound_elements(word)
        return word

    def _compound_elements(self, word: Word) -> None:
        """`NAME=(...)` / `NAME+=(...)`: read the element words up to the closing `)`."""
        self.i += 1
        word.elements = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == ")":
                self.i += 1
                return
            if c in " \t\n":
                self.i += 1
            elif c == "#":
                end = self.s.find("\n", self.i)
                self.i = self.n if end < 0 else end
            elif c in _METACHARACTERS:
                self.i += 1  # not valid inside an array; bash rejects the line
            else:
                element = self._read_word()
                word.elements.append(element)
                word.dynamic = word.dynamic or element.dynamic
                word.substitutions.extend(element.substitutions)
        self.result.unterminated = True

    def _single_quoted(self) -> str:
        end = self.s.find("'", self.i + 1)
        if end < 0:
            self.result.unterminated = True
            text, self.i = self.s[self.i + 1:], self.n
            return text
        text, self.i = self.s[self.i + 1:end], end + 1
        return text

    def _ansi_c_quoted(self) -> str:
        self.i += 2
        out: list[str] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "'":
                self.i += 1
                return "".join(out)
            if c == "\\" and self.i + 1 < self.n:
                out.append(self._ansi_c_escape())
                continue
            out.append(c)
            self.i += 1
        self.result.unterminated = True
        return "".join(out)

    def _ansi_c_escape(self) -> str:
        nxt = self.s[self.i + 1]
        if nxt in _ANSI_C_ESCAPES:
            self.i += 2
            return _ANSI_C_ESCAPES[nxt]
        if nxt == "x":
            digits = self._take_while(self.i + 2, "0123456789abcdefABCDEF", 2)
            self.i += 2 + len(digits)
            return chr(int(digits, 16)) if digits else "x"
        if nxt in "01234567":
            digits = self._take_while(self.i + 1, "01234567", 3)
            self.i += 1 + len(digits)
            return chr(int(digits, 8))
        self.i += 2
        return "\\" + nxt

    def _take_while(self, start: int, allowed: str, limit: int) -> str:
        end = start
        while end < self.n and end - start < limit and self.s[end] in allowed:
            end += 1
        return self.s[start:end]

    def _double_quoted(self, word: Word) -> str:
        self.i += 1
        out: list[str] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == '"':
                self.i += 1
                return "".join(out)
            if c == "\\" and self._peek(1) in '$`"\\\n':
                if self._peek(1) != "\n":
                    out.append(self._peek(1))
                self.i += 2
            elif c in "$`":
                out.append(self._expansion(word))
            else:
                out.append(c)
                self.i += 1
        self.result.unterminated = True
        return "".join(out)

    # ------------------------------------------------------------------ #
    # Expansions and substitutions
    # ------------------------------------------------------------------ #

    def _expansion(self, word: Word) -> str:
        """Consume a `$...` or backtick expansion; return its source text verbatim."""
        start = self.i
        c = self.s[self.i]
        if c == "`":
            body = self._backtick_body()
            self._substitute(word, [body])
            word.dynamic = True
            return self.s[start:self.i]

        nxt = self._peek(1)
        if nxt == "(" and self._peek(2) == "(":
            self.i += 3
            # Arithmetic runs any command substitution inside it: `$(( $(cmd) + 1 ))`.
            body = self._balanced(")", "(")
            self._substitute(word, nested_substitutions(body))
            word.dynamic = True
            return self.s[start:self.i]
        if nxt == "(":
            self.i += 2
            body = self._balanced(")", "(")
            self._substitute(word, [body])
            word.dynamic = True
            return self.s[start:self.i]
        if nxt == "{":
            self.i += 2
            body = self._balanced("}", "{")
            self._substitute(word, nested_substitutions(body))
            word.dynamic = True
            return self.s[start:self.i]
        if nxt and (nxt.isalnum() or nxt in "_@*#?$!-"):
            self.i += 2
            if nxt.isalpha() or nxt == "_":
                while self.i < self.n and (self.s[self.i].isalnum() or self.s[self.i] == "_"):
                    self.i += 1
            word.dynamic = True
            return self.s[start:self.i]
        self.i += 1
        return "$"

    def _backtick_body(self) -> str:
        self.i += 1
        out: list[str] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\" and self._peek(1) in "`$\\":
                out.append(self._peek(1))
                self.i += 2
                continue
            if c == "`":
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1
        self.result.unterminated = True
        return "".join(out)

    def _balanced(self, close: str, open_: str) -> str:
        """Consume up to the matching `close`, honouring quotes and heredocs.

        Returns the inner text. Heredoc bodies are skipped as data, so an
        apostrophe in `$(cat <<'EOF' ... it's ... EOF)` does not open a quote.
        """
        start = self.i
        depth = 1
        pending_heredocs: list[tuple[str, bool]] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == "<" and self.s.startswith("<<", self.i) and not self.s.startswith("<<<", self.i):
                pending_heredocs.append(self._scan_heredoc_delimiter())
                continue
            if c == "\n" and pending_heredocs:
                self.i += 1
                for delimiter, strip_tabs in pending_heredocs:
                    self._skip_heredoc_body(delimiter, strip_tabs)
                pending_heredocs = []
                continue
            if c == "'":
                end = self.s.find("'", self.i + 1)
                self.i = self.n if end < 0 else end + 1
                continue
            if c == '"':
                self._skip_double_quoted()
                continue
            if c == "`":
                end = self.s.find("`", self.i + 1)
                self.i = self.n if end < 0 else end + 1
                continue
            if c == open_:
                depth += 1
            elif c == close:
                depth -= 1
                if depth == 0:
                    body = self.s[start:self.i]
                    self.i += 1
                    return body
            self.i += 1
        self.result.unterminated = True
        return self.s[start:]

    def _scan_heredoc_delimiter(self) -> tuple[str, bool]:
        """At `<<`: consume the operator and delimiter word; return (delimiter, strip_tabs)."""
        self.i += 2
        strip_tabs = self._peek(0) == "-"
        if strip_tabs:
            self.i += 1
        while self.i < self.n and self.s[self.i] in " \t":
            self.i += 1
        start = self.i
        while self.i < self.n and self.s[self.i] not in _METACHARACTERS:
            self.i += 1
        raw = self.s[start:self.i]
        return raw.replace("'", "").replace('"', "").replace("\\", ""), strip_tabs

    def _skip_heredoc_body(self, delimiter: str, strip_tabs: bool) -> None:
        while self.i < self.n:
            end = self.s.find("\n", self.i)
            line = self.s[self.i:] if end < 0 else self.s[self.i:end]
            self.i = self.n if end < 0 else end + 1
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                return

    def _skip_double_quoted(self) -> None:
        self.i += 1
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            self.i += 1
            if c == '"':
                return
        self.result.unterminated = True

    def _substitute(self, word: Word, bodies: list[str]) -> None:
        word.substitutions.extend(bodies)
        self.result.substitutions.extend(bodies)

    def _process_substitution(self) -> None:
        start = self.i
        self.i += 2
        body = self._balanced(")", "(")
        word = Word(self.s[start:self.i], dynamic=True)
        self._substitute(word, [body])
        self.result.tokens.append(word)

    # ------------------------------------------------------------------ #
    # Heredocs
    # ------------------------------------------------------------------ #

    def _heredoc_delimiter(self, strip_tabs: bool) -> None:
        while self.i < self.n and self.s[self.i] in " \t":
            self.i += 1
        if self.i >= self.n or self.s[self.i] in _METACHARACTERS:
            return
        start = self.i
        delimiter = self._read_word()
        raw = self.s[start:self.i]
        # The delimiter is literal text; any `$` in it is not an expansion.
        word = Word(delimiter.text if not delimiter.dynamic else raw, quoted=delimiter.quoted)
        self.result.tokens.append(word)
        self._pending_heredocs.append((word, strip_tabs))

    def _read_heredoc_bodies(self) -> None:
        pending, self._pending_heredocs = self._pending_heredocs, []
        for word, strip_tabs in pending:
            lines: list[str] = []
            terminated = False
            while self.i < self.n:
                end = self.s.find("\n", self.i)
                line = self.s[self.i:] if end < 0 else self.s[self.i:end]
                self.i = self.n if end < 0 else end + 1
                if (line.lstrip("\t") if strip_tabs else line) == word.text:
                    terminated = True
                    break
                lines.append(line.lstrip("\t") if strip_tabs else line)
            word.heredoc_body = "\n".join(lines)
            if not terminated:
                self.result.unterminated = True
            if not word.quoted:
                # An unquoted heredoc expands $(...) and backticks in its body.
                self._substitute(word, nested_substitutions(word.heredoc_body))


def nested_substitutions(text: str) -> list[str]:
    """Command substitution bodies found anywhere in `text` (e.g. a heredoc or ${...})."""
    if "$(" not in text and "`" not in text:
        return []
    found: list[str] = []
    lexer = _Lexer(text)
    while lexer.i < lexer.n:
        c = lexer.s[lexer.i]
        if c == "\\":
            lexer.i += 2
        elif c in "$`":
            lexer._expansion(Word(""))
        else:
            lexer.i += 1
    found.extend(lexer.result.substitutions)
    return found


def lex(text: str) -> LexResult:
    """Tokenize `text`. Never raises."""
    return _Lexer(text).run()
