"""
Builtins and assignments that change the shell's state for later commands:
`X=v` (and before special builtins), `export`/`declare` with attributes,
`alias`, `read`, `set --`, `shift`, `unset`, `printf -v`, `for` variables.
Part of shell_parse; import from there.
"""

from __future__ import annotations

import re
import shlex

import shell_ifs
from shell_expand import (
    _echoed_text,
    _ifs_values,
)
from shell_lexer import HEREDOC_OPERATORS, Word, lex
from shell_model import (
    _ASSIGNMENT,
    _FIELD,
    _NAME,
    _POSITIONALS,
    UNSET,
    Redirect,
    SimpleCommand,
    _assign,
    _at,
    _Context,
    _positionals,
    _target_name,
)
from shell_streams import (
    _input_file_text,
    _produced_text,
)
from shell_wrappers import (
    RESERVED_PREFIXES,
)


def _strip_prelude(words: list[Word], ctx: _Context) -> tuple[list[Word], tuple[str, ...]]:
    """Drop reserved words and leading assignments.

    `X=rm` on its own sets X for the rest of the line. `X=echo true` only puts
    X in `true`'s environment: it is returned, applied to what `true` runs,
    and not remembered afterwards.
    """
    index = 0
    assignments: list[str] = []
    while index < len(words):
        word = words[index]
        reserved = word.text in RESERVED_PREFIXES and not word.quoted
        time_flag = word.text == "-p" and index > 0 and words[index - 1].text == "time"
        if word.text == "function" and not word.quoted:
            # `function name { body; }`: the body is parsed like any other command list.
            index += 2
        elif reserved or time_flag:
            index += 1
        elif _ASSIGNMENT.match(word.text):
            assignments.append(word.text)
            index += 1
        else:
            break
    rest = words[index:]
    if not rest:
        for text in assignments:
            _assign(_through_nameref(text, ctx), ctx.variables, ctx.conditional)
        return rest, ()
    if rest[0].text in _SPECIAL_BUILTINS and not rest[0].quoted:
        _persist_prefix(assignments, rest, ctx)
    # `export X=rm` / `readonly X=...` / `declare X=...`: also an assignment.
    if rest and rest[0].text in ("export", "readonly", "declare", "typeset", "local"):
        for word in _declare_attributes(rest[0].text, rest[1:], ctx):
            if ctx.local_scopes and rest[0].text in ("local", "declare", "typeset"):
                # Inside a function these shadow the caller's variable until it returns.
                name = word.text.split("=", 1)[0].split("[", 1)[0].rstrip("+")
                if _NAME.fullmatch(name):
                    ctx.local_scopes[-1].add(name)
                    if "=" not in word.text:
                        ctx.variables[name] = (UNSET,)  # `local X`: a new, unset X
            if _ASSIGNMENT.match(word.text):
                _assign(_through_nameref(word.text, ctx), ctx.variables, ctx.conditional)
    return rest, tuple(assignments)


# POSIX special builtins: assignments before them stay in the shell (sh, dash,
# bash --posix). Plain bash keeps them only when `export`/`readonly` names them.
_SPECIAL_BUILTINS = frozenset({":", ".", "source", "break", "continue", "eval", "exec", "exit", "export",
                               "readonly", "return", "set", "shift", "times", "trap", "unset"})


def _persist_prefix(assignments: list[str], rest: list[Word], ctx: _Context) -> None:
    """`X=r export X` keeps X=r in every shell; `X=r :` keeps it in POSIX shells
    only, so there the value is one possibility beside the old one."""
    named = {w.text.split("=", 1)[0] for w in rest[1:]} if rest[0].text in ("export", "readonly") else set()
    for text in assignments:
        name = text.split("=", 1)[0].rstrip("+")
        certain = name in named
        if not certain and name not in ctx.variables:
            ctx.variables[name] = (UNSET,)
        _assign(_through_nameref(text, ctx), ctx.variables, ctx.conditional or not certain)


def _through_nameref(text: str, ctx: _Context) -> str:
    """`NAME=value` with NAME replaced by the variable it refers to (`declare -n`)."""
    name, equals, value = text.partition("=")
    base = name.split("[", 1)[0].rstrip("+")
    target = _target_name(base, ctx)
    return text if target == base else target + name[len(base):] + equals + value


