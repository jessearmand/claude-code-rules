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

This module drives the parse (command lists, blocks, functions, the order in
which each command is expanded, unwrapped and recorded) and is the only one
callers import. The stages live beside it, each importing only those above:

  shell_model     the data returned and the context/variable table
  shell_expand    word expansion (braces, globs, parameters, IFS, `$(echo)`)
  shell_streams   text a command reads (`< file`, `<(printf ...)`)
  shell_state     builtins and assignments that change later commands
  shell_effects   cd, files written on the line, links and copies
  shell_unwrap    wrappers, runners, xargs, find -exec, parallel
  shell_payloads  program text a command runs (bash -c, eval, trap, scripts)

Where a stage meets a nested command list it imports `parse`/`_parse_into`
from here inside the function, so no module imports another at load time
in a cycle.
"""

from __future__ import annotations

import itertools
import os
from math import prod

from shell_effects import (
    _change_directory,
    _executable,
    _follow_directory_change,
    _record_link,
    _record_written_file,
)
from shell_expand import (
    _arithmetic_expressions,
    _arithmetic_substitutions,
    _at_word_pieces,
    _brace_expand,
    _compound_word,
    _expand_word,
    _glob_words,
    _split_unquoted,
    _star_separator,
    echoed_text,
)
from shell_lexer import HEREDOC_OPERATORS, REDIRECT_OPERATORS, Operator, Word, lex
from shell_model import (
    _ASSIGNMENT,
    _FIELD,
    _MAX_UNWRAP,
    _POSITIONALS,
    _REFERENCE,
    MAX_DEPTH,
    SOURCE_TOO_LARGE,
    UNSET,
    NestedSource,
    ParseResult,
    Redirect,
    SimpleCommand,
    SourceResolver,
    WrittenFile,
    _BodyStep,
    _bounded,
    _Context,
    _positionals,
    _target_name,
    program_name,
)
from shell_payloads import (
    _parse_payloads,
)
from shell_state import (
    _builtin_mutation,
    _expand_alias,
    _own_assign,
    _strip_prelude,
)
from shell_unwrap import (
    _feeder,
    _unwrap_once,
    _xargs_runs,
    option_value,
)
from shell_wrappers import (
    RESERVED_PREFIXES,
)

__all__ = [
    "MAX_DEPTH",
    "SOURCE_TOO_LARGE",
    "UNSET",
    "NestedSource",
    "ParseResult",
    "Redirect",
    "SimpleCommand",
    "SourceResolver",
    "WrittenFile",
    "echoed_text",
    "option_value",
    "parse",
    "program_name",
]


_SEPARATORS = frozenset({"&&", "||", ";", ";;", ";&", ";;&", "&", "\n", "(", ")"})
_PIPES = frozenset({"|", "|&"})
_AND_OR = frozenset({"&&", "||"})
# Reserved words that open and close a compound command whose body may not run.
_BLOCK_OPENERS = frozenset({"if", "while", "until", "for", "select", "case", "{"})
_BLOCK_CLOSERS = frozenset({"fi", "done", "esac", "}"})
_MAX_VARIANTS = 64


def parse(command: str, cwd: str = "", sources: SourceResolver | None = None) -> ParseResult:
    """Every simple command `command` would run. Never raises."""
    start = cwd or os.getcwd()
    context = _Context(ParseResult(), start, sources, (start,))
    _parse_into(command, context, via=(), depth=0, indirect=False)
    _run_exit_traps(context)
    context.result.written_files = dict(context.written_files)
    return context.result


def _parse_into(text: str, ctx: _Context, via: tuple[str, ...], depth: int, indirect: bool,
                shares_shell: bool = False, return_boundary: bool = False) -> None:
    """Parse `text` as a command list. Nested code runs in a child shell unless
    `shares_shell` (eval, source): then its `cd` and assignments persist.
    `return_boundary` (a sourced script): a `return` in it ends the script."""
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
    new_shell = depth > 0 and not shares_shell
    if new_shell:
        ctx.trap_frames.append([])
    outer_returned = ctx.returned
    ctx.returned = False

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
                may_not_run = previous in _AND_OR or blocks > 0
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
                    ctx.returned = False  # a `return` in a subshell ends only that
                else:
                    _segment(segment, ctx, via, depth, indirect, pipeline, stage)
                if functions:
                    ctx.returned = False  # a body being declared does not run yet
                if ctx.returned and return_boundary:
                    # `return` in a sourced script ends it; after one that may not have
                    # run, the rest of the script may not run either.
                    ctx.returned = False
                    if not may_not_run:
                        segment = []
                        break
                    outer_conditional = True
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
    if new_shell:
        # The shell ends here: its EXIT traps run with the state it ends in.
        _run_exit_traps(ctx)
        ctx.trap_frames.pop()
        ctx.returned = outer_returned
        ctx.cwd, ctx.cwd_options, ctx.variables = entry
    elif not shares_shell or return_boundary:
        ctx.returned = outer_returned


def _run_exit_traps(ctx: _Context) -> None:
    """Check the shell's trap handlers again with the state it exits in:
    `X=echo; trap '"$X" -rf v' EXIT; X=rm` runs rm. They run in a scope of
    their own, so nothing after them sees their assignments."""
    handlers, ctx.trap_frames[-1] = ctx.trap_frames[-1], []
    for handler, via, depth, indirect in handlers:
        saved = (ctx.cwd, ctx.cwd_options, dict(ctx.variables))
        _parse_into(handler, ctx, via, depth, indirect, shares_shell=True)
        ctx.cwd, ctx.cwd_options, ctx.variables = saved


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
        # `g`, or `{ g` / `then g` / `do g`: a nested declaration's name follows
        # the reserved word that opens the enclosing block.
        words = [t for t in segment if isinstance(t, Word)]
        while len(words) > 1 and not words[0].quoted and words[0].text in RESERVED_PREFIXES:
            words = words[1:]
        single = len(words) == 1 and len(segment) == len([t for t in segment if isinstance(t, Word)])
        self._name_seen = words[0].text if single else ""
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
        guards = getattr(token, "guards", {})
        for index, body in enumerate(getattr(token, "substitutions", ())):
            guard = guards.get(index)
            if guard is not None and _branch_skipped(guard, ctx):
                # `X=ok; echo "${X:-$(cmd)}"`: bash never runs cmd. It is still
                # checked, as text the agent did not type to run, so it asks.
                _parse_into(body, ctx, (*via, f"${{{guard[0]}{guard[1]}$(...)}} (not taken)"), depth + 1, True)
                continue
            # A subshell: _parse_into restores the caller's directory and variables.
            _parse_into(body, ctx, (*via, "$(...)"), depth + 1, indirect)
    words = [t for t in tokens if isinstance(t, Word)]
    expressions = [e for word in words for e in _arithmetic_expressions(word.text)]
    if words and words[0].text == "let" and not words[0].quoted:
        expressions += [word.text for word in words[1:]]  # `let EXPR...`
    for expression in expressions:
        for body in _arithmetic_substitutions(expression, ctx):
            # `$((X))` with X='a[$(cmd)]': evaluating X runs cmd.
            _parse_into(body, ctx, (*via, "$((variable))"), depth + 1, indirect)


def _branch_skipped(guard: tuple[str, str], ctx: _Context) -> bool:
    """Whether `${NAME<op>word}` certainly does not expand `word` here: NAME's
    every possible value picks NAME itself (`-`, `=`, `?`) or nothing (`+`).
    An unknown NAME may pick either, so it is not skipped."""
    name, op = guard
    if name.isdigit() or name in ("@", "*", "#"):
        params = _positionals(ctx)
        if params is None:
            return False
        values: tuple[str, ...] = (str(len(params)) if name == "#" else
                                   " ".join(params) if name in ("@", "*") else
                                   params[int(name) - 1] if 0 < int(name) <= len(params) else UNSET,)
    elif _target_name(name, ctx) in ctx.variables:
        values = ctx.variables[_target_name(name, ctx)]
    elif name == "HOME":
        values = (os.path.expanduser("~"),)
    else:
        return False

    def selects_word(value: str) -> bool:
        is_set = value != UNSET and (value != "" or not op.startswith(":"))
        return is_set if op.endswith("+") else not is_set

    return not any(selects_word(value) for value in values)


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
    if command.name in ("return", "exit") and not command.dynamic_head:
        # `return`, `then return`, `command return`: the enclosing function or
        # sourced script ends here (`exit` ends even more).
        ctx.returned = True
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
        may_have_returned = False
        for step in body:
            # After a `return` that may have run (`cmd && return`), the rest may not.
            ctx.conditional = conditional or step.conditional or may_have_returned
            saved = (ctx.cwd, ctx.cwd_options, dict(ctx.variables)) if step.isolated else None
            ctx.returned = False
            _segment(list(step.tokens), ctx, (*command.via, f"{command.name}()"), depth + 1, command.indirect,
                     ctx.next_pipeline(), 0)
            if saved:
                ctx.cwd, ctx.cwd_options, ctx.variables = saved
            if ctx.returned and not step.isolated:
                if not step.conditional:
                    break  # `return`: nothing after it in the body runs
                may_have_returned = True
    finally:
        # `local` variables give the caller its own values back.
        for name in ctx.local_scopes.pop():
            if name in before:
                ctx.variables[name] = before[name]
            else:
                ctx.variables.pop(name, None)
        ctx.calling.discard(command.name)
        ctx.conditional = conditional
        ctx.returned = False
        # The caller's positional parameters come back when the function returns.
        if positionals is None:
            ctx.variables.pop(_POSITIONALS, None)
        else:
            ctx.variables[_POSITIONALS] = positionals
