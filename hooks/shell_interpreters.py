"""
Shell commands embedded in interpreter one-liners and awk programs.

`python3 -c "import os; os.system('rm -rf ~')"` and
`awk 'BEGIN { system("rm -rf ~") }'` run a shell command just as surely as
`bash -c` does. This module finds the inline program of an interpreter
invocation and, when that program can hand a string to a shell (os.system,
subprocess, child_process, backticks, Perl system/qx, awk system/pipes),
returns the string literals in it so the parser can analyze them as shell.

Only literal strings are visible. Commands assembled at run time
(`"r" + "m"`, chr(), base64) are not; the model judge sees the whole program.
Native file APIs such as shutil.rmtree are likewise left to the judge.
"""

from __future__ import annotations

import re
import shlex

from shell_lexer import HEREDOC_OPERATORS

# Inline-code flags per interpreter, and flags that consume a value.
_CODE_FLAGS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "python": (frozenset({"-c"}), frozenset({"-W", "-X", "-m"})),
    "node": (frozenset({"-e", "--eval", "-p", "--print"}),
             frozenset({"-r", "--require", "-C", "--conditions", "--import", "--loader",
                        "--input-type", "--env-file"})),
    "bun": (frozenset({"-e", "--eval", "-p", "--print"}), frozenset({"-r", "--preload"})),
    "deno": (frozenset({"eval"}), frozenset()),
    "ruby": (frozenset({"-e"}), frozenset({"-C", "-E", "-F", "-I", "-r"})),
    "perl": (frozenset({"-e", "-E"}), frozenset({"-F", "-I", "-M", "-m"})),
    "php": (frozenset({"-r"}), frozenset({"-c", "-d", "-f"})),
    "osascript": (frozenset({"-e"}), frozenset({"-l"})),
}
_PYTHON = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")
_AWKS = frozenset({"awk", "gawk", "mawk", "nawk"})

# Calls that pass a string to a shell or exec.
_EXEC_SINK = re.compile(
    r"`|%x|\bsystem\b|\bexec(?:Sync|File|FileSync|vp?|lp?)?\b|\bspawn(?:Sync)?\b|\bpopen\b"
    r"|\bsubprocess\b|\bchild_process\b|\bopen3\b|\bqx\b|\bcheck_output\b|\bcheck_call\b"
    r"|\bgetoutput\b|\bshell_exec\b|\bpassthru\b|\bproc_open\b|\bdo shell script\b|\bDeno\.Command\b"
    r"|\bBun\.spawn\b|\$\(",
)
_STRING_LITERAL = re.compile(
    r'"""(.*?)"""|\'\'\'(.*?)\'\'\'|"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`((?:[^`\\]|\\.)*)`'
    r"|\bqx\s*\{([^}]*)\}|\bqx\s*\(([^)]*)\)",
    re.DOTALL,
)
_AWK_COMMANDS = re.compile(
    r'\bsystem\s*\(\s*"((?:[^"\\]|\\.)*)"|\|\s*"((?:[^"\\]|\\.)*)"|"((?:[^"\\]|\\.)*)"\s*\|\s*getline'
)


def interpreter_kind(name: str) -> str | None:
    if _PYTHON.match(name):
        return "python"
    return name if name in _CODE_FLAGS else None


def inline_programs(name: str, args: tuple[str, ...], redirects: tuple = ()) -> list[str]:
    """The program text an interpreter runs from its arguments or a heredoc."""
    kind = interpreter_kind(name)
    if kind is None:
        return []
    code_flags, with_arg = _CODE_FLAGS[kind]
    programs: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in code_flags and index + 1 < len(args):
            programs.append(args[index + 1])
            index += 2
            continue
        if arg.startswith("--") and "=" in arg and arg.split("=", 1)[0] in code_flags:
            programs.append(arg.split("=", 1)[1])
            index += 1
            continue
        attached = next((f for f in code_flags if f.startswith("-") and not f.startswith("--")
                         and arg.startswith(f) and len(arg) > len(f)), None)
        if attached:
            programs.append(arg[len(attached):])
        elif arg.startswith("-") and len(arg) > 2 and arg[-1] in "ce" and arg[1] != "-":
            # Clusters such as `perl -ne`, `ruby -pe`, `python -Bc`.
            if f"-{arg[-1]}" in code_flags and index + 1 < len(args):
                programs.append(args[index + 1])
                index += 2
                continue
        elif arg in with_arg:
            index += 1
        index += 1
    for redirect in redirects:
        if redirect.op in HEREDOC_OPERATORS and redirect.body:
            programs.append(redirect.body)
        elif redirect.op == "<<<":
            programs.append(redirect.target)
    return programs


