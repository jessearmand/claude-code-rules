"""
Program text a command executes from disk, for shell_parse.parse(sources=...).

Writing `rm -rf ~` into `cleanup.sh` and then running `./cleanup.sh` used to
pass every guard: the hooks only ever saw the second command. This resolver
reads what such a command would run, so the guards judge the body instead of
the file name:

  ./script.sh, bash script.sh, source/. script.sh
      the file, when it is a small text file with a shell shebang (or no
      shebang and a shell extension)
  npm/pnpm/yarn/bun run NAME, npm test/start/...
      package.json scripts[NAME] plus its pre/post hooks
  make [TARGET...]
      the recipe lines of those targets (or the default target) and, one
      level down, their prerequisites

Everything returned is marked `indirect` by the parser: the agent did not
type it in this call, and the file may predate the session, so guards
generally ask about it rather than deny. Reading never raises; anything that
cannot be read cleanly is skipped.
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re

from shell_parse import SOURCE_TOO_LARGE, SimpleCommand, WrittenFile
from shell_wrappers import CLI_OPTIONS_WITH_VALUE, SHELLS, shell_invocation

MAX_SOURCE_BYTES = 256 * 1024
_SHEBANG_SHELL = re.compile(r"^#!\s*\S*?(?:/env\s+(?:-S\s+)?)?\S*?\b(?:ba|z|da|k|mk|a)?sh\b")
_SHELL_SUFFIXES = (".sh", ".bash", ".zsh", ".ksh")
_PACKAGE_RUNNERS = frozenset({"npm", "pnpm", "yarn", "bun"})
_NPM_LIFECYCLE = frozenset({"test", "start", "stop", "restart"})
_MAKE_OPTIONS_WITH_ARG = frozenset({"-C", "--directory", "-f", "--file", "--makefile", "-I", "-E", "--eval",
                                    "--include-dir", "-o", "--old-file", "-W", "--what-if",
                                    "-l", "--load-average"})
_MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")
_MAKE_RULE = re.compile(r"^([^\s:#=][^:=]*?)\s*::?(?!=)\s*(.*)$")
_MAX_MAKE_TARGETS = 256


def resolve(
    command: SimpleCommand, cwd: str, written: dict[str, WrittenFile] | None = None
) -> list[tuple[str, str]]:
    """(program text, label) pairs that `command` would execute from disk.

    `written` holds files the same command line wrote from literal text
    before running them; they are used in place of what is on disk.
    """
    try:
        return _resolve(command, cwd, written or {})
    except (OSError, ValueError, RecursionError):
        return []


def _resolve(command: SimpleCommand, cwd: str, written: dict[str, WrittenFile]) -> list[tuple[str, str]]:
    if not command.argv or command.dynamic_head:
        return []
    name, args = command.name, list(command.args)
    head = command.argv[0]

    if name in SHELLS:
        shell = shell_invocation(name, args)
        if shell.syntax_only or shell.script is None:
            return []  # command strings and stdin are parsed by the parser itself
        return _script(shell.script, cwd, written, require_shebang=False)
    if name in ("source", "."):
        return _script(_source_path(args[0], command, cwd, written), cwd, written, require_shebang=False) if args else []
    if "/" in head:
        return _script(head, cwd, written, require_shebang=True)
    if name in _PACKAGE_RUNNERS:
        return _package_script(name, args, cwd, written)
    if name in ("make", "gmake"):
        return _make(args, cwd, written)
    return []


def _source_path(operand: str, command: SimpleCommand, cwd: str, written: dict[str, WrittenFile]) -> str:
    """The file `source NAME` reads: a NAME without a slash is looked up in PATH
    first (bash's default), then in the current directory."""
    if "/" in operand:
        return operand
    path_values = command.env_values("PATH") or (os.environ.get("PATH", ""),)
    for directory in path_values[0].split(os.pathsep):
        candidate = os.path.join(os.path.expanduser(directory or "."), operand)
        absolute = os.path.normpath(os.path.join(cwd, candidate))
        if absolute in written or os.path.isfile(absolute):
            return absolute
    return operand


# --------------------------------------------------------------------------- #
# Shell scripts
# --------------------------------------------------------------------------- #




def _too_large(path: str) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) > MAX_SOURCE_BYTES


