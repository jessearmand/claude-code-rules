"""
Field splitting by IFS, as bash does it for unquoted expansions and `read`.

`IFS=,; X=rm,-rf,/; $X` runs `rm -rf /`, and `IFS=x read A B <<< rxm` sets
A=r, B=m. Whitespace characters in IFS collapse and are trimmed at both ends;
any other IFS character ends a field on its own, taking the IFS whitespace
around it along. An empty IFS does not split at all.
"""

from __future__ import annotations

DEFAULT = " \t\n"
_WHITESPACE = " \t\n"


def split(text: str, ifs: str = DEFAULT, max_fields: int | None = None) -> list[str]:
    """The fields of `text`. With `max_fields` (as `read` with that many names),
    the last field keeps the rest of the text, less trailing IFS whitespace."""
    if ifs == "":
        return [text] if text else []
    space = "".join(c for c in ifs if c in _WHITESPACE)
    text = text.strip(space) if space else text
    fields: list[str] = []
    start = index = 0
    while index < len(text):
        if max_fields is not None and len(fields) == max_fields - 1:
            return [*fields, _last_field(text[start:], ifs, space)]
        if text[index] in ifs:
            fields.append(text[start:index])
            start = index = _delimiter_end(text, index, ifs, space)
        else:
            index += 1
    if start < len(text):
        fields.append(text[start:])
    return fields


def _delimiter_end(text: str, index: int, ifs: str, space: str) -> int:
    """Where the delimiter starting at `index` ends: IFS whitespace around at
    most one other IFS character."""
    other_seen = text[index] not in space
    index += 1
    while index < len(text) and text[index] in ifs:
        if text[index] not in space:
            if other_seen:
                break  # `a,,b`: the second comma starts an empty field's end
            other_seen = True
        index += 1
    return index


def _last_field(rest: str, ifs: str, space: str) -> str:
    """What `read`'s last name gets: the rest of the line, less trailing IFS
    whitespace; a rest that is one field and a closing delimiter (`b,`) is
    just that field."""
    rest = rest.rstrip(space) if space else rest
    index = 0
    while index < len(rest) and rest[index] not in ifs:
        index += 1
    if index < len(rest) and _delimiter_end(rest, index, ifs, space) == len(rest):
        return rest[:index]
    return rest
