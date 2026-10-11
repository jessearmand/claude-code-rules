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

import shell_ifs
import shell_params
import shell_printf
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
_ELEMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]")
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
# Starts the value of a compound array assignment; the elements follow, _FIELD-separated.
_ARRAY = "\x1e"
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
    # Files the line writes from literal text before using them (see _Context.written_files).
    written_files: dict[str, WrittenFile] = field(default_factory=dict)
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


@dataclass(frozen=True)
class _BodyStep:
    """One command of a function body, as declared."""

    tokens: tuple
    isolated: bool  # a pipeline stage or background command: its own subshell
    conditional: bool  # after && / || or inside an if/loop in the body


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
    # Declared functions: name -> the body's commands, replayed at each call.
    functions: dict[str, list[_BodyStep]] = field(default_factory=dict)
    calling: set[str] = field(default_factory=set)  # functions being replayed (no recursion)
    # Names declared `local` in each function call being replayed, innermost last.
    local_scopes: list[set[str]] = field(default_factory=list)
    # Files this command line writes from literal text before running them:
    # `cat > x.sh <<'EOF' ... EOF; bash x.sh`. Absolute path -> content.
    written_files: dict[str, WrittenFile] = field(default_factory=dict)
    # `alias x=/bin/rm`: name -> replacement text, expanded at the head of later commands.
    aliases: dict[str, str] = field(default_factory=dict)
    expanding: set[str] = field(default_factory=set)  # aliases being expanded (no recursion)
    # `declare -n Y=X`: Y names X. `declare -l/-u X`: X's value is lower/upper-cased.
    namerefs: dict[str, str] = field(default_factory=dict)
    case_attributes: dict[str, str] = field(default_factory=dict)
    # `cp /bin/rm x`: x is now that program (absolute paths); links are in result.links.
    copies: dict[str, str] = field(default_factory=dict)

    def next_pipeline(self) -> int:
        self.pipeline_counter[0] += 1
        return self.pipeline_counter[0]


def parse(command: str, cwd: str = "", sources: SourceResolver | None = None) -> ParseResult:
    """Every simple command `command` would run. Never raises."""
    start = cwd or os.getcwd()
    context = _Context(ParseResult(), start, sources, (start,))
    _parse_into(command, context, via=(), depth=0, indirect=False)
    context.result.written_files = dict(context.written_files)
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
    # Function bodies being declared: (block depth before the body, state to restore
    # at its end, name, the body's commands).
    functions: list[tuple[int, tuple[str | None, tuple[str | None, ...], dict[str, tuple[str, ...]]], str,
                          list[_BodyStep]]] = []
    header = _FunctionHeader()
    for token in [*lexed.tokens, Operator("\n")]:
        if isinstance(token, Operator) and (token.op in _SEPARATORS or token.op in _PIPES):
            if segment:
                # After `&&`/`||`, or inside an if/loop/case body, the command may not run.
                ctx.conditional = outer_conditional or previous in _AND_OR or blocks > 0
                if header.opens_body(segment):
                    # A declared function runs only when called: check its body, but
                    # do not let its assignments or cd change the declaring shell.
                    functions.append((blocks, (ctx.cwd, ctx.cwd_options, dict(ctx.variables)), header.name, []))
                isolated = token.op in _PIPES or stage > 0 or token.op == "&"
                if functions:
                    level = functions[-1][0]
                    functions[-1][3].append(_BodyStep(tuple(segment), isolated,
                                                      previous in _AND_OR or blocks > level + 1))
                blocks = _track_blocks(segment, blocks)
                if isolated:
                    # Every part of a pipeline, and a background command, runs in its own subshell.
                    saved = (ctx.cwd, ctx.cwd_options, dict(ctx.variables))
                    _segment(segment, ctx, via, depth, indirect, pipeline, stage)
                    ctx.cwd, ctx.cwd_options, ctx.variables = saved
                else:
                    _segment(segment, ctx, via, depth, indirect, pipeline, stage)
                while functions and blocks <= functions[-1][0]:
                    _, saved, name, body = functions.pop()
                    if name:
                        ctx.functions[name] = body  # replayed, with its arguments, at each call
                    ctx.cwd, ctx.cwd_options, ctx.variables = saved
                header.after_segment(segment)
                segment = []
            header.after_operator(token.op)
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


class _FunctionHeader:
    """Spots where a function body starts: `function NAME {` or `NAME() {`."""

    def __init__(self) -> None:
        self._name_seen = ""  # a one-word command, possibly a function name
        self._parens = 0  # 1 after `NAME (`, 2 after `NAME ( )`
        self.name = ""  # the function whose body opens_body last found

    def opens_body(self, segment: list) -> bool:
        words = [t for t in segment if isinstance(t, Word)]
        if words and words[0].text == "function" and not words[0].quoted:
            self.name = words[1].text if len(words) > 1 else ""
            return True
        if self._parens == 2 and words and words[0].text == "{" and not words[0].quoted:
            self.name = self._name_seen
            return True
        return False

    def after_segment(self, segment: list) -> None:
        single = len(segment) == 1 and isinstance(segment[0], Word)
        self._name_seen = segment[0].text if single else ""
        self._parens = 0

    def after_operator(self, op: str) -> None:
        if op == "(" and self._name_seen and self._parens == 0:
            self._parens = 1
        elif op == ")" and self._parens == 1:
            self._parens = 2
        elif not (op == "\n" and self._parens == 2):  # `f()` may end a line before `{`
            self._name_seen, self._parens = "", 0


