"""
Decompose a shell command into the simple commands it would actually run.

The guards ask "does this command line run `rm -rf`, write to settings.json,
or pipe a download into a shell?" Answering that by regex over the raw string
is how every bypass happened: a newline, `bash -c '...'`, `eval`, a backtick,
`sudo -u root`, `timeout 5`. This module answers it structurally:

  * shell_lexer splits words and operators the way bash does;
  * each simple command is unwrapped through sudo/env/nice/timeout/xargs/...
    (shell_wrappers.py) until the real program is at argv[0];
  * command strings that are parsed again by a shell are parsed again here:
    `bash -c`, `eval`, `su -c`, `watch`, `ssh host CMD`, `env -S`, `$(...)`,
    backticks, `<(...)`, heredocs and here-strings fed to a shell, and
    `echo '...' | sh`;
  * optionally (`sources=`), program text the command executes from disk is
    parsed too: shell scripts, `source` files, package.json scripts and
    Makefile recipes (shell_sources.py). Those commands are marked
    `indirect`, because the agent did not type them in this call.

Parsing never raises. Input that cannot be finished sets `unparseable`;
nesting beyond MAX_DEPTH sets `truncated`. Callers decide how conservative
to be in either case.
"""

from __future__ import annotations

import glob
import itertools
import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from math import prod

import shell_params
from shell_interpreters import awk_commands, inline_programs, shell_strings
from shell_lexer import HEREDOC_OPERATORS, REDIRECT_OPERATORS, Operator, Word, lex
from shell_wrappers import (
    CLI_OPTIONS_WITH_VALUE,
    PREFIX_WRAPPERS,
    RESERVED_PREFIXES,
    SHELLS,
    SSH_OPTIONS_WITH_ARG,
    STRING_RUNNERS,
    SUBCOMMAND_RUNNERS,
    WrapperSpec,
    shell_invocation,
)

MAX_DEPTH = 6
_MAX_UNWRAP = 16
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=")
# A parameter reference: `$NAME`, `${NAME}`, `${NAME:-default}` and the like.
_REFERENCE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ONE_CHAR = re.compile(r".")
# `${NAME<op>word}` forms whose result is NAME's value or `word`.
_BRACED = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(:?[-=+?])(.*)", re.DOTALL)
_UNRESOLVED = re.compile(r"\$|`")
_SEPARATORS = frozenset({"&&", "||", ";", ";;", ";&", ";;&", "&", "\n", "(", ")"})
_PIPES = frozenset({"|", "|&"})
_AND_OR = frozenset({"&&", "||"})
# Reserved words that open and close a compound command whose body may not run.
_BLOCK_OPENERS = frozenset({"if", "while", "until", "for", "select", "case", "{"})
_BLOCK_CLOSERS = frozenset({"fi", "done", "esac", "}"})
_MAX_VARIANTS = 64
_MAX_BRACE_WORDS = 64
# Program text a source resolver found but could not inspect (too large).
SOURCE_TOO_LARGE = "\0too-large"
# Positional parameters (`set -- a b`) live in the variable table under this
# name, joined by _FIELD, so subshells and branches treat them like variables.
_POSITIONALS = "@"
_FIELD = "\x1f"
_GLOB_CHARS = re.compile(r"[*?[]")
# A variable value meaning "may be unset" (`true || unset X`).
UNSET = "\0unset"
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})


@dataclass(frozen=True)
class Redirect:
    op: str
    target: str
    dynamic: bool = False
    body: str | None = None  # heredoc body, when op is << or <<-


@dataclass(frozen=True)
class SimpleCommand:
    """One program invocation, wrappers removed."""

    argv: tuple[str, ...]
    redirects: tuple[Redirect, ...] = ()
    # How this command was reached, outermost first: ("bash -c",), ("$(...)",).
    via: tuple[str, ...] = ()
    wrappers: tuple[str, ...] = ()
    # argv[0] is computed at run time (`$CMD -rf x`, `$(which rm) ...`).
    dynamic_head: bool = False
    # Parsed from a file the command executes, not typed by the agent.
    indirect: bool = False
    pipeline: int = 0
    stage: int = 0
    # Directory the command runs in, following `cd`/`pushd` earlier in the same
    # line. None when a `cd` target is computed at run time (`cd "$DIR"`).
    cwd: str | None = None
    # Every directory it might run in. A `cd` can fail (missing directory) and
    # `cd a || cd b` runs only one of them, so earlier directories stay possible.
    cwd_options: tuple[str | None, ...] = ()
    # Values each variable may hold when the command starts: its prefix
    # assignments (`X=1 cmd`) and assignments earlier in the line. Programs that
    # read a variable by name (`git --config-env=key=VAR`) look it up here.
    environment: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def possible_cwds(self) -> tuple[str | None, ...]:
        return self.cwd_options or (self.cwd,)

    @property
    def name(self) -> str:
        return program_name(self.argv[0]) if self.argv else ""

    @property
    def args(self) -> tuple[str, ...]:
        return self.argv[1:]

    def rendered(self) -> str:
        return " ".join(self.argv)

    def origin(self) -> str:
        return " → ".join(self.via)

    def env_values(self, name: str) -> tuple[str, ...]:
        """Every value `name` may hold in this command's environment; () if unknown."""
        values = next((values for key, values in self.environment if key == name), ())
        return tuple(value for value in values if value != UNSET)


@dataclass(frozen=True)
class NestedSource:
    """A string parsed as shell code beyond the top level."""

    text: str
    via: tuple[str, ...]
    indirect: bool = False


@dataclass
class ParseResult:
    commands: list[SimpleCommand] = field(default_factory=list)
    # Every string that was parsed as shell code beyond the top level, with
    # how it was reached. Regex-based guards rescan these.
    nested_sources: list[NestedSource] = field(default_factory=list)
    # Symlinks and hard links the line creates: absolute link path -> absolute target.
    links: dict[str, str] = field(default_factory=dict)
    # (command, label) for program text a command runs that was too large to read.
    skipped_sources: list[tuple[SimpleCommand, str]] = field(default_factory=list)
    unparseable: bool = False
    truncated: bool = False

    def previous_stage(self, command: SimpleCommand) -> SimpleCommand | None:
        """The command whose stdout feeds `command` through a pipe, if any."""
        if command.stage == 0:
            return None
        for candidate in self.commands:
            if candidate.pipeline == command.pipeline and candidate.stage == command.stage - 1:
                return candidate
        return None


def program_name(token: str) -> str:
    """`/usr/bin/sudo` -> `sudo`; `\\rm` was already unescaped by the lexer."""
    return token.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class WrittenFile:
    """Literal text a command line wrote to a file before running it."""

    text: str
    # False when the line only appended (`>>`, `tee -a`): what was on disk comes first.
    replaces: bool = True