def shell_strings(program: str) -> list[str]:
    """String literals in `program` that could reach a shell, if it has an exec sink."""
    if not _EXEC_SINK.search(program):
        return []
    found: list[str] = []
    for match in _STRING_LITERAL.finditer(program):
        literal = _unescape(next((group for group in match.groups() if group is not None), ""))
        if literal.strip() and literal not in found:
            found.append(literal)
    return _with_argv_joins(program, found)


# A call whose first argument a shell (or exec) runs: `os.system("...")`,
# `execSync("...")`, `system("...")`, `subprocess.run([...])`, `do shell script "..."`.
_SINK_CALL = re.compile(
    r"\b(?:os\.(?:system|popen|exec\w*|spawn\w*)|subprocess\.\w+|commands\.getoutput|check_output|check_call"
    r"|getoutput|system|popen|exec(?:Sync|File|FileSync)?|spawn(?:Sync)?|shell_exec|passthru|proc_open"
    r"|Open3\.\w+|IO\.popen|Kernel\.(?:system|exec|spawn)|Deno\.Command|Bun\.spawn(?:Sync)?)\s*\(\s*"
    r"|\bdo shell script\s+",
)
# Interpreters where a backtick string (or qx//, %x()) runs a shell command.
_BACKTICK_RUNS = frozenset({"ruby", "perl", "php"})


def string_literals(program: str) -> list[str]:
    """Every string literal in `program`, unescaped, in order (no duplicates)."""
    found = (_unescape(next((g for g in m.groups() if g is not None), ""))
             for m in _STRING_LITERAL.finditer(program))
    return [text for text in dict.fromkeys(found) if text.strip()]


def direct_shell_strings(program: str, kind: str | None) -> list[str]:
    """The literals in `program` that a shell or exec certainly receives: the
    first argument of a sink call (a string, or a list joined into argv), and
    backtick/qx/%x strings where those run commands. Other literals
    `shell_strings` returns may only be data (`["echo", "rm -rf x"]`)."""
    found: list[str] = []
    for call in _SINK_CALL.finditer(program):
        rest = program[call.end():]
        literal = _STRING_LITERAL.match(rest)
        if literal:
            found.append(_unescape(next((g for g in literal.groups() if g is not None), "")))
        elif rest.startswith("["):
            joined = _with_argv_joins(rest, [])
            found += joined[:1]
    if kind in _BACKTICK_RUNS:
        for match in _STRING_LITERAL.finditer(program):
            groups = match.groups()
            command = next((g for g in groups[4:] if g is not None), None)  # `...`, qx{...}, qx(...)
            if command is not None:
                found.append(_unescape(command))
        found += [_unescape(m.group(1)) for m in re.finditer(r"%x\(([^)]*)\)", program)]
    return [text for text in dict.fromkeys(found) if text.strip()]


def _unescape(literal: str) -> str:
    return literal.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n")


def _with_argv_joins(program: str, literals: list[str]) -> list[str]:
    """Also join list-form argv (`['rm', '-rf', p]`) so `rm -rf` is visible as one command."""
    joined: list[str] = []
    for match in re.finditer(r"\[\s*((?:(['\"])[^'\"]*\2\s*,\s*)+(?:(['\"])[^'\"]*\3)?)", program):
        words = re.findall(r"['\"]([^'\"]*)['\"]", match.group(1))
        if len(words) > 1:
            # An argv list reaches no shell: each element stays one word (`>` is text).
            joined.append(shlex.join(words))
    return literals + [text for text in joined if text not in literals]


def awk_commands(name: str, args: tuple[str, ...]) -> list[str]:
    """Literal commands an awk program runs through system(), `| "cmd"` or getline."""
    if name not in _AWKS:
        return []
    program = _awk_program(args)
    if not program:
        return []
    found: list[str] = []
    for match in _AWK_COMMANDS.finditer(program):
        literal = next(group for group in match.groups() if group is not None)
        literal = literal.replace('\\"', '"').replace("\\\\", "\\")
        if literal.strip():
            found.append(literal)
    return found


def _awk_program(args: tuple[str, ...]) -> str | None:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-e", "--source") and index + 1 < len(args):
            return args[index + 1]
        if arg in ("-f", "--file", "-F", "-v", "--assign", "--field-separator"):
            index += 2
            continue
        if arg.startswith("-") and arg != "-":
            index += 1
            continue
        return arg
    return None