def _track_blocks(tokens: list, blocks: int) -> int:
    """Open if/loop/case/group bodies after this command's leading reserved words
    (`function NAME` included: its `{` opens the body)."""
    words = [t for t in tokens if isinstance(t, Word)]
    if words and words[0].text == "function" and not words[0].quoted:
        words = words[2:]
    for token in words:
        if token.quoted or token.text not in RESERVED_PREFIXES | _BLOCK_OPENERS | _BLOCK_CLOSERS:
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
        elif isinstance(token, Word) and token.elements is not None:
            words.append(_compound_word(token, ctx, choice))
        elif isinstance(token, Word):
            params = _positionals(ctx) if token.text in ("$@", "${@}", "$*", "${*}") else None
            pieces = _at_word_pieces(token, ctx) if params is None else None
            if params is not None:
                # `"$@"`: one word per positional parameter; quoted `"$*"` joins them
                # with the first character of IFS (`IFS=; "$*"` runs them together).
                joined = token.quoted and "*" in token.text
                words.extend([Word(_star_separator(ctx).join(params))] if joined else [Word(p) for p in params])
            elif pieces is not None:
                for piece in pieces:
                    words.extend(_split_unquoted(piece, _expand_word(piece, ctx, choice), ctx))
            else:
                for piece in _brace_expand(token, ctx):
                    words.extend(_split_unquoted(piece, _expand_word(piece, ctx, choice), ctx))
        index += 1

    words, environment = _strip_prelude(words, ctx)
    words = _expand_alias(words, ctx, via, depth, indirect)
    if words is None:
        return
    _builtin_mutation([word.text for word in words], redirects, ctx, environment)
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


def _at_word_pieces(token: Word, ctx: _Context) -> list[Word] | None:
    """`r"$@"x` with $1=a $2=b is two words, `ra` and `bx`: each parameter is its own
    word, the surrounding text joins the first and last. None if not that shape."""
    marker = next((m for m in ("${@}", "$@") if m in token.text), None)
    params = _positionals(ctx) if marker else None
    if params is None or token.text.count(marker) != 1:
        return None
    prefix, suffix = token.text.split(marker)
    if not params:
        texts = [prefix + suffix] if prefix or suffix else []
    elif len(params) == 1:
        texts = [prefix + params[0] + suffix]
    else:
        texts = [prefix + params[0], *params[1:-1], params[-1] + suffix]
    return [Word(text, dynamic=bool(_UNRESOLVED.search(text)), quoted=token.quoted, splits=token.splits)
            for text in texts]


def _glob_words(words: list[Word], ctx: _Context) -> list[Word]:
    """Pathname expansion of unquoted words, in the directory the command runs in:
    `/bin/r[m]` runs /bin/rm, and `rm -* x` next to a file named `-rf` is
    `rm -rf x`. A pattern matching nothing stays literal, as in bash."""
    if ctx.cwd is None:
        return words
    expanded: list[Word] = []
    for index, word in enumerate(words):
        expanded += _glob_word(word, ctx, command_word=index == 0)
    return expanded


def _glob_word(word: Word, ctx: _Context, command_word: bool = False) -> list[Word]:
    cwd = ctx.cwd or "/"
    if word.quoted or word.dynamic or not _GLOB_CHARS.search(word.text):
        return [word]
    try:
        matches = sorted(glob.glob(os.path.expanduser(word.text), root_dir=cwd))
    except (OSError, ValueError):
        return [word]
    if not matches:
        return [word]
    if len(matches) > _MAX_BRACE_WORDS:
        # Too many to list: keep the pattern, plus any match that reads as an option,
        # and in command position the first match, which is what runs. Incomplete.
        ctx.result.truncated = True
        head = [Word(matches[0])] if command_word else []
        return [*head, word, *(Word(m) for m in matches if m.startswith("-"))]
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


def _compound_word(token: Word, ctx: _Context, choice: dict[str, str]) -> Word:
    """`X=(r "a b" $Y)` as one assignment word: `X=` then the expanded elements,
    marked as an array, which `_assign` stores as X[0], X[1]..."""
    items: list[str] = []
    for element in token.elements or []:
        for piece in _brace_expand(element, ctx):
            items += [w.text for w in _split_unquoted(piece, _expand_word(piece, ctx, choice), ctx)]
    text = token.text + _ARRAY + _FIELD.join(items)
    return Word(text, dynamic=bool(_UNRESOLVED.search(text)), substitutions=token.substitutions)


def _split_unquoted(original: Word, expanded: Word, ctx: _Context) -> list[Word]:
    """Word splitting: an unquoted expansion holding IFS characters becomes several
    words (`X='rm -rf ~'; $X` runs rm with two arguments; so does `IFS=,; X=rm,-rf,~`).
    Quoted `"$X"` stays one."""
    if not original.splits or not original.dynamic or expanded.text == original.text:
        return [expanded]
    splits = [tuple(shell_ifs.split(expanded.text, ifs)) for ifs in _ifs_values(ctx)]
    if len(set(splits)) > 1:
        ctx.result.truncated = True  # IFS may hold several values that split this differently
    parts = splits[0]
    if len(parts) <= 1:
        # `IFS=,; X=rm,; $X` runs `rm`: the delimiters go even when one word remains.
        return [expanded] if not parts or parts[0] == expanded.text else [
            Word(parts[0], dynamic=bool(_UNRESOLVED.search(parts[0])), substitutions=expanded.substitutions)]
    return [Word(part, dynamic=bool(_UNRESOLVED.search(part)), substitutions=expanded.substitutions if i == 0 else [])
            for i, part in enumerate(parts)]


def _star_separator(ctx: _Context) -> str:
    """What `"$*"` puts between parameters: IFS's first character."""
    return _ifs_values(ctx)[0][:1]