# A source resolver maps (command, cwd, files written earlier in the same
# command line) to program text it executes from disk, e.g. the body of
# `./deploy.sh` or the `build` script in package.json.
SourceResolver = Callable[[SimpleCommand, str, "dict[str, WrittenFile]"], "list[tuple[str, str]]"]
_MAX_CWD_OPTIONS = 8


@dataclass
class _Context:
    result: ParseResult
    # Current directory as the line runs; None once a computed `cd` makes it unknown.
    cwd: str | None
    sources: SourceResolver | None
    # Every directory the line might be in at this point (see possible_cwds).
    cwd_options: tuple[str | None, ...] = ()
    pipeline_counter: list[int] = field(default_factory=lambda: [0])
    # Every value each variable may hold, the latest assignment first. An
    # assignment that may not run (`A && X=1`, an `if` body) keeps the old values.
    variables: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # The command being parsed may not run: assignments add values, not replace them.
    conditional: bool = False
    # Files this command line writes from literal text before running them:
    # `cat > x.sh <<'EOF' ... EOF; bash x.sh`. Absolute path -> content.
    written_files: dict[str, WrittenFile] = field(default_factory=dict)

    def next_pipeline(self) -> int:
        self.pipeline_counter[0] += 1
        return self.pipeline_counter[0]


def parse(command: str, cwd: str = "", sources: SourceResolver | None = None) -> ParseResult:
    """Every simple command `command` would run. Never raises."""
    start = cwd or os.getcwd()
    context = _Context(ParseResult(), start, sources, (start,))
    _parse_into(command, context, via=(), depth=0, indirect=False)
    return context.result


# --------------------------------------------------------------------------- #
# Command lists
# --------------------------------------------------------------------------- #


def _parse_into(text: str, ctx: _Context, via: tuple[str, ...], depth: int, indirect: bool,
                shares_shell: bool = False) -> None:
    """Parse `text` as a command list. Nested code runs in a child shell unless
    `shares_shell` (eval, source): then its `cd` and assignments persist."""
    if depth > MAX_DEPTH:
        ctx.result.truncated = True
        return
    if depth > 0:
        ctx.result.nested_sources.append(NestedSource(text, via, indirect))

    lexed = lex(text)
    if lexed.unterminated:
        ctx.result.unparseable = True
    # Nested code (`bash -c`, `$(...)`, scripts) runs in its own process: a
    # `cd` or assignment inside it does not reach the caller.
    entry = (ctx.cwd, ctx.cwd_options, dict(ctx.variables))

    pipeline, stage = ctx.next_pipeline(), 0
    segment: list = []
    subshells: list[tuple[str | None, tuple[str | None, ...], dict[str, tuple[str, ...]]]] = []
    outer_conditional, previous, blocks = ctx.conditional, ";", 0
    for token in [*lexed.tokens, Operator("\n")]:
        if isinstance(token, Operator) and (token.op in _SEPARATORS or token.op in _PIPES):
            if segment:
                # After `&&`/`||`, or inside an if/loop/case body, the command may not run.
                ctx.conditional = outer_conditional or previous in _AND_OR or blocks > 0
                blocks = _track_blocks(segment, blocks)
                if token.op in _PIPES or stage > 0 or token.op == "&":
                    # Every part of a pipeline, and a background command, runs in its own subshell.
                    saved = (ctx.cwd, ctx.cwd_options, dict(ctx.variables))
                    _segment(segment, ctx, via, depth, indirect, pipeline, stage)
                    ctx.cwd, ctx.cwd_options, ctx.variables = saved
                else:
                    _segment(segment, ctx, via, depth, indirect, pipeline, stage)
                segment = []
            previous = token.op
            if token.op == "(":
                subshells.append((ctx.cwd, ctx.cwd_options, dict(ctx.variables)))
            elif token.op == ")" and subshells:
                # `(cd x; X=1)` changes neither the caller's directory nor its variables.
                ctx.cwd, ctx.cwd_options, ctx.variables = subshells.pop()
            if token.op in _PIPES:
                stage += 1
            else:
                pipeline, stage = ctx.next_pipeline(), 0
            continue
        segment.append(token)
    ctx.conditional = outer_conditional
    if depth > 0 and not shares_shell:
        ctx.cwd, ctx.cwd_options, ctx.variables = entry


def _track_blocks(tokens: list, blocks: int) -> int:
    """Open if/loop/case/group bodies after this command's leading reserved words."""
    for token in tokens:
        if not isinstance(token, Word) or token.quoted or token.text not in RESERVED_PREFIXES:
            break
        if token.text in _BLOCK_OPENERS:
            blocks += 1
        elif token.text in _BLOCK_CLOSERS:
            blocks = max(blocks - 1, 0)
    return blocks


def _segment(tokens: list, ctx: _Context, via: tuple[str, ...], depth: int, indirect: bool,
             pipeline: int, stage: int) -> None:
    """Run one simple command: once per combination of values its variables may hold
    (`P=a || P=b; cat > "$P"` may write a or b)."""
    _run_substitutions(tokens, ctx, via, depth, indirect)
    choices = _variable_choices(tokens, ctx)
    if len(choices) == 1:
        _expand_and_emit(tokens, ctx, via, depth, indirect, pipeline, stage, choices[0])
        return
    start, conditional = (ctx.cwd, ctx.cwd_options), ctx.conditional
    ends: list[tuple[str | None, tuple[str | None, ...]]] = []
    for index, choice in enumerate(choices):
        ctx.cwd, ctx.cwd_options = start
        # Each reading adds to what an assignment may hold; only one of them runs.
        ctx.conditional = conditional or index > 0
        _expand_and_emit(tokens, ctx, via, depth, indirect, pipeline, stage, choice)
        ends.append((ctx.cwd, ctx.cwd_options))
    ctx.conditional = conditional
    ctx.cwd = ends[0][0]
    ctx.cwd_options = _bounded([option for _, options in ends for option in options])


def _variable_choices(tokens: list, ctx: _Context) -> list[dict[str, str]]:
    """One {name: value} per combination of values this command's variables may
    hold, the current values first; [{}] when every variable has one value."""
    names: list[str] = []
    for token in tokens:
        if not isinstance(token, Word) or not token.dynamic:
            continue
        for match in _REFERENCE.finditer(token.text):
            name = match.group(1)
            if len(ctx.variables.get(name, ())) > 1 and name not in names:
                names.append(name)
    if not names:
        return [{}]
    options = [ctx.variables[name] for name in names]
    if prod(len(values) for values in options) <= _MAX_VARIANTS:
        return [dict(zip(names, values, strict=True)) for values in itertools.product(*options)]
    # Too many combinations: still give every value of every variable one turn,
    # so no possible value goes unchecked, and mark the parse incomplete because
    # pairings across variables are not all covered.
    ctx.result.truncated = True
    longest = max(len(values) for values in options)
    return [{name: values[index % len(values)] for name, values in zip(names, options, strict=True)}
            for index in range(longest)]


