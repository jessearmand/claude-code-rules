"""
Which paths a parsed shell command writes, deletes, moves or re-permissions.

Used by guards that protect files from the Bash tool as well as from the
Write/Edit tools. Works on shell_parse.SimpleCommand, so wrappers and nested
strings are already resolved: `sudo tee ~/.claude/settings.json`,
`bash -c 'cat x > .mcp.json'` and `xargs -I{} cp {} ~/.zshrc` all reach here
as the plain command.

Coverage is deliberately about the common spellings, not every program that
can write a file. Interpreters (`python -c "open(p,'w')"`) are out of scope;
the model judge sees those.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shell_parse import SimpleCommand

WRITE = "write"
DELETE = "delete"
MOVE = "move"  # the source of a move or rename: it disappears
PERMISSIONS = "permissions"
LINK = "link"

_WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})


@dataclass(frozen=True)
class PathWrite:
    path: str
    action: str
    dynamic: bool = False


def _operands(args: tuple[str, ...], with_arg: frozenset[str] = frozenset()) -> list[str]:
    """Non-option arguments, honouring `--` and options that take a value."""
    operands: list[str] = []
    index, options_ended = 0, False
    while index < len(args):
        arg = args[index]
        if options_ended or not arg.startswith("-") or arg == "-":
            operands.append(arg)
        elif arg == "--":
            options_ended = True
        elif arg in with_arg:
            index += 1
        index += 1
    return operands


def _option_value(args: tuple[str, ...], names: tuple[str, ...]) -> str | None:
    for index, arg in enumerate(args):
        for name in names:
            if arg == name and index + 1 < len(args):
                return args[index + 1]
            if name.startswith("--") and arg.startswith(name + "="):
                return arg.split("=", 1)[1]
            if not name.startswith("--") and arg.startswith(name) and len(arg) > len(name):
                return arg[len(name):]
    return None


def _copy_like(args: tuple[str, ...], with_arg: frozenset[str]) -> list[PathWrite]:
    """cp/mv/install/ln: the destination is written (and, for mv, sources vanish)."""
    directory = _option_value(args, ("-t", "--target-directory"))
    operands = _operands(args, with_arg | {"-t", "--target-directory", "-S", "--suffix"})
    if directory:
        return [PathWrite(f"{directory.rstrip('/')}/{source.rsplit('/', 1)[-1]}", WRITE)
                for source in operands] + [PathWrite(directory, WRITE)]
    if len(operands) < 2:
        return []
    return [PathWrite(operands[-1], WRITE)]


def _sed_in_place(args: tuple[str, ...]) -> list[PathWrite]:
    in_place = any(a.startswith(("-i", "--in-place")) or re.fullmatch(r"-[a-zA-Z]*i[a-zA-Z]*", a)
                   for a in args)
    if not in_place:
        return []
    operands = _operands(args, frozenset({"-e", "--expression", "-f", "--file", "-l",
                                          "--line-length"}))
    has_script_option = any(a in ("-e", "--expression", "-f", "--file") or a.startswith("--expression=")
                            for a in args)
    files = operands if has_script_option else operands[1:]
    return [PathWrite(path, WRITE) for path in files]


def _dd(args: tuple[str, ...]) -> list[PathWrite]:
    return [PathWrite(arg[3:], WRITE) for arg in args if arg.startswith("of=")]


def _download(name: str, args: tuple[str, ...]) -> list[PathWrite]:
    names = ("-o", "--output") if name == "curl" else ("-O", "--output-document")
    value = _option_value(args, names)
    return [PathWrite(value, WRITE)] if value and value != "-" else []


_TARGET_ONLY = {
    "tee": (WRITE, frozenset()),
    "touch": (WRITE, frozenset({"-d", "-r", "-t", "--date", "--reference"})),
    "truncate": (WRITE, frozenset({"-s", "--size", "-r", "--reference"})),
    "sponge": (WRITE, frozenset()),
    "rm": (DELETE, frozenset()),
    "unlink": (DELETE, frozenset()),
    "shred": (DELETE, frozenset({"-n", "--iterations", "-s", "--size", "--random-source"})),
    "rmdir": (DELETE, frozenset()),
    "trash": (DELETE, frozenset()),
    "chattr": (PERMISSIONS, frozenset()),
    "setfacl": (PERMISSIONS, frozenset({"-m", "-x", "-M", "-X", "--modify", "--remove"})),
    "xattr": (PERMISSIONS, frozenset({"-w", "-d"})),
}
# Mode/owner is the first operand; the rest are targets.
_LEADING_SPEC = {"chmod": PERMISSIONS, "chown": PERMISSIONS, "chgrp": PERMISSIONS,
                 "chflags": PERMISSIONS}
_COPY_WITH_ARG = {
    "cp": frozenset(),
    "mv": frozenset(),
    "ln": frozenset(),
    "install": frozenset({"-m", "--mode", "-o", "--owner", "-g", "--group"}),
    "rsync": frozenset({"-e", "--rsh", "--exclude", "--include", "--filter", "-f",
                        "--exclude-from", "--include-from", "--files-from", "--chmod"}),
    "ditto": frozenset(),
    "scp": frozenset({"-P", "-i", "-o", "-F", "-c", "-l", "-S", "-J"}),
}


def written_paths(command: SimpleCommand) -> list[PathWrite]:
    """Every path `command` writes, deletes, moves away, links, or re-permissions."""
    found = [PathWrite(r.target, WRITE, r.dynamic) for r in command.redirects if r.op in _WRITE_REDIRECTS]
    if not command.argv or command.dynamic_head:
        return found
    name, args = command.name, command.args

    if name in _TARGET_ONLY:
        action, with_arg = _TARGET_ONLY[name]
        found += [PathWrite(path, action) for path in _operands(args, with_arg)]
    elif name in _LEADING_SPEC:
        operands = _operands(args, frozenset({"--reference", "--from"}))
        found += [PathWrite(path, _LEADING_SPEC[name]) for path in operands[1:]]
    elif name in _COPY_WITH_ARG:
        found += _copy_like(args, _COPY_WITH_ARG[name])
        if name == "ln":
            found = [PathWrite(w.path, LINK) if w.action == WRITE else w for w in found]
        if name == "mv":
            operands = _operands(args, frozenset({"-t", "--target-directory", "-S", "--suffix"}))
            target_dir = _option_value(args, ("-t", "--target-directory"))
            sources = operands if target_dir else operands[:-1]
            found += [PathWrite(path, MOVE) for path in sources]
    elif name in ("sed", "gsed", "perl"):
        found += _sed_in_place(args) if name != "perl" else _perl_in_place(args)
    elif name == "dd":
        found += _dd(args)
    elif name in ("curl", "wget"):
        found += _download(name, args)
    elif name in ("ed", "ex", "patch"):
        found += [PathWrite(path, WRITE) for path in _operands(args, frozenset({"-i", "-o", "-r", "-d",
                                                                              "-p", "-B", "-z", "-D"}))[:1]]
    return found


def _perl_in_place(args: tuple[str, ...]) -> list[PathWrite]:
    if not any(re.fullmatch(r"-[a-zA-Z]*i\S*", a) for a in args):
        return []
    operands = _operands(args, frozenset({"-e", "-E", "-M", "-I"}))
    has_script = any(a in ("-e", "-E") or re.fullmatch(r"-[a-zA-Z]*[eE]", a) for a in args)
    files = operands if has_script else operands[1:]
    return [PathWrite(path, WRITE) for path in files]
