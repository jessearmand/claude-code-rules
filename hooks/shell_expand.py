"""
Word expansion as bash does it, for values the parser knows: brace and
pathname expansion, parameters (`$X`, `${X:-d}`, `${X%.bak}`, `${!X}`),
word splitting by IFS, literal `$(echo ...)`/`$(printf ...)` output and
`$(mktemp)`. What stays unknown stays as written and marks the word dynamic.
Part of shell_parse; import from there.
"""

from __future__ import annotations

import glob
import itertools
import os
import re
from collections.abc import Callable
from fnmatch import fnmatchcase

import shell_arith
import shell_ifs
import shell_params
import shell_printf
from shell_lexer import Word, nested_substitutions
from shell_model import (
    _ARRAY,
    _FIELD,
    _NAME,
    _UNRESOLVED,
    SHELL_OPTION,
    UNSET,
    ParseResult,
    SimpleCommand,
    _array_items,
    _assign,
    _Context,
    _positionals,
    _target_name,
)

_ONE_CHAR = re.compile(r".")

# `${NAME<op>word}` forms whose result is NAME's value or `word`.
_BRACED = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\])?)(:?[-=+?])(.*)", re.DOTALL)

_MAX_BRACE_WORDS = 64

_GLOB_CHARS = re.compile(r"[*?[]")


def _arithmetic_expressions(text: str) -> list[str]:
    """The arithmetic in a word: each `$((expr))`, or the whole of `((expr))`."""
    found: list[str] = []
    if text.startswith("((") and text.endswith("))"):
        found.append(text[2:-2])
    start = text.find("$((")
    while start >= 0:
        depth, index = 2, start + 3
        while index < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[index], 0)
            index += 1
        found.append(text[start + 3:max(index - 2, start + 3)])
        start = text.find("$((", index)
    return found


_UNMODELLED_TRANSFORM = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?:\[[^]]*\])?@[A-Za-z]\}")
_SUBSCRIPTED = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]")


def _element_value(name: str, subscript: str, ctx: _Context) -> str | None:
    """`X[N]` (negative N counts back from the last element), `X[@]`/`X[*]`
    (every element, space-joined) or `X[#]` (their count), when X is known."""
    items = _array_items(name, ctx.variables)
    if items is None:
        return None
    if subscript in ("@", "*"):
        return (_star_separator(ctx) if subscript == "*" else " ").join(v for _, v in items)
    if subscript == "#":
        return str(len(items))
    if not re.fullmatch(r"-?\d+", subscript):
        return None
    index = int(subscript)
    if index < 0:
        index = (items[-1][0] + 1 + index) if items else -1
    return next((v for i, v in items if i == index), None) if index >= 0 else None


def array_words(token: Word, ctx: _Context) -> list[Word] | None:
    """`"${X[@]}"`: one word per element; `"${X[*]}"` one word joining them;
    unquoted, each element is split further. None if not that shape or X unknown."""
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\[([@*])\]\}", token.text)
    items = _array_items(_target_name(match.group(1), ctx), ctx.variables) if match else None
    if items is None:
        return None
    values = [v for _, v in items]
    if token.quoted:
        return [Word(_star_separator(ctx).join(values))] if match.group(2) == "*" else [Word(v) for v in values]
    return [Word(part) for v in values for part in shell_ifs.split(v, _ifs_values(ctx)[0])]


def _arithmetic_index(expression: str, value: Callable[[str, bool], str | None]) -> int | None:
    """An array subscript's value: bash integer arithmetic over known variables
    (`i`, `$i`, `n-1`, `1&1`); None if any part is not known."""
    return shell_arith.evaluate(expression, lambda name: value(name, False))