def _expand_and_emit(tokens: list, ctx: _Context, via: tuple[str, ...], depth: int, indirect: bool,
                     pipeline: int, stage: int, choice: dict[str, str]) -> None:
    words: list[Word] = []
    redirects: list[Redirect] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if isinstance(token, Operator) and token.op in REDIRECT_OPERATORS:
            target = tokens[index + 1] if index + 1 < len(tokens) else None
            if isinstance(target, Word):
                if token.op in HEREDOC_OPERATORS:
                    redirects.append(Redirect(token.op, target.text, False, target.heredoc_body))
                else:
                    expanded = _expand_word(target, ctx, choice)
                    redirects.append(Redirect(token.op, expanded.text, expanded.dynamic))
                index += 2
                continue
        elif isinstance(token, Word):
            params = _positionals(ctx) if token.text in ("$@", "${@}", "$*", "${*}") else None
            if params is not None:
                # `"$@"`: one word per positional parameter; quoted `"$*"` joins them.
                joined = token.quoted and "*" in token.text
                words.extend([Word(" ".join(params))] if joined else [Word(p) for p in params])
            else:
                for piece in _brace_expand(token, ctx):
                    words.extend(_split_unquoted(piece, _expand_word(piece, ctx, choice)))
        index += 1

    words, environment = _strip_prelude(words, ctx)
    _builtin_mutation([word.text for word in words], redirects, ctx)
    words = _glob_words(words, ctx)
    if not words:
        if redirects:
            # `> file` and `X=1 > file` still open (and truncate) the file.
            ctx.result.commands.append(SimpleCommand((), tuple(redirects), via, (), False, indirect, pipeline,
                                                     stage, ctx.cwd, ctx.cwd_options))
        return
    argv = [w.text for w in words]
    _emit(argv, tuple(redirects), ctx, via, depth, indirect, pipeline, stage, words[0].dynamic, (),
          environment)


def _run_substitutions(tokens: list, ctx: _Context, via: tuple[str, ...], depth: int,
                       indirect: bool) -> None:
    """Parse the command substitutions in this command's words, left to right.

    They run when this command is reached, so they see the directory and
    variables left by the commands before it (`cd ~/.claude && echo $(...)`,
    `X=rm; echo $($X ...)`). Each runs in a subshell: a `cd` inside does not
    move the caller.
    """
    for token in tokens:
        for body in getattr(token, "substitutions", ()):
            # A subshell: _parse_into restores the caller's directory and variables.
            _parse_into(body, ctx, (*via, "$(...)"), depth + 1, indirect)


def _glob_words(words: list[Word], ctx: _Context) -> list[Word]:
    """Pathname expansion of unquoted words, in the directory the command runs in:
    `/bin/r[m]` runs /bin/rm, and `rm -* x` next to a file named `-rf` is
    `rm -rf x`. A pattern matching nothing stays literal, as in bash."""
    if ctx.cwd is None:
        return words
    expanded: list[Word] = []
    for word in words:
        expanded += _glob_word(word, ctx.cwd)
    return expanded


def _glob_word(word: Word, cwd: str) -> list[Word]:
    if word.quoted or word.dynamic or not _GLOB_CHARS.search(word.text):
        return [word]
    try:
        matches = sorted(glob.glob(os.path.expanduser(word.text), root_dir=cwd))
    except (OSError, ValueError):
        return [word]
    if not matches:
        return [word]
    if len(matches) > _MAX_BRACE_WORDS:
        # Too many to list: keep the pattern, plus any match that reads as an option.
        return [word, *(Word(m) for m in matches if m.startswith("-"))]
    return [Word(match) for match in matches]


def _positionals(ctx: _Context) -> tuple[str, ...] | None:
    """The positional parameters `set --` gave this line, or None if unknown."""
    values = ctx.variables.get(_POSITIONALS)
    if not values or values[0] == UNSET:
        return None
    return tuple(values[0].split(_FIELD)) if values[0] else ()


def _brace_expand(word: Word, ctx: _Context) -> list[Word]:
    """Brace expansion, which bash does first: `{rm,-rf,~/x}` is three words,
    `a{b,c}d` is `abd acd`. Only unquoted braces and commas count.

    Past _MAX_BRACE_WORDS, every alternative's text becomes a word of its own
    (nothing is dropped, so `rm {x,...,-rf} v` still shows `-rf`) and the
    parse is marked truncated."""
    if not word.braces:
        return [word]
    texts = _expand_brace_text(word.text, frozenset(word.braces))
    if len(texts) > _MAX_BRACE_WORDS:
        ctx.result.truncated = True
        texts = _brace_fragments(word.text, word.braces)
    if len(texts) == 1:
        return [word]
    return [Word(text, dynamic=word.dynamic and bool(_UNRESOLVED.search(text)), quoted=word.quoted, splits=word.splits,
                 substitutions=word.substitutions if index == 0 else [])
            for index, text in enumerate(texts)]


def _brace_fragments(text: str, braces: tuple[int, ...]) -> list[str]:
    """The text between unquoted braces and commas, each piece once."""
    pieces, start = [], 0
    for position in (*braces, len(text)):
        pieces.append(text[start:position])
        start = position + 1
    return list(dict.fromkeys(piece for piece in pieces if piece))


def _expand_brace_text(text: str, active: frozenset[int]) -> list[str]:
    for start in sorted(p for p in active if text[p] == "{"):
        depth, commas, end = 0, [], None
        for index in sorted(p for p in active if p >= start):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
            elif depth == 1:
                commas.append(index)
        if end is None or not commas:
            continue  # `{}`, `{x}` and unbalanced braces stay literal
        prefix, suffix = text[:start], text[end + 1:]
        expanded: list[str] = []
        bounds = [start, *commas, end]
        for left, right in itertools.pairwise(bounds):
            piece = text[left + 1:right]
            moved = ({p for p in active if p < start}
                     | {p - left - 1 + len(prefix) for p in active if left < p < right}
                     | {p - end - 1 + len(prefix) + len(piece) for p in active if p > end})
            expanded += _expand_brace_text(prefix + piece + suffix, frozenset(moved))
            if len(expanded) > _MAX_BRACE_WORDS:
                break  # the caller falls back to fragments
        return expanded
    return [text]


def _split_unquoted(original: Word, expanded: Word) -> list[Word]:
    """Word splitting: an unquoted expansion holding spaces becomes several words
    (`X='rm -rf ~'; $X` runs rm with two arguments). Quoted `"$X"` stays one."""
    if not original.splits or not original.dynamic or expanded.text == original.text:
        return [expanded]
    parts = expanded.text.split()
    if len(parts) <= 1:
        return [expanded]
    return [Word(part, dynamic=bool(_UNRESOLVED.search(part)), substitutions=expanded.substitutions if i == 0 else [])
            for i, part in enumerate(parts)]


