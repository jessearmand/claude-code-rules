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
DELETE_TREE = "delete-tree"  # recursive removal: everything below goes too
INTO_DIR = "into-dir"  # copied into this directory; the exact file names may not be known
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
    """cp/mv/install/ln/rsync: what the destination may become.

    A destination that is (or turns out to be) a directory receives each
    source under its own name: `cp settings.json ~/.claude/` writes
    `~/.claude/settings.json`. Both readings are returned, plus the
    directory itself (INTO_DIR) for copies whose file names cannot be known,
    such as `rsync -a dir/ dest/`.
    """
    directory = _option_value(args, ("-t", "--target-directory"))
    operands = _operands(args, with_arg | {"-t", "--target-directory", "-S", "--suffix"})
    if directory:
        sources, destination = operands, directory
    elif len(operands) >= 2:
        sources, destination = operands[:-1], operands[-1]
    else:
        return []
    found = [] if directory else [PathWrite(destination, WRITE)]
    for source in sources:
        name = source.rstrip("/").rsplit("/", 1)[-1]
        if name and not source.endswith("/"):
            found.append(PathWrite(f"{destination.rstrip('/')}/{name}", WRITE))
    found.append(PathWrite(destination, INTO_DIR))
    return found


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


# Short options that take a value: in a cluster, the rest of the argument (or
# the next one) is that value, so `curl -sLo out URL` saves to `out`.
_DOWNLOAD_SHORT_WITH_VALUE = {
    "curl": frozenset("AbcCdDeEFHKmoPQrtTuUwxXyYz"),
    "wget": frozenset("aABDeiIlOoPQRtTUwX"),
}
# (output, remote-name flag, directory) as (short letter, long names).
_DOWNLOAD_OPTIONS = {
    "curl": (("o", ("--output",)), ("O", ("--remote-name", "--remote-name-all")), ("", ("--output-dir",))),
    "wget": (("O", ("--output-document",)), ("", ()), ("P", ("--directory-prefix",))),
}


def _short_options(name: str, args: tuple[str, ...]) -> list[tuple[str, str | None]]:
    """Each short option of a curl/wget call with its value, clusters expanded:
    `-sLo out` gives s, L and (o, "out"). wget's `-nv`/`-nc` are single options."""
    with_value = _DOWNLOAD_SHORT_WITH_VALUE[name]
    found: list[tuple[str, str | None]] = []
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "--":
            break
        if not arg.startswith("-") or arg.startswith("--") or len(arg) < 2:
            continue
        position = 1
        while position < len(arg):
            letter = arg[position]
            if name == "wget" and letter == "n":
                position += 2  # -nv, -nc, -nd, -nH, -np
                continue
            if letter in with_value:
                value = arg[position + 1:]
                if not value and index < len(args):
                    value, index = args[index], index + 1
                found.append((letter, value))
                break
            found.append((letter, None))
            position += 1
    return found


def _download(name: str, args: tuple[str, ...]) -> list[PathWrite]:
    """Files a curl/wget call saves: an explicit output, or the URL's own file name
    (`curl -O URL`, and wget's default)."""
    (out_letter, out_long), (remote_letter, remote_long), (dir_letter, dir_long) = _DOWNLOAD_OPTIONS[name]
    short = _short_options(name, args)
    explicit = [value for letter, value in short if letter == out_letter and value is not None]
    long_output = _option_value(args, out_long)
    if long_output is not None:
        explicit.append(long_output)
    directory = next((value for letter, value in short if dir_letter and letter == dir_letter), None)
    directory = _option_value(args, dir_long) or directory
    names = [path for path in explicit if path != "-"]
    remote = any(letter == remote_letter for letter, _ in short) or any(a in remote_long for a in args)
    if not explicit and (name == "wget" or remote):
        urls = [a for a in args if "://" in a and not a.startswith("-")]
        names = [url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "index.html" for url in urls]
    return [PathWrite(f"{directory.rstrip('/')}/{n}" if directory and not n.startswith("/") else n, WRITE)
            for n in names]


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
        if name == "rm" and _recursive(args):
            action = DELETE_TREE
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


def _recursive(args: tuple[str, ...]) -> bool:
    for arg in args:
        if arg == "--":
            return False
        if arg.startswith("--") and len(arg) > 3 and "--recursive".startswith(arg):
            return True
        if arg.startswith("-") and not arg.startswith("--") and ("r" in arg or "R" in arg):
            return True
    return False


def _perl_in_place(args: tuple[str, ...]) -> list[PathWrite]:
    if not any(re.fullmatch(r"-[a-zA-Z]*i\S*", a) for a in args):
        return []
    operands = _operands(args, frozenset({"-e", "-E", "-M", "-I"}))
    has_script = any(a in ("-e", "-E") or re.fullmatch(r"-[a-zA-Z]*[eE]", a) for a in args)
    files = operands if has_script else operands[1:]
    return [PathWrite(path, WRITE) for path in files]