_ARITHMETIC_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _arithmetic_substitutions(expression: str, ctx: _Context, seen: frozenset[str] = frozenset()) -> list[str]:
    """Command substitutions bash runs while evaluating `expression`: a variable
    named in arithmetic is itself evaluated as arithmetic, so `X='a[$(cmd)]';
    $((X))` runs cmd. Every known value of every variable named is followed."""
    found: list[str] = []
    for name in dict.fromkeys(_ARITHMETIC_NAME.findall(expression)):
        if name in seen or name not in ctx.variables:
            continue
        for value in ctx.variables[name]:
            if value == UNSET:
                continue
            found += nested_substitutions(value)
            if len(seen) < 8:
                found += _arithmetic_substitutions(value, ctx, seen | {name})
    return list(dict.fromkeys(found))


def _at_word_pieces(token: Word, ctx: _Context) -> list[Word] | None:
    """`r"$@"x` with $1=a $2=b is two words, `ra` and `bx`: each parameter is its own
    word, the surrounding text joins the first and last. None if not that shape."""
    marker = next((m for m in ("${@}", "$@") if m in token.text), None)
    params = _positionals(ctx) if marker else None
    if params is None or token.text.count(marker) != 1:
        return None
    prefix, suffix = token.text.split(marker)
    if not params:
        texts = [prefix + suffix] if prefix or suffix else []
    elif len(params) == 1:
        texts = [prefix + params[0] + suffix]
    else:
        texts = [prefix + params[0], *params[1:-1], params[-1] + suffix]
    return [Word(text, dynamic=bool(_UNRESOLVED.search(text)), quoted=token.quoted, splits=token.splits)
            for text in texts]


def _glob_words(words: list[Word], ctx: _Context) -> list[Word]:
    """Pathname expansion of unquoted words, in the directory the command runs in:
    `/bin/r[m]` runs /bin/rm, and `rm -* x` next to a file named `-rf` is
    `rm -rf x`. A pattern matching nothing stays literal, as in bash."""
    if ctx.cwd is None:
        return words
    expanded: list[Word] = []
    for index, word in enumerate(words):
        expanded += _glob_word(word, ctx, command_word=index == 0)
    return expanded


def _glob_word(word: Word, ctx: _Context, command_word: bool = False) -> list[Word]:
    cwd = ctx.cwd or "/"
    if word.quoted or word.dynamic or not _GLOB_CHARS.search(word.text):
        return [word]
    if ctx.created_unknown:
        ctx.result.truncated = True  # a file made earlier under a computed name may match
    try:
        on_disk = glob.glob(os.path.expanduser(word.text), root_dir=cwd)
    except (OSError, ValueError):
        return [word]
    matches = sorted({*on_disk, *_created_matches(word.text, cwd, ctx)})
    if not matches:
        return [word]
    if len(matches) > _MAX_BRACE_WORDS:
        # Too many to list: keep the pattern, plus any match that reads as an option,
        # and in command position the first match, which is what runs. Incomplete.
        ctx.result.truncated = True
        head = [Word(matches[0])] if command_word else []
        return [*head, word, *(Word(m) for m in matches if m.startswith("-"))]
    return [Word(match) for match in matches]


def _created_matches(pattern: str, cwd: str, ctx: _Context) -> list[str]:
    """Paths created earlier on the line that `pattern` matches, segment by segment
    as pathname expansion does (a leading `.` must be matched explicitly)."""
    expanded = os.path.expanduser(pattern)
    absolute = os.path.isabs(expanded)
    wanted = os.path.normpath(os.path.join(cwd, expanded)).split(os.sep)
    found: list[str] = []
    for path in ctx.created_paths:
        parts = path.split(os.sep)
        if len(parts) == len(wanted) and all(
                fnmatchcase(part, want) and (not part.startswith(".") or want.startswith("."))
                for part, want in zip(parts, wanted, strict=True)):
            relative = os.path.relpath(path, cwd)
            # Written as the pattern was: `./x*` gives `./x1`.
            found.append(path if absolute else "./" + relative if expanded.startswith("./") else relative)
    return found


