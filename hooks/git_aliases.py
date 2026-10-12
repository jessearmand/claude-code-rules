"""
What a git alias runs, for shell_sources.resolve.

`git wipe` is harmless text until git looks up `alias.wipe` in the
repository, global or system config. A `!` alias runs a shell command; any
other alias is a git command line. Neither appears in the command the agent
typed, so the guards never saw it. This module asks git for the alias the way
git itself would, and returns what it expands to as program text the command
runs: the shell command for `!rm -rf x`, or `git clean -fdx` for `clean -fdx`.

An alias set inline with `git -c alias.x=…` wins over the config files, as in
git, and counts as typed. One read from config counts as not typed (the file
may predate the session), so guards ask about it rather than deny.

Only `git config --get` is run, with a short timeout: it reads config files and
runs no hooks or helpers. Builtin subcommands are skipped without a lookup,
because git never lets an alias shadow them.
"""

from __future__ import annotations

import functools
import shlex
import subprocess

from dangerous_git import git_invocation
from shell_parse import SimpleCommand

LOOKUP_TIMEOUT_SECONDS = 2.0

# `git --list-cmds=builtins,main` (git 2.43): commands an alias cannot replace.
BUILTINS = frozenset("""
add am annotate apply archive bisect blame branch bugreport bundle cat-file check-attr check-ignore
check-mailmap check-ref-format checkout checkout--worker checkout-index cherry cherry-pick clean
clone column commit commit-graph commit-tree config count-objects credential credential-cache
credential-cache--daemon credential-store daemon describe diagnose diff diff-files diff-index
diff-tree difftool difftool--helper fast-export fast-import fetch fetch-pack filter-branch
fmt-merge-msg for-each-ref for-each-repo format-patch fsck fsck-objects fsmonitor--daemon gc
get-tar-commit-id grep hash-object help hook http-backend http-fetch http-push imap-send index-pack
init init-db instaweb interpret-trailers log ls-files ls-remote ls-tree mailinfo mailsplit
maintenance merge merge-base merge-file merge-index merge-octopus merge-one-file merge-ours
merge-recursive merge-recursive-ours merge-recursive-theirs merge-resolve merge-subtree merge-tree
mergetool mktag mktree multi-pack-index mv name-rev notes pack-objects pack-redundant pack-refs
patch-id pickaxe prune prune-packed pull push quiltimport range-diff read-tree rebase receive-pack
reflog remote remote-ext remote-fd remote-ftp remote-ftps remote-http remote-https repack replace
request-pull rerere reset restore rev-list rev-parse revert rm send-pack sh-i18n--envsubst shell
shortlog show show-branch show-index show-ref sparse-checkout stage stash status stripspace
submodule submodule--helper subtree switch symbolic-ref tag unpack-file unpack-objects update-index
update-ref update-server-info upload-archive upload-archive--writer upload-pack var verify-commit
verify-pack verify-tag version web--browse whatchanged worktree write-tree
""".split())


@functools.lru_cache(maxsize=64)
def _configured(directory: str | None, key: str) -> str | None:
    """`git config --get KEY` as git would read it from `directory`, or None."""
    command = ["git", *(["-C", directory] if directory else []), "config", "--get", key]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=LOOKUP_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = completed.stdout.rstrip("\n")
    return value if completed.returncode == 0 and value else None


def _program_text(alias: str, value: str, args: tuple[str, ...]) -> str:
    """What `git ALIAS ARGS` runs when alias.ALIAS is `value`."""
    quoted = " ".join(shlex.quote(arg) for arg in args)
    if value.startswith("!"):
        # git runs `sh -c '<command> "$@"' <command> ARGS`: the arguments follow the command.
        return f"{value[1:].strip()} {quoted}".strip()
    return f"git {value} {quoted}".strip()


def resolve(command: SimpleCommand) -> list[tuple[str, str]]:
    """(program text, label) for the alias `command` invokes, if it is one."""
    git = git_invocation(command)
    if git is None or not git.subcommand or git.subcommand in BUILTINS:
        return []
    key = f"alias.{git.subcommand.lower()}"
    inline = [item.partition("=")[2] for item in git.config if item.partition("=")[0] == key]
    if inline:
        return [(_program_text(git.subcommand, inline[-1], git.args), f"written git alias {git.subcommand}")]
    found: list[tuple[str, str]] = []
    for directory in git.directories or (None,):
        value = _configured(directory, key)
        if value is not None:
            entry = (_program_text(git.subcommand, value, git.args), f"git alias {git.subcommand}")
            if entry not in found:
                found.append(entry)
    return found
