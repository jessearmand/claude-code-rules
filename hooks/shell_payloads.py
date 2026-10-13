"""
Program text a command runs: `bash -c`, `eval`, `trap`, `mapfile -C`,
`source <(...)`, `ssh host CMD`, interpreter one-liners, awk `system()`,
string runners, `$PS4` while `set -x` traces, and scripts read from disk
through the source resolver.
Each is parsed again as a nested command list. Part of shell_parse.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable

from shell_expand import (
    _echoed_text,
)
from shell_interpreters import (
    awk_commands,
    direct_shell_strings,
    inline_programs,
    interpreter_kind,
    shell_strings,
)
from shell_lexer import HEREDOC_OPERATORS
from shell_model import (
    _FIELD,
    _POSITIONALS,
    MAX_DEPTH,
    SOURCE_TOO_LARGE,
    Redirect,
    SimpleCommand,
    _Context,
    _Trap,
)
from shell_state import _MAPFILE_WITH_VALUE, _opened_text
from shell_streams import (
    _producer_output,
    parse_producer,
)
from shell_unwrap import (
    _find_exec_commands,
    _parallel_command,
    _skip_options,
    option_value,
)
from shell_wrappers import (
    SHELLS,
    SSH_OPTIONS_WITH_ARG,
    STRING_RUNNERS,
    WrapperSpec,
    shell_invocation,
)


def _parse_payloads(command: SimpleCommand, ctx: _Context, depth: int) -> None:
    from shell_parse import _emit, _parse_into  # re-entry: parsed as a command list
    name, args, via = command.name, list(command.args), command.via
    nested_via = via
    indirect = command.indirect

    def nested(text: str, label: str, not_typed: bool = False, shares_shell: bool = False,
               return_boundary: bool = False) -> None:
        # `not_typed`: text the agent did not type as a command here (a file's
        # program, a string literal), so the guards ask about it instead of denying.
        _parse_into(text, ctx, (*nested_via, label), depth + 1, indirect or not_typed, shares_shell,
                    return_boundary)

    if name == "set":
        tracing = _xtrace_option(args)
        ctx.xtrace = ctx.xtrace if tracing is None else tracing
    if ctx.xtrace:
        _trace_prompt(command, ctx, nested)

    if name in SHELLS:
        _shell_payload(command, args, ctx, nested)
    elif name in ("sqlite3", "sqlite"):
        for text in _sqlite_shell_commands(command):
            nested(text, f"{name} .shell")
        for text, typed in _sqlite_shell_commands_in_files(command, ctx):
            nested(text, f"{name} -init .shell", not_typed=not typed)
    elif name in _CLIENT_SHELL:
        for text in _client_shell_commands(command):
            nested(text, f"{name} shell escape")
        for text, typed in _client_shell_commands_in_files(command, ctx):
            nested(text, f"{name} -f shell escape", not_typed=not typed)
    elif name == "git":
        for url in args:
            if url.startswith("ext::"):
                # `git clone 'ext::sh -c rm% -rf% x'`: git runs the command in the URL.
                nested(shlex.join(_ext_argv(url[len("ext::"):])), "git ext::")
    elif inline_programs(name, command.args, command.redirects):
        for program in inline_programs(name, command.args, command.redirects):
            direct = direct_shell_strings(program, interpreter_kind(name))
            for literal in shell_strings(program):
                # A string passed straight to a shell (`os.system("...")`) is a command;
                # any other literal of the program may only be data
                # (`subprocess.run(["echo", "rm -rf x"])` deletes nothing), so it asks.
                nested(literal, f"{name} string", not_typed=literal not in direct)
    elif awk_commands(name, command.args):
        for literal in awk_commands(name, command.args):
            nested(literal, f"{name} system()")
    elif name == "eval":
        if args:
            nested(" ".join(args), "eval", shares_shell=True)
    elif name == "trap":
        handler = _trap_handler(args)
        if handler:
            # `trap 'CMD' EXIT`: the shell runs CMD later, on the signal. It is checked
            # now, but what it assigns must not change the commands that run before;
            # then again when the shell exits (EXIT), or after each later command
            # that changes the variables (a signal may arrive at any point).
            nested(handler, "trap")
            names = [s.upper().removeprefix("SIG") for s in _trap_signals(args)]
            exits = [n in ("EXIT", "0") for n in names]
            others = [n for n in names if n not in ("EXIT", "0", "DEBUG")]
            ctx.trap_frames[-1].append(_Trap(handler, (*via, "trap"), depth + 1, indirect, on_exit=any(exits),
                                             on_signal=bool(others), on_debug="DEBUG" in names))
    elif name in ("mapfile", "readarray"):
        callback = _mapfile_option(args, "C")
        if callback:
            _mapfile_callbacks(callback, _mapfile_option(args, "c") or "5000", ctx, nested, f"{name} -C")
    elif name in ("source", ".") and args:
        # `source <(printf CMD)`: the shell runs what the substitution prints.
        _produced_script(args[0], command, ctx, nested, shares_shell=True, return_boundary=True)
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
            # `source FILE ARGS`, `bash FILE ARGS` and `./FILE ARGS` make ARGS its
            # $1..$n for the duration; with none given, they stay unknown.
            sourcing = command.name in ("source", ".")
            caller_params = ctx.variables.get(_POSITIONALS)
            arguments = _script_arguments(command)
            if arguments:
                ctx.variables[_POSITIONALS] = (_FIELD.join(arguments),)
            nested(text, label, not_typed=not label.startswith("written "), shares_shell=sourcing,
                   return_boundary=sourcing)
            if arguments:
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
    if command.name == "bash":
        # A non-interactive bash first runs the file $BASH_ENV names, `bash -c` included.
        for value in command.env_values("BASH_ENV"):
            for path in _startup_paths(value, command, ctx):
                _run_file(path, command, ctx, nested)
    if command.name == "bash" and shell.interactive:
        _interactive_hooks(command, nested)
    if _xtrace_option(_leading_options(args)):
        # `bash -x`: the child traces from its first command, expanding $PS4.
        for value in command.env_values("PS4"):
            nested(_prompt_expansion(value), f"{command.name} -x PS4")
    if shell.interactive and _reads_env_file(command.name, args):
        # An interactive POSIX shell first runs the file $ENV names (PR review).
        for value in command.env_values("ENV"):
            for path in _startup_paths(value, command, ctx):
                _run_file(path, command, ctx, nested)
    prelude = _option_prelude(command.name, args)
    for text in shell.commands:
        nested(prelude + _bind_positionals(text, list(shell.command_args)), f"{command.name} -c")
    if shell.script:
        _produced_script(shell.script, command, ctx, nested)  # `bash <(echo CMD)`
    if not shell.reads_stdin:
        return  # a command string or a script file (shell_sources resolves the file)
    # Redirections apply in order: the shell reads only the last one to stdin,
    # which also replaces a pipe (`bash <<< 'rm -rf x' <<< :` runs only `:`).
    stdin = [r for r in command.redirects if r.op in _STDIN_OPERATORS and (r.fd is None or r.fd == 0)]
    if stdin:
        _stdin_script(stdin[-1], command, ctx, nested)
        return
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


# Shells whose interactive startup runs $ENV. bash does so only in POSIX mode;
# zsh only when emulating sh or ksh, which a command line rarely asks for.
_ENV_SHELLS = frozenset({"sh", "dash", "ash", "ksh", "mksh", "yash", "posh"})


def _reads_env_file(name: str, args: list[str]) -> bool:
    if name in _ENV_SHELLS:
        return True
    return name == "bash" and ("--posix" in args or _set_option(args, "posix"))


def _set_option(args: list[str], option: str) -> bool:
    """`bash -o posix`: the option is set (a later `+o posix` unsets it)."""
    state = False
    for index, arg in enumerate(args[:-1]):
        if arg in ("-o", "+o") and args[index + 1] == option:
            state = arg == "-o"
    return state


def _xtrace_option(args: list[str]) -> bool | None:
    """What `set ARGS` does to xtrace: True for `-x`/`-o xtrace`, False for
    `+x`/`+o xtrace`, None when it is not mentioned. The last mention wins."""
    state: bool | None = None
    for index, arg in enumerate(args):
        if arg in ("--", "-"):
            break
        if arg in ("-o", "+o") and index + 1 < len(args) and args[index + 1] == "xtrace":
            state = arg == "-o"
        elif len(arg) > 1 and arg[0] in "-+" and arg[1] != "-" and "x" in arg[1:]:
            state = arg[0] == "-"
        elif not arg.startswith(("-", "+")) and (index == 0 or args[index - 1] not in ("-o", "+o")):
            break  # the positional parameters start here
    return state


def _leading_options(args: list[str]) -> list[str]:
    """A shell's own options: the words before its command string or script."""
    options: list[str] = []
    for index, arg in enumerate(args):
        if arg.startswith(("-", "+")) or (index > 0 and args[index - 1] in ("-o", "+o")):
            options.append(arg)
            if "c" in arg.lstrip("-+") and not arg.startswith("--"):
                break  # `-c` / `-xc`: the next word is the command string
        else:
            break
    return options


