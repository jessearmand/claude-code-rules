"""
The parser's data: what it returns (SimpleCommand, ParseResult, ...), the
context it threads through a command line, and the variable table.

Variables hold every value they may have, the latest first; `UNSET` means
"may be unset". Positional parameters live under `@`, joined with `\x1f`,
array elements under `X[N]`. Part of shell_parse; import from there.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_DEPTH = 6
_MAX_UNWRAP = 16
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=")
# A parameter reference: `$NAME`, `${NAME}`, `${NAME:-default}` and the like.
_REFERENCE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_ELEMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]")

_UNRESOLVED = re.compile(r"\$|`")

# Program text a source resolver found but could not inspect (too large).
SOURCE_TOO_LARGE = "\0too-large"
# Positional parameters (`set -- a b`) live in the variable table under this
# name, joined by _FIELD, so subshells and branches treat them like variables.
_POSITIONALS = "@"
_FIELD = "\x1f"
# Starts the value of a compound array assignment; the elements follow, _FIELD-separated.
_ARRAY = "\x1e"

# A variable value meaning "may be unset" (`true || unset X`).
UNSET = "\0unset"


@dataclass(frozen=True)
class Redirect:
    op: str
    target: str
    dynamic: bool = False
    body: str | None = None  # heredoc body, when op is << or <<-


@dataclass(frozen=True)
class SimpleCommand:
    """One program invocation, wrappers removed."""

    argv: tuple[str, ...]
    redirects: tuple[Redirect, ...] = ()
    # How this command was reached, outermost first: ("bash -c",), ("$(...)",).
    via: tuple[str, ...] = ()
    wrappers: tuple[str, ...] = ()
    # argv[0] is computed at run time (`$CMD -rf x`, `$(which rm) ...`).
    dynamic_head: bool = False
    # Parsed from a file the command executes, not typed by the agent.
    indirect: bool = False
    pipeline: int = 0
    stage: int = 0
    # Directory the command runs in, following `cd`/`pushd` earlier in the same
    # line. None when a `cd` target is computed at run time (`cd "$DIR"`).
    cwd: str | None = None
    # Every directory it might run in. A `cd` can fail (missing directory) and
    # `cd a || cd b` runs only one of them, so earlier directories stay possible.
    cwd_options: tuple[str | None, ...] = ()
    # Values each variable may hold when the command starts: its prefix
    # assignments (`X=1 cmd`) and assignments earlier in the line. Programs that
    # read a variable by name (`git --config-env=key=VAR`) look it up here.
    environment: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def possible_cwds(self) -> tuple[str | None, ...]:
        return self.cwd_options or (self.cwd,)

    @property
    def name(self) -> str:
        return program_name(self.argv[0]) if self.argv else ""

    @property
    def args(self) -> tuple[str, ...]:
        return self.argv[1:]

    def rendered(self) -> str:
        return " ".join(self.argv)

    def origin(self) -> str:
        return " → ".join(self.via)

    def env_values(self, name: str) -> tuple[str, ...]:
        """Every value `name` may hold in this command's environment; () if unknown."""
        values = next((values for key, values in self.environment if key == name), ())
        return tuple(value for value in values if value != UNSET)


@dataclass(frozen=True)
class NestedSource:
    """A string parsed as shell code beyond the top level."""

    text: str
    via: tuple[str, ...]
    indirect: bool = False


@dataclass
class ParseResult:
    commands: list[SimpleCommand] = field(default_factory=list)
    # Every string that was parsed as shell code beyond the top level, with
    # how it was reached. Regex-based guards rescan these.
    nested_sources: list[NestedSource] = field(default_factory=list)
    # Files the line writes from literal text before using them (see _Context.written_files).
    written_files: dict[str, WrittenFile] = field(default_factory=dict)
    # Symlinks and hard links the line creates: absolute link path -> absolute target.
    links: dict[str, str] = field(default_factory=dict)
    # (command, label) for program text a command runs that was too large to read.
    skipped_sources: list[tuple[SimpleCommand, str]] = field(default_factory=list)
    unparseable: bool = False
    truncated: bool = False

    def previous_stage(self, command: SimpleCommand) -> SimpleCommand | None:
        """The command whose stdout feeds `command` through a pipe, if any."""
        if command.stage == 0:
            return None
        for candidate in self.commands:
            if candidate.pipeline == command.pipeline and candidate.stage == command.stage - 1:
                return candidate
        return None


def program_name(token: str) -> str:
    """`/usr/bin/sudo` -> `sudo`; `\\rm` was already unescaped by the lexer."""
    return token.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class WrittenFile:
    """Literal text a command line wrote to a file before running it."""

    text: str
    # False when the line only appended (`>>`, `tee -a`): what was on disk comes first.
    replaces: bool = True


# A source resolver maps (command, cwd, files written earlier in the same
# command line) to program text it executes from disk, e.g. the body of
# `./deploy.sh` or the `build` script in package.json.
SourceResolver = Callable[[SimpleCommand, str, "dict[str, WrittenFile]"], "list[tuple[str, str]]"]
_MAX_CWD_OPTIONS = 8


@dataclass(frozen=True)
class _BodyStep:
    """One command of a function body, as declared."""

    tokens: tuple
    isolated: bool  # a pipeline stage or background command: its own subshell
    conditional: bool  # after && / || or inside an if/loop in the body