def _brace_expand(word: Word, ctx: _Context) -> list[Word]:
    """Brace expansion, which bash does first: `{rm,-rf,~/x}` is three words,
    `a{b,c}d` is `abd acd`. Only unquoted braces and commas count.

    Past _MAX_BRACE_WORDS, every alternative's text becomes a word of its own
    (nothing is dropped, so `rm {x,...,-rf} v` still shows `-rf`) and the
    parse is marked truncated."""
    if not word.braces:
        return [word]
    texts = _expand_brace_text(word.text, frozenset(word.braces))
    if len(texts) > _MAX_BRACE_WORDS:
        ctx.result.truncated = True
        texts = _brace_fragments(word.text, word.braces)
    if len(texts) == 1:
        return [word]
    return [Word(text, dynamic=word.dynamic and bool(_UNRESOLVED.search(text)), quoted=word.quoted, splits=word.splits,
                 substitutions=word.substitutions if index == 0 else [])
            for index, text in enumerate(texts)]


def _brace_fragments(text: str, braces: tuple[int, ...]) -> list[str]:
    """The text between unquoted braces and commas, each piece once."""
    pieces, start = [], 0
    for position in (*braces, len(text)):
        pieces.append(text[start:position])
        start = position + 1
    return list(dict.fromkeys(piece for piece in pieces if piece))


def _expand_brace_text(text: str, active: frozenset[int]) -> list[str]:
    for start in sorted(p for p in active if text[p] == "{"):
        depth, commas, end = 0, [], None
        for index in sorted(p for p in active if p >= start):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
            elif depth == 1:
                commas.append(index)
        if end is None or not commas:
            continue  # `{}`, `{x}` and unbalanced braces stay literal
        prefix, suffix = text[:start], text[end + 1:]
        expanded: list[str] = []
        bounds = [start, *commas, end]
        for left, right in itertools.pairwise(bounds):
            piece = text[left + 1:right]
            moved = ({p for p in active if p < start}
                     | {p - left - 1 + len(prefix) for p in active if left < p < right}
                     | {p - end - 1 + len(prefix) + len(piece) for p in active if p > end})
            expanded += _expand_brace_text(prefix + piece + suffix, frozenset(moved))
            if len(expanded) > _MAX_BRACE_WORDS:
                break  # the caller falls back to fragments
        return expanded
    return [text]


def _compound_word(token: Word, ctx: _Context, choice: dict[str, str]) -> Word:
    """`X=(r "a b" $Y)` as one assignment word: `X=` then the expanded elements,
    marked as an array, which `_assign` stores as X[0], X[1]..."""
    items: list[str] = []
    for element in token.elements or []:
        for piece in _brace_expand(element, ctx):
            items += [w.text for w in _split_unquoted(piece, _expand_word(piece, ctx, choice), ctx)]
    text = token.text + _ARRAY + _FIELD.join(items)
    return Word(text, dynamic=bool(_UNRESOLVED.search(text)), substitutions=token.substitutions)


def _split_unquoted(original: Word, expanded: Word, ctx: _Context) -> list[Word]:
    """Word splitting: an unquoted expansion holding IFS characters becomes several
    words (`X='rm -rf ~'; $X` runs rm with two arguments; so does `IFS=,; X=rm,-rf,~`).
    Quoted `"$X"` stays one."""
    if not original.splits or not original.dynamic or expanded.text == original.text:
        return [expanded]
    splits = [tuple(shell_ifs.split(expanded.text, ifs)) for ifs in _ifs_values(ctx)]
    if len(set(splits)) > 1:
        ctx.result.truncated = True  # IFS may hold several values that split this differently
    parts = splits[0]
    if len(parts) <= 1:
        # `IFS=,; X=rm,; $X` runs `rm`: the delimiters go even when one word remains.
        return [expanded] if not parts or parts[0] == expanded.text else [
            Word(parts[0], dynamic=bool(_UNRESOLVED.search(parts[0])), substitutions=expanded.substitutions)]
    return [Word(part, dynamic=bool(_UNRESOLVED.search(part)), substitutions=expanded.substitutions if i == 0 else [])
            for i, part in enumerate(parts)]


def _star_separator(ctx: _Context) -> str:
    """What `"$*"` puts between parameters: IFS's first character."""
    return _ifs_values(ctx)[0][:1]