def _expand_alias(words: list[Word], ctx: _Context, via: tuple[str, ...], depth: int,
                  indirect: bool) -> list[Word] | None:
    """`alias x=/bin/rm; x -rf v` runs `/bin/rm -rf v`. Bash expands aliases only
    with `expand_aliases` (sh, zsh and interactive shells always do), so a known
    alias is applied whatever the mode. An alias that is a plain word list
    replaces the command word; one holding operators or expansions is parsed as
    command text with the arguments appended (None: nothing left to emit)."""
    from shell_parse import _parse_into  # re-entry: parsed as a command list
    if not words or words[0].quoted or words[0].dynamic:
        return words
    name = words[0].text
    if name not in ctx.aliases or name in ctx.expanding:
        return words
    replacement = ctx.aliases[name]
    lexed = lex(replacement)
    tokens = lexed.tokens
    ctx.expanding.add(name)
    try:
        if tokens and not lexed.unterminated and not lexed.substitutions and all(isinstance(t, Word) and not t.dynamic for t in tokens):
            return _expand_alias([*tokens, *words[1:]], ctx, via, depth, indirect)
        _parse_into(" ".join([replacement, *(shlex.quote(w.text) for w in words[1:])]), ctx,
                    (*via, f"alias {name}"), depth + 1, indirect, shares_shell=True)
    finally:
        ctx.expanding.discard(name)
    return None


def _define_aliases(args: list[str], ctx: _Context) -> None:
    """`alias NAME=VALUE...` defines; `unalias NAME...` / `unalias -a` removes."""
    for arg in args:
        name, equals, value = arg.partition("=")
        if equals and name and not name.startswith("-"):
            ctx.aliases[name] = value


def _declare_attributes(builtin: str, words: list[Word], ctx: _Context) -> list[Word]:
    """Apply `declare`/`typeset`/`local` attribute flags; the words left to assign.

    `-n` makes a name refer to another variable, `-l`/`-u` change the case of
    every value, `-i` makes it a number (never a command name). Other flags
    (arrays, export, readonly, scope) do not change what a value expands to.
    A computed flag word is not known, so the analysis is incomplete.
    """
    flags = ""
    rest = list(words)
    while rest and rest[0].text[:1] in "-+" and len(rest[0].text) > 1 and "=" not in rest[0].text:
        if rest[0].dynamic:
            ctx.result.truncated = True
        if rest[0].text.startswith("-"):
            flags += rest[0].text[1:]
        rest = rest[1:]
    if builtin == "export" or not flags:
        return rest
    remaining: list[Word] = []
    for word in rest:
        name, equals, value = word.text.partition("=")
        name = name.split("[", 1)[0].rstrip("+")
        if not _NAME.fullmatch(name):
            remaining.append(word)
            continue
        if "n" in flags:
            if equals and _NAME.fullmatch(value):
                ctx.namerefs[name] = value
            else:
                # The referenced name is not known (a function body before its call
                # binds `$1`): what the reference reads is unknown.
                ctx.namerefs.pop(name, None)
                ctx.variables.pop(name, None)
            continue
        for letter in "lu":
            if letter in flags:
                ctx.case_attributes[name] = letter
        if "i" in flags:
            ctx.variables.pop(name, None)  # integer: whatever is assigned evaluates to a number
            continue
        remaining.append(word)
    return remaining


