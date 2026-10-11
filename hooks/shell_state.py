"""
Builtins and assignments that change the shell's state for later commands:
`X=v` (and before special builtins), `export`/`declare` with attributes,
`alias`, `read`, `set --`, `shift`, `unset`, `printf -v`, `for` variables.
Part of shell_parse; import from there.
"""

from __future__ import annotations

import glob
import os
import re
import shlex
from collections.abc import Callable

import shell_ifs
from shell_expand import (
    _GLOB_CHARS,
    _echoed_text,
    _ifs_values,
)
from shell_lexer import HEREDOC_OPERATORS, Word, lex
from shell_model import (
    _ASSIGNMENT,
    _ELEMENT,
    _FIELD,
    _NAME,
    _POSITIONALS,
    ALIAS,
    CASE,
    DESCRIPTOR,
    FUNCTION,
    HASHED,
    NAMEREF,
    SHELL_OPTION,
    UNSET,
    Redirect,
    SimpleCommand,
    _assign,
    _assign_setting,
    _at,
    _Context,
    _positionals,
    _target_name,
    _variable_keys,
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
        if ctx.local_scopes and rest[0].text in ("local", "declare", "typeset"):
            _declare_locals(rest[1:], ctx)
        for word in _declare_attributes(rest[0].text, rest[1:], ctx):
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


def _declare_locals(words: list[Word], ctx: _Context) -> None:
    """`local X`, `local -u X`, `declare -n R=Y` inside a function: each name
    shadows the caller's variable (elements and attributes included) until the
    function returns; without a value it starts out unset."""
    for word in words:
        if word.text.startswith(("-", "+")):
            continue
        name = word.text.split("=", 1)[0].split("[", 1)[0].rstrip("+")
        if not _NAME.fullmatch(name):
            continue
        ctx.local_scopes[-1].add(name)
        if "=" not in word.text:
            for key in _variable_keys(name, ctx.variables):
                ctx.variables[key] = ("",) if key.startswith(CASE) else (UNSET,)
            ctx.variables[name] = (UNSET,)  # `local X`: a new, unset X


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
    replacements = ctx.variables.get(ALIAS + name, (UNSET,))
    if replacements == (UNSET,) or name in ctx.expanding:
        return words
    if len(replacements) > 1:
        # `alias x=rm; false && alias x=echo`: each definition may be the one in force.
        _expand_each_alias(name, replacements, words, ctx, via, depth, indirect)
        return None
    replacement = replacements[0]
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


def _expand_each_alias(name: str, replacements: tuple[str, ...], words: list[Word], ctx: _Context,
                       via: tuple[str, ...], depth: int, indirect: bool) -> None:
    """Parse the command once per definition the alias may have (the plain
    command when it may have none), each as a run that may not happen."""
    from shell_parse import _parse_into  # re-entry: parsed as a command list
    arguments = [shlex.quote(w.text) for w in words[1:]]
    conditional = ctx.conditional
    ctx.expanding.add(name)
    ctx.conditional = True
    try:
        for replacement in replacements:
            head = shlex.quote(name) if replacement == UNSET else replacement
            _parse_into(" ".join([head, *arguments]), ctx, (*via, f"alias {name}"), depth + 1, indirect,
                        shares_shell=True)
    finally:
        ctx.expanding.discard(name)
        ctx.conditional = conditional


def _define_aliases(args: list[str], ctx: _Context) -> None:
    """`alias NAME=VALUE...` defines; `unalias NAME...` / `unalias -a` removes."""
    for arg in args:
        name, equals, value = arg.partition("=")
        if equals and name and not name.startswith("-"):
            _assign_setting(f"{ALIAS}{name}", value, UNSET, ctx.variables, ctx.conditional)


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
                _assign_setting(f"{NAMEREF}{name}", value, UNSET, ctx.variables, ctx.conditional)
            else:
                # The referenced name is not known (a function body before its call
                # binds `$1`): what the reference reads is unknown.
                _assign(f"{NAMEREF}{name}={UNSET}", ctx.variables, ctx.conditional)
                ctx.variables.pop(name, None)
            continue
        for letter in "lu":
            if letter in flags:
                _assign_setting(f"{CASE}{name}", letter, "", ctx.variables, ctx.conditional)
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
        names = [k[len(ALIAS):] for k in ctx.variables if k.startswith(ALIAS)] if "-a" in argv[1:] else argv[1:]
        for name in names:
            _assign(f"{ALIAS}{name}={UNSET}", ctx.variables, ctx.conditional)
    elif len(argv) >= 2 and argv[0] in ("for", "select") and _NAME.fullmatch(argv[1]):
        # `for X in a b; do ... done`: X takes each word, or keeps its value if the
        # list is empty (or the loop may not run). `select` sets X to "" for an
        # invalid choice. Without `in`, the words are the positional parameters.
        if len(argv) >= 3 and argv[2] == "in":
            items = tuple(argv[3:])
        else:
            items = _positionals(ctx)
        if items is None:
            ctx.variables.pop(argv[1], None)
        else:
            earlier = ctx.variables.get(argv[1], (UNSET,)) if not items or ctx.conditional else ()
            invalid = ("",) if argv[0] == "select" else ()
            ctx.variables[argv[1]] = tuple(dict.fromkeys((*items, *invalid, *earlier)))
    elif len(argv) >= 4 and argv[0] == "printf" and argv[1] == "-v" and (
            _NAME.fullmatch(argv[2]) or _ELEMENT.fullmatch(argv[2])):
        # `printf -v X ...` or an array element, `printf -v 'X[0]' ...`.
        text = _echoed_text(SimpleCommand(("printf", *argv[3:])))
        target = _target_name(argv[2], ctx) if _NAME.fullmatch(argv[2]) else argv[2]
        if text or "%" not in argv[3]:
            _assign(f"{target}={text}", ctx.variables, ctx.conditional)
        else:
            # A format we do not model: the value is unknown, and so is what runs with it.
            ctx.variables.pop(target, None)
            ctx.result.truncated = True
    elif argv and argv[0] == "getopts" and len(argv) >= 3:
        _getopts_builtin(argv[1], argv[2], argv[3:], ctx)
    elif argv == ["exec"]:
        _exec_redirects(redirects, ctx)
    elif argv and argv[0] == "shopt":
        _shopt_builtin(argv[1:], ctx)
    elif argv and argv[0] == "hash":
        _hash_builtin(argv[1:], ctx)
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
    elif argv and argv[0] == "unset" and "-f" in argv[1:]:
        for name in (a for a in argv[1:] if not a.startswith("-")):
            _assign(f"{FUNCTION}{name}={UNSET}", ctx.variables, ctx.conditional)  # `unset -f f`
    elif argv and argv[0] == "unset":
        for name in argv[1:]:
            if _ELEMENT.fullmatch(name):
                _assign(f"{name}={UNSET}", ctx.variables, ctx.conditional)  # `unset 'X[0]'`: that element
            elif _NAME.fullmatch(name):
                # `unset X`: the variable, every element if it is an array, and its case attribute.
                for key in [k for k in ctx.variables if k in (name, CASE + name) or k.startswith(name + "[")]:
                    _assign(f"{key}={UNSET}", ctx.variables, ctx.conditional)
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
    # A name, or an array element (`read 'X[0]'`).
    names = [_target_name(n, ctx) if _NAME.fullmatch(n) else n for n in names
             if _NAME.fullmatch(n) or _ELEMENT.fullmatch(n)] or ["REPLY"]
    timeout = options.get("t")
    if timeout is not None and re.fullmatch(r"0*\.?0*", timeout) and timeout.strip("."):
        return  # `read -t 0`: only tests for input; nothing is read or assigned
    saved = ctx.conditional
    if timeout is not None and not re.fullmatch(r"\d*\.?\d+|\d+\.", timeout):
        ctx.conditional = True  # a computed timeout may be 0: the variables may keep their values
    try:
        _each_stream(_exec_descriptor(redirects, ctx, _descriptor_option(options)), ctx,
                     lambda: _read_assign(args, options, names, delimiter, redirects, ctx, ifs_values))
    finally:
        ctx.conditional = saved


def _read_assign(args: list[str], options: dict[str, str], names: list[str], delimiter: str,
                 redirects: list[Redirect], ctx: _Context, ifs_values: tuple[str, ...]) -> None:
    """The assignments `read` makes once its options are known."""
    count_option = "N" if "N" in options else "n" if "n" in options else None
    if count_option and not options[count_option].isdigit():
        for name in names:
            ctx.variables.pop(name, None)  # `read -n "$K"`: how much is read is not known
        return
    text, persisted = _stdin_text(redirects, ctx, _descriptor_option(options))
    array = "a" in options
    if text is None:
        for name in names:
            ctx.variables.pop(name, None)
        return
    # `-n K`: at most K characters of the line; `-N K`: exactly K, delimiter or not.
    limit = int(options[count_option]) if count_option else None
    line, consumed = _read_line(text, delimiter, raw="r" in options, limit=limit, whole=count_option == "N")
    if persisted is not None:
        # Reading from a descriptor `exec` opened takes what was read off it.
        _assign(f"{DESCRIPTOR}{persisted}={text[consumed:]}", ctx.variables, ctx.conditional)
    if count_option == "N":
        # No field splitting: the first name takes it all.
        for position, name in enumerate(names):
            _assign(f"{name}={line if position == 0 else ''}", ctx.variables, ctx.conditional)
        return
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


# Where getopts is inside a cluster such as `-rf`: "OPTIND:offset".
_GETOPTS_POSITION = "\0getopts"


def _getopts_builtin(optstring: str, name: str, args: list[str], ctx: _Context) -> None:
    """`getopts OPTSTRING NAME [ARGS]`: the next option from ARGS (or the
    positional parameters) into NAME, its argument into OPTARG, and OPTIND
    past it, as bash does (`set -- -r; getopts r X` sets X=r). Unknown
    parameters or OPTIND make NAME unknown."""
    params = tuple(args) if args else _positionals(ctx)
    optind_values = ctx.variables.get("OPTIND", ("1",))
    if params is None or not _NAME.fullmatch(name) or len(optind_values) != 1 or not optind_values[0].isdigit():
        for key in (name, "OPTARG", "OPTIND"):
            ctx.variables.pop(key, None)
        return
    optind = int(optind_values[0])
    stored = ctx.variables.get(_GETOPTS_POSITION, ("",))[0].split(":")
    offset = int(stored[1]) if len(stored) == 2 and stored[0] == str(optind) else 1
    silent = optstring.startswith(":")
    keep = ctx.conditional
    index = optind - 1
    if index >= len(params) or not params[index].startswith("-") or params[index] == "-" or params[index] == "--":
        # No more options: NAME=?, OPTARG unset, OPTIND past `--` or at most $#+1.
        end = optind + 1 if index < len(params) and params[index] == "--" else min(optind, len(params) + 1)
        _assign(f"{name}=?", ctx.variables, keep)
        _assign(f"OPTARG={UNSET}", ctx.variables, keep)
        _assign(f"OPTIND={end}", ctx.variables, keep)
        return
    word = params[index]
    letter = word[offset] if offset < len(word) else "?"
    known = letter != ":" and letter in optstring
    takes_argument = known and optstring[optstring.index(letter) + 1:optstring.index(letter) + 2] == ":"
    result, optarg, next_optind, next_offset = letter if known else "?", None, optind, offset + 1
    if takes_argument:
        next_offset = 1
        if offset + 1 < len(word):
            optarg, next_optind = word[offset + 1:], optind + 1
        elif index + 1 < len(params):
            optarg, next_optind = params[index + 1], optind + 2
        else:
            result, optarg, next_optind = (":" if silent else "?"), (letter if silent else None), optind + 1
    elif not known and silent:
        optarg = letter
    if not takes_argument and next_offset >= len(word):
        next_optind, next_offset = optind + 1, 1
    _assign(f"{name}={result}", ctx.variables, keep)
    _assign(f"OPTARG={UNSET if optarg is None else optarg}", ctx.variables, keep)
    _assign(f"OPTIND={next_optind}", ctx.variables, keep)
    _assign(f"{_GETOPTS_POSITION}={next_optind}:{next_offset}", ctx.variables, keep)


def _shopt_builtin(args: list[str], ctx: _Context) -> None:
    """`shopt -s NAME...` / `-u`: options that change what later commands do
    (`xpg_echo`, `lastpipe`), kept in the variable table."""
    flags = [a for a in args if a.startswith("-")]
    setting = "on" if "-s" in flags else "off" if "-u" in flags else None
    if setting is None or "-o" in flags:
        return
    for name in (a for a in args if not a.startswith("-")):
        _assign(f"{SHELL_OPTION}{name}={setting}", ctx.variables, ctx.conditional)


def _hash_builtin(args: list[str], ctx: _Context) -> None:
    """`hash -p PATH NAME...` makes NAME run PATH; `-d NAME` forgets it, `-r` all.
    Kept in the variable table, so a mapping in a branch that may not run
    leaves the earlier one possible too."""
    if "-r" in args:
        for key in [k for k in ctx.variables if k.startswith(HASHED)]:
            _assign(f"{key}={UNSET}", ctx.variables, ctx.conditional)
    if "-d" in args:
        for name in args[args.index("-d") + 1:]:
            _assign(f"{HASHED}{name}={UNSET}", ctx.variables, ctx.conditional)
    if "-p" in args and args.index("-p") + 1 < len(args):
        path = args[args.index("-p") + 1]
        if _GLOB_CHARS.search(path) and ctx.cwd is not None:
            # `hash -p /bin/r[m] x`: the path is globbed like any argument.
            matches = sorted(glob.glob(os.path.join(ctx.cwd, os.path.expanduser(path))))
            path = matches[0] if matches else path
        for name in args[args.index("-p") + 2:]:
            if not name.startswith("-"):
                _assign_setting(f"{HASHED}{name}", path, UNSET, ctx.variables, ctx.conditional)


_INPUT_OPERATORS = frozenset({"<", "<<<", "<&", "<>"}) | HEREDOC_OPERATORS


def _descriptor_option(options: dict[str, str]) -> int | None:
    """The descriptor `read -u N` / `mapfile -u N` reads; 0 by default, None if computed."""
    if "u" not in options:
        return 0
    return int(options["u"]) if options["u"].isdigit() else None


def _stdin_text(redirects: list[Redirect], ctx: _Context, fd: int | None = 0) -> tuple[str | None, int | None]:
    """What a builtin reads from descriptor `fd`, when it is literal text, and
    which descriptor `exec` opened it came from (None if from this command's own
    redirects or a pipe). Sources: a here-string or heredoc, `< <(producer)`,
    a file (written earlier on the line or on disk), `<&N`, the pipe feeding
    stdin, or a descriptor `exec 3<<< text` opened earlier.

    Redirections apply in order, so the last one to `fd` is what is read:
    `read X <<< echo <<< rm` reads rm."""
    if fd is None:
        return None, None
    opened = _exec_descriptor(redirects, ctx, fd)
    if opened is not None:
        return _opened_text(opened, ctx)
    inputs = [r for r in redirects if r.op in _INPUT_OPERATORS and (r.fd if r.fd is not None else 0) == fd]
    if inputs:
        return _redirect_text(inputs[-1], ctx), None
    return ctx.piped_text if fd == 0 else None, None  # `printf r | read X`


def _exec_descriptor(redirects: list[Redirect], ctx: _Context, fd: int | None) -> int | None:
    """The descriptor `exec` opened that reading `fd` takes from, if it is one:
    `read -u3`, `read <&3`; None for this command's own input or a pipe."""
    if fd is None:
        return None
    inputs = [r for r in redirects if r.op in _INPUT_OPERATORS and (r.fd if r.fd is not None else 0) == fd]
    if inputs:
        last = inputs[-1]
        return _shared_descriptor(int(last.target), ctx) if last.op == "<&" and last.target.isdigit() else None
    if fd == 0 and ctx.piped_text is not None:
        return None
    return _shared_descriptor(fd, ctx)


def _each_stream(fd: int | None, ctx: _Context, read: Callable[[], None]) -> None:
    """Run `read` against each stream descriptor `fd` may hold
    (`false && exec 3<<< x` leaves the earlier one possible), each as a read
    that may not happen; afterwards the descriptor holds what each left."""
    key = f"{DESCRIPTOR}{fd}"
    streams = ctx.variables.get(key, ()) if fd is not None else ()
    if len(streams) < 2:
        read()
        return
    conditional, remainders = ctx.conditional, []
    ctx.conditional = True
    try:
        for stream in streams:
            ctx.variables[key] = (stream,)
            read()
            remainders.append(ctx.variables.get(key, (UNSET,))[0])
    finally:
        ctx.conditional = conditional
    ctx.variables[key] = tuple(dict.fromkeys(remainders))


# A descriptor `exec 4<&3` made holds "\0dup:3": it shares fd 3's stream and
# position, so reading either advances both.
_DUPLICATE = "\0dup:"


def _shared_descriptor(fd: int, ctx: _Context) -> int:
    """The descriptor whose stream `fd` reads: itself, or the one it duplicates."""
    for _ in range(8):
        values = ctx.variables.get(f"{DESCRIPTOR}{fd}", ())
        if len(values) != 1 or not values[0].startswith(_DUPLICATE):
            break
        fd = int(values[0][len(_DUPLICATE):])
    return fd


def _opened_text(fd: int, ctx: _Context) -> tuple[str | None, int | None]:
    fd = _shared_descriptor(fd, ctx)
    values = ctx.variables.get(f"{DESCRIPTOR}{fd}")
    if not values or len(values) > 1 or values[0] == UNSET or values[0].startswith(_DUPLICATE):
        # Closed, or several streams may be open (`false && exec 3<<< x`): unknown.
        return None, None
    return values[0], fd


def _redirect_text(redirect: Redirect, ctx: _Context) -> str | None:
    """The text an input redirection supplies, when it is known."""
    if redirect.op == "<<<":
        return None if redirect.dynamic else redirect.target + "\n"
    if redirect.op in HEREDOC_OPERATORS:
        return redirect.body
    if redirect.op in ("<", "<>"):
        return None if redirect.dynamic else _input_text(redirect.target, ctx)
    return None


def _exec_redirects(redirects: list[Redirect], ctx: _Context) -> None:
    """`exec 3<<< text` / `exec < file` / `exec 3<&-`: with no command, exec's
    redirections change this shell's descriptors for every later command."""
    for redirect in redirects:
        if redirect.op not in _INPUT_OPERATORS and redirect.op != ">&":
            continue
        fd = redirect.fd if redirect.fd is not None else 0
        if redirect.op == ">&" and redirect.target != "-":
            continue
        if redirect.op == "<&" and redirect.target.isdigit():
            source = _shared_descriptor(int(redirect.target), ctx)
            if source == fd:
                continue  # `exec 3<&3`: unchanged
            value = f"{_DUPLICATE}{source}"
        else:
            # `exec 3<&-` closes; anything else opens new text.
            text = None if redirect.target == "-" and redirect.op in ("<&", ">&") else _redirect_text(redirect, ctx)
            value = UNSET if text is None else text
        _detach_duplicates(fd, ctx)
        _assign(f"{DESCRIPTOR}{fd}={value}", ctx.variables, ctx.conditional)


def _detach_duplicates(fd: int, ctx: _Context) -> None:
    """Before `fd` is closed or reopened, its duplicates keep the stream they
    share: the first takes it over and the rest now duplicate that one."""
    marker = f"{_DUPLICATE}{fd}"
    duplicates = [key for key, values in ctx.variables.items() if key.startswith(DESCRIPTOR) and values == (marker,)]
    if not duplicates:
        return
    owner = duplicates[0]
    ctx.variables[owner] = ctx.variables.get(f"{DESCRIPTOR}{fd}", (UNSET,))
    for key in duplicates[1:]:
        ctx.variables[key] = (f"{_DUPLICATE}{owner[len(DESCRIPTOR):]}",)


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
    ctx.mapfile_records = []
    _each_stream(_exec_descriptor(redirects, ctx, _descriptor_option(options)), ctx,
                 lambda: _mapfile_assign(name, options, redirects, ctx))


def _mapfile_assign(name: str, options: dict[str, str], redirects: list[Redirect], ctx: _Context) -> None:
    """The elements `mapfile` sets once its options are known."""
    numbers = {k: options[k] for k in "nOs" if k in options}
    text, persisted = _stdin_text(redirects, ctx, _descriptor_option(options))
    if text is not None and persisted is not None:
        _assign(f"{DESCRIPTOR}{persisted}=", ctx.variables, ctx.conditional)  # read to the end
    if text is None or not all(v.isdigit() for v in numbers.values()):
        # Input or counts not known: so are the elements.
        ctx.mapfile_records = None
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
    if ctx.mapfile_records is not None:
        ctx.mapfile_records.append(list(enumerate(lines, start=origin)))


def _read_line(text: str, delimiter: str, raw: bool, limit: int | None = None,
               whole: bool = False) -> tuple[str, int]:
    """The line `read` takes from `text` and how many characters of input it
    consumes: up to and including the first delimiter. Without -r a backslash
    escapes the next character, an escaped delimiter does not end the line,
    and backslash-newline joins the next line (`r\\<newline>m` is rm).
    `limit` stops after that many characters of the line (`-n K`, an escape
    counts once); `whole` also reads past delimiters (`-N K`)."""
    out: list[str] = []
    index = 0
    while index < len(text) and (limit is None or len(out) < limit):
        char = text[index]
        if not raw and char == "\\" and index + 1 < len(text):
            if text[index + 1] != "\n":
                out.append(text[index + 1])
            index += 2
            continue
        index += 1
        if char == delimiter and not whole:
            break
        out.append(char)
    return "".join(out), index


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