def _prompt_expansion(value: str) -> str:
    """A command whose expansion runs what expanding prompt `value` runs."""
    return ': "' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _trace_prompt(command: SimpleCommand, ctx: _Context, nested: Callable[..., None]) -> None:
    """While `set -x` is on, bash expands $PS4 before each command it runs, so a
    `$(...)` in it runs too (PR review). Each value is parsed once."""
    for value in command.env_values("PS4"):
        if value in ctx.traced_prompts or ("$(" not in value and "`" not in value and "$((" not in value):
            continue
        ctx.traced_prompts.add(value)
        nested(_prompt_expansion(value), "set -x PS4")


def _interactive_hooks(command: SimpleCommand, nested: Callable[..., None]) -> None:
    """An interactive bash (`bash -i`) runs $PROMPT_COMMAND before each prompt and
    expands $PS0/$PS1, whose `$(...)` runs too: parse what the environment sets."""
    for value in command.env_values("PROMPT_COMMAND"):
        nested(value, "bash -i PROMPT_COMMAND")
    for name in ("PS0", "PS1"):
        for value in command.env_values(name):
            if "$(" in value or "`" in value:
                nested(': "' + value.replace('"', '\\"') + '"', f"bash -i {name}")


_STARTUP_REFERENCE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def _startup_paths(value: str, command: SimpleCommand, ctx: _Context) -> list[str]:
    """The files a `BASH_ENV` value names once bash expands it: `$PWD/env.sh` in
    each directory the command may run in, `$HOME` and the command's other
    variables by value. One it cannot expand leaves the analysis incomplete."""
    if not value:
        return []
    if "$" not in value:
        return [value]
    paths: list[str] = []
    for cwd in command.possible_cwds or (None,):
        def replace(match: re.Match[str], cwd: str | None = cwd) -> str:
            name = match.group(1) or match.group(2)
            values = (cwd,) if name == "PWD" else (os.path.expanduser("~"),) if name == "HOME" \
                else command.env_values(name)
            if len(values) != 1 or values[0] is None:
                raise LookupError(name)
            return values[0]
        try:
            expanded = _STARTUP_REFERENCE.sub(replace, value)
        except LookupError:
            ctx.result.truncated = True
            continue
        if "$" in expanded or "`" in expanded:
            ctx.result.truncated = True  # command or arithmetic substitution
            continue
        paths.append(expanded)
    return list(dict.fromkeys(paths))


