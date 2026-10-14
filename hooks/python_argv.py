"""
What a Python interpreter runs, read the way CPython reads its command line.

Short options group (`-Im json.tool`, `-imcode`), and `-W`, `-X`, `-c` and
`-m` take the rest of the word or the next word as their value
(`-W -mjson.tool` sets a warning filter; no module runs). Option parsing ends
at `-c`, `-m`, `-`, `--` or the first operand, which is the script.
"""

from __future__ import annotations

from dataclasses import dataclass

# Short options whose value is the rest of the word or the next word.
_VALUED = frozenset("WX")
# Long options that take the next word as their value.
_VALUED_LONG = frozenset({"--check-hash-based-pycs"})


@dataclass(frozen=True)
class PythonProgram:
    """`kind` is "stdin", "script", "command" or "module"; `target` is the
    script path, command text or module name. `inspect` is set by `-i`: after
    the program ends, an interactive prompt runs what remains on stdin."""

    kind: str
    target: str | None = None
    inspect: bool = False


def python_program(args: tuple[str, ...], environment: dict[str, tuple[str, ...]]) -> PythonProgram:
    """The program a `python ARGS...` invocation runs."""
    inspect = any(environment.get("PYTHONINSPECT", ()))
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "-":
            return PythonProgram("stdin", inspect=inspect)
        if arg == "--" or not arg.startswith("-"):
            script = arg if arg != "--" else (args[index] if index < len(args) else None)
            return PythonProgram("script", script, inspect) if script else PythonProgram("stdin", inspect=inspect)
        if arg.startswith("--"):
            if arg in _VALUED_LONG:
                index += 1
            continue
        for position, flag in enumerate(arg[1:], start=2):
            if flag == "i":
                inspect = True
            elif flag in _VALUED or flag in "cm":
                value = arg[position:]
                if not value:
                    value = args[index] if index < len(args) else None
                    index += 1
                if flag == "c":
                    return PythonProgram("command", value, inspect)
                if flag == "m":
                    return PythonProgram("module", value, inspect)
                break
    return PythonProgram("stdin", inspect=inspect)
