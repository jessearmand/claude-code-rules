"""
Files a database client runs: those its command line names, and those its
input tells it to read.

Both the destructive-SQL rule (dangerous_infra) and the shell-escape scan
(shell_payloads) need the statements a client runs, wherever they come from.
Besides its arguments, a client runs:

- a command file named on the line (`sqlite3 -init FILE`, `psql -f FILE`,
  `< FILE`), read as written earlier on the same line or from disk;
- every file an include meta-command names, as if its lines were typed there
  (PR review): sqlite3 and duckdb `.read FILE`, psql and cockroach `\\i FILE` /
  `\\include FILE` and `\\ir FILE` / `\\include_relative FILE` (relative to the
  including file), mysql and mariadb `source FILE` / `\\. FILE`, sqlcmd
  `:r FILE`. Included files are followed in turn, within MAX_DEPTH and MAX_FILES.

A file is "typed" when the same command line wrote it and everything that led
to it was typed, so a guard may treat its contents as the agent's own command.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from shell_model import WrittenFile

MAX_FILE_BYTES = 256 * 1024
MAX_DEPTH = 8
MAX_FILES = 32


@dataclass(frozen=True)
class ClientInput:
    text: str
    typed: bool  # on the command line, or in a file the same line wrote
    directory: str | None = None  # the directory of the file it came from


# A quoted or bare file name: the value of an include meta-command.
_NAME = r"""(?:'([^']*)'|"([^"]*)"|([^\s'"]\S*))"""
# (pattern, whether a name is relative to the including file). `.read |CMD`
# runs a command, which shell_payloads parses instead.
_SQLITE_INCLUDE = ((re.compile(rf"^[ \t]*\.read[ \t]+(?!['\"]?\|){_NAME}", re.MULTILINE), False),)
_INCLUDES: dict[str, tuple[tuple[re.Pattern[str], bool], ...]] = {
    "sqlite": _SQLITE_INCLUDE,
    # psql meta-commands may follow SQL on a line; their arguments end at a backslash.
    "psql": (
        (re.compile(r"\\(?:include|i)[ \t]+(?:'([^']*)'|([^\s\\']+))"), False),
        (re.compile(r"\\(?:include_relative|ir)[ \t]+(?:'([^']*)'|([^\s\\']+))"), True),
    ),
    # mysql: `source FILE` on its own line (an optional `;`), or `\. FILE` anywhere.
    "mysql": ((re.compile(r"(?:^[ \t]*source[ \t]+|\\\.[ \t]*)(?:'([^']*)'|\"([^\"]*)\"|([^\n;]*?))[ \t]*;?[ \t]*$",
                          re.MULTILINE | re.IGNORECASE), False),),
    "sqlcmd": ((re.compile(rf"^[ \t]*:r[ \t]+{_NAME}", re.MULTILINE | re.IGNORECASE), False),),
}
_INCLUDES["cockroach"] = _INCLUDES["psql"]
# Each client's dialect of meta-commands.
DIALECTS = {"sqlite3": "sqlite", "sqlite": "sqlite", "duckdb": "sqlite", "psql": "psql",
            "cockroach": "cockroach", "mysql": "mysql", "mariadb": "mysql", "sqlcmd": "sqlcmd"}
# Options naming a file of statements the client runs.
FILE_OPTIONS = {"psql": ("-f", "--file"), "sqlite3": ("-init", "--init"), "sqlite": ("-init", "--init"),
                "duckdb": ("-init",), "sqlcmd": ("-i",), "clickhouse-client": ("--queries-file",),
                "cockroach": ("-f", "--file")}
# Clients whose single-letter options take an attached value (`psql -c'DROP …'`),
# as getopt allows (PR review). duckdb and clickhouse use `-csv`-style words.
GETOPT_CLIENTS = frozenset({"psql", "mysql", "mariadb", "cockroach", "sqlcmd"})


def option_values(args: tuple[str, ...], flags: tuple[str, ...], attached: bool = True) -> list[str]:
    """Values given to `flags`, as `-f FILE`, `--file=FILE` or, for getopt-style
    clients (`attached`), `-fFILE`. duckdb's `-csv` is one option, not `-c sv`."""
    values: list[str] = []
    for index, arg in enumerate(args):
        for flag in flags:
            if arg == flag and index + 1 < len(args):
                values.append(args[index + 1])
            elif flag.startswith("--") and arg.startswith(flag + "="):
                values.append(arg[len(flag) + 1:])
            elif attached and not flag.startswith("--") and len(flag) == 2 and arg.startswith(flag) and len(arg) > 2:
                values.append(arg[2:])
    return values


def file_option_values(client: str, args: tuple[str, ...]) -> list[str]:
    """The names of the command files `client`'s options give (`psql -f q.sql`)."""
    return option_values(args, FILE_OPTIONS.get(client, ()), attached=client in GETOPT_CLIENTS)


def read_file(name: str, cwds: Iterable[str | None], written: dict[str, WrittenFile]) -> ClientInput | None:
    """The file `name` as the same line wrote it (typed), else as on disk, in
    the first of `cwds` where it is found. None when it is not there or too large."""
    for directory in cwds:
        if directory is None:
            continue
        absolute = os.path.normpath(os.path.join(directory, os.path.expanduser(name)))
        entry = written.get(absolute)
        if entry is not None:
            return ClientInput(entry.text, True, os.path.dirname(absolute))
        try:
            if os.path.isfile(absolute) and os.path.getsize(absolute) <= MAX_FILE_BYTES:
                with open(absolute, encoding="utf-8", errors="replace") as handle:
                    return ClientInput(handle.read(), False, os.path.dirname(absolute))
        except OSError:
            continue
    return None


def included(client: str, inputs: Iterable[ClientInput], cwds: tuple[str | None, ...],
             written: dict[str, WrittenFile], unknown: Callable[[str], None] = lambda name: None) -> list[ClientInput]:
    """Every file `inputs` include, transitively, for the client `client`.

    `cwds`: the directories the client may run in, which a name not relative
    to its including file is resolved against. `unknown(name)` is called for an
    include that cannot be followed: a psql `:variable`, or past the bounds."""
    patterns = _INCLUDES.get(DIALECTS.get(client, ""), ())
    found: list[ClientInput] = []
    seen: set[tuple[str | None, str]] = set()
    frontier = [(source, 0) for source in inputs]
    while frontier:
        source, depth = frontier.pop(0)
        for pattern, relative in patterns:
            for match in pattern.finditer(source.text):
                name = next((g for g in match.groups() if g), "").strip()
                if not name:
                    continue
                if client == "psql" and name.startswith(":"):
                    unknown(name)  # `\i :file`: a psql variable
                    continue
                base = (source.directory,) if relative and source.directory else cwds
                key = (base[0], name)
                if key in seen:
                    continue
                seen.add(key)
                if depth >= MAX_DEPTH or len(found) >= MAX_FILES:
                    unknown(name)
                    continue
                entry = read_file(name, base, written)
                if entry is None:
                    continue
                entry = ClientInput(entry.text, entry.typed and source.typed, entry.directory)
                found.append(entry)
                frontier.append((entry, depth + 1))
    return found
