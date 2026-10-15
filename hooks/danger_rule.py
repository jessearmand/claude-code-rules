"""
The rule type shared by the dangerous_commands rule tables, plus argument helpers.

A rule matches one parsed command (shell_parse.SimpleCommand) instead of the
raw command text, so wrappers, nesting and quoting are already resolved when
it runs. `match` returns a short detail string naming what it saw, or None.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from shell_parse import ParseResult, SimpleCommand
from shell_wrappers import CLI_OPTIONS_WITH_VALUE

CRITICAL = "critical"
HIGH = "high"
STRICT = "strict"
LEVEL_ORDER = {CRITICAL: 1, HIGH: 2, STRICT: 3}


@dataclass(frozen=True)
class RuleContext:
    parsed: ParseResult
    cwd: str


@dataclass(frozen=True)
class MatchDetail:
    """A match whose evidence came from a file rather than the typed command."""

    text: str
    indirect: bool = False
    origin: str = "SQL command file"


Matcher = Callable[[SimpleCommand, RuleContext], "str | MatchDetail | None"]


@dataclass(frozen=True)
class DangerRule:
    rule_id: str
    level: str
    reason: str
    remediation: str
    match: Matcher
    # "deny", or "ask" for commands that are often legitimate but deserve a
    # human look. A deny can be softened to ask by configuration; an ask is
    # never softened further.
    decision: str = "deny"


# --------------------------------------------------------------------------- #
# Argument helpers
# --------------------------------------------------------------------------- #


def short_flag(args: tuple[str, ...] | list[str], letters: str, with_value: str = "") -> bool:
    """True if any of `letters` appears as a short option or in a cluster (`-fd`).

    Letters in `with_value` take a value, so the rest of the cluster is that
    value: in `git switch -cfeature`, `f` belongs to the branch name.
    """
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            return False
        if not (arg.startswith("-") and not arg.startswith("--") and len(arg) > 1):
            continue
        for position, letter in enumerate(arg[1:], start=1):
            if letter in letters:
                return True
            if letter in with_value:
                skip_next = position == len(arg) - 1
                break
    return False


def long_flag(args: tuple[str, ...] | list[str], *names: str, min_prefix: int = 5) -> bool:
    """True for `--name`, `--name=x`, or an unambiguous prefix such as `--forc`."""
    for arg in args:
        if arg == "--":
            return False
        if not arg.startswith("--"):
            continue
        key = arg.split("=", 1)[0]
        for name in names:
            if key == name or (len(key) >= min_prefix and name.startswith(key)):
                return True
    return False


def has(args: tuple[str, ...] | list[str], *tokens: str) -> bool:
    return any(arg in tokens for arg in args)


def operands(args: tuple[str, ...] | list[str], with_arg: frozenset[str] = frozenset()) -> list[str]:
    found: list[str] = []
    index, ended = 0, False
    while index < len(args):
        arg = args[index]
        if ended or not arg.startswith("-") or arg == "-":
            found.append(arg)
        elif arg == "--":
            ended = True
        elif arg in with_arg:
            index += 1
        index += 1
    return found


def expand(path: str, cwd: str) -> str:
    path = os.path.expanduser(path.replace("${HOME}", "~").replace("$HOME", "~"))
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))


def is_inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


_BROAD_ROOTS = ("/", "/bin", "/boot", "/dev", "/etc", "/lib", "/opt", "/sbin", "/srv", "/usr",
                "/var", "/System", "/Library", "/Applications", "/Users", "/home", "/private")


def is_broad_target(path: str, cwd: str) -> bool:
    """Filesystem root, a system root, the home directory, or a strict ancestor of cwd."""
    resolved = expand(path, cwd)
    home = os.path.expanduser("~")
    strict_ancestor = resolved != cwd and is_inside(cwd, resolved)
    return resolved in _BROAD_ROOTS or resolved == home or strict_ancestor or os.path.dirname(resolved) == "/"


def positional_words(command: SimpleCommand) -> list[str]:
    """The command's positional words (subcommand first), skipping its options' values.

    `cargo +nightly publish` names a toolchain before the subcommand; it is skipped too.
    """
    words = operands(command.args, CLI_OPTIONS_WITH_VALUE.get(command.name, frozenset()))
    if command.name == "cargo":
        words = [word for word in words if not word.startswith("+")]
    return words