def _ifs_values(ctx: _Context, environment: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Every value IFS may hold: `IFS=, read ...` sets it for one command; unset means the default."""
    for text in reversed(environment):
        if text.startswith("IFS="):
            return (text[4:],)
    values = ctx.variables.get("IFS", (UNSET,))
    return tuple(dict.fromkeys(shell_ifs.DEFAULT if value == UNSET else value for value in values))


def _expand_word(word: Word, ctx: _Context, choice: dict[str, str] | None = None) -> Word:
    """Substitute parameters whose value is known at this point in the line.

    `P=$HOME/.claude/settings.json; echo x > "$P"` writes a known path; so
    does `X=rm; $X -rf ~`. A word that still holds an expansion afterwards
    (an unset variable, `${X:-d}`, `$(...)`) stays dynamic. `choice` picks the
    value of a variable that may hold several.
    """
    if not word.dynamic:
        return word

    def value(name: str, at_start: bool) -> str | None:
        if name.startswith(_WRITTEN_FILE):
            return _written_text(name[len(_WRITTEN_FILE):], ctx)
        if name.isdigit() or name in ("#", "@", "*"):
            params = _positionals(ctx)
            if params is None or name == "0":
                return None
            if name in ("@", "*"):
                # Inside a longer word: `"$@"m` with $1=r is rm.
                return (_star_separator(ctx) if name == "*" else " ").join(params)
            return str(len(params)) if name == "#" else (params[int(name) - 1] if int(name) <= len(params) else "")
        name = _target_name(name, ctx)
        if choice and name in choice:
            return None if choice[name] == UNSET else choice[name]
        if name in ctx.variables:
            current = ctx.variables[name][0]
            return None if current == UNSET else current
        subscripted = _SUBSCRIPTED.fullmatch(name)
        if subscripted:
            return _element_value(_target_name(subscripted.group(1), ctx), subscripted.group(2), ctx)
        if "[" in name:
            return None
        if name == "HOME":
            return os.path.expanduser("~")
        if name == "PWD":
            # `$PWD/x` is `./x`: relative paths are checked in every directory the
            # command might run in, which a single expanded value would lose.
            if at_start:
                return "."
            if ctx.cwd is not None and len(set(ctx.cwd_options)) <= 1:
                return ctx.cwd
        return None

    def assign(name: str, text: str, certain: bool) -> None:
        # `${X:=word}` assigns word when X is unset (or empty). If X is not known
        # it may already be set, so both stay possible: word, or unknown.
        if not certain and name not in ctx.variables:
            ctx.variables[name] = (UNSET,)
        _assign(f"{name}={text}", ctx.variables, ctx.conditional or not certain)
        if choice is not None and name in choice:
            choice[name] = text

    text = _expand_text(word.text, value, at_start=True, assign=assign)
    if _UNMODELLED_TRANSFORM.search(text):
        # `${X@P}`, `${X@A}`...: bash computes a value this parser cannot.
        ctx.result.truncated = True
    return Word(text, dynamic=bool(_UNRESOLVED.search(text)), quoted=word.quoted, splits=word.splits,
                heredoc_body=word.heredoc_body, substitutions=word.substitutions)


# Records `${NAME:=word}`: (name, value, whether the assignment certainly happens).
_Assigner = Callable[[str, str, bool], None]


def _expand_text(text: str, value: Callable[[str, bool], str | None], at_start: bool,
                 assign: _Assigner | None = None) -> str:
    """Replace `$NAME`/`${NAME}` whose value is known, and resolve `${NAME:-word}`,
    `${NAME:=word}` and `${NAME:+word}` to the value they select. When NAME is not
    known it may be unset, so the default or alternate `word` is the candidate:
    `> "${OUT:-$HOME/.claude/settings.json}"` writes settings.json. Anything else
    (`${#X}`, `${X/a/b}`, unknown names) stays as written. `assign` records
    what `${NAME:=word}` assigns, which later expansions then see."""
    out: list[str] = []
    index = 0
    while index < len(text):
        literal = _literal_substitution(text, index, value(SHELL_OPTION + "xpg_echo", False) == "on", value)
        if literal is not None:
            # `$(printf git)` / `` `echo git` ``: the output of a literal echo is known.
            output, index = literal
            out.append(output)
            continue
        if text.startswith("${", index):
            end = _closing_brace(text, index + 2)
            if end is None:
                out.append(text[index:])
                break
            out.append(_expand_braced(text[index + 2:end], text[index:end + 1], value,
                                      at_start and index == 0, assign))
            index = end + 1
            continue
        name = _NAME.match(text, index + 1) if text[index] == "$" else None
        if name is None and text[index] == "$" and index + 1 < len(text) and text[index + 1] in "0123456789#@*":
            name = _ONE_CHAR.match(text, index + 1)  # `$1`, `$#`, `$@`
        if name:
            found = value(name.group(0), at_start and index == 0)
            out.append(text[index:name.end()] if found is None else found)
            index = name.end()
            continue
        out.append(text[index])
        index += 1
    return "".join(out)


# `value("\0written:PATH")` is the text this line wrote to PATH, if it did.
_WRITTEN_FILE = "\0written:"


def _written_text(path: str, ctx: _Context) -> str | None:
    """The text this line wrote to `path` (`printf r > f; printf m >> f`), when the
    directory is known; files on disk are not read here."""
    if ctx.cwd is None or len(set(ctx.cwd_options)) > 1:
        return None
    entry = ctx.written_files.get(os.path.normpath(os.path.join(ctx.cwd, os.path.expanduser(path))))
    return entry.text if entry is not None and entry.replaces else None


def _literal_substitution(text: str, index: int, xpg_echo: bool = False,
                          value: Callable[[str, bool], str | None] | None = None) -> tuple[str, int] | None:
    """(output, end) for a command substitution at `index` whose body only echoes
    literal text; trailing newlines are dropped, as bash does. None otherwise."""
    from shell_parse import parse  # re-entry: parsed as a command list
    if text.startswith("$(", index) and not text.startswith("$((", index):
        depth, end = 1, index + 2
        while end < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[end], 0)
            end += 1
        if depth:
            return None
        body = text[index + 2:end - 1]
    elif text[index] == "`":
        close = text.find("`", index + 1)
        if close < 0:
            return None
        body, end = text[index + 1:close], close + 1
    else:
        return None
    producer = parse(body, "/")
    temporary = _mktemp_path(producer)
    if temporary is not None:
        return temporary, end
    if not producer.commands or producer.unparseable or any(
            c.name not in ("echo", "printf", "cat") or c.dynamic_head or c.via or c.redirects
            or any("$" in a or "`" in a for a in c.argv) for c in producer.commands):
        return None
    printed = [_printed(c, xpg_echo, value) for c in producer.commands]
    if any(p is None for p in printed):
        return None
    return "".join(p for p in printed if p is not None).rstrip("\n"), end


def _printed(command: SimpleCommand, xpg_echo: bool, value: Callable[[str, bool], str | None] | None) -> str | None:
    """What one producer command prints: echo/printf text, or `cat FILE...` of
    files written earlier on the line (`printf r > f; "$(cat f)"`)."""
    if command.name != "cat":
        return shell_printf.printed_text(command.name, command.args, xpg_echo)
    if value is None or not command.args or any(a.startswith("-") for a in command.args):
        return None
    texts = [value(_WRITTEN_FILE + path, False) for path in command.args]
    return None if any(t is None for t in texts) else "".join(t for t in texts if t is not None)


def _mktemp_path(producer: ParseResult) -> str | None:
    """`$(mktemp)`, `$(mktemp -d -p DIR)`, `$(mktemp -t name.XXXX)`: a new path in
    a known directory. Its name is random, so the template stands for it."""
    if len(producer.commands) != 1 or producer.commands[0].name != "mktemp" or producer.commands[0].via:
        return None
    args = list(producer.commands[0].args)
    directory, template, under_tmpdir, index = None, None, False, 0
    while index < len(args):
        arg = args[index]
        if arg in ("-p", "--tmpdir") and index + 1 < len(args):
            directory, index = args[index + 1], index + 1
        elif arg.startswith("--tmpdir="):
            directory = arg.split("=", 1)[1]
        elif arg == "-t":
            under_tmpdir = True
        elif arg.startswith("--suffix=") or arg in ("-d", "--directory", "-u", "--dry-run", "-q", "--quiet"):
            pass
        elif arg.startswith("-"):
            return None
        else:
            template = arg
        index += 1
    if any("$" in part for part in (directory or "", template or "")):
        return None
    temporary_root = os.environ.get("TMPDIR") or "/tmp"
    if template is None:
        return os.path.join(directory or temporary_root, "tmp.XXXXXXXXXX")
    if directory is None and not under_tmpdir:
        return template  # `mktemp out.XXXX`: relative to the current directory
    return os.path.join(directory or temporary_root, template)


def _closing_brace(text: str, start: int) -> int | None:
    depth = 1
    for index in range(start, len(text)):
        if text.startswith("${", index):
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _expand_braced(body: str, original: str, value: Callable[[str, bool], str | None],
                   at_start: bool, assign: _Assigner | None = None) -> str:
    if _NAME.fullmatch(body) or body.isdigit() or body in ("@", "*"):
        found = value(body, at_start)
        return original if found is None else found
    element = _SUBSCRIPTED.fullmatch(body)
    if element:
        # `${X[0]}`, `${X[-1]}`, `${X[i+1]}`, `${X[@]}`: elements are stored as X[0],
        # X[1]...; X[0] is also plain $X. A subscript is arithmetic.
        name, subscript = element.groups()
        if subscript not in ("@", "*"):
            index = _arithmetic_index(subscript, value)
            if index is None:
                return original
            subscript = str(index)
        found = value(f"{name}[{subscript}]", False)
        return original if found is None else found
    counted = _SUBSCRIPTED.fullmatch(body[1:]) if body.startswith("#") else None
    if counted and counted.group(2) in ("@", "*"):
        found = value(f"{counted.group(1)}[#]", False)  # `${#X[@]}`: how many elements
        return original if found is None else found
    if body.startswith("!") and _NAME.fullmatch(body[1:]):
        # `${!X}`: the value of the variable whose name X holds.
        target = value(body[1:], False)
        found = value(target, False) if target and _NAME.fullmatch(target) else None
        return original if found is None else found
    if body.startswith("#") and _NAME.fullmatch(body[1:]):
        found = value(body[1:], False)
        return original if found is None else str(len(found))  # ${#X}
    match = _BRACED.fullmatch(body)
    if not match:
        # Substring, pattern removal/replacement, case: computable once X is known.
        name = _NAME.match(body)
        current = value(name.group(0), False) if name else None
        if current is None:
            return original
        result = shell_params.apply(current, _expand_text(body[name.end():], value, False, assign),
                                    extglob=value(SHELL_OPTION + "extglob", False) == "on")
        return original if result is None else result
    name, op, word = match.groups()
    current = value(name, False)
    if op.endswith("?"):
        return original if current is None else current
    alternative = _expand_text(word, value, at_start, assign)
    set_and_used = current is not None and (current != "" or not op.startswith(":"))
    if op.endswith("+"):
        # Unknown NAME may be set: the alternate word is the value to check.
        return alternative if current is None or set_and_used else ""
    if op.endswith("=") and not set_and_used and assign is not None:
        assign(name, alternative, current is not None)
    return current if set_and_used else alternative


def echoed_text(command: SimpleCommand) -> str:
    """The text an `echo`/`printf` prints, when it is literal ("" if not modelled)."""
    return _echoed_text(command)


def _echoed_text(command: SimpleCommand) -> str:
    """What an echo/printf prints, trailing newlines dropped ("" if not modelled)."""
    printed = command_output(command)
    return printed.rstrip("\n") if printed is not None else ""


def command_output(command: SimpleCommand) -> str | None:
    """The exact bytes an echo/printf writes, honouring `shopt -s xpg_echo`."""
    return shell_printf.printed_text(command.name, command.args, command.option_on("xpg_echo"))
