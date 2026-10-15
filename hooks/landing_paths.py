"""
Where the files of a copy or archive extraction land.

`tar xf payload.tar` in a project, or `cp -r template/. .`, writes files whose
names the command line does not show. When the destination holds protected
config (`.claude/`, `.git/hooks/`, `~/.ssh/`), config_guard needs those names:
this lists them, relative to the destination, or returns None when they cannot
be known (an archive read from stdin, too large, renamed on the way out, or a
member link pointing outside the destination).
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from collections.abc import Callable, Iterator

from shell_writes import Contents, kept_paths

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024  # listing a compressed tar reads all of it
MAX_ENTRIES = 20_000


class _Unknown(Exception):
    pass


def landing_paths(contents: Contents | None, cwd: str, descend: Callable[[str], bool],
                  made_earlier: Callable[[str], bool] = lambda name: True) -> list[str] | None:
    """Paths relative to the destination, or None when they cannot be known.

    `descend(relative_dir)`: whether a copied directory's contents matter. Trees
    are walked only where protected paths may lie, so a large copy stays cheap.
    `made_earlier(path)`: whether the line writes that file before this command;
    a missing archive it does not write makes the extraction fail, landing nothing.
    """
    if contents is None:
        return None
    try:
        if contents.archive is not None:
            return list(_archive_members(contents, cwd, made_earlier))
        return list(_copied(contents, cwd, descend))
    except (_Unknown, OSError, tarfile.TarError, zipfile.BadZipFile, ValueError, EOFError):
        return None


def _archive_members(contents: Contents, cwd: str, made_earlier: Callable[[str], bool]) -> Iterator[str]:
    archive = contents.archive or ""
    if "$" in archive or "`" in archive:
        raise _Unknown(archive)
    path = os.path.join(cwd, os.path.expanduser(archive))
    if not os.path.exists(path):
        if made_earlier(archive):
            raise _Unknown(archive)  # downloaded or written earlier on the line
        return  # nothing to extract: the command fails
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_ARCHIVE_BYTES:
        raise _Unknown(archive)
    entries = _zip_entries(path) if contents.zip_archive else _tar_entries(path)
    links: dict[str, str] = {}  # a symlink member -> where it points, both relative to the destination
    for count, (name, symlink) in enumerate(entries):
        if count >= MAX_ENTRIES:
            raise _Unknown(archive)
        member = _stripped(name, contents.strip)
        if member is None:
            continue
        # tar drops a leading `/` unless -P; unzip always does.
        member = member if contents.absolute_names and not contents.zip_archive else member.lstrip("/")
        landed = _through_links(member, links)
        yield landed
        if symlink is not None:
            # Later members under this name land where it points (`link -> .claude`,
            # then `link/settings.json`), as tar and unzip write through it.
            links[member] = _through_links(os.path.normpath(os.path.join(os.path.dirname(landed), symlink)), links)


def _through_links(member: str, links: dict[str, str]) -> str:
    """`member` after the archive's own links: `link/x` with `link -> .claude` is `.claude/x`."""
    for _ in range(16):
        parts = os.path.normpath(member).split("/")
        for length in range(len(parts), 0, -1):
            prefix = "/".join(parts[:length])
            if prefix in links:
                member = "/".join([links[prefix], *parts[length:]])
                break
        else:
            return member
    raise _Unknown(member)  # a cycle of links


def _zip_entries(path: str) -> Iterator[tuple[str, str | None]]:
    """(name, symlink target or None) per member; unzip restores Unix symlinks."""
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                target = archive.read(info).decode("utf-8", "replace")
                if _escapes(info.filename, target):
                    raise _Unknown(info.filename)
                yield info.filename, target
            else:
                yield info.filename, None


def _tar_entries(path: str) -> Iterator[tuple[str, str | None]]:
    with tarfile.open(path, mode="r:*") as archive:
        for member in archive:
            if (member.issym() or member.islnk()) and _escapes(member.name, member.linkname):
                raise _Unknown(member.name)  # later members may be written through it
            yield member.name, member.linkname if member.issym() else None


def _escapes(name: str, target: str) -> bool:
    """Whether a link member points outside the directory it is extracted into."""
    if os.path.isabs(target):
        return True
    resolved = os.path.normpath(os.path.join(os.path.dirname(name), target))
    return resolved == ".." or resolved.startswith("../")


def _stripped(name: str, strip: int) -> str | None:
    if strip <= 0:
        return name
    parts = [part for part in name.split("/") if part]
    return "/".join(parts[strip:]) if len(parts) > strip else None


def _copied(contents: Contents, cwd: str, descend: Callable[[str], bool]) -> Iterator[str]:
    """`cp a b dir/`: `a` and `b` under their own names. A directory copied
    recursively lands as `dir/NAME/...`, or as `dir/...` for `src/.`, for
    `src/` where the tool reads it so (rsync, BSD cp), and for ditto. A
    path-keeping copy (`cp --parents`, `rsync -R`) lands it under its path."""
    for source in contents.sources:
        dynamic = "$" in source or "`" in source or ":" in source.split("/", 1)[0]
        if dynamic:
            if contents.recursive:
                raise _Unknown(source)  # a computed or remote tree
            continue  # a single file under a name that cannot be known: no protected directory reached
        path = os.path.join(cwd, os.path.expanduser(source))
        if contents.keep_parents:
            names = kept_paths(source, contents.rsync_relative)
        else:
            names = [os.path.basename(os.path.normpath(path))]
        if not os.path.isdir(path):
            yield from names
            continue
        if not contents.recursive:
            continue  # cp without -r skips directories
        holds = not contents.keep_parents and (contents.copies_contents or source.endswith("/.") or (
            source.endswith("/") and contents.slash_copies_contents))
        for name in names:
            if not holds:
                yield name
            yield from _tree(path, "" if holds else name, descend, contents.follow_links)


def _tree(root: str, prefix: str, descend: Callable[[str], bool], follow_links: bool = False) -> Iterator[str]:
    info = os.stat(root)
    ancestors = frozenset({(info.st_dev, info.st_ino)})
    stack, seen = [("", root, ancestors)], 0
    while stack:
        relative, directory, ancestors = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                seen += 1
                if seen > MAX_ENTRIES:
                    raise _Unknown(root)
                child = f"{relative}/{entry.name}" if relative else entry.name
                landed = f"{prefix}/{child}" if prefix else child
                yield landed
                if entry.is_dir(follow_symlinks=follow_links) and descend(landed):
                    info = entry.stat(follow_symlinks=follow_links)
                    identity = (info.st_dev, info.st_ino)
                    if identity in ancestors:
                        raise _Unknown(entry.path)
                    stack.append((child, entry.path, ancestors | {identity}))