def _expand_word(word: Word, ctx: _Context, choice: dict[str, str] | None = None) -> Word:
    """Substitute parameters whose value is known at this point in the line.

    `P=$HOME/.claude/settings.json; echo x > "$P"` writes a known path; so
    does `X=rm; $X -rf ~`. A word that still holds an expansion afterwards
    (an unset variable, `${X:-d}`, `$(...)`) stays dynamic. `choice` picks the
    value of a variable that may hold several.
    """
    if not word.dynamic:
        return word

    def value(name: str, at_start: bool) -> str | None:
        if name.isdigit() or name == "#":
            params = _positionals(ctx)
            if params is None or name == "0":
                return None
            return str(len(params)) if name == "#" else (params[int(name) - 1] if int(name) <= len(params) else "")
        if choice and name in choice:
            return None if choice[name] == UNSET else choice[name]
        if name in ctx.variables:
            current = ctx.variables[name][0]
            return None if current == UNSET else current
        if name == "HOME":
            return os.path.expanduser("~")
        if name == "PWD":
            # `$PWD/x` is `./x`: relative paths are checked in every directory the
            # command might run in, which a single expanded value would lose.
            if at_start:
                return "."
            if ctx.cwd is not None and len(set(ctx.cwd_options)) <= 1:
                return ctx.cwd
        return None

    text = _expand_text(word.text, value, at_start=True)
    return Word(text, dynamic=bool(_UNRESOLVED.search(text)), quoted=word.quoted, splits=word.splits,
                heredoc_body=word.heredoc_body, substitutions=word.substitutions)


def _expand_text(text: str, value: Callable[[str, bool], str | None], at_start: bool) -> str:
    """Replace `$NAME`/`${NAME}` whose value is known, and resolve `${NAME:-word}`,
    `${NAME:=word}` and `${NAME:+word}` to the value they select. When NAME is not
    known it may be unset, so the default or alternate `word` is the candidate:
    `> "${OUT:-$HOME/.claude/settings.json}"` writes settings.json. Anything else
    (`${#X}`, `${X/a/b}`, unknown names) stays as written."""
    out: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("${", index):
            end = _closing_brace(text, index + 2)
            if end is None:
                out.append(text[index:])
                break
            out.append(_expand_braced(text[index + 2:end], text[index:end + 1], value,
                                      at_start and index == 0))
            index = end + 1
            continue
        name = _NAME.match(text, index + 1) if text[index] == "$" else None
        if name is None and text[index] == "$" and index + 1 < len(text) and text[index + 1] in "0123456789#":
            name = _ONE_CHAR.match(text, index + 1)  # `$1`, `$#`
        if name:
            found = value(name.group(0), at_start and index == 0)
            out.append(text[index:name.end()] if found is None else found)
            index = name.end()
            continue
        out.append(text[index])
        index += 1
    return "".join(out)


def _closing_brace(text: str, start: int) -> int | None:
    depth = 1
    for index in range(start, len(text)):
        if text.startswith("${", index):
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _expand_braced(body: str, original: str, value: Callable[[str, bool], str | None],
                   at_start: bool) -> str:
    if _NAME.fullmatch(body) or body.isdigit():
        found = value(body, at_start)
        return original if found is None else found
    if body.startswith("!") and _NAME.fullmatch(body[1:]):
        # `${!X}`: the value of the variable whose name X holds.
        target = value(body[1:], False)
        found = value(target, False) if target and _NAME.fullmatch(target) else None
        return original if found is None else found
    if body.startswith("#") and _NAME.fullmatch(body[1:]):
        found = value(body[1:], False)
        return original if found is None else str(len(found))  # ${#X}
    match = _BRACED.fullmatch(body)
    if not match:
        # Substring, pattern removal/replacement, case: computable once X is known.
        name = _NAME.match(body)
        current = value(name.group(0), False) if name else None
        if current is None:
            return original
        result = shell_params.apply(current, _expand_text(body[name.end():], value, False))
        return original if result is None else result
    name, op, word = match.groups()
    current = value(name, False)
    if op.endswith("?"):
        return original if current is None else current
    alternative = _expand_text(word, value, at_start)
    set_and_used = current is not None and (current != "" or not op.startswith(":"))
    if op.endswith("+"):
        # Unknown NAME may be set: the alternate word is the value to check.
        return alternative if current is None or set_and_used else ""
    return current if set_and_used else alternative


def _strip_prelude(words: list[Word], ctx: _Context) -> tuple[list[Word], tuple[str, ...]]:
    """Drop reserved words and leading assignments.

    `X=rm` on its own sets X for the rest of the line. `X=echo true` only puts
    X in `true`'s environment: it is returned, applied to what `true` runs,
    and not remembered afterwards.
    """
    index = 0
    assignments: list[str] = []
    while index < len(words):
        word = words[index]
        reserved = word.text in RESERVED_PREFIXES and not word.quoted
        time_flag = word.text == "-p" and index > 0 and words[index - 1].text == "time"
        if word.text == "function" and not word.quoted:
            # `function name { body; }`: the body is parsed like any other command list.
            index += 2
        elif reserved or time_flag:
            index += 1
        elif _ASSIGNMENT.match(word.text):
            assignments.append(word.text)
            index += 1
        else:
            break
    rest = words[index:]
    if not rest:
        for text in assignments:
            _assign(text, ctx.variables, ctx.conditional)
        return rest, ()
    # `export X=rm` / `readonly X=...` / `declare X=...`: also an assignment.
    if rest and rest[0].text in ("export", "readonly", "declare", "typeset", "local"):
        for word in rest[1:]:
            if _ASSIGNMENT.match(word.text):
                _assign(word.text, ctx.variables, ctx.conditional)
    return rest, tuple(assignments)


def _builtin_mutation(argv: list[str], redirects: list[Redirect], ctx: _Context) -> None:
    """Builtins that set or clear a variable: `printf -v X FMT ARGS`, `unset X`,
    `set -- ARGS`, `read X <<< TEXT`."""
    if argv and argv[0] == "read":
        _read_builtin(argv[1:], redirects, ctx)
    elif len(argv) >= 4 and argv[0] == "printf" and argv[1] == "-v" and _NAME.fullmatch(argv[2]):
        text = _echoed_text(SimpleCommand(("printf", *argv[3:])))
        if text or "%" not in argv[3]:
            _assign(f"{argv[2]}={text}", ctx.variables, ctx.conditional)
        else:
            ctx.variables.pop(argv[2], None)  # a format we do not model: the value is unknown
    elif argv and argv[0] == "set":
        params = _set_positionals(argv[1:])
        if params is not None:
            _assign(f"{_POSITIONALS}={_FIELD.join(params)}", ctx.variables, ctx.conditional)
    elif argv and argv[0] == "unset":
        for name in argv[1:]:
            if _NAME.fullmatch(name):
                _assign(f"{name}={UNSET}", ctx.variables, ctx.conditional)