_STDIN_OPERATORS = frozenset({"<", "<<<", "<&", "<>"}) | HEREDOC_OPERATORS


def _stdin_script(redirect: Redirect, command: SimpleCommand, ctx: _Context, nested: Callable[..., None]) -> None:
    """Parse the script a shell reads from its stdin redirection."""
    if redirect.op in HEREDOC_OPERATORS and redirect.body is not None:
        nested(redirect.body, f"{command.name} <<heredoc")
    elif redirect.op == "<<<":
        nested(redirect.target, f"{command.name} <<<")
    elif redirect.op in ("<", "<>") and _produced_script(redirect.target, command, ctx, nested):
        pass  # `bash < <(printf CMD)`
    elif redirect.op in ("<", "<>") and not redirect.dynamic:
        # `bash < script.sh` runs the file just like `bash script.sh`.
        _run_file(redirect.target, command, ctx, nested)
    elif redirect.op == "<&" and redirect.target.isdigit():
        text, _ = _opened_text(int(redirect.target), ctx)  # `exec 3<<< CMD; bash <&3`
        if text is None:
            ctx.result.truncated = True  # the descriptor's text is not known
        else:
            nested(text, f"{command.name} <&{redirect.target}")
    elif redirect.dynamic:
        ctx.result.truncated = True  # a computed file: the script is not known


