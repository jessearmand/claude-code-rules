"""
Program text a command runs: `bash -c`, `eval`, `trap`, `mapfile -C`,
`source <(...)`, `ssh host CMD`, interpreter one-liners, awk `system()`,
string runners, and scripts read from disk through the source resolver.
Each is parsed again as a nested command list. Part of shell_parse.
"""

from __future__ import annotations

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
    SimpleCommand,
    _Context,
)
from shell_state import _MAPFILE_WITH_VALUE
from shell_streams import (
    _producer_output,
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

    if name in SHELLS:
        _shell_payload(command, args, ctx, nested)
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
            # and again when the shell exits, with the variables it has then.
            nested(handler, "trap")
            ctx.trap_frames[-1].append((handler, (*via, "trap (on exit)"), depth + 1, indirect))
    elif name in ("mapfile", "readarray"):
        callback = _mapfile_callback(args)
        if callback:
            # `mapfile -C CMD`: CMD runs with the index and the line appended.
            nested(f'{callback} 0 "$MAPFILE_LINE"', f"{name} -C", shares_shell=True)
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
            # `source`/`.` run the file in this shell: its cd and assignments persist,
            # and `source FILE ARGS` makes ARGS its $1..$n for the duration.
            sourcing = command.name in ("source", ".")
            caller_params = ctx.variables.get(_POSITIONALS)
            if sourcing and len(command.args) > 1:
                ctx.variables[_POSITIONALS] = (_FIELD.join(command.args[1:]),)
            nested(text, label, not_typed=not label.startswith("written "), shares_shell=sourcing,
                   return_boundary=sourcing)
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
    if command.name == "bash":
        # A non-interactive bash first runs the file $BASH_ENV names, `bash -c` included.
        for path in command.env_values("BASH_ENV"):
            if path and "$" not in path:
                _run_file(path, command, ctx, nested)
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
                     shares_shell: bool = False, return_boundary: bool = False) -> bool:
    """A script given as `<(producer)`: parse what the producer prints when that
    is known statically (`echo`/`printf` text, `cat FILE`), as one stream. False
    if `operand` is not a process substitution."""
    from shell_parse import parse  # re-entry: parsed as a command list
    if not (operand.startswith("<(") and operand.endswith(")")):
        return False
    producer = parse(operand[2:-1], shell.cwd or "/")
    output = _producer_output(producer, ctx)
    label = f"{shell.name} <({'; '.join(c.name for c in producer.commands)})"
    if output is not None:
        if output:
            nested(output, label, shares_shell=shares_shell, return_boundary=return_boundary)
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
        nested(text, label, not_typed=not label.startswith("written "))


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
