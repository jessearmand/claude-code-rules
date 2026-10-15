"""
Unwrapping: from `sudo -u x env A=b timeout 5 nice rm ...` to the program
that runs, through prefix wrappers, `env -S`/`-C`, subcommand runners
(`npm exec`, `uv run`, `mise exec -c`), `xargs` with a known feeder,
`find -exec` and `parallel`. Part of shell_parse.
"""

from __future__ import annotations

import shlex

from shell_effects import (
    _change_directory,
)
from shell_expand import (
    _MAX_BRACE_WORDS,
    _echoed_text,
)
from shell_model import (
    SimpleCommand,
    _Context,
)
from shell_wrappers import (
    CLI_OPTIONS_WITH_VALUE,
    PREFIX_WRAPPERS,
    SUBCOMMAND_RUNNERS,
    WrapperSpec,
)

_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})


def _unwrap_once(name: str, argv: list[str], ctx: _Context, via: tuple[str, ...], depth: int,
                 indirect: bool) -> tuple[list[str] | None, str, str | None] | None:
    """One unwrapping step: (inner argv or None, label, directory it switches to).

    None: not a wrapper. Inner None: the wrapper runs nothing further here.
    """
    from shell_parse import _parse_into  # re-entry: parsed as a command list
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
    from shell_parse import _parse_into  # re-entry: parsed as a command list
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