def _option_prelude(name: str, args: list[str]) -> str:
    """Options a new shell starts with, as commands to parse first: dash's and
    sh's echo always expands escapes, as does bash's with `-O xpg_echo`."""
    xpg = name in ("sh", "dash", "ash") or any(
        a == "xpg_echo" and i > 0 and args[i - 1] == "-O" or a == "-Oxpg_echo" for i, a in enumerate(args))
    return "shopt -s xpg_echo\n" if xpg else ""


_SQLITE_SHELL = re.compile(r"^\s*\.(?:shell|system)\s+(.+)$", re.MULTILINE)
# `.read "|CMD"`, `.import '|CMD' t`, `.output |CMD`, `.once |CMD`: a file name
# starting with `|` is a command sqlite3 runs and reads from or writes to. Unquoted,
# the rest of the line is the command (sqlite3 joins the remaining arguments).
_SQLITE_PIPE = re.compile(r"""^\s*\.(?:read|import|output|once)\s+(?:"\|([^"]*)"|'\|([^']*)'|\|(.+?)\s*$)""",
                          re.MULTILINE)


def _client_texts(command: SimpleCommand) -> list[str]:
    """What a database client reads as input: its arguments (`-c SQL`, `-cmd SQL`,
    trailing statements, and a value attached to a short option, `-c'SQL'`), a
    heredoc or a here-string."""
    texts = [a for a in command.args if not a.startswith("-") or a.startswith("-cmd")]
    if command.name in _GETOPT_CLIENTS:
        texts += [a[2:] for a in command.args if len(a) > 2 and a[0] == "-" and a[1] != "-"]
    return texts + [r.body or "" for r in command.redirects if r.body] + [
        r.target for r in command.redirects if r.op == "<<<"]


# Clients whose single-letter options take an attached value (`psql -c'\! rm -rf x'`).
_GETOPT_CLIENTS = frozenset({"psql", "mysql", "mariadb"})
# Options naming a file of statements the client runs: sqlite3 `-init`, psql `-f`.
_CLIENT_FILE_OPTIONS = {"sqlite3": ("-init", "--init"), "sqlite": ("-init", "--init"),
                        "psql": ("-f", "--file")}
_MAX_CLIENT_FILE = 256 * 1024


def _client_file_texts(command: SimpleCommand, ctx: _Context) -> list[tuple[str, bool]]:
    """(text, typed) for each file of statements the client reads: a command
    file option, or `< FILE` on stdin. Typed when the same line wrote the file
    (`printf '.shell …' > i.sql; sqlite3 -init i.sql`), else read from disk
    (PR review). A computed name leaves the analysis incomplete."""
    options = _CLIENT_FILE_OPTIONS.get(command.name, ())
    names = [command.args[i + 1] for i, a in enumerate(command.args[:-1]) if a in options]
    names += [a.split("=", 1)[1] for a in command.args if a.startswith(tuple(o + "=" for o in options if o.startswith("--")))]
    if command.name == "psql":
        names += [a[2:] for a in command.args if a.startswith("-f") and len(a) > 2 and not a.startswith("-fi")]
    for redirect in command.redirects:
        if redirect.op == "<" and (redirect.fd is None or redirect.fd == 0):
            if redirect.dynamic:
                ctx.result.truncated = True
                continue
            names.append(redirect.target)
    found: list[tuple[str, bool]] = []
    for name in names:
        if "$" in name or "`" in name:
            ctx.result.truncated = True  # which file runs is not known
            continue
        for directory in command.possible_cwds:
            if directory is None:
                continue
            absolute = os.path.normpath(os.path.join(directory, os.path.expanduser(name)))
            entry = ctx.written_files.get(absolute)
            if entry is not None:
                found.append((entry.text, True))
                break
            try:
                if os.path.isfile(absolute) and os.path.getsize(absolute) <= _MAX_CLIENT_FILE:
                    with open(absolute, encoding="utf-8", errors="replace") as handle:
                        found.append((handle.read(), False))
                    break
            except OSError:
                continue
    return found


