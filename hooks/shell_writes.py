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

`git apply` names its targets inside the patch, so `written_paths` takes the
files the same line wrote and otherwise reads the patch from disk.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass

from shell_parse import SimpleCommand, WrittenFile

WRITE = "write"
DELETE = "delete"
DELETE_TREE = "delete-tree"  # recursive removal: everything below goes too
INTO_DIR = "into-dir"  # copied into this directory; the exact file names may not be known
MOVE = "move"  # the source of a move or rename: it disappears
PERMISSIONS = "permissions"
LINK = "link"

_WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})


@dataclass(frozen=True)
class Contents:
    """What an INTO_DIR write puts in its directory, so it can be listed."""

    archive: str | None = None  # a tar or zip file whose members land there
    zip_archive: bool = False
    strip: int = 0  # tar --strip-components
    absolute_names: bool = False  # tar -P: members keep a leading /
    sources: tuple[str, ...] = ()  # copied paths: a file under its own name, a directory with its tree
    recursive: bool = False  # directories among the sources are copied with what they hold
    # `src/` copies what src holds, not src itself: rsync, and BSD cp (macOS).
    slash_copies_contents: bool = False
    copies_contents: bool = False  # ditto: always what the source holds
    keep_parents: bool = False  # cp --parents: a source lands under its own path, not its name


@dataclass(frozen=True)
class PathWrite:
    path: str
    action: str
    dynamic: bool = False
    contents: Contents | None = None  # INTO_DIR: None when what lands there cannot be listed


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


_RECURSIVE_COPY = re.compile(r"-[A-Za-z]*[rRa][A-Za-z]*|--recursive|--archive")


def _copy_like(args: tuple[str, ...], with_arg: frozenset[str], name: str = "cp") -> list[PathWrite]:
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
    # GNU `cp --parents a/b/c dest` writes dest/a/b/c, keeping the source's directory
    # components, so `cd evil && cp --parents .claude/settings.json ..` lands in the
    # parent project's `.claude/` (PR review). The destination must be a directory.
    keep_parents = name in ("cp", "gcp") and "--parents" in args
    found = [] if directory or keep_parents else [PathWrite(destination, WRITE)]
    for source in sources:
        base = _parent_path(source) if keep_parents else source.rstrip("/").rsplit("/", 1)[-1]
        if base and not source.endswith("/"):
            found.append(PathWrite(f"{destination.rstrip('/')}/{base}", WRITE))
    contents = Contents(
        sources=tuple(sources),
        recursive=name in ("mv", "ditto") or any(_RECURSIVE_COPY.fullmatch(a) for a in args),
        slash_copies_contents=name == "rsync" or (name == "cp" and sys.platform == "darwin"),
        copies_contents=name == "ditto",
        keep_parents=keep_parents,
    )
    found.append(PathWrite(destination, INTO_DIR, contents=contents))
    return found


def _parent_path(source: str) -> str:
    """The path `cp --parents` appends to the destination: the source as written,
    without a leading `/` or `./` (`/a/b` -> `a/b`, `./x/y` -> `x/y`)."""
    path = source.rstrip("/")
    while path.startswith(("./", "/")):
        path = path[2:] if path.startswith("./") else path[1:]
    return path


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