@dataclass
class _Context:
    result: ParseResult
    # Current directory as the line runs; None once a computed `cd` makes it unknown.
    cwd: str | None
    sources: SourceResolver | None
    # Every directory the line might be in at this point (see possible_cwds).
    cwd_options: tuple[str | None, ...] = ()
    pipeline_counter: list[int] = field(default_factory=lambda: [0])
    # Every value each variable may hold, the latest assignment first. An
    # assignment that may not run (`A && X=1`, an `if` body) keeps the old values.
    variables: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # The command being parsed may not run: assignments add values, not replace them.
    conditional: bool = False
    # Declared functions: name -> the body's commands, replayed at each call.
    functions: dict[str, list[_BodyStep]] = field(default_factory=dict)
    calling: set[str] = field(default_factory=set)  # functions being replayed (no recursion)
    # Names declared `local` in each function call being replayed, innermost last.
    local_scopes: list[set[str]] = field(default_factory=list)
    # Files this command line writes from literal text before running them:
    # `cat > x.sh <<'EOF' ... EOF; bash x.sh`. Absolute path -> content.
    written_files: dict[str, WrittenFile] = field(default_factory=dict)
    # `alias x=/bin/rm`: name -> replacement text, expanded at the head of later commands.
    aliases: dict[str, str] = field(default_factory=dict)
    expanding: set[str] = field(default_factory=set)  # aliases being expanded (no recursion)
    # `declare -n Y=X`: Y names X. `declare -l/-u X`: X's value is lower/upper-cased.
    namerefs: dict[str, str] = field(default_factory=dict)
    case_attributes: dict[str, str] = field(default_factory=dict)
    # `cp /bin/rm x`: x is now that program (absolute paths); links are in result.links.
    copies: dict[str, str] = field(default_factory=dict)
    # `hash -p /bin/rm x`: bare name -> the program it runs.
    hashed: dict[str, str] = field(default_factory=dict)
    # A `return` ran in the step being parsed; the enclosing function call or
    # sourced script decides what it ends.
    returned: bool = False
    # Per shell, innermost last: trap handlers to check again when it exits,
    # as (handler, via, depth, indirect).
    trap_frames: list[list[tuple[str, tuple[str, ...], int, bool]]] = field(default_factory=lambda: [[]])

    def next_pipeline(self) -> int:
        self.pipeline_counter[0] += 1
        return self.pipeline_counter[0]


def _positionals(ctx: _Context) -> tuple[str, ...] | None:
    """The positional parameters `set --` gave this line, or None if unknown."""
    values = ctx.variables.get(_POSITIONALS)
    if not values or values[0] == UNSET:
        return None
    return tuple(values[0].split(_FIELD)) if values[0] else ()


def _target_name(name: str, ctx: _Context) -> str:
    """The variable `name` stands for: through `declare -n` references."""
    for _ in range(8):
        if name not in ctx.namerefs:
            break
        name = ctx.namerefs[name]
    return name


def _with_case(name: str, value: str, ctx: _Context) -> str:
    attribute = ctx.case_attributes.get(name)
    return value.lower() if attribute == "l" else value.upper() if attribute == "u" else value


def _assign(text: str, variables: dict[str, tuple[str, ...]], keep_previous: bool = False) -> None:
    """Remember `NAME=value` (already expanded); `NAME+=value` appends.

    `keep_previous` is for an assignment that may not run: the variable may
    still hold any of its earlier values.
    """
    name, _, value = text.partition("=")
    append = name.endswith("+")
    name = name[:-1] if append else name
    if value.startswith(_ARRAY) and _NAME.fullmatch(name):
        _assign_array(name, value[1:].split(_FIELD) if value[1:] else [], append, variables, keep_previous)
        return
    element = _ELEMENT.fullmatch(name)
    # `X[2]=v` sets element X[2]; element 0 is also plain $X, as in bash.
    keys = [f"{element.group(1)}[{int(element.group(2))}]"] if element else []
    if not element or int(element.group(2)) == 0:
        keys.append(name.split("[", 1)[0])
    for key in keys:
        values = (tuple(("" if earlier == UNSET else earlier) + value for earlier in variables.get(key, ("",)))
                  if append else (value,))
        if keep_previous:
            values = (*values, *variables.get(key, ()))
        # Not capped: every value stays possible. Their number is bounded by the
        # assignments written in the command line itself.
        variables[key] = tuple(dict.fromkeys(values))


_INDEXED = re.compile(r"\[(\d+)\]=(.*)", re.DOTALL)


def _assign_array(name: str, items: list[str], append: bool, variables: dict[str, tuple[str, ...]],
                  keep_previous: bool) -> None:
    """`X=(a b)` sets X[0]=a, X[1]=b (and $X is X[0]); `X+=(c)` appends after the
    last element; `[N]=v` sets element N."""
    indexes = [int(m.group(1)) for k in variables if (m := re.fullmatch(re.escape(name) + r"\[(\d+)\]", k))]
    if not append and not keep_previous:
        for key in [k for k in variables if k == name or k.startswith(name + "[")]:
            variables.pop(key)
        indexes = []
    position = max(indexes) + 1 if append and indexes else 0
    for item in items:
        indexed = _INDEXED.fullmatch(item)
        if indexed:
            position, item = int(indexed.group(1)), indexed.group(2)
        _assign(f"{name}[{position}]={item}", variables, keep_previous)
        position += 1


def _bounded(options: list[str | None]) -> tuple[str | None, ...]:
    """Distinct directory options; past the limit, the rest become unknown (None)."""
    distinct = list(dict.fromkeys(options))
    if len(distinct) > _MAX_CWD_OPTIONS:
        distinct = [*distinct[:_MAX_CWD_OPTIONS - 1], None]
    return tuple(distinct)


def _at(args: list[str], index: int) -> str | None:
    return args[index] if index < len(args) else None