def _sqlite_shell_commands_in_files(command: SimpleCommand, ctx: _Context) -> list[tuple[str, bool]]:
    return [(m.group(1), typed) for text, typed in _client_file_texts(command, ctx)
            for m in _SQLITE_SHELL.finditer(text)] + [
        (_first_group(m), typed) for text, typed in _client_file_texts(command, ctx)
        for m in _SQLITE_PIPE.finditer(text)]


def _client_shell_commands_in_files(command: SimpleCommand, ctx: _Context) -> list[tuple[str, bool]]:
    pattern = _CLIENT_SHELL[command.name]
    return [(_first_group(m), typed) for text, typed in _client_file_texts(command, ctx)
            for m in pattern.finditer(text)]


def _ext_argv(spec: str) -> list[str]:
    """The argv git-remote-ext builds from `ext::CMD ARGS`: words split on single
    spaces, `% ` a space inside a word, `%%` a `%`, `%s`/`%S` the service name,
    `%G`/`%V` the repository and host (unknown here)."""
    argv, word, index = [], "", 0
    while index < len(spec):
        char = spec[index]
        if char == "%" and index + 1 < len(spec):
            escape = spec[index + 1]
            word += {" ": " ", "%": "%", "s": "git-upload-pack", "S": "git-upload-pack"}.get(escape, "")
            index += 2
            continue
        if char == " ":
            if word:
                argv.append(word)
            word = ""
        else:
            word += char
        index += 1
    if word:
        argv.append(word)
    return argv


def _first_group(match: re.Match[str]) -> str:
    return next(g for g in match.groups() if g is not None)


def _sqlite_shell_commands(command: SimpleCommand) -> list[str]:
    """Commands sqlite3 runs in a system shell (`.shell CMD`, `.system CMD`, and
    a `|CMD` file name), from its SQL arguments, `-cmd` options, a heredoc or
    here-string."""
    texts = _client_texts(command)
    found = [m.group(1) for text in texts for m in _SQLITE_SHELL.finditer(text)]
    return found + [_first_group(m) for text in texts for m in _SQLITE_PIPE.finditer(text)]


# Shell escapes of other database clients (PR review):
# psql `\! CMD`, output to a pipe (`\o |CMD`, `\g |CMD`, `\w |CMD`), and
# `COPY`/`\copy ... PROGRAM 'CMD'`; mysql/mariadb `system CMD`, `\! CMD` and a `pager`.
_PSQL_SHELL = re.compile(
    r"\\!\s*(.+?)\s*$"
    r"|\\(?:o|out|g|w|write)\s+(?:'\|([^']*)'|\|\s*(.+?)\s*$)"
    r"|\bPROGRAM\s+(?:'([^']*)'|E'([^']*)')",
    re.MULTILINE | re.IGNORECASE,
)
_MYSQL_SHELL = re.compile(r"^\s*(?:system|\\!|pager|\\P)\s+(.+?)\s*;?\s*$", re.MULTILINE)
_CLIENT_SHELL = {"psql": _PSQL_SHELL, "mysql": _MYSQL_SHELL, "mariadb": _MYSQL_SHELL}


def _client_shell_commands(command: SimpleCommand) -> list[str]:
    pattern = _CLIENT_SHELL[command.name]
    return [_first_group(m) for text in _client_texts(command) for m in pattern.finditer(text)]