_READ_OPTIONS_WITH_VALUE = frozenset({"-a", "-d", "-i", "-n", "-N", "-p", "-t", "-u"})


def _read_builtin(args: list[str], redirects: list[Redirect], ctx: _Context) -> None:
    """`read A B <<< "x y z"` sets A=x, B="y z". Input that is not literal text
    (a pipe, a file, the terminal) makes the variables unknown."""
    names: list[str] = []
    delimiter = "\n"
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _READ_OPTIONS_WITH_VALUE:
            if arg == "-a":
                names.append(_at(args, index + 1) or "")  # an array: its value is unknown
            elif arg == "-d":
                delimiter = (_at(args, index + 1) or "")[:1] or "\0"
            index += 2
            continue
        if not arg.startswith("-"):
            names += args[index:]
            break
        index += 1
    names = [n for n in names if _NAME.fullmatch(n)] or ["REPLY"]
    text = next((r.target + "\n" if r.op == "<<<" else r.body for r in redirects
                 if (r.op == "<<<" and not r.dynamic) or (r.op in HEREDOC_OPERATORS and r.body is not None)),
                None)
    array = "-a" in args
    if text is None or array:
        for name in names:
            ctx.variables.pop(name, None)
        return
    line = text.split(delimiter, 1)[0]
    fields = line.split(maxsplit=len(names) - 1) if len(names) > 1 else [line.strip()]
    for position, name in enumerate(names):
        _assign(f"{name}={fields[position] if position < len(fields) else ''}", ctx.variables, ctx.conditional)


def _set_positionals(args: list[str]) -> list[str] | None:
    """The new positional parameters of `set [options] [--] args`, or None if unchanged."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return args[index + 1:]
        if arg in ("-o", "+o"):
            index += 2
        elif arg.startswith(("-", "+")) and len(arg) > 1:
            index += 1
        else:
            return args[index:]
    return None


def _assign(text: str, variables: dict[str, tuple[str, ...]], keep_previous: bool = False) -> None:
    """Remember `NAME=value` (already expanded); `NAME+=value` appends.

    `keep_previous` is for an assignment that may not run: the variable may
    still hold any of its earlier values.
    """
    name, _, value = text.partition("=")
    if name.endswith("+"):
        name = name[:-1]
        values = tuple(("" if earlier == UNSET else earlier) + value
                       for earlier in variables.get(name.split("[", 1)[0], ("",)))
    else:
        values = (value,)
    name = name.split("[", 1)[0]
    if keep_previous:
        values = (*values, *variables.get(name, ()))
    # Not capped: every value stays possible. Their number is bounded by the
    # assignments written in the command line itself.
    variables[name] = tuple(dict.fromkeys(values))


def _bounded(options: list[str | None]) -> tuple[str | None, ...]:
    """Distinct directory options; past the limit, the rest become unknown (None)."""
    distinct = list(dict.fromkeys(options))
    if len(distinct) > _MAX_CWD_OPTIONS:
        distinct = [*distinct[:_MAX_CWD_OPTIONS - 1], None]
    return tuple(distinct)


# --------------------------------------------------------------------------- #
# Unwrapping
# --------------------------------------------------------------------------- #


def _emit(argv: list[str], redirects: tuple[Redirect, ...], ctx: _Context, via: tuple[str, ...],
          depth: int, indirect: bool, pipeline: int, stage: int, dynamic_head: bool,
          wrappers: tuple[str, ...], environment: tuple[str, ...] = ()) -> None:
    """Unwrap `argv`, record the resulting command, and parse anything it runs.

    `environment` holds the command's prefix assignments (`X=1 cmd`): visible
    to what cmd runs (`X=1 sh -c 'echo $X'`), gone once cmd returns.
    """
    # The caller's variables, once the command has an environment of its own.
    caller: list[dict[str, tuple[str, ...]]] = []
    for text in environment:
        _own_assign(text, ctx, caller)
    try:
        _unwrap_and_record(argv, redirects, ctx, via, depth, indirect, pipeline, stage,
                           dynamic_head, wrappers, caller)
    finally:
        if caller:
            # The command's own environment reached what it runs, and is gone now.
            ctx.variables = caller[0]


def _own_assign(text: str, ctx: _Context, caller: list[dict[str, tuple[str, ...]]]) -> None:
    """Put `text` in the current command's environment only, copying the shell's
    variables the first time so the caller's stay untouched."""
    if not caller:
        caller.append(ctx.variables)
        ctx.variables = dict(ctx.variables)
    _assign(text, ctx.variables)


def _unwrap_and_record(argv: list[str], redirects: tuple[Redirect, ...], ctx: _Context,
                       via: tuple[str, ...], depth: int, indirect: bool, pipeline: int, stage: int,
                       dynamic_head: bool, wrappers: tuple[str, ...],
                       caller: list[dict[str, tuple[str, ...]]]) -> None:
    outer: tuple[str | None, tuple[str | None, ...]] | None = None
    for _ in range(_MAX_UNWRAP):
        while argv and _ASSIGNMENT.match(argv[0]):
            # `env A=b cmd`, `sudo A=b cmd`: A is in cmd's environment.
            _own_assign(argv[0], ctx, caller)
            argv = argv[1:]
        if not argv:
            return
        name = program_name(argv[0])
        step = _unwrap_once(name, argv, ctx, via, depth, indirect)
        if step is None:
            break
        inner, label, chdir = step
        if name == "xargs" and inner:
            # Arguments xargs reads from a statically known feeder join the command.
            runs = _xargs_runs(argv[1:], inner, _feeder(ctx, pipeline, stage))
            for extra in runs[1:]:
                _emit(extra, (), ctx, via, depth, indirect, pipeline, stage, False, (*wrappers, label))
            inner = runs[0]
        if chdir is not None:
            # `env -C dir cmd`, `sudo -D dir cmd`: cmd (and what it runs) starts in dir.
            outer = outer or (ctx.cwd, ctx.cwd_options)
            ctx.cwd = _change_directory(ctx.cwd, chdir)
            ctx.cwd_options = tuple(dict.fromkeys(_change_directory(o, chdir) for o in ctx.cwd_options))
        if inner is None:
            # The wrapper is a query (`command -v rm`) or ran a string we
            # already parsed; record the wrapper itself and stop.
            break
        argv, wrappers, dynamic_head = inner, (*wrappers, label), False
    else:
        ctx.result.truncated = True  # more nested wrappers than are unwrapped

    command = SimpleCommand(tuple(argv), redirects, via, wrappers, dynamic_head, indirect,
                            pipeline, stage, ctx.cwd, ctx.cwd_options, tuple(ctx.variables.items()))
    ctx.result.commands.append(command)
    _record_written_file(command, ctx)
    _record_link(command, ctx)
    _parse_payloads(command, ctx, depth)
    if outer is not None:
        ctx.cwd, ctx.cwd_options = outer  # the wrapper's directory applied to the child only
    else:
        _follow_directory_change(command, ctx)


