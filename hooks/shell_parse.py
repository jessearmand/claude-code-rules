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

import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

from shell_interpreters import awk_commands, inline_programs, shell_strings
from shell_lexer import HEREDOC_OPERATORS, REDIRECT_OPERATORS, Operator, Word, lex
from shell_wrappers import (
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
_PARAMETER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_UNRESOLVED = re.compile(r"\$|`")
_SEPARATORS = frozenset({"&&", "||", ";", ";;", ";&", ";;&", "&", "\n", "(", ")"})
_PIPES = frozenset({"|", "|&"})
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
    variables: dict[str, str] = field(default_factory=dict)
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


def _parse_into(text: str, ctx: _Context, via: tuple[str, ...], depth: int, indirect: bool) -> None:
    if depth > MAX_DEPTH:
        ctx.result.truncated = True
        return
    if depth > 0:
        ctx.result.nested_sources.append(NestedSource(text, via, indirect))

    lexed = lex(text)
    if lexed.unterminated:
        ctx.result.unparseable = True
    # Nested code (`bash -c`, `$(...)`, scripts) runs in its own process: a
    # `cd` inside it does not move the caller.
    entry = (ctx.cwd, ctx.cwd_options)
    for body in lexed.substitutions:
        _parse_into(body, ctx, (*via, "$(...)"), depth + 1, indirect)
        ctx.cwd, ctx.cwd_options = entry

    pipeline, stage = ctx.next_pipeline(), 0
    segment: list = []
    subshells: list[tuple[str | None, tuple[str | None, ...]]] = []
    for token in [*lexed.tokens, Operator("\n")]:
        if isinstance(token, Operator) and (token.op in _SEPARATORS or token.op in _PIPES):
            if segment:
                _segment(segment, ctx, via, depth, indirect, pipeline, stage)
                segment = []
            if token.op == "(":
                subshells.append((ctx.cwd, ctx.cwd_options))
            elif token.op == ")" and subshells:
                ctx.cwd, ctx.cwd_options = subshells.pop()  # `(cd x; ...)` does not move the caller
            if token.op in _PIPES:
                stage += 1
            else:
                pipeline, stage = ctx.next_pipeline(), 0
            continue
        segment.append(token)
    if depth > 0:
        ctx.cwd, ctx.cwd_options = entry


def _segment(tokens: list, ctx: _Context, via: tuple[str, ...], depth: int, indirect: bool,
             pipeline: int, stage: int) -> None:
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
                    expanded = _expand_word(target, ctx)
                    redirects.append(Redirect(token.op, expanded.text, expanded.dynamic))
                index += 2
                continue
        elif isinstance(token, Word):
            words.append(_expand_word(token, ctx))
        index += 1

    words = _strip_prelude(words, ctx)
    if not words:
        return
    argv = [w.text for w in words]
    _emit(argv, tuple(redirects), ctx, via, depth, indirect, pipeline, stage, words[0].dynamic, ())


def _expand_word(word: Word, ctx: _Context) -> Word:
    """Substitute parameters whose value is known at this point in the line.

    `P=$HOME/.claude/settings.json; echo x > "$P"` writes a known path; so
    does `X=rm; $X -rf ~`. A word that still holds an expansion afterwards
    (an unset variable, `${X:-d}`, `$(...)`) stays dynamic.
    """
    if not word.dynamic:
        return word

    def value(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name in ctx.variables:
            return ctx.variables[name]
        if name == "HOME":
            return os.path.expanduser("~")
        if name == "PWD" and ctx.cwd is not None:
            return ctx.cwd
        return match.group(0)

    text = _PARAMETER.sub(value, word.text)
    return Word(text, dynamic=bool(_UNRESOLVED.search(text)), quoted=word.quoted,
                heredoc_body=word.heredoc_body)


def _strip_prelude(words: list[Word], ctx: _Context) -> list[Word]:
    """Drop reserved words and leading assignments; remember literal assignments."""
    index = 0
    while index < len(words):
        word = words[index]
        reserved = word.text in RESERVED_PREFIXES and not word.quoted
        time_flag = word.text == "-p" and index > 0 and words[index - 1].text == "time"
        if reserved or time_flag:
            index += 1
        elif _ASSIGNMENT.match(word.text):
            _assign(word.text, ctx)
            index += 1
        else:
            break
    rest = words[index:]
    # `export X=rm` / `readonly X=...` / `declare X=...`: also an assignment.
    if rest and rest[0].text in ("export", "readonly", "declare", "typeset", "local"):
        for word in rest[1:]:
            if _ASSIGNMENT.match(word.text):
                _assign(word.text, ctx)
    return rest


def _assign(text: str, ctx: _Context) -> None:
    """Remember `NAME=value` (already expanded); `NAME+=value` appends."""
    name, _, value = text.partition("=")
    if name.endswith("+"):
        name = name[:-1]
        value = ctx.variables.get(name, "") + value
    ctx.variables[name.split("[", 1)[0]] = value


# --------------------------------------------------------------------------- #
# Unwrapping
# --------------------------------------------------------------------------- #


def _emit(argv: list[str], redirects: tuple[Redirect, ...], ctx: _Context, via: tuple[str, ...],
          depth: int, indirect: bool, pipeline: int, stage: int, dynamic_head: bool,
          wrappers: tuple[str, ...]) -> None:
    """Unwrap `argv`, record the resulting command, and parse anything it runs."""
    for _ in range(_MAX_UNWRAP):
        while argv and _ASSIGNMENT.match(argv[0]):
            argv = argv[1:]  # `sudo FOO=1 cmd`, `env A=b cmd`
        if not argv:
            return
        name = program_name(argv[0])
        step = _unwrap_once(name, argv, ctx, via, depth, indirect)
        if step is None:
            break
        inner, label = step
        if inner is None:
            # The wrapper is a query (`command -v rm`) or ran a string we
            # already parsed; record the wrapper itself and stop.
            break
        argv, wrappers, dynamic_head = inner, (*wrappers, label), False

    command = SimpleCommand(tuple(argv), redirects, via, wrappers, dynamic_head, indirect,
                            pipeline, stage, ctx.cwd, ctx.cwd_options)
    ctx.result.commands.append(command)
    _record_written_file(command, ctx)
    _parse_payloads(command, ctx, depth)
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
    options = list(dict.fromkeys([*ctx.cwd_options, *moved]))
    if len(options) > _MAX_CWD_OPTIONS:
        options = [*options[:_MAX_CWD_OPTIONS - 1], None]
    ctx.cwd_options = tuple(options)


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


def _join_lines(first: str, second: str) -> str:
    return f"{first}{'' if not first or first.endswith(chr(10)) else chr(10)}{second}"


def _unwrap_once(name: str, argv: list[str], ctx: _Context, via: tuple[str, ...], depth: int,
                 indirect: bool) -> tuple[list[str] | None, str] | None:
    """One unwrapping step. None: not a wrapper. (None, label): runs nothing further here."""
    args = argv[1:]
    if name == "env":
        return _unwrap_env(args, ctx, via, depth, indirect)
    if name in PREFIX_WRAPPERS:
        spec = PREFIX_WRAPPERS[name]
        if spec.command_option and spec.command_option in args:
            command = _option_value(args, spec.command_option)
            if command is not None:
                _parse_into(command, ctx, (*via, f"{name} {spec.command_option}"), depth + 1, indirect)
                return None, name
        inner = _skip_options(args, spec)
        return (None if inner is None or not inner else inner), name
    if name == "find":
        return None
    if name in SUBCOMMAND_RUNNERS:
        inner = _skip_subcommand_runner(args, name)
        return (inner, f"{name} {args[0]}") if inner else None
    return None


def _unwrap_env(args: list[str], ctx: _Context, via: tuple[str, ...], depth: int,
                indirect: bool) -> tuple[list[str] | None, str]:
    for index, arg in enumerate(args):
        split = _split_string_value(args, index, ("-S", "--split-string"))
        if split is not None:
            _parse_into(split, ctx, (*via, "env -S"), depth + 1, indirect)
            return None, "env"
        if not arg.startswith("-"):
            break
    inner = _skip_options(args, PREFIX_WRAPPERS["env"])
    return (inner or None), "env"


def _split_string_value(args: list[str], index: int, names: tuple[str, ...]) -> str | None:
    arg = args[index]
    for name in names:
        if arg == name:
            return " ".join(args[index + 1:])
        if arg.startswith(name + "=") or (len(name) == 2 and arg.startswith(name) and len(arg) > 2):
            first = arg.split("=", 1)[1] if "=" in arg and name.startswith("--") else arg[len(name):]
            return " ".join([first, *args[index + 1:]])
    return None


def _option_value(args: list[str], option: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == option and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith(option) and len(arg) > len(option) and not arg.startswith("--"):
            return arg[len(option):]
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


def _skip_subcommand_runner(args: list[str], name: str) -> list[str] | None:
    runner = SUBCOMMAND_RUNNERS[name]
    if not args or args[0] not in runner.subcommands:
        return None
    rest = args[1:]
    if "--" in rest:
        return rest[rest.index("--") + 1:] or None
    inner = _skip_options(rest, WrapperSpec(runner.with_arg, runner.positionals))
    return inner or None


# --------------------------------------------------------------------------- #
# Program text a command runs: shells, eval, ssh, find -exec, scripts
# --------------------------------------------------------------------------- #


def _parse_payloads(command: SimpleCommand, ctx: _Context, depth: int) -> None:
    name, args, via = command.name, list(command.args), command.via
    nested_via = via
    indirect = command.indirect

    def nested(text: str, label: str, from_file: bool = False) -> None:
        _parse_into(text, ctx, (*nested_via, label), depth + 1, indirect or from_file)

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
            nested(" ".join(args), "eval")
    elif name in STRING_RUNNERS:
        spec = STRING_RUNNERS[name]
        if spec.command_option:
            value = _option_value(args, spec.command_option) or _long_command(args)
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
            nested(" ".join(inner), "parallel")

    if depth < MAX_DEPTH:
        for text, label in _sources_for(command, ctx):
            # A file written earlier in this same command line was typed by the agent.
            nested(text, label, from_file=not label.startswith("written "))


def _long_command(args: list[str]) -> str | None:
    for index, arg in enumerate(args):
        if arg == "--command" and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--command="):
            return arg.split("=", 1)[1]
    return None


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
    if not shell.reads_stdin:
        return  # a command string or a script file (shell_sources resolves the file)
    for redirect in command.redirects:
        if redirect.op in HEREDOC_OPERATORS and redirect.body is not None:
            nested(redirect.body, f"{command.name} <<heredoc")
        elif redirect.op == "<<<":
            nested(redirect.target, f"{command.name} <<<")
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


def _echoed_text(command: SimpleCommand) -> str:
    args = list(command.args)
    if command.name == "echo":
        while args and re.fullmatch(r"-[neE]+", args[0]):
            args = args[1:]
        return " ".join(args)
    if args and "%" not in args[0]:
        return args[0].replace("\\n", "\n")
    return " ".join(args[1:]) if args and args[0] in ("%s", "%s\\n", "%b") else ""


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