def _builtin_mutation(argv: list[str], redirects: list[Redirect], ctx: _Context,
                      environment: tuple[str, ...] = ()) -> None:
    """Builtins that set or clear a variable: `printf -v X FMT ARGS`, `unset X`,
    `set -- ARGS`, `read X <<< TEXT`. `environment` is the command's own
    prefix assignments (`IFS=, read A B`)."""
    if argv and argv[0] == "read":
        _read_builtin(argv[1:], redirects, ctx, _ifs_values(ctx, environment))
    elif argv and argv[0] in ("mapfile", "readarray"):
        _mapfile_builtin(argv[1:], redirects, ctx)
    elif argv and argv[0] == "alias":
        _define_aliases(argv[1:], ctx)
    elif argv and argv[0] == "unalias":
        if "-a" in argv[1:]:
            ctx.aliases.clear()
        for name in argv[1:]:
            ctx.aliases.pop(name, None)
    elif len(argv) >= 2 and argv[0] in ("for", "select") and _NAME.fullmatch(argv[1]):
        # `for X in a b; do ... done`: X takes each word, or keeps its value if the
        # list is empty. Without `in`, the words are the positional parameters.
        if len(argv) >= 3 and argv[2] == "in":
            items = tuple(argv[3:])
        else:
            items = _positionals(ctx)
        if items is None:
            ctx.variables.pop(argv[1], None)
        else:
            earlier = ctx.variables.get(argv[1], (UNSET,))
            ctx.variables[argv[1]] = tuple(dict.fromkeys((*items, *earlier)))
    elif len(argv) >= 4 and argv[0] == "printf" and argv[1] == "-v" and _NAME.fullmatch(argv[2]):
        text = _echoed_text(SimpleCommand(("printf", *argv[3:])))
        target = _target_name(argv[2], ctx)
        if text or "%" not in argv[3]:
            _assign(f"{target}={text}", ctx.variables, ctx.conditional)
        else:
            ctx.variables.pop(target, None)  # a format we do not model: the value is unknown
    elif argv and argv[0] == "shift":
        # `shift N`: $N+1 becomes $1; past the last parameter nothing changes.
        params = _positionals(ctx)
        count = argv[1] if len(argv) > 1 else "1"
        if not count.isdigit():
            ctx.variables.pop(_POSITIONALS, None)
        elif params is not None and int(count) <= len(params):
            _assign(f"{_POSITIONALS}={_FIELD.join(params[int(count):])}", ctx.variables, ctx.conditional)
    elif argv and argv[0] == "set":
        params = _set_positionals(argv[1:])
        if params is not None:
            _assign(f"{_POSITIONALS}={_FIELD.join(params)}", ctx.variables, ctx.conditional)
    elif argv and argv[0] == "unset":
        for name in argv[1:]:
            if _NAME.fullmatch(name):
                _assign(f"{name}={UNSET}", ctx.variables, ctx.conditional)


_READ_OPTIONS_WITH_VALUE = frozenset("adinNptu")


def _read_options(args: list[str]) -> tuple[dict[str, str], list[str]]:
    """`read`'s options (letter -> value, "" for flags) and its variable names.
    Clusters take their value from the rest of the word or the next one: `-rn1`."""
    options: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return options, args[index + 1:]
        if not arg.startswith("-") or arg == "-":
            return options, args[index:]
        for offset, letter in enumerate(arg[1:], start=2):
            if letter in _READ_OPTIONS_WITH_VALUE:
                rest = arg[offset:]
                if not rest:
                    index += 1
                    rest = _at(args, index) or ""
                options[letter] = rest
                break
            options[letter] = ""
        index += 1
    return options, []


def _read_builtin(args: list[str], redirects: list[Redirect], ctx: _Context, ifs_values: tuple[str, ...]) -> None:
    """`read A B <<< "x y z"` sets A=x, B="y z", splitting by each value IFS may
    hold. Input that is not literal text (a pipe, the terminal) makes the
    variables unknown."""
    options, names = _read_options(args)
    delimiter = options["d"][:1] or "\0" if "d" in options else "\n"
    if "a" in options:
        names.insert(0, options["a"])
    names = [_target_name(n, ctx) for n in names if _NAME.fullmatch(n)] or ["REPLY"]
    count_option = "N" if "N" in options else "n" if "n" in options else None
    if count_option and not options[count_option].isdigit():
        for name in names:
            ctx.variables.pop(name, None)  # `read -n "$K"`: how much is read is not known
        return
    text = _stdin_text(redirects, ctx)
    array = "a" in options
    if text is None:
        for name in names:
            ctx.variables.pop(name, None)
        return
    if count_option == "N":
        # `-N K`: exactly K characters, delimiter or not, and no field splitting.
        line = text[:int(options["N"])]
        if "r" not in options:
            line = re.sub(r"\\(.)", r"\1", line)
        for position, name in enumerate(names):
            _assign(f"{name}={line if position == 0 else ''}", ctx.variables, ctx.conditional)
        return
    line = _read_line(text, delimiter, raw="r" in options)
    if count_option == "n":
        line = line[:int(options["n"])]  # `-n K`: at most K characters of the line
    for reading, ifs in enumerate(ifs_values):
        # Another possible IFS gives another possible value; earlier readings stay possible.
        keep = ctx.conditional or reading > 0
        if array:
            # `read -a X <<< "a b"`: X[0]=a, X[1]=b (and $X is X[0]).
            name = names[0]
            if reading == 0:
                for key in [k for k in ctx.variables if k == name or k.startswith(name + "[")]:
                    ctx.variables.pop(key)
            for position, item in enumerate(shell_ifs.split(line, ifs)):
                _assign(f"{name}[{position}]={item}", ctx.variables, keep)
            continue
        fields = shell_ifs.split(line, ifs, len(names))
        for position, name in enumerate(names):
            _assign(f"{name}={fields[position] if position < len(fields) else ''}", ctx.variables, keep)


