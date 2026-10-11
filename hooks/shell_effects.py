"""
What a command does to the filesystem that later commands on the line depend
on: `cd`, files written from literal text, and links or copies that make one
path stand for another. Part of shell_parse.
"""

from __future__ import annotations

import os
import re

import shell_printf
from shell_lexer import HEREDOC_OPERATORS
from shell_model import (
    SimpleCommand,
    WrittenFile,
    _at,
    _bounded,
    _Context,
)


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