def written_paths(command: SimpleCommand, written: dict[str, WrittenFile] | None = None) -> list[PathWrite]:
    """Every path `command` writes, deletes, moves away, links, or re-permissions.

    `written` holds the files the same command line wrote from literal text,
    by absolute path; `git apply` reads its patch from there before disk."""
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
        # `chmod --reference=RFILE FILE...`: no mode operand, every operand is a target.
        by_reference = any(a == "--reference" or a.startswith("--reference=") for a in args)
        found += [PathWrite(path, _LEADING_SPEC[name]) for path in operands[0 if by_reference else 1:]]
    elif name in _COPY_WITH_ARG:
        found += _copy_like(args, _COPY_WITH_ARG[name], name)
        if name == "ln":
            found = [PathWrite(w.path, LINK) if w.action == WRITE else w for w in found]
        if name == "mv" or (name == "rsync" and "--remove-source-files" in args):
            # mv, and rsync --remove-source-files, take the sources away.
            operands = _operands(args, frozenset({"-t", "--target-directory", "-S", "--suffix"})
                                 | _COPY_WITH_ARG.get(name, frozenset()))
            target_dir = _option_value(args, ("-t", "--target-directory"))
            sources = operands if target_dir else operands[:-1]
            found += [PathWrite(path, MOVE) for path in sources]
    elif name in ("sed", "gsed", "perl"):
        found += _sed_in_place(args) if name != "perl" else _perl_in_place(args)
    elif name == "dd":
        found += _dd(args)
    elif name in ("curl", "wget"):
        found += _download(name, args)
    elif name in ("tar", "bsdtar", "gtar", "unzip", "7z", "7za"):
        found += _extract(name, args)
    elif name == "git":
        found += _git_checkout_dirs(args)
        found += _git_config_file(args)
        found += _git_apply(args, command, written or {})
    elif name in ("ed", "ex", "patch"):
        found += [PathWrite(path, WRITE) for path in _operands(args, frozenset({"-i", "-o", "-r", "-d",
                                                                              "-p", "-B", "-z", "-D"}))[:1]]
    return found


# git's global options that take a value as the next word.
_GIT_GLOBAL_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
                                    "--exec-path", "--super-prefix", "--list-cmds"})
# Per subcommand: options taking a value, and whether the directory is the
# second operand (clone, submodule add: after the repository) or the first.
_GIT_DIRECTORY_COMMANDS = {
    ("clone",): (frozenset({"-o", "--origin", "-b", "--branch", "-u", "--upload-pack", "--reference",
                            "--reference-if-able", "--separate-git-dir", "--depth", "--shallow-since",
                            "--shallow-exclude", "-c", "--config", "-j", "--jobs", "--template", "--filter",
                            "--bundle-uri", "--server-option", "--ref-format"}), True),
    ("submodule", "add"): (frozenset({"-b", "--branch", "--name", "--reference", "--depth"}), True),
    ("worktree", "add"): (frozenset({"-b", "-B", "--reason"}), False),
}


def _git_checkout_dirs(args: tuple[str, ...]) -> list[PathWrite]:
    """`git clone URL [DIR]`, `git submodule add URL [PATH]`, `git worktree add PATH`:
    the directory filled with a checkout. Without DIR, clone and submodule add
    use the repository's name. `git -C D` makes a relative directory start at D."""
    base, index = None, 0
    while index < len(args) and args[index].startswith("-"):
        if args[index] == "-C" and index + 1 < len(args):
            # `git -C a -C b`: a relative b continues from a; an absolute one replaces it.
            target = args[index + 1]
            absolute = target.startswith(("/", "~", "$HOME"))
            base = target if base is None or absolute else f"{base.rstrip('/')}/{target}"
        index += 2 if args[index] in _GIT_GLOBAL_WITH_VALUE else 1
    rest = args[index:]
    for words, (with_arg, after_repository) in _GIT_DIRECTORY_COMMANDS.items():
        if rest[:len(words)] != words:
            continue
        operands = _operands(rest[len(words):], with_arg)
        if after_repository:
            if not operands:
                return []
            directory = operands[1] if len(operands) > 1 else _repository_name(operands[0])
        else:
            if not operands:
                return []
            directory = operands[0]
        if base is not None and not directory.startswith(("/", "~")):
            directory = f"{base.rstrip('/')}/{directory}"
        return [PathWrite(directory, INTO_DIR, "$" in directory)]
    return []