def _ifs_values(ctx: _Context, environment: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Every value IFS may hold: `IFS=, read ...` sets it for one command; unset means the default."""
    for text in reversed(environment):
        if text.startswith("IFS="):
            return (text[4:],)
    values = ctx.variables.get("IFS", (UNSET,))
    return tuple(dict.fromkeys(shell_ifs.DEFAULT if value == UNSET else value for value in values))


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
        if name.isdigit() or name in ("#", "@", "*"):
            params = _positionals(ctx)
            if params is None or name == "0":
                return None
            if name in ("@", "*"):
                # Inside a longer word: `"$@"m` with $1=r is rm.
                return (_star_separator(ctx) if name == "*" else " ").join(params)
            return str(len(params)) if name == "#" else (params[int(name) - 1] if int(name) <= len(params) else "")
        name = _target_name(name, ctx)
        if choice and name in choice:
            return None if choice[name] == UNSET else _with_case(name, choice[name], ctx)
        if name in ctx.variables:
            current = ctx.variables[name][0]
            return None if current == UNSET else _with_case(name, current, ctx)
        if "[" in name:
            return None
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

    def assign(name: str, text: str, certain: bool) -> None:
        # `${X:=word}` assigns word when X is unset (or empty). If X is not known
        # it may already be set, so both stay possible: word, or unknown.
        if not certain and name not in ctx.variables:
            ctx.variables[name] = (UNSET,)
        _assign(f"{name}={text}", ctx.variables, ctx.conditional or not certain)
        if choice is not None and name in choice:
            choice[name] = text

    text = _expand_text(word.text, value, at_start=True, assign=assign)
    return Word(text, dynamic=bool(_UNRESOLVED.search(text)), quoted=word.quoted, splits=word.splits,
                heredoc_body=word.heredoc_body, substitutions=word.substitutions)


# Records `${NAME:=word}`: (name, value, whether the assignment certainly happens).
_Assigner = Callable[[str, str, bool], None]


def _expand_text(text: str, value: Callable[[str, bool], str | None], at_start: bool,
                 assign: _Assigner | None = None) -> str:
    """Replace `$NAME`/`${NAME}` whose value is known, and resolve `${NAME:-word}`,
    `${NAME:=word}` and `${NAME:+word}` to the value they select. When NAME is not
    known it may be unset, so the default or alternate `word` is the candidate:
    `> "${OUT:-$HOME/.claude/settings.json}"` writes settings.json. Anything else
    (`${#X}`, `${X/a/b}`, unknown names) stays as written. `assign` records
    what `${NAME:=word}` assigns, which later expansions then see."""
    out: list[str] = []
    index = 0
    while index < len(text):
        literal = _literal_substitution(text, index)
        if literal is not None:
            # `$(printf git)` / `` `echo git` ``: the output of a literal echo is known.
            output, index = literal
            out.append(output)
            continue
        if text.startswith("${", index):
            end = _closing_brace(text, index + 2)
            if end is None:
                out.append(text[index:])
                break
            out.append(_expand_braced(text[index + 2:end], text[index:end + 1], value,
                                      at_start and index == 0, assign))
            index = end + 1
            continue
        name = _NAME.match(text, index + 1) if text[index] == "$" else None
        if name is None and text[index] == "$" and index + 1 < len(text) and text[index + 1] in "0123456789#@*":
            name = _ONE_CHAR.match(text, index + 1)  # `$1`, `$#`, `$@`
        if name:
            found = value(name.group(0), at_start and index == 0)
            out.append(text[index:name.end()] if found is None else found)
            index = name.end()
            continue
        out.append(text[index])
        index += 1
    return "".join(out)


def _literal_substitution(text: str, index: int) -> tuple[str, int] | None:
    """(output, end) for a command substitution at `index` whose body only echoes
    literal text; trailing newlines are dropped, as bash does. None otherwise."""
    if text.startswith("$(", index) and not text.startswith("$((", index):
        depth, end = 1, index + 2
        while end < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[end], 0)
            end += 1
        if depth:
            return None
        body = text[index + 2:end - 1]
    elif text[index] == "`":
        close = text.find("`", index + 1)
        if close < 0:
            return None
        body, end = text[index + 1:close], close + 1
    else:
        return None
    producer = parse(body, "/")
    temporary = _mktemp_path(producer)
    if temporary is not None:
        return temporary, end
    if not producer.commands or producer.unparseable or any(
            c.name not in ("echo", "printf") or c.dynamic_head or c.via or c.redirects
            or any("$" in a or "`" in a for a in c.argv) for c in producer.commands):
        return None
    printed = [shell_printf.printed_text(c.name, c.args) for c in producer.commands]
    if any(p is None for p in printed):
        return None
    return "".join(printed).rstrip("\n"), end


def _mktemp_path(producer: ParseResult) -> str | None:
    """`$(mktemp)`, `$(mktemp -d -p DIR)`, `$(mktemp -t name.XXXX)`: a new path in
    a known directory. Its name is random, so the template stands for it."""
    if len(producer.commands) != 1 or producer.commands[0].name != "mktemp" or producer.commands[0].via:
        return None
    args = list(producer.commands[0].args)
    directory, template, under_tmpdir, index = None, None, False, 0
    while index < len(args):
        arg = args[index]
        if arg in ("-p", "--tmpdir") and index + 1 < len(args):
            directory, index = args[index + 1], index + 1
        elif arg.startswith("--tmpdir="):
            directory = arg.split("=", 1)[1]
        elif arg == "-t":
            under_tmpdir = True
        elif arg.startswith("--suffix=") or arg in ("-d", "--directory", "-u", "--dry-run", "-q", "--quiet"):
            pass
        elif arg.startswith("-"):
            return None
        else:
            template = arg
        index += 1
    if any("$" in part for part in (directory or "", template or "")):
        return None
    temporary_root = os.environ.get("TMPDIR") or "/tmp"
    if template is None:
        return os.path.join(directory or temporary_root, "tmp.XXXXXXXXXX")
    if directory is None and not under_tmpdir:
        return template  # `mktemp out.XXXX`: relative to the current directory
    return os.path.join(directory or temporary_root, template)


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
                   at_start: bool, assign: _Assigner | None = None) -> str:
    if _NAME.fullmatch(body) or body.isdigit() or body in ("@", "*"):
        found = value(body, at_start)
        return original if found is None else found
    element = _ELEMENT.fullmatch(body)
    if element:
        # `${X[0]}`: elements are stored as X[0], X[1]...; X[0] is also plain $X.
        found = value(f"{element.group(1)}[{int(element.group(2))}]", False)
        if found is None and int(element.group(2)) == 0:
            found = value(element.group(1), False)
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
        result = shell_params.apply(current, _expand_text(body[name.end():], value, False, assign))
        return original if result is None else result
    name, op, word = match.groups()
    current = value(name, False)
    if op.endswith("?"):
        return original if current is None else current
    alternative = _expand_text(word, value, at_start, assign)
    set_and_used = current is not None and (current != "" or not op.startswith(":"))
    if op.endswith("+"):
        # Unknown NAME may be set: the alternate word is the value to check.
        return alternative if current is None or set_and_used else ""
    if op.endswith("=") and not set_and_used and assign is not None:
        assign(name, alternative, current is not None)
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
            _assign(_through_nameref(text, ctx), ctx.variables, ctx.conditional)
        return rest, ()
    if rest[0].text in _SPECIAL_BUILTINS and not rest[0].quoted:
        _persist_prefix(assignments, rest, ctx)
    # `export X=rm` / `readonly X=...` / `declare X=...`: also an assignment.
    if rest and rest[0].text in ("export", "readonly", "declare", "typeset", "local"):
        for word in _declare_attributes(rest[0].text, rest[1:], ctx):
            if ctx.local_scopes and rest[0].text in ("local", "declare", "typeset"):
                # Inside a function these shadow the caller's variable until it returns.
                name = word.text.split("=", 1)[0].split("[", 1)[0].rstrip("+")
                if _NAME.fullmatch(name):
                    ctx.local_scopes[-1].add(name)
                    if "=" not in word.text:
                        ctx.variables[name] = (UNSET,)  # `local X`: a new, unset X
            if _ASSIGNMENT.match(word.text):
                _assign(_through_nameref(word.text, ctx), ctx.variables, ctx.conditional)
    return rest, tuple(assignments)


# POSIX special builtins: assignments before them stay in the shell (sh, dash,
# bash --posix). Plain bash keeps them only when `export`/`readonly` names them.
_SPECIAL_BUILTINS = frozenset({":", ".", "source", "break", "continue", "eval", "exec", "exit", "export",
                               "readonly", "return", "set", "shift", "times", "trap", "unset"})


def _persist_prefix(assignments: list[str], rest: list[Word], ctx: _Context) -> None:
    """`X=r export X` keeps X=r in every shell; `X=r :` keeps it in POSIX shells
    only, so there the value is one possibility beside the old one."""
    named = {w.text.split("=", 1)[0] for w in rest[1:]} if rest[0].text in ("export", "readonly") else set()
    for text in assignments:
        name = text.split("=", 1)[0].rstrip("+")
        certain = name in named
        if not certain and name not in ctx.variables:
            ctx.variables[name] = (UNSET,)
        _assign(_through_nameref(text, ctx), ctx.variables, ctx.conditional or not certain)


def _through_nameref(text: str, ctx: _Context) -> str:
    """`NAME=value` with NAME replaced by the variable it refers to (`declare -n`)."""
    name, equals, value = text.partition("=")
    base = name.split("[", 1)[0].rstrip("+")
    target = _target_name(base, ctx)
    return text if target == base else target + name[len(base):] + equals + value


def _expand_alias(words: list[Word], ctx: _Context, via: tuple[str, ...], depth: int,
                  indirect: bool) -> list[Word] | None:
    """`alias x=/bin/rm; x -rf v` runs `/bin/rm -rf v`. Bash expands aliases only
    with `expand_aliases` (sh, zsh and interactive shells always do), so a known
    alias is applied whatever the mode. An alias that is a plain word list
    replaces the command word; one holding operators or expansions is parsed as
    command text with the arguments appended (None: nothing left to emit)."""
    if not words or words[0].quoted or words[0].dynamic:
        return words
    name = words[0].text
    if name not in ctx.aliases or name in ctx.expanding:
        return words
    replacement = ctx.aliases[name]
    lexed = lex(replacement)
    tokens = lexed.tokens
    ctx.expanding.add(name)
    try:
        if tokens and not lexed.unterminated and not lexed.substitutions and all(isinstance(t, Word) and not t.dynamic for t in tokens):
            return _expand_alias([*tokens, *words[1:]], ctx, via, depth, indirect)
        _parse_into(" ".join([replacement, *(shlex.quote(w.text) for w in words[1:])]), ctx,
                    (*via, f"alias {name}"), depth + 1, indirect, shares_shell=True)
    finally:
        ctx.expanding.discard(name)
    return None


def _define_aliases(args: list[str], ctx: _Context) -> None:
    """`alias NAME=VALUE...` defines; `unalias NAME...` / `unalias -a` removes."""
    for arg in args:
        name, equals, value = arg.partition("=")
        if equals and name and not name.startswith("-"):
            ctx.aliases[name] = value


def _declare_attributes(builtin: str, words: list[Word], ctx: _Context) -> list[Word]:
    """Apply `declare`/`typeset`/`local` attribute flags; the words left to assign.

    `-n` makes a name refer to another variable, `-l`/`-u` change the case of
    every value, `-i` makes it a number (never a command name). Other flags
    (arrays, export, readonly, scope) do not change what a value expands to.
    A computed flag word is not known, so the analysis is incomplete.
    """
    flags = ""
    rest = list(words)
    while rest and rest[0].text[:1] in "-+" and len(rest[0].text) > 1 and "=" not in rest[0].text:
        if rest[0].dynamic:
            ctx.result.truncated = True
        if rest[0].text.startswith("-"):
            flags += rest[0].text[1:]
        rest = rest[1:]
    if builtin == "export" or not flags:
        return rest
    remaining: list[Word] = []
    for word in rest:
        name, equals, value = word.text.partition("=")
        name = name.split("[", 1)[0].rstrip("+")
        if not _NAME.fullmatch(name):
            remaining.append(word)
            continue
        if "n" in flags:
            if equals and _NAME.fullmatch(value):
                ctx.namerefs[name] = value
            else:
                # The referenced name is not known (a function body before its call
                # binds `$1`): what the reference reads is unknown.
                ctx.namerefs.pop(name, None)
                ctx.variables.pop(name, None)
            continue
        for letter in "lu":
            if letter in flags:
                ctx.case_attributes[name] = letter
        if "i" in flags:
            ctx.variables.pop(name, None)  # integer: whatever is assigned evaluates to a number
            continue
        remaining.append(word)
    return remaining


def _target_name(name: str, ctx: _Context) -> str:
    """The variable `name` stands for: through `declare -n` references."""
    for _ in range(8):
        if name not in ctx.namerefs:
            break
        name = ctx.namerefs[name]
    return name


def _with_case(name: str, value: str, ctx: _Context) -> str:
    attribute = ctx.case_attributes.get(name)
    return value.lower() if attribute == "l" else value.upper() if attribute == "u" else value


def _builtin_mutation(argv: list[str], redirects: list[Redirect], ctx: _Context,
                      environment: tuple[str, ...] = ()) -> None:
    """Builtins that set or clear a variable: `printf -v X FMT ARGS`, `unset X`,
    `set -- ARGS`, `read X <<< TEXT`. `environment` is the command's own
    prefix assignments (`IFS=, read A B`)."""
    if argv and argv[0] == "read":
        _read_builtin(argv[1:], redirects, ctx, _ifs_values(ctx, environment))
    elif argv and argv[0] == "alias":
        _define_aliases(argv[1:], ctx)
    elif argv and argv[0] == "unalias":
        if "-a" in argv[1:]:
            ctx.aliases.clear()
        for name in argv[1:]:
            ctx.aliases.pop(name, None)
    elif len(argv) >= 2 and argv[0] in ("for", "select") and _NAME.fullmatch(argv[1]):
        # `for X in a b; do ... done`: X takes each word, or keeps its value if the
        # list is empty. Without `in`, the words are the positional parameters.
        if len(argv) >= 3 and argv[2] == "in":
            items = tuple(argv[3:])
        else:
            items = _positionals(ctx)
        if items is None:
            ctx.variables.pop(argv[1], None)
        else:
            earlier = ctx.variables.get(argv[1], (UNSET,))
            ctx.variables[argv[1]] = tuple(dict.fromkeys((*items, *earlier)))
    elif len(argv) >= 4 and argv[0] == "printf" and argv[1] == "-v" and _NAME.fullmatch(argv[2]):
        text = _echoed_text(SimpleCommand(("printf", *argv[3:])))
        target = _target_name(argv[2], ctx)
        if text or "%" not in argv[3]:
            _assign(f"{target}={text}", ctx.variables, ctx.conditional)
        else:
            ctx.variables.pop(target, None)  # a format we do not model: the value is unknown
    elif argv and argv[0] == "shift":
        # `shift N`: $N+1 becomes $1; past the last parameter nothing changes.
        params = _positionals(ctx)
        count = argv[1] if len(argv) > 1 else "1"
        if not count.isdigit():
            ctx.variables.pop(_POSITIONALS, None)
        elif params is not None and int(count) <= len(params):
            _assign(f"{_POSITIONALS}={_FIELD.join(params[int(count):])}", ctx.variables, ctx.conditional)
    elif argv and argv[0] == "set":
        params = _set_positionals(argv[1:])
        if params is not None:
            _assign(f"{_POSITIONALS}={_FIELD.join(params)}", ctx.variables, ctx.conditional)
    elif argv and argv[0] == "unset":
        for name in argv[1:]:
            if _NAME.fullmatch(name):
                _assign(f"{name}={UNSET}", ctx.variables, ctx.conditional)


_READ_OPTIONS_WITH_VALUE = frozenset("adinNptu")


def _read_options(args: list[str]) -> tuple[dict[str, str], list[str]]:
    """`read`'s options (letter -> value, "" for flags) and its variable names.
    Clusters take their value from the rest of the word or the next one: `-rn1`."""
    options: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return options, args[index + 1:]
        if not arg.startswith("-") or arg == "-":
            return options, args[index:]
        for offset, letter in enumerate(arg[1:], start=2):
            if letter in _READ_OPTIONS_WITH_VALUE:
                rest = arg[offset:]
                if not rest:
                    index += 1
                    rest = _at(args, index) or ""
                options[letter] = rest
                break
            options[letter] = ""
        index += 1
    return options, []


def _read_builtin(args: list[str], redirects: list[Redirect], ctx: _Context, ifs_values: tuple[str, ...]) -> None:
    """`read A B <<< "x y z"` sets A=x, B="y z", splitting by each value IFS may
    hold. Input that is not literal text (a pipe, the terminal) makes the
    variables unknown."""
    options, names = _read_options(args)
    delimiter = options["d"][:1] or "\0" if "d" in options else "\n"
    if "a" in options:
        names.insert(0, options["a"])
    names = [_target_name(n, ctx) for n in names if _NAME.fullmatch(n)] or ["REPLY"]
    count_option = "N" if "N" in options else "n" if "n" in options else None
    if count_option and not options[count_option].isdigit():
        for name in names:
            ctx.variables.pop(name, None)  # `read -n "$K"`: how much is read is not known
        return
    text = next((r.target + "\n" if r.op == "<<<" else r.body for r in redirects
                 if (r.op == "<<<" and not r.dynamic) or (r.op in HEREDOC_OPERATORS and r.body is not None)),
                None)
    if text is None:
        # `read X < <(echo rm)`: what the producer prints, when that is known;
        # `read X < file`: the file, as written earlier on the line or on disk.
        text = next((t for r in redirects if r.op == "<" and not r.dynamic
                     and (t := _produced_text(r.target, ctx) or _input_file_text(r.target, ctx)) is not None),
                    None)
    array = "a" in options
    if text is None:
        for name in names:
            ctx.variables.pop(name, None)
        return
    if count_option == "N":
        # `-N K`: exactly K characters, delimiter or not, and no field splitting.
        line = text[:int(options["N"])]
        if "r" not in options:
            line = re.sub(r"\\(.)", r"\1", line)
        for position, name in enumerate(names):
            _assign(f"{name}={line if position == 0 else ''}", ctx.variables, ctx.conditional)
        return
    line = text.split(delimiter, 1)[0]
    if count_option == "n":
        line = line[:int(options["n"])]  # `-n K`: at most K characters of the line
    if "r" not in options:
        line = re.sub(r"\\(.)", r"\1", line)  # without -r, a backslash escapes the next character
    for reading, ifs in enumerate(ifs_values):
        # Another possible IFS gives another possible value; earlier readings stay possible.
        keep = ctx.conditional or reading > 0
        if array:
            # `read -a X <<< "a b"`: X[0]=a, X[1]=b (and $X is X[0]).
            name = names[0]
            if reading == 0:
                for key in [k for k in ctx.variables if k == name or k.startswith(name + "[")]:
                    ctx.variables.pop(key)
            for position, item in enumerate(shell_ifs.split(line, ifs)):
                _assign(f"{name}[{position}]={item}", ctx.variables, keep)
            continue
        fields = shell_ifs.split(line, ifs, len(names))
        for position, name in enumerate(names):
            _assign(f"{name}={fields[position] if position < len(fields) else ''}", ctx.variables, keep)


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
    append = name.endswith("+")
    name = name[:-1] if append else name
    if value.startswith(_ARRAY) and _NAME.fullmatch(name):
        _assign_array(name, value[1:].split(_FIELD) if value[1:] else [], append, variables, keep_previous)
        return
    element = _ELEMENT.fullmatch(name)
    # `X[2]=v` sets element X[2]; element 0 is also plain $X, as in bash.
    keys = [f"{element.group(1)}[{int(element.group(2))}]"] if element else []
    if not element or int(element.group(2)) == 0:
        keys.append(name.split("[", 1)[0])
    for key in keys:
        values = (tuple(("" if earlier == UNSET else earlier) + value for earlier in variables.get(key, ("",)))
                  if append else (value,))
        if keep_previous:
            values = (*values, *variables.get(key, ()))
        # Not capped: every value stays possible. Their number is bounded by the
        # assignments written in the command line itself.
        variables[key] = tuple(dict.fromkeys(values))


_INDEXED = re.compile(r"\[(\d+)\]=(.*)", re.DOTALL)


def _assign_array(name: str, items: list[str], append: bool, variables: dict[str, tuple[str, ...]],
                  keep_previous: bool) -> None:
    """`X=(a b)` sets X[0]=a, X[1]=b (and $X is X[0]); `X+=(c)` appends after the
    last element; `[N]=v` sets element N."""
    indexes = [int(m.group(1)) for k in variables if (m := re.fullmatch(re.escape(name) + r"\[(\d+)\]", k))]
    if not append and not keep_previous:
        for key in [k for k in variables if k == name or k.startswith(name + "[")]:
            variables.pop(key)
        indexes = []
    position = max(indexes) + 1 if append and indexes else 0
    for item in items:
        indexed = _INDEXED.fullmatch(item)
        if indexed:
            position, item = int(indexed.group(1)), indexed.group(2)
        _assign(f"{name}[{position}]={item}", variables, keep_previous)
        position += 1


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
        argv = [_executable(argv[0], ctx), *argv[1:]]
        name = program_name(argv[0])
        step = _unwrap_once(name, argv, ctx, via, depth, indirect)
        if step is None:
            break
        inner, label, chdir = step
        if name == "xargs" and inner:
            # Arguments xargs reads from a statically known feeder join the command.
            runs = _xargs_runs(argv[1:], inner, _feeder(ctx, pipeline, stage), ctx)
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
    _call_function(command, ctx, depth)
    if outer is not None:
        ctx.cwd, ctx.cwd_options = outer  # the wrapper's directory applied to the child only
    else:
        _follow_directory_change(command, ctx)


def _call_function(command: SimpleCommand, ctx: _Context, depth: int) -> None:
    """Calling a declared function runs its body here, in this shell, with the
    call's arguments as $1..$n: `f(){ "$1" -rf x; }; f rm` runs rm."""
    body = ctx.functions.get(command.name) if not command.dynamic_head else None
    if body is None or command.name in ctx.calling:
        return
    if depth >= MAX_DEPTH:
        ctx.result.truncated = True
        return
    positionals = ctx.variables.get(_POSITIONALS)
    conditional = ctx.conditional
    before = dict(ctx.variables)
    ctx.variables[_POSITIONALS] = (_FIELD.join(command.args),)
    ctx.calling.add(command.name)
    ctx.local_scopes.append(set())
    try:
        for step in body:
            ctx.conditional = conditional or step.conditional
            saved = (ctx.cwd, ctx.cwd_options, dict(ctx.variables)) if step.isolated else None
            _segment(list(step.tokens), ctx, (*command.via, f"{command.name}()"), depth + 1, command.indirect,
                     ctx.next_pipeline(), 0)
            if saved:
                ctx.cwd, ctx.cwd_options, ctx.variables = saved
    finally:
        # `local` variables give the caller its own values back.
        for name in ctx.local_scopes.pop():
            if name in before:
                ctx.variables[name] = before[name]
            else:
                ctx.variables.pop(name, None)
        ctx.calling.discard(command.name)
        ctx.conditional = conditional
        # The caller's positional parameters come back when the function returns.
        if positionals is None:
            ctx.variables.pop(_POSITIONALS, None)
        else:
            ctx.variables[_POSITIONALS] = positionals


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
    # The exact bytes written: a heredoc ends with a newline, `echo -n` and
    # `printf r` do not, so `printf r > f; printf m >> f` builds `rm`.
    body = next((r.body + ("" if r.body.endswith("\n") else "\n") for r in command.redirects
                 if r.op in HEREDOC_OPERATORS and r.body is not None), None)
    if body is None and command.name in ("echo", "printf") and not command.dynamic_head:
        body = shell_printf.printed_text(command.name, command.args)
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
            ctx.written_files[path] = WrittenFile(earlier.text + body, earlier.replaces)
        else:
            ctx.written_files[path] = WrittenFile(body, replaces=not appends)


def _executable(word: str, ctx: _Context) -> str:
    """The program a path runs once this line's links and copies are followed:
    `ln -s /bin/rm x; ./x` and `cp /bin/rm x; ./x` run /bin/rm. A bare name is
    looked up in PATH, which is not modelled, and stays as written."""
    if "/" not in word or ctx.cwd is None:
        return word
    path = os.path.normpath(os.path.join(ctx.cwd, os.path.expanduser(word)))
    seen = {path}
    while (following := ctx.result.links.get(path) or ctx.copies.get(path)) and following not in seen:
        path = following
        seen.add(path)
    return word if len(seen) == 1 else path


# Options taking a value, per command that makes a path stand for another.
_PATH_MAKER_VALUES = {
    "ln": frozenset({"-S", "--suffix"}),
    "cp": frozenset({"-S", "--suffix"}),
    "mv": frozenset({"-S", "--suffix"}),
    "install": frozenset({"-S", "--suffix", "-m", "--mode", "-o", "--owner", "-g", "--group"}),
}


def _record_link(command: SimpleCommand, ctx: _Context) -> None:
    """Remember the links `ln` creates, so later writes through them resolve to the
    target: `ln -s ~/.claude/settings.json x; printf y > x` writes settings.json.
    Copies and moves (`cp`, `mv`, `install`) are remembered too, for what a later
    command runs: `cp /bin/rm x; ./x -rf v` runs rm."""
    if command.name not in _PATH_MAKER_VALUES or command.dynamic_head:
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
        elif arg in _PATH_MAKER_VALUES[command.name]:
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
            if command.name != "ln":
                ctx.copies[link] = os.path.normpath(os.path.join(cwd, target))
                continue
            if not os.path.isabs(target):
                target = os.path.join(os.path.dirname(link) if symbolic else cwd, target)
            ctx.result.links[link] = os.path.normpath(target)


def _at(args: list[str], index: int) -> str | None:
    return args[index] if index < len(args) else None


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
        command = _runner_command_string(args, name)
        if command is not None:
            # `npm exec -c 'CMD'`, `mise exec -c 'CMD'`: a string a shell runs.
            sub, value = command
            _parse_into(value, ctx, (*via, f"{name} {sub} -c"), depth + 1, indirect)
            return None, f"{name} {sub}", None
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


def _subcommand_index(args: list[str], name: str) -> int | None:
    """Where the runner's subcommand is, after any global options."""
    runner = SUBCOMMAND_RUNNERS[name]
    global_with_value = runner.with_arg | CLI_OPTIONS_WITH_VALUE.get(name, frozenset())
    index = 0
    while index < len(args) and args[index].startswith("-") and args[index] != "--":
        index += 2 if args[index] in global_with_value else 1
    return index if index < len(args) and args[index] in runner.subcommands else None


def _runner_command_string(args: list[str], name: str) -> tuple[str, str] | None:
    """(subcommand, command string) for `npm exec -c 'CMD'` and the like."""
    options = SUBCOMMAND_RUNNERS[name].command_options
    index = _subcommand_index(args, name) if options else None
    if index is None:
        return None
    # Only the runner's own options, before the command it runs (`exec sh -c` is sh's -c).
    runner, own, rest = SUBCOMMAND_RUNNERS[name], [], args[index + 1:]
    position = 0
    while position < len(rest) and rest[position].startswith("-") and rest[position] != "--":
        takes_value = rest[position] in runner.with_arg or rest[position] in options
        own += rest[position:position + (2 if takes_value else 1)]
        position += 2 if takes_value else 1
    value = option_value(own, options)
    return (args[index], value) if value is not None else None


def _skip_subcommand_runner(args: list[str], name: str) -> tuple[str, list[str]] | None:
    """(subcommand, command it runs) for `uv [opts] run [opts] cmd...` and the like.

    Global options may come before the subcommand (`uv --directory . run ...`).
    """
    runner = SUBCOMMAND_RUNNERS[name]
    index = _subcommand_index(args, name)
    if index is None:
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
    elif name == "trap":
        handler = _trap_handler(args)
        if handler:
            # `trap 'CMD' EXIT`: the shell runs CMD later, in itself.
            nested(handler, "trap", shares_shell=True)
    elif name in ("mapfile", "readarray"):
        callback = _mapfile_callback(args)
        if callback:
            # `mapfile -C CMD`: CMD runs with the index and the line appended.
            nested(f'{callback} 0 "$MAPFILE_LINE"', f"{name} -C", shares_shell=True)
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
            # `source`/`.` run the file in this shell: its cd and assignments persist,
            # and `source FILE ARGS` makes ARGS its $1..$n for the duration.
            sourcing = command.name in ("source", ".")
            caller_params = ctx.variables.get(_POSITIONALS)
            if sourcing and len(command.args) > 1:
                ctx.variables[_POSITIONALS] = (_FIELD.join(command.args[1:]),)
            nested(text, label, from_file=not label.startswith("written "), shares_shell=sourcing)
            if sourcing and len(command.args) > 1:
                if caller_params is None:
                    ctx.variables.pop(_POSITIONALS, None)
                else:
                    ctx.variables[_POSITIONALS] = caller_params


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
    return _producer_output(parse(operand[2:-1], ctx.cwd or "/"), ctx)


def _producer_output(producer: ParseResult, ctx: _Context) -> str | None:
    """The exact bytes a producer list writes, one command's output straight
    after the other's (`printf r; printf m` gives `rm`): echo/printf of literal
    text and `cat` of files whose text is known. None if any part is unknown."""
    if not producer.commands or producer.unparseable or producer.truncated:
        return None
    parts: list[str] = []
    for command in producer.commands:
        if command.via or command.dynamic_head or command.redirects or any(
                "$" in a or "`" in a for a in command.args):
            return None
        if command.name in ("echo", "printf"):
            part = shell_printf.printed_text(command.name, command.args)
        elif command.name == "cat" and command.args and not any(a.startswith("-") for a in command.args):
            texts = [_input_file_text(path, ctx) for path in command.args]
            part = None if any(t is None for t in texts) else "".join(texts)
        else:
            return None
        if part is None:
            return None
        parts.append(part)
    return "".join(parts)


def _produced_script(operand: str, shell: SimpleCommand, ctx: _Context, nested: Callable[..., None],
                     shares_shell: bool = False) -> bool:
    """A script given as `<(producer)`: parse what the producer prints when that
    is known statically (`echo`/`printf` text, `cat FILE`), as one stream. False
    if `operand` is not a process substitution."""
    if not (operand.startswith("<(") and operand.endswith(")")):
        return False
    producer = parse(operand[2:-1], shell.cwd or "/")
    output = _producer_output(producer, ctx)
    label = f"{shell.name} <({'; '.join(c.name for c in producer.commands)})"
    if output is not None:
        if output:
            nested(output, label, shares_shell=shares_shell)
        return True
    if len(producer.commands) > 1:
        # Where one command's output ends and the next one's begins is not known.
        ctx.result.truncated = True
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


def _trap_handler(args: list[str]) -> str | None:
    """The code `trap CODE SIGNAL...` installs; None for `-p`/`-l`, a reset (`-`,
    a lone signal) or an ignore (`''`)."""
    operands = args[1:] if args[:1] == ["--"] else args
    if not operands or operands[0].startswith("-") and operands[0] != "-":
        return None
    if len(operands) < 2 or operands[0] in ("-", ""):
        return None
    return operands[0]


_MAPFILE_WITH_VALUE = frozenset("dnOsucC")


def _mapfile_callback(args: list[str]) -> str | None:
    """The `-C callback` of `mapfile`/`readarray`, attached (`-Ccmd`) or not."""
    index = 0
    while index < len(args) and args[index].startswith("-") and args[index] != "--":
        arg = args[index]
        for offset, letter in enumerate(arg[1:], start=2):
            if letter in _MAPFILE_WITH_VALUE:
                value = arg[offset:] or (args[index + 1] if index + 1 < len(args) else "")
                if not arg[offset:]:
                    index += 1
                if letter == "C":
                    return value or None
                break
        index += 1
    return None


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


def _xargs_runs(args: list[str], inner: list[str], feeder: SimpleCommand | None,
                ctx: _Context) -> list[list[str]]:
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
        if len(items) > _MAX_BRACE_WORDS:
            ctx.result.truncated = True  # more input lines than are checked
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


def echoed_text(command: SimpleCommand) -> str:
    """The text an `echo`/`printf` prints, when it is literal ("" if not modelled)."""
    return _echoed_text(command)


def _echoed_text(command: SimpleCommand) -> str:
    """What an echo/printf prints, trailing newlines dropped ("" if not modelled)."""
    printed = shell_printf.printed_text(command.name, command.args)
    return printed.rstrip("\n") if printed is not None else ""


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