def _follow_directory_change(command: SimpleCommand, ctx: _Context) -> None:
    """Move the tracked directory for `cd`/`pushd`/`popd` run in the current shell."""
    if command.name not in ("cd", "pushd", "popd") or command.stage > 0:
        return  # a `cd` inside a pipeline runs in a subshell
    if command.name == "popd":
        target = None
    else:
        targets = [arg for arg in command.args if not (arg.startswith("-") and arg != "-")]
        target = targets[0] if targets else "~"
    ctx.cwd = _change_directory(ctx.cwd, target)
    # The `cd` may fail, or sit in a branch that does not run (`cd a || cd b`):
    # the directories possible before it stay possible after it.
    moved = [_change_directory(option, target) for option in ctx.cwd_options]
    ctx.cwd_options = _bounded([*ctx.cwd_options, *moved])


def _change_directory(current: str | None, target: str | None) -> str | None:
    """The directory after `cd target` from `current`; None when it cannot be known."""
    if target is None:
        return None  # popd: the directory stack is not tracked
    target = re.sub(r"^(?:\$HOME|\$\{HOME\})(?=/|$)", "~", target)
    if target == "-" or "$" in target or "`" in target:
        return None  # OLDPWD or a value computed at run time
    expanded = os.path.expanduser(target)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return None if current is None else os.path.normpath(os.path.join(current, expanded))


def _record_written_file(command: SimpleCommand, ctx: _Context) -> None:
    """Remember literal text written to a file, so running it later is judged by its body."""
    body = next((r.body for r in command.redirects if r.op in HEREDOC_OPERATORS and r.body is not None), None)
    if body is None and command.name in ("echo", "printf") and not command.dynamic_head:
        body = _echoed_text(command)
    directories = [d for d in command.possible_cwds if d is not None]
    if body is None or not directories:
        return
    targets = [(r.target, r.op == ">>") for r in command.redirects
               if r.op in (">", ">>", ">|") and not r.dynamic]
    if command.name == "tee":
        appends = any(a in ("-a", "--append") for a in command.args)
        targets += [(arg, appends) for arg in command.args if not arg.startswith("-")]
    paths = [(os.path.normpath(os.path.join(d, os.path.expanduser(t))), appends)
             for t, appends in targets for d in directories]
    for path, appends in dict.fromkeys(paths):
        earlier = ctx.written_files.get(path)
        if appends and earlier is not None:
            ctx.written_files[path] = WrittenFile(_join_lines(earlier.text, body), earlier.replaces)
        else:
            ctx.written_files[path] = WrittenFile(body, replaces=not appends)


def _record_link(command: SimpleCommand, ctx: _Context) -> None:
    """Remember the links `ln` creates, so later writes through them resolve to the
    target: `ln -s ~/.claude/settings.json x; printf y > x` writes settings.json."""
    if command.name != "ln" or command.dynamic_head:
        return
    args = list(command.args)
    operands: list[str] = []
    directory: str | None = None
    index, ended = 0, False
    while index < len(args):
        arg = args[index]
        short = arg.startswith("-") and not arg.startswith("--") and len(arg) > 1
        if ended or not arg.startswith("-") or arg == "-":
            operands.append(arg)
        elif arg == "--":
            ended = True
        elif arg.startswith("--target-directory"):
            directory, index = (arg.split("=", 1)[1], index) if "=" in arg else (_at(args, index + 1), index + 1)
        elif arg in ("-S", "--suffix"):
            index += 1
        elif short and "t" in arg:
            # `-t DIR`, `-sfDIR`-style clusters: the rest of the cluster, or the next word.
            rest = arg[arg.index("t") + 1:]
            directory, index = (rest, index) if rest else (_at(args, index + 1), index + 1)
        index += 1
    if directory is None and len(operands) == 1:
        directory = "."
    if directory is None and len(operands) < 2:
        return
    sources = operands if directory is not None else operands[:-1]
    symbolic = any(a == "--symbolic" or (a.startswith("-") and not a.startswith("--") and "s" in a)
                   for a in args)
    for cwd in (d for d in command.possible_cwds if d is not None):
        for source in sources:
            destination = os.path.join(cwd, os.path.expanduser(directory if directory is not None else operands[-1]))
            if directory is not None or len(sources) > 1 or os.path.isdir(destination):
                destination = os.path.join(destination, os.path.basename(source.rstrip("/")))
            link = os.path.normpath(destination)
            target = os.path.expanduser(source)
            if not os.path.isabs(target):
                target = os.path.join(os.path.dirname(link) if symbolic else cwd, target)
            ctx.result.links[link] = os.path.normpath(target)


def _at(args: list[str], index: int) -> str | None:
    return args[index] if index < len(args) else None


def _join_lines(first: str, second: str) -> str:
    return f"{first}{'' if not first or first.endswith(chr(10)) else chr(10)}{second}"


def _unwrap_once(name: str, argv: list[str], ctx: _Context, via: tuple[str, ...], depth: int,
                 indirect: bool) -> tuple[list[str] | None, str, str | None] | None:
    """One unwrapping step: (inner argv or None, label, directory it switches to).

    None: not a wrapper. Inner None: the wrapper runs nothing further here.
    """
    args = argv[1:]
    if name == "env":
        return _unwrap_env(args, ctx, via, depth, indirect)
    if name in PREFIX_WRAPPERS:
        spec = PREFIX_WRAPPERS[name]
        chdir = option_value(args, spec.chdir_options)
        command = option_value(args, spec.command_options)
        if command is not None:
            _parse_into(command, ctx, (*via, f"{name} -c"), depth + 1, indirect)
            return None, name, chdir
        inner = _skip_options(args, spec)
        return (inner or None), name, chdir
    if name == "find":
        return None
    if name in SUBCOMMAND_RUNNERS:
        found = _skip_subcommand_runner(args, name)
        if not found:
            return None
        chdir = option_value(args[:len(args) - len(found[1])], SUBCOMMAND_RUNNERS[name].chdir_options)
        return found[1], f"{name} {found[0]}", chdir
    return None