def _first_line(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(512).split(b"\n", 1)[0].decode("utf-8", "replace")
    except OSError:
        return ""


def _read_text(path: str) -> str | None:
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_SOURCE_BYTES:
        return None
    with open(path, "rb") as handle:
        data = handle.read(MAX_SOURCE_BYTES + 1)
    if b"\0" in data:
        return None
    # bash runs a script with stray non-UTF-8 bytes; read it the same way.
    return data.decode("utf-8", errors="replace")


def _absolute(path: str, cwd: str) -> str:
    expanded = os.path.expanduser(path)
    return os.path.normpath(expanded if os.path.isabs(expanded) else os.path.join(cwd, expanded))


def _script(path: str, cwd: str, written: dict[str, WrittenFile], require_shebang: bool) -> list[tuple[str, str]]:
    absolute = _absolute(path, cwd)
    if absolute in written:
        # Written by this same command line: its author is the agent, right now.
        # Appended text runs after whatever the file already held.
        entry = written[absolute]
        found = [(entry.text, f"written {path}")]
        if not entry.replaces:
            if _too_large(absolute):
                found.insert(0, (SOURCE_TOO_LARGE, f"script {path}"))
            elif on_disk := _read_text(absolute):
                found.insert(0, (on_disk, f"script {path}"))
        return found
    if require_shebang:
        first_line = _first_line(absolute)
        has_shebang = first_line.startswith("#!")
        is_shell = bool(_SHEBANG_SHELL.match(first_line)) if has_shebang else path.endswith(_SHELL_SUFFIXES)
        if not is_shell:
            return []
    if _too_large(absolute):
        return [(SOURCE_TOO_LARGE, f"script {path}")]  # the caller asks instead of guessing
    text = _read_text(absolute)
    if text is None:
        # A file a shell runs that cannot be read as text: ask rather than guess.
        return [(SOURCE_TOO_LARGE, f"script {path}")] if os.path.isfile(absolute) else []
    return [(text, f"script {path}")]


# --------------------------------------------------------------------------- #
# package.json scripts
# --------------------------------------------------------------------------- #


def _operands(args: list[str], with_value: frozenset[str]) -> list[str]:
    """Positional words, skipping option values (`npm -w web run build`)."""
    words: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break  # arguments for the script itself
        if not arg.startswith("-"):
            words.append(arg)
        elif arg in with_value:
            index += 1
        index += 1
    return words


# Options naming the package directory to run in, and options selecting workspaces.
_PACKAGE_DIR_OPTIONS = frozenset({"--prefix", "-C", "--dir", "--cwd"})
_WORKSPACE_OPTIONS = frozenset({"-w", "--workspace", "-F", "--filter"})
_ALL_WORKSPACES = frozenset({"-ws", "--workspaces", "-r", "--recursive"})


def _package_script(runner: str, args: list[str], cwd: str,
                    written: dict[str, WrittenFile]) -> list[tuple[str, str]]:
    """package.json scripts a package-manager command runs, in the package it selects.

    `npm -w app run clean`, `pnpm --filter app clean` and `yarn workspace app clean`
    run app/package.json's script, not the root's; `npm --prefix dir run x` and
    `pnpm -C dir x` run dir's. An unresolvable workspace selector is checked
    against every workspace package.
    """
    positionals = _operands(args, CLI_OPTIONS_WITH_VALUE.get(runner, frozenset()))
    selectors = _option_values(args, _WORKSPACE_OPTIONS)
    if runner == "yarn" and positionals[:1] == ["workspace"] and len(positionals) > 1:
        selectors.append(positionals[1])
        positionals = positionals[2:]
    elif runner == "yarn" and positionals[:2] == ["workspaces", "foreach"]:
        positionals = positionals[2:]
        selectors.append("*")
    script = _script_name(runner, positionals)
    if script is None:
        return []

    root = _package_root(args, cwd)
    if any(a in _ALL_WORKSPACES or a.startswith("--workspaces=") for a in args):
        selectors.append("*")
    directories = _select_workspaces(root, selectors) if selectors else [root]

    found: list[tuple[str, str]] = []
    for directory in directories:
        scripts, typed = _package_scripts(directory, written)
        if script not in scripts:
            continue
        where = "" if directory == root else f" in {os.path.relpath(directory, root)}"
        # A package.json this same line wrote: its scripts count as typed by the agent.
        prefix = "written " if typed else ""
        for name in (f"pre{script}", script, f"post{script}"):
            if name in scripts:
                found.append((scripts[name], f"{prefix}{runner} script {name}{where}"))
    return found


def _script_name(runner: str, positionals: list[str]) -> str | None:
    if not positionals:
        return None
    verb = positionals[0]
    if verb in ("run", "run-script"):
        return positionals[1] if len(positionals) > 1 else None
    if runner == "npm":
        return verb if verb in _NPM_LIFECYCLE else None
    return verb if runner in ("yarn", "bun", "pnpm") else None  # `yarn build` runs a script


def _option_values(args: list[str], names: frozenset[str]) -> list[str]:
    values: list[str] = []
    for index, arg in enumerate(args):
        key, sep, value = arg.partition("=")
        if key in names and sep:
            values.append(value)
        elif arg in names and index + 1 < len(args):
            values.append(args[index + 1])
    return values


def _package_root(args: list[str], cwd: str) -> str:
    chosen = _option_values(args, _PACKAGE_DIR_OPTIONS)
    return _absolute(chosen[-1], cwd) if chosen else cwd


def _workspace_dirs(root: str) -> list[str]:
    """Package directories listed by package.json `workspaces` or pnpm-workspace.yaml."""
    patterns: list[str] = []
    text = _read_text(os.path.join(root, "package.json"))
    data = json.loads(text) if text else {}
    declared = data.get("workspaces") if isinstance(data, dict) else None
    if isinstance(declared, dict):
        declared = declared.get("packages")
    if isinstance(declared, list):
        patterns += [p for p in declared if isinstance(p, str)]
    pnpm = _read_text(os.path.join(root, "pnpm-workspace.yaml")) or ""
    patterns += re.findall(r"^\s*-\s*['\"]?([^'\"#\n]+?)['\"]?\s*$", pnpm, re.MULTILINE)
    found: list[str] = []
    for pattern in patterns:
        if pattern.startswith("!"):
            continue
        for path in sorted(glob.glob(os.path.join(root, pattern))):
            if os.path.isfile(os.path.join(path, "package.json")) and path not in found:
                found.append(os.path.normpath(path))
    return found


def _package_name(directory: str) -> str:
    text = _read_text(os.path.join(directory, "package.json"))
    data = json.loads(text) if text else {}
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else ""


def _select_workspaces(root: str, selectors: list[str]) -> list[str]:
    """Workspace directories matching any selector by path, directory name or package name."""
    candidates = _workspace_dirs(root)
    chosen: list[str] = []
    for selector in selectors:
        target = selector.strip("{}").lstrip("./") if selector not in (".", "./") else ""
        direct = _absolute(selector, root)
        if os.path.isfile(os.path.join(direct, "package.json")):
            chosen.append(direct)
            continue
        matches = [d for d in candidates
                   if selector == "*" or fnmatch.fnmatch(_package_name(d), target)
                   or fnmatch.fnmatch(os.path.relpath(d, root), target)
                   or fnmatch.fnmatch(os.path.basename(d), target)]
        # Cannot tell which package is meant: judge every workspace, never none.
        chosen += matches or candidates
    return list(dict.fromkeys(chosen)) or [root]


def _package_scripts(cwd: str, written: dict[str, WrittenFile]) -> tuple[dict[str, str], bool]:
    """The `scripts` of cwd/package.json, and whether that file was written by this line."""
    path = os.path.normpath(os.path.join(cwd, "package.json"))
    entry = written.get(path)
    if entry is not None:
        text = entry.text if entry.replaces else (_read_text(path) or "") + entry.text
    else:
        text = _read_text(path)
    if text is None:
        return {}, False
    data = json.loads(text)
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return {}, entry is not None
    return {key: value for key, value in scripts.items() if isinstance(value, str)}, entry is not None


# --------------------------------------------------------------------------- #
# Makefile recipes
# --------------------------------------------------------------------------- #


def _make(args: list[str], cwd: str, written: dict[str, WrittenFile]) -> list[tuple[str, str]]:
    directory, makefile, targets, evaluated = cwd, None, [], []
    index = 0
    while index < len(args):
        arg, value, step = _make_option(args, index)
        if arg in ("-E", "--eval"):
            # `make --eval='x:; cmd' x`: a makefile statement typed in the command.
            evaluated.append(value)
            index += step
        elif arg in ("-C", "--directory"):
            directory, index = _absolute(value, directory), index + step
        elif arg in ("-f", "--file", "--makefile"):
            makefile, index = value, index + step
        elif arg in _MAKE_OPTIONS_WITH_ARG:
            index += step
        elif arg.startswith("-") or "=" in arg:
            index += 1
        else:
            targets.append(arg)
            index += 1

    path = _absolute(makefile, directory) if makefile else next(
        (p for p in (os.path.join(directory, n) for n in _MAKEFILE_NAMES) if p in written or os.path.isfile(p)),
        None,
    )
    if path in written:
        # A Makefile this same command line wrote: make reads the new text, and
        # its recipes count as typed by the agent.
        entry = written[path]
        on_disk = "" if entry.replaces else (_read_text(path) or "")
        text, typed = on_disk + entry.text, True
    elif path and _too_large(path):
        return [(SOURCE_TOO_LARGE, f"make {path}")]
    else:
        text, typed = (_read_text(path) if path else None) or "", False
    statements = "\n".join(evaluated)
    rules, default = _make_rules(text + "\n" + statements)
    typed_targets = set(_make_rules(statements)[0]) if statements else set()
    wanted = targets or ([default] if default else [])
    return [(recipe, "written " + label if typed or label.split(" ", 1)[-1] in typed_targets else label)
            for recipe, label in _make_recipes(rules, wanted)]


def _make_option(args: list[str], index: int) -> tuple[str, str, int]:
    """(option name, value, words consumed) for `-C dir`, `-Cdir`, `--directory=dir`."""
    arg = args[index]
    following = args[index + 1] if index + 1 < len(args) else ""
    if arg.startswith("--") and "=" in arg:
        name, value = arg.split("=", 1)
        return name, value, 1
    if not arg.startswith("--") and len(arg) > 2 and arg[:2] in _MAKE_OPTIONS_WITH_ARG:
        return arg[:2], arg[2:], 1
    return arg, following, 2


def _make_rules(text: str) -> tuple[dict[str, tuple[list[str], list[str]]], str | None]:
    """target -> (prerequisites, recipe lines), plus the default target."""
    rules: dict[str, tuple[list[str], list[str]]] = {}
    default: str | None = None
    current: list[str] = []
    for raw in text.replace("\\\n", " ").split("\n"):
        if raw.startswith("\t"):
            line = raw[1:].lstrip("@-+ \t")
            for target in current:
                rules[target][1].append(line)
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _MAKE_RULE.match(raw)
        if not match:
            current = []
            continue
        names = match.group(1).split()
        prerequisites = match.group(2).split(";", 1)
        current = [name for name in names if "%" not in name]
        for name in current:
            rules.setdefault(name, ([], []))[0].extend(prerequisites[0].split())
            if len(prerequisites) > 1 and prerequisites[1].strip():
                rules[name][1].append(prerequisites[1].strip())
            if default is None and not name.startswith("."):
                default = name
    return rules, default


def _make_recipes(rules: dict[str, tuple[list[str], list[str]]], wanted: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    queue = list(wanted)
    while queue:
        if len(seen) >= _MAX_MAKE_TARGETS:
            found.append((SOURCE_TOO_LARGE, f"make: more than {_MAX_MAKE_TARGETS} targets"))
            break
        target = queue.pop(0)
        if target in seen or target not in rules:
            continue
        seen.add(target)
        prerequisites, recipe = rules[target]
        if recipe:
            found.append(("\n".join(recipe), f"make {target}"))
        queue.extend(prerequisites)
    return found