def _stdin_text(redirects: list[Redirect], ctx: _Context) -> str | None:
    """What a builtin reads on stdin, when it is literal text: a here-string or
    heredoc, what `< <(producer)` prints, or a file written earlier on the line
    or on disk (`< file`). None otherwise (a pipe, the terminal)."""
    text = next((r.target + "\n" if r.op == "<<<" else r.body for r in redirects
                 if (r.op == "<<<" and not r.dynamic) or (r.op in HEREDOC_OPERATORS and r.body is not None)),
                None)
    if text is None:
        text = next((t for r in redirects if r.op == "<" and not r.dynamic
                     and (t := _input_text(r.target, ctx)) is not None), None)
    return text


def _input_text(target: str, ctx: _Context) -> str | None:
    """`< <(echo rm)`: what the producer prints; `< file`: the file's text."""
    produced = _produced_text(target, ctx)
    return produced if produced is not None else _input_file_text(target, ctx)


_MAPFILE_WITH_VALUE = frozenset("dnOsucC")


def _mapfile_builtin(args: list[str], redirects: list[Redirect], ctx: _Context) -> None:
    """`mapfile -t X < <(printf 'a\\nb')` sets X[0]=a, X[1]=b. Without -O the array
    is cleared first; -O N starts at N and keeps the rest; -s skips lines,
    -n caps their number, -t drops each line's delimiter."""
    options: dict[str, str] = {}
    index = 0
    while index < len(args) and args[index].startswith("-") and args[index] != "-":
        arg = args[index]
        if arg == "--":
            index += 1
            break
        for offset, letter in enumerate(arg[1:], start=2):
            if letter in _MAPFILE_WITH_VALUE:
                value = arg[offset:]
                if not value:
                    index += 1
                    value = args[index] if index < len(args) else ""
                options[letter] = value
                break
            options[letter] = ""
        index += 1
    name = args[index] if index < len(args) else "MAPFILE"
    if not _NAME.fullmatch(name):
        return
    numbers = {k: options[k] for k in "nOs" if k in options}
    text = _stdin_text(redirects, ctx) if "u" not in options else None
    if text is None or not all(v.isdigit() for v in numbers.values()):
        # Input or counts not known: so are the elements.
        for key in [k for k in ctx.variables if k == name or k.startswith(name + "[")]:
            ctx.variables.pop(key)
        return
    delimiter = options["d"][:1] or "\0" if "d" in options else "\n"
    lines = [part + delimiter for part in text.split(delimiter)]
    lines[-1] = lines[-1][:-1]
    lines = [line for line in lines if line][int(numbers.get("s", 0)):]
    if "n" in numbers and int(numbers["n"]) > 0:
        lines = lines[:int(numbers["n"])]
    if "t" in options:
        lines = [line[:-1] if line.endswith(delimiter) else line for line in lines]
    origin = int(numbers.get("O", 0))
    if "O" not in numbers and not ctx.conditional:
        for key in [k for k in ctx.variables if k == name or k.startswith(name + "[")]:
            ctx.variables.pop(key)
    for position, line in enumerate(lines, start=origin):
        _assign(f"{name}[{position}]={line}", ctx.variables, ctx.conditional)


def _read_line(text: str, delimiter: str, raw: bool) -> str:
    """The line `read` takes from `text`: up to the first delimiter. Without -r
    a backslash escapes the next character, an escaped delimiter does not end
    the line, and backslash-newline joins the next line (`r\\<newline>m` is rm)."""
    if raw:
        return text.split(delimiter, 1)[0]
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            if text[index + 1] != "\n":
                out.append(text[index + 1])
            index += 2
            continue
        if char == delimiter:
            break
        out.append(char)
        index += 1
    return "".join(out)


def _set_positionals(args: list[str]) -> list[str] | None:
    """The new positional parameters of `set [options] [--] args`, or None if unchanged."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return args[index + 1:]
        if arg in ("-o", "+o"):
            index += 2
        elif arg.startswith(("-", "+")) and len(arg) > 1:
            index += 1
        else:
            return args[index:]
    return None


def _own_assign(text: str, ctx: _Context, caller: list[dict[str, tuple[str, ...]]]) -> None:
    """Put `text` in the current command's environment only, copying the shell's
    variables the first time so the caller's stay untouched."""
    if not caller:
        caller.append(ctx.variables)
        ctx.variables = dict(ctx.variables)
    _assign(text, ctx.variables)