def _unwrap_env(args: list[str], ctx: _Context, via: tuple[str, ...], depth: int,
                indirect: bool) -> tuple[list[str] | None, str, str | None]:
    spec = PREFIX_WRAPPERS["env"]
    chdir = option_value(args, spec.chdir_options)
    index = 0
    while index < len(args):
        arg = args[index]
        split = _split_string_value(args, index, ("-S", "--split-string"))
        if split is not None:
            # `env -C dir -S "cmd"`: the split command runs in dir.
            saved = (ctx.cwd, ctx.cwd_options)
            if chdir is not None:
                ctx.cwd = _change_directory(ctx.cwd, chdir)
                ctx.cwd_options = tuple(dict.fromkeys(_change_directory(o, chdir) for o in ctx.cwd_options))
            try:
                _parse_into(split, ctx, (*via, "env -S"), depth + 1, indirect)
            finally:
                ctx.cwd, ctx.cwd_options = saved
            return None, "env", None
        if arg == "--" or not arg.startswith("-"):
            break
        index += 2 if arg in spec.with_arg else 1
    inner = _skip_options(args, PREFIX_WRAPPERS["env"])
    return (inner or None), "env", chdir


def option_value(args: list[str] | tuple[str, ...], names: frozenset[str]) -> str | None:
    """The value of the first option in `names`, in any spelling: `-c X`, `-cX`,
    `--command X`, `--command=X`. Stops at `--`."""
    for index, arg in enumerate(args):
        if arg == "--":
            return None
        for name in names:
            if arg == name:
                return args[index + 1] if index + 1 < len(args) else None
            if name.startswith("--") and arg.startswith(name + "="):
                return arg.split("=", 1)[1]
            short_attached = len(name) == 2 and not arg.startswith("--") and arg.startswith(name)
            if short_attached and len(arg) > 2:
                return arg[2:]
    return None


def _split_string_value(args: list[str], index: int, names: tuple[str, ...]) -> str | None:
    arg = args[index]
    for name in names:
        if arg == name:
            return " ".join(args[index + 1:])
        if arg.startswith(name + "=") or (len(name) == 2 and arg.startswith(name) and len(arg) > 2):
            first = arg.split("=", 1)[1] if "=" in arg and name.startswith("--") else arg[len(name):]
            return " ".join([first, *args[index + 1:]])
    return None