def _produced_script(operand: str, shell: SimpleCommand, ctx: _Context, nested: Callable[..., None],
                     shares_shell: bool = False, return_boundary: bool = False) -> bool:
    """A script given as `<(producer)`: parse what the producer prints when that
    is known statically (`echo`/`printf` text, `cat FILE`), as one stream. False
    if `operand` is not a process substitution."""
    if not (operand.startswith("<(") and operand.endswith(")")):
        return False
    producer = parse_producer(operand, shell.cwd, ctx)
    output = _producer_output(producer, ctx)
    commands = [c for c in producer.commands if c.name != "shopt"]
    label = f"{shell.name} <({'; '.join(c.name for c in commands)})"
    if output is not None:
        if output:
            nested(output, label, shares_shell=shares_shell, return_boundary=return_boundary)
        return True
    single_file = len(commands) == 1 and commands[0].name == "cat" and len(commands[0].args) == 1
    if not single_file:
        # A script whose text cannot be known (`<(base64 -d <<< ...)`, or where one
        # command's output ends and the next begins): the analysis is incomplete.
        ctx.result.truncated = True
    for command in commands:
        if command.via or command.dynamic_head:
            continue
        if command.name in ("echo", "printf"):
            text = _echoed_text(command)
            if text:
                nested(text, f"{shell.name} <({command.name})", shares_shell=shares_shell,
                       return_boundary=return_boundary)
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


def _trap_signals(args: list[str]) -> list[str]:
    """The conditions `trap CODE SIGNAL...` installs CODE for."""
    operands = args[1:] if args[:1] == ["--"] else args
    return operands[1:]


def _mapfile_callbacks(callback: str, quantum: str, ctx: _Context, nested: Callable[..., None], label: str) -> None:
    """`mapfile -C CMD -c N`: after every Nth line read, CMD runs with the index
    that line goes to and the line appended (`CMD 0 'text'`), in this shell.
    Lines not known (or a computed N) leave the analysis incomplete."""
    records = ctx.mapfile_records
    if records is None or not quantum.isdigit() or int(quantum) == 0:
        ctx.result.truncated = True
        nested(f'{callback} 0 "$MAPFILE_LINE"', label, shares_shell=True)
        return
    conditional = ctx.conditional
    ctx.conditional = conditional or len(records) > 1  # several streams: each may be the one read
    try:
        for stream in records:
            for count, (index, line) in enumerate(stream, start=1):
                if count % int(quantum) == 0:
                    nested(f"{callback} {index} {shlex.quote(line)}", label, shares_shell=True)
    finally:
        ctx.conditional = conditional


def _mapfile_option(args: list[str], wanted: str) -> str | None:
    """The value of `mapfile`/`readarray` option `-C` or `-c`, attached (`-Ccmd`) or not."""
    index = 0
    while index < len(args) and args[index].startswith("-") and args[index] != "--":
        arg = args[index]
        for offset, letter in enumerate(arg[1:], start=2):
            if letter in _MAPFILE_WITH_VALUE:
                value = arg[offset:] or (args[index + 1] if index + 1 < len(args) else "")
                if not arg[offset:]:
                    index += 1
                if letter == wanted:
                    return value or None
                break
        index += 1
    return None


def _run_file(path: str, shell: SimpleCommand, ctx: _Context, nested: Callable[..., None]) -> None:
    """Parse a file a shell reads as its program, via the source resolver."""
    as_script = SimpleCommand((shell.argv[0], path), indirect=shell.indirect, cwd=shell.cwd,
                              cwd_options=shell.cwd_options)
    for text, label in _sources_for(as_script, ctx):
        nested(text, label, not_typed=not label.startswith("written "))


def _script_arguments(command: SimpleCommand) -> tuple[str, ...]:
    """The arguments a script `command` runs is given as $1, $2, ..."""
    if command.name in ("source", "."):
        return command.args[1:]
    if command.name in SHELLS:
        return shell_invocation(command.name, command.args).script_args
    if command.argv and "/" in command.argv[0]:
        return command.args  # `./cleanup.sh a b`
    return ()  # package scripts and Make recipes do not see the caller's arguments


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