def _git_subcommand(args: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """(subcommand, its arguments) after git's global options; ("", ()) if none."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 2 if args[index] in _GIT_GLOBAL_WITH_VALUE else 1
    return (args[index], args[index + 1:]) if index < len(args) else ("", ())


# `git config` forms that only read; anything else with a file writes it.
_GIT_CONFIG_READS = frozenset({"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color",
                               "--get-colorbool", "-l", "--list", "--show-origin", "--show-scope"})
_GIT_CONFIG_WRITES = frozenset({"--unset", "--unset-all", "--add", "--replace-all", "--rename-section",
                                "--remove-section", "-e", "--edit"})
_GIT_CONFIG_WITH_VALUE = frozenset({"-f", "--file", "--blob", "--type", "--default", "--comment",
                                    "--value", "--url"})


def _git_config_file(args: tuple[str, ...]) -> list[PathWrite]:
    """`git config -f FILE k v` writes FILE itself, wherever it is (PR review).
    A lookup (`--get`, `--list`, one key operand) or `--blob` only reads."""
    subcommand, rest = _git_subcommand(args)
    if subcommand != "config":
        return []
    path = _option_value(rest, ("-f", "--file"))
    if path is None or any(a in _GIT_CONFIG_READS for a in rest) or any(a.startswith("--blob") for a in rest):
        return []
    operands = _operands(rest, _GIT_CONFIG_WITH_VALUE)
    if operands and operands[0] in ("get", "list"):
        return []
    writes = any(a in _GIT_CONFIG_WRITES for a in rest) or len(operands) >= 2 \
        or (operands and operands[0] in ("set", "unset", "rename-section", "remove-section", "edit"))
    return [PathWrite(path, WRITE, "$" in path or "`" in path)] if writes else []


# `git apply` forms that touch no working-tree file.
_GIT_APPLY_DRY = frozenset({"--check", "--stat", "--numstat", "--summary", "--cached"})
_GIT_APPLY_WITH_VALUE = frozenset({"-p", "-C", "--whitespace", "--directory", "--exclude", "--include",
                                   "--build-fake-ancestor"})
_MAX_PATCH_BYTES = 1024 * 1024
# `+++ b/path` and `--- a/path` carry -p's leading component; `rename to path` and
# `copy to path` do not. A tab ends a traditional header's path (a timestamp follows).
_PATCH_TARGET = re.compile(r"^(?P<header>\+\+\+ |--- |rename to |copy to )(?P<path>\"(?:[^\"\\]|\\.)*\"|[^\t\n]+)",
                           re.MULTILINE)


def _git_apply(args: tuple[str, ...], command: SimpleCommand, written: dict[str, WrittenFile]) -> list[PathWrite]:
    """The files a patch given to `git apply` changes, from its headers, with
    `-pN` stripped and `--directory=ROOT` prepended (PR review). When the patch
    cannot be read (a pipe, a computed name) the target is unknown, which asks."""
    subcommand, rest = _git_subcommand(args)
    if subcommand != "apply" or any(a in _GIT_APPLY_DRY for a in rest):
        return []
    strip = _option_value(rest, ("-p",))
    strip = int(strip) if strip is not None and strip.isdigit() else 1
    root = (_option_value(rest, ("--directory",)) or "").strip("/")
    texts: list[str] = []
    unknown = False
    sources = _operands(rest, _GIT_APPLY_WITH_VALUE)
    for redirect in command.redirects:
        if redirect.op in ("<<", "<<-") and redirect.body is not None:
            texts.append(redirect.body)
        elif redirect.op == "<<<":
            texts.append(redirect.target)
        elif redirect.op in ("<", "<>") and (redirect.fd is None or redirect.fd == 0):
            if redirect.dynamic:
                unknown = True
            else:
                sources.append(redirect.target)
    if not sources and not texts:
        unknown = True  # the patch arrives on a pipe, or there is none
    for source in sources:
        text = _patch_text(source, command, written)
        if text is None:
            unknown = True
        else:
            texts.append(text)
    found = [PathWrite("$(patch)", WRITE, True)] if unknown else []
    for text in texts:
        for match in _PATCH_TARGET.finditer(text):
            prefixed = match.group("header") in ("+++ ", "--- ")
            target = _patch_path(match.group("path"), strip if prefixed else 0, root)
            if target:
                found.append(PathWrite(target, WRITE))
    return list(dict.fromkeys(found))


def _patch_text(source: str, command: SimpleCommand, written: dict[str, WrittenFile]) -> str | None:
    """The patch `source` holds: written on this line, else read from disk."""
    if source == "-" or "$" in source or "`" in source:
        return None
    for directory in command.possible_cwds:
        if directory is None:
            continue
        absolute = os.path.normpath(os.path.join(directory, os.path.expanduser(source)))
        entry = written.get(absolute)
        if entry is not None:
            return entry.text
        try:
            if os.path.isfile(absolute) and os.path.getsize(absolute) <= _MAX_PATCH_BYTES:
                with open(absolute, encoding="utf-8", errors="replace") as handle:
                    return handle.read()
        except OSError:
            continue
    return None


def _patch_path(name: str, strip: int, root: str) -> str | None:
    if name.startswith('"'):
        try:
            name = shlex.split(name)[0]
        except ValueError:
            return None
    name = name.strip()
    if not name or name == "/dev/null":
        return None
    parts = [part for part in name.split("/") if part]
    if len(parts) <= strip:
        return None
    path = "/".join(parts[strip:])
    return f"{root}/{path}" if root else path


def _repository_name(repository: str) -> str:
    """The directory git names a clone after: `https://h/a/evil.git` -> `evil`."""
    name = re.split(r"[/:]", repository.rstrip("/"))[-1]
    if name == ".git":
        name = re.split(r"[/:]", repository.rstrip("/")[:-len("/.git")])[-1]
    return name.removesuffix(".git") or name


def _extract(name: str, args: tuple[str, ...]) -> list[PathWrite]:
    """The directory an archive extraction writes into (file names unknown)."""
    if name == "unzip":
        if any(a in ("-l", "-v", "-t", "-Z", "-p", "-c") for a in args):
            return []  # list, test or print to stdout
        archive = next(iter(_operands(args, frozenset({"-d", "-P"}))), None)
        contents = Contents(archive=archive, zip_archive=True) if archive else None
        return [PathWrite(_option_value(args, ("-d",)) or ".", INTO_DIR, contents=contents)]
    if name in ("7z", "7za"):
        if not args or args[0] not in ("x", "e"):
            return []
        output = next((a[2:] for a in args if a.startswith("-o") and len(a) > 2), ".")
        return [PathWrite(output, INTO_DIR)]
    first = args[0] if args else ""
    extracting = any(a in ("-x", "--extract", "--get") for a in args) or (
        first and not first.startswith("-") and "x" in first) or any(
        re.fullmatch(r"-[a-zA-Z]*x[a-zA-Z]*", a) for a in args)
    if not extracting or any(a in ("-O", "--to-stdout") for a in args):
        return []
    directory = _option_value(args, ("-C", "--directory"))
    return [PathWrite(directory or ".", INTO_DIR, contents=_tar_contents(args))]


_TAR_RENAMES = ("--transform", "--xform", "-s")  # member names rewritten: not listable here


def _tar_contents(args: tuple[str, ...]) -> Contents | None:
    archive = _tar_archive(args)
    if not archive or archive == "-" or any(a.split("=", 1)[0] in _TAR_RENAMES for a in args):
        return None  # read from stdin, or renamed on the way out
    strip = _option_value(args, ("--strip-components",))
    if strip is not None and not strip.isdigit():
        return None
    absolute = "--absolute-names" in args or any(re.fullmatch(r"-[A-Za-z]*P[A-Za-z]*", a) for a in args)
    return Contents(archive=archive, strip=int(strip or 0), absolute_names=absolute)


def _tar_archive(args: tuple[str, ...]) -> str | None:
    """The file `tar` reads: `-f FILE`, `--file=FILE`, `-xzf FILE`, `xzf FILE`."""
    named = _option_value(args, ("--file",))
    if named is not None:
        return named
    for index, arg in enumerate(args):
        if arg.startswith("--"):
            continue
        if arg.startswith("-"):
            cluster = arg[1:]
        elif index == 0:
            cluster = arg  # old style: `tar xzf FILE`
        else:
            continue
        if "f" in cluster:
            attached = cluster[cluster.index("f") + 1:]
            if attached and arg.startswith("-"):
                return attached  # `-fFILE`, `-xfFILE`
            return args[index + 1] if index + 1 < len(args) else None
    return None


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
