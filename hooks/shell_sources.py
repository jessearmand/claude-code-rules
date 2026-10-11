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

import json
import os
import re

from shell_parse import SimpleCommand, WrittenFile
from shell_wrappers import CLI_OPTIONS_WITH_VALUE, SHELL_OPTIONS_WITH_ARG, SHELLS

MAX_SOURCE_BYTES = 256 * 1024
_SHEBANG_SHELL = re.compile(r"^#!\s*\S*?(?:/env\s+(?:-S\s+)?)?\S*?\b(?:ba|z|da|k|mk|a)?sh\b")
_SHELL_SUFFIXES = (".sh", ".bash", ".zsh", ".ksh")
_PACKAGE_RUNNERS = frozenset({"npm", "pnpm", "yarn", "bun"})
_NPM_LIFECYCLE = frozenset({"test", "start", "stop", "restart"})
_MAKE_OPTIONS_WITH_ARG = frozenset({"-C", "--directory", "-f", "--file", "--makefile", "-I",
                                    "--include-dir", "-o", "--old-file", "-W", "--what-if",
                                    "-l", "--load-average"})
_MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")
_MAKE_RULE = re.compile(r"^([^\s:#=][^:=]*?)\s*::?(?!=)\s*(.*)$")
_MAX_MAKE_TARGETS = 8


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
        script = _shell_script_argument(args)
        return _script(script, cwd, written, require_shebang=False) if script else []
    if name in ("source", "."):
        return _script(args[0], cwd, written, require_shebang=False) if args else []
    if "/" in head:
        return _script(head, cwd, written, require_shebang=True)
    if name in _PACKAGE_RUNNERS:
        return _package_script(name, args, cwd)
    if name in ("make", "gmake"):
        return _make(args, cwd)
    return []


# --------------------------------------------------------------------------- #
# Shell scripts
# --------------------------------------------------------------------------- #


def _shell_script_argument(args: list[str]) -> str | None:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if arg in SHELL_OPTIONS_WITH_ARG:
            index += 2
            continue
        if arg.startswith("-") and not arg.startswith("--") and ("c" in arg[1:] or "n" in arg[1:]):
            return None  # `bash -c STRING` is parsed by the parser itself; `-n` runs nothing
        if arg.startswith(("-", "+")) and arg not in ("-", "+"):
            index += 1
            continue
        break
    return args[index] if index < len(args) and args[index] != "-" else None


def _read_text(path: str) -> str | None:
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_SOURCE_BYTES:
        return None
    with open(path, "rb") as handle:
        data = handle.read(MAX_SOURCE_BYTES + 1)
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


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
            on_disk = _read_text(absolute)
            if on_disk:
                found.insert(0, (on_disk, f"script {path}"))
        return found
    text = _read_text(absolute)
    if text is None:
        return []
    first_line = text.split("\n", 1)[0]
    if require_shebang:
        has_shebang = first_line.startswith("#!")
        is_shell = bool(_SHEBANG_SHELL.match(first_line)) if has_shebang else path.endswith(_SHELL_SUFFIXES)
        if not is_shell:
            return []
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


def _package_script(runner: str, args: list[str], cwd: str) -> list[tuple[str, str]]:
    positionals = _operands(args, CLI_OPTIONS_WITH_VALUE.get(runner, frozenset()))
    if not positionals:
        return []
    verb = positionals[0]
    if verb in ("run", "run-script"):
        if len(positionals) < 2:
            return []
        script = positionals[1]
    elif runner == "npm" and verb in _NPM_LIFECYCLE:
        script = verb
    elif runner in ("yarn", "bun", "pnpm"):
        script = verb  # `yarn build` runs the script when one is defined
    else:
        return []

    scripts = _package_scripts(cwd)
    if script not in scripts:
        return []
    names = [f"pre{script}", script, f"post{script}"]
    return [(scripts[name], f"{runner} script {name}") for name in names if name in scripts]


def _package_scripts(cwd: str) -> dict[str, str]:
    text = _read_text(os.path.join(cwd, "package.json"))
    if text is None:
        return {}
    data = json.loads(text)
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return {}
    return {key: value for key, value in scripts.items() if isinstance(value, str)}


# --------------------------------------------------------------------------- #
# Makefile recipes
# --------------------------------------------------------------------------- #


def _make(args: list[str], cwd: str) -> list[tuple[str, str]]:
    directory, makefile, targets = cwd, None, []
    index = 0
    while index < len(args):
        arg = args[index]
        value = args[index + 1] if index + 1 < len(args) else ""
        if arg in ("-C", "--directory"):
            directory, index = _absolute(value, directory), index + 2
        elif arg in ("-f", "--file", "--makefile"):
            makefile, index = value, index + 2
        elif arg in _MAKE_OPTIONS_WITH_ARG:
            index += 2
        elif arg.startswith("-") or "=" in arg:
            index += 1
        else:
            targets.append(arg)
            index += 1

    path = _absolute(makefile, directory) if makefile else next(
        (p for p in (os.path.join(directory, n) for n in _MAKEFILE_NAMES) if os.path.isfile(p)), None
    )
    text = _read_text(path) if path else None
    if text is None:
        return []
    rules, default = _make_rules(text)
    wanted = targets or ([default] if default else [])
    return _make_recipes(rules, wanted)


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
    while queue and len(seen) < _MAX_MAKE_TARGETS:
        target = queue.pop(0)
        if target in seen or target not in rules:
            continue
        seen.add(target)
        prerequisites, recipe = rules[target]
        if recipe:
            found.append(("\n".join(recipe), f"make {target}"))
        queue.extend(prerequisites)
    return found