def _skip_options(args: list[str], spec: WrapperSpec) -> list[str] | None:
    """Skip a wrapper's options and own positionals. None if it runs nothing."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if not arg.startswith("-") or arg == "-":
            break
        if arg in spec.no_exec or arg.split("=", 1)[0] in spec.no_exec:
            return None
        if arg.startswith("--"):
            index += 1 if "=" in arg else (2 if arg in spec.with_arg else 1)
            continue
        if arg[1:].isdigit():  # `nice -10`
            index += 1
            continue
        if any(f"-{letter}" in spec.no_exec for letter in arg[1:]):
            return None
        if arg[:2] in spec.with_arg and len(arg) > 2:
            index += 1  # value attached: `-uroot`, `-oL`
        elif f"-{arg[-1]}" in spec.with_arg:
            index += 2  # last letter of a cluster takes the next word: `-Eu root`
        else:
            index += 1
    index += spec.positionals
    return args[index:] if index <= len(args) else []


def _skip_subcommand_runner(args: list[str], name: str) -> tuple[str, list[str]] | None:
    """(subcommand, command it runs) for `uv [opts] run [opts] cmd...` and the like.

    Global options may come before the subcommand (`uv --directory . run ...`).
    """
    runner = SUBCOMMAND_RUNNERS[name]
    global_with_value = runner.with_arg | CLI_OPTIONS_WITH_VALUE.get(name, frozenset())
    index = 0
    while index < len(args) and args[index].startswith("-") and args[index] != "--":
        index += 2 if args[index] in global_with_value else 1
    if index >= len(args) or args[index] not in runner.subcommands:
        return None
    subcommand, rest = args[index], args[index + 1:]
    if "--" in rest:
        inner = rest[rest.index("--") + 1:]
    else:
        inner = _skip_options(rest, WrapperSpec(runner.with_arg, runner.positionals)) or []
    return (subcommand, inner) if inner else None


# --------------------------------------------------------------------------- #
# Program text a command runs: shells, eval, ssh, find -exec, scripts
# --------------------------------------------------------------------------- #


def _parse_payloads(command: SimpleCommand, ctx: _Context, depth: int) -> None:
    name, args, via = command.name, list(command.args), command.via
    nested_via = via
    indirect = command.indirect

    def nested(text: str, label: str, from_file: bool = False, shares_shell: bool = False) -> None:
        _parse_into(text, ctx, (*nested_via, label), depth + 1, indirect or from_file, shares_shell)

    if name in SHELLS:
        _shell_payload(command, args, ctx, nested)
    elif inline_programs(name, command.args, command.redirects):
        for program in inline_programs(name, command.args, command.redirects):
            for literal in shell_strings(program):
                nested(literal, f"{name} string")
    elif awk_commands(name, command.args):
        for literal in awk_commands(name, command.args):
            nested(literal, f"{name} system()")
    elif name == "eval":
        if args:
            nested(" ".join(args), "eval", shares_shell=True)
    elif name in ("source", ".") and args:
        # `source <(printf CMD)`: the shell runs what the substitution prints.
        _produced_script(args[0], command, ctx, nested, shares_shell=True)
    elif name in STRING_RUNNERS:
        spec = STRING_RUNNERS[name]
        if spec.command_options:
            value = option_value(args, spec.command_options)
            if value is not None:
                nested(value, f"{name} -c")
        else:
            inner = _skip_options(args, spec)
            if inner:
                nested(" ".join(inner), name)
    elif name in ("ssh", "autossh"):
        remote = _skip_options(args, WrapperSpec(SSH_OPTIONS_WITH_ARG))
        if remote and len(remote) > 1:
            nested(" ".join(remote[1:]), f"ssh {remote[0]}")
    elif name == "find":
        for inner in _find_exec_commands(args):
            _emit(inner, (), ctx, (*via, "find -exec"), depth + 1, indirect, ctx.next_pipeline(),
                  0, False, ("find",))
    elif name == "parallel" and "--dry-run" not in args:
        inner = _parallel_command(args)
        if inner:
            # parallel runs the argv it was given; keep each word's quoting.
            nested(shlex.join(inner), "parallel")

    if depth < MAX_DEPTH:
        for text, label in _sources_for(command, ctx):
            if text == SOURCE_TOO_LARGE:
                ctx.result.truncated = True
                ctx.result.skipped_sources.append((command, label))
                continue
            # A file written earlier in this same command line was typed by the agent.
            # `source`/`.` run the file in this shell: its cd and assignments persist.
            nested(text, label, from_file=not label.startswith("written "),
                   shares_shell=command.name in ("source", "."))


def _shell_payload(command: SimpleCommand, args: list[str], ctx: _Context,
                   nested: Callable[..., None]) -> None:
    """`bash -c STR` (and fish's command options), and a script read from stdin:
    `bash <<EOF`, `bash <<< STR`, `bash < file`, `echo STR | bash`,
    `cat file | bash`, `bash -s args < file`."""
    shell = shell_invocation(command.name, args)
    if shell.syntax_only:
        return  # `-n`: read and syntax-check only, never execute
    for text in shell.commands:
        nested(_bind_positionals(text, list(shell.command_args)), f"{command.name} -c")
    if shell.script:
        _produced_script(shell.script, command, ctx, nested)  # `bash <(echo CMD)`
    if not shell.reads_stdin:
        return  # a command string or a script file (shell_sources resolves the file)
    for redirect in command.redirects:
        if redirect.op in HEREDOC_OPERATORS and redirect.body is not None:
            nested(redirect.body, f"{command.name} <<heredoc")
        elif redirect.op == "<<<":
            nested(redirect.target, f"{command.name} <<<")
        elif redirect.op == "<" and _produced_script(redirect.target, command, ctx, nested):
            pass  # `bash < <(printf CMD)`
        elif redirect.op == "<" and not redirect.dynamic:
            # `bash < script.sh` runs the file just like `bash script.sh`.
            _run_file(redirect.target, command, ctx, nested)
    feeder = ctx.result.previous_stage(command)
    if feeder is None or feeder.dynamic_head:
        return
    if feeder.name in ("echo", "printf"):
        text = _echoed_text(feeder)
        if text:
            nested(text, f"{feeder.name} | {command.name}")
    elif feeder.name == "cat" and len(feeder.args) == 1:
        # `cat script.sh | bash` runs the file just like `bash script.sh`.
        _run_file(feeder.args[0], command, ctx, nested)


def _produced_script(operand: str, shell: SimpleCommand, ctx: _Context, nested: Callable[..., None],
                     shares_shell: bool = False) -> bool:
    """A script given as `<(producer)`: parse what the producer prints when that
    is known statically (`echo`/`printf` text, `cat FILE`). False if `operand`
    is not a process substitution."""
    if not (operand.startswith("<(") and operand.endswith(")")):
        return False
    producer = parse(operand[2:-1], shell.cwd or "/")
    for command in producer.commands:
        if command.via or command.dynamic_head:
            continue
        if command.name in ("echo", "printf"):
            text = _echoed_text(command)
            if text:
                nested(text, f"{shell.name} <({command.name})", shares_shell=shares_shell)
        elif command.name == "cat" and len(command.args) == 1:
            _run_file(command.args[0], shell, ctx, nested)
    return True


def _run_file(path: str, shell: SimpleCommand, ctx: _Context, nested: Callable[..., None]) -> None:
    """Parse a file a shell reads as its program, via the source resolver."""
    as_script = SimpleCommand((shell.argv[0], path), indirect=shell.indirect, cwd=shell.cwd,
                              cwd_options=shell.cwd_options)
    for text, label in _sources_for(as_script, ctx):
        nested(text, label, not label.startswith("written "))


def _sources_for(command: SimpleCommand, ctx: _Context) -> list[tuple[str, str]]:
    """Program text `command` runs from disk, looked up in every directory it might
    run in (`cd a || cd b; ./x.sh` may run a/x.sh or b/x.sh). Duplicates are dropped."""
    if ctx.sources is None:
        return []
    found: list[tuple[str, str]] = []
    for directory in command.possible_cwds:
        if directory is None:
            continue
        for item in ctx.sources(command, directory, ctx.written_files):
            if item not in found:
                found.append(item)
    return found


_POSITIONAL = re.compile(r'"\$\{?(\d)\}?"|\$\{(\d+)\}|\$(\d)|"\$[@*]"|\$[@*]')


def _bind_positionals(script: str, arguments: list[str]) -> str:
    """`bash -c 'rm -rf "$1"' _ ~` runs `rm -rf ~`: substitute literal arguments.

    arguments[0] is $0; the rest are $1, $2, ... An unset parameter is left
    as written, which the guards treat as an unknown value.
    """
    if not arguments or "$" not in script:
        return script

    def replace(match: re.Match[str]) -> str:
        number = match.group(1) or match.group(2) or match.group(3)
        if number is None:
            return " ".join(shlex.quote(arg) for arg in arguments[1:])
        index = int(number)
        return shlex.quote(arguments[index]) if index < len(arguments) else match.group(0)

    return _POSITIONAL.sub(replace, script)


def _feeder(ctx: _Context, pipeline: int, stage: int) -> SimpleCommand | None:
    """The command piping into stage `stage` of `pipeline`, if already recorded."""
    if stage == 0:
        return None
    return next((c for c in reversed(ctx.result.commands) if c.pipeline == pipeline and c.stage == stage - 1),
                None)


def _xargs_runs(args: list[str], inner: list[str], feeder: SimpleCommand | None) -> list[list[str]]:
    """The command lines xargs builds when its input is known text
    (`printf '%s\\n' -rf x | xargs rm` runs `rm -rf x`). `[inner]` otherwise."""
    own = args[:len(args) - len(inner)]
    if (feeder is None or feeder.dynamic_head or feeder.name not in ("echo", "printf")
            or option_value(own, frozenset({"-a", "--arg-file"})) is not None):
        return [inner]
    text = _echoed_text(feeder)
    replace = option_value(own, frozenset({"-I", "--replace"}))
    replace = replace or next((a[2:] or "{}" for a in own if a.startswith("-i")), None)
    delimiter = "\0" if ("-0" in own or "--null" in own) else option_value(own, frozenset({"-d", "--delimiter"}))
    if replace:
        separator = delimiter.encode().decode("unicode_escape") if delimiter else "\n"
        items = [item.strip() for item in text.split(separator) if item.strip()]
        runs = [[arg.replace(replace, item) for arg in inner] for item in items[:_MAX_BRACE_WORDS]]
        return runs or [inner]
    if delimiter:
        items = [item for item in text.split(delimiter.encode().decode("unicode_escape")) if item]
    else:
        try:
            items = shlex.split(text)
        except ValueError:
            items = text.split()
    return [inner + items]


def _echoed_text(command: SimpleCommand) -> str:
    args = list(command.args)
    if command.name == "echo":
        while args and re.fullmatch(r"-[neE]+", args[0]):
            args = args[1:]
        return " ".join(args)
    if args and "%" not in args[0]:
        return args[0].replace("\\n", "\n")
    if args and args[0] in ("%s\\n", "%b\\n"):
        return "\n".join(args[1:])
    return "".join(args[1:]) if args and args[0] in ("%s", "%b") else ""


def _find_exec_commands(args: list[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    index = 0
    while index < len(args):
        if args[index] in _FIND_EXEC:
            end = index + 1
            while end < len(args) and args[end] not in (";", "+"):
                end += 1
            if end > index + 1:
                commands.append(args[index + 1:end])
            index = end
        index += 1
    return commands


def _parallel_command(args: list[str]) -> list[str] | None:
    end = next((i for i, arg in enumerate(args) if arg in (":::", "::::", ":::+", "::::+")), len(args))
    command = args[:end]
    while command and command[0].startswith("-"):
        command = command[1:]
    return command or None
