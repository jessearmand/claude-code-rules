"""
Git commands that destroy work, rewrite shared history, or weaken git's safety.

Rule set follows cc-safety-net's git rules (MIT, kenryu42/cc-safety-net,
src/gate/analyzer/git/rules.ts) and dcg's core.git pack: discarding
uncommitted changes, force pushes and remote deletes, force branch deletes,
dropping stashes, rewriting history, plus config that hides commands (`!`
aliases) or moves hooks (`core.hooksPath`).

The safe spellings stay allowed: `push --force-with-lease`, `branch -d`,
`clean -n`, `restore --staged`, `checkout -b`, `stash pop`, `rm --cached`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from danger_rule import (
    HIGH,
    STRICT,
    DangerRule,
    RuleContext,
    has,
    long_flag,
    operands,
    short_flag,
)
from shell_parse import SimpleCommand

# Global options that take a value (`git -C dir`, `git -c k=v`).
_GLOBAL_WITH_VALUE = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace",
                                "--super-prefix", "--config-env", "--exec-path", "--list-cmds"})


@dataclass(frozen=True)
class Git:
    subcommand: str
    args: tuple[str, ...]
    config: tuple[str, ...]  # values of -c / --config-env, lower-cased keys


def git_invocation(command: SimpleCommand) -> Git | None:
    if command.name != "git" or command.dynamic_head:
        return None
    args, config = list(command.args), []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _GLOBAL_WITH_VALUE:
            if arg in ("-c", "--config-env") and index + 1 < len(args):
                config.append(args[index + 1])
            index += 2
            continue
        if arg.startswith("-c") and len(arg) > 2:
            config.append(arg[2:])
        elif arg.startswith("--config-env="):
            config.append(arg.split("=", 1)[1])
        if arg.startswith("-"):
            index += 1
            continue
        return Git(arg, tuple(args[index + 1:]), tuple(_normalize_config(c) for c in config))
    return Git("", (), tuple(_normalize_config(c) for c in config)) if config else None


def _normalize_config(item: str) -> str:
    key, sep, value = item.partition("=")
    return f"{key.lower()}{sep}{value}"


def _git(sub: str | tuple[str, ...]):
    """Decorator: run `check` only for the named git subcommand(s)."""
    names = (sub,) if isinstance(sub, str) else sub

    def wrap(check):
        def matcher(command: SimpleCommand, ctx: RuleContext) -> str | None:
            git = git_invocation(command)
            if git is None or git.subcommand not in names:
                return None
            return check(git, ctx)
        return matcher
    return wrap


# --------------------------------------------------------------------------- #
# Discarding uncommitted work
# --------------------------------------------------------------------------- #


@_git("reset")
def _reset_hard(git: Git, ctx: RuleContext) -> str | None:
    if long_flag(git.args, "--hard", min_prefix=4):
        return "git reset --hard"
    if long_flag(git.args, "--merge", min_prefix=4):
        return "git reset --merge"
    return None


def _looks_like_path(operand: str, cwd: str) -> bool:
    if operand in (".", "..") or operand.startswith(("./", "../", "/", ":")) or operand.endswith("/"):
        return True
    if any(ch in operand for ch in "*?["):
        return True  # a pathspec glob; branch names cannot contain these
    return os.path.exists(os.path.join(cwd, operand))


# `git checkout` options that take a separate value.
_CHECKOUT_WITH_VALUE = frozenset({"--conflict", "--inter-hunk-context", "--unified", "-U",
                                  "--pathspec-from-file", "--orphan", "-b", "-B"})


@_git("checkout")
def _checkout_discard(git: Git, ctx: RuleContext) -> str | None:
    args = git.args
    if short_flag(args, "bB", with_value="U") or long_flag(args, "--orphan"):
        return None  # creates a branch: nothing is discarded
    if short_flag(args, "f", with_value="U") or long_flag(args, "--force"):
        return "git checkout --force"
    if long_flag(args, "--pathspec-from-file"):
        return "git checkout --pathspec-from-file"
    if "--" in args:
        if args[args.index("--") + 1:]:
            return "git checkout -- <path>"
        return None
    positionals = [p for p in operands(args, _CHECKOUT_WITH_VALUE) if p != "-"]
    if len(positionals) >= 2:
        return "git checkout <ref> <path>"
    if len(positionals) == 1 and _looks_like_path(positionals[0], ctx.cwd):
        return f"git checkout {positionals[0]} (a path)"
    return None


@_git("switch")
def _switch_discard(git: Git, ctx: RuleContext) -> str | None:
    if long_flag(git.args, "--discard-changes"):
        return "git switch --discard-changes"
    if short_flag(git.args, "f", with_value="cC") or long_flag(git.args, "--force"):
        return "git switch --force"
    return None


@_git("restore")
def _restore_worktree(git: Git, ctx: RuleContext) -> str | None:
    args = git.args
    if has(args, "-h", "--help", "--version"):
        return None
    pathspecs = operands(args, frozenset({"-s", "--source", "--conflict", "--inter-hunk-context",
                                          "-U", "--unified"}))
    if not pathspecs and not long_flag(args, "--pathspec-from-file"):
        return None  # nothing named: git refuses to restore anything
    staged = short_flag(args, "S", with_value="sU") or has(args, "--staged")
    worktree = short_flag(args, "W", with_value="sU") or has(args, "--worktree")
    if has(args, "--no-worktree"):
        worktree = False
    elif not staged and not has(args, "--no-staged"):
        worktree = True  # git restore's default target is the working tree
    return "git restore (working tree)" if worktree else None


@_git("clean")
def _clean_force(git: Git, ctx: RuleContext) -> str | None:
    if short_flag(git.args, "n") or long_flag(git.args, "--dry-run"):
        return None
    if short_flag(git.args, "f") or long_flag(git.args, "--force"):
        return "git clean -f"
    return None


@_git("rm")
def _rm_force(git: Git, ctx: RuleContext) -> str | None:
    if long_flag(git.args, "--cached") or short_flag(git.args, "n") or long_flag(git.args, "--dry-run"):
        return None
    if short_flag(git.args, "f") or long_flag(git.args, "--force"):
        return "git rm -f"
    return None


@_git("stash")
def _stash_drop(git: Git, ctx: RuleContext) -> str | None:
    if git.args and git.args[0] in ("drop", "clear"):
        return f"git stash {git.args[0]}"
    return None


@_git("worktree")
def _worktree_remove_force(git: Git, ctx: RuleContext) -> str | None:
    if git.args and git.args[0] == "remove" and (short_flag(git.args[1:], "f") or long_flag(git.args[1:], "--force")):
        return "git worktree remove --force"
    return None


# --------------------------------------------------------------------------- #
# Rewriting or deleting history and refs
# --------------------------------------------------------------------------- #


_SAFE_FORCE = frozenset({"--force-with-lease", "--force-if-includes"})


def _forces(args: tuple[str, ...]) -> bool:
    """`-f`, `--force` or a prefix of it; never `--force-with-lease`/`--force-if-includes`."""
    for arg in args:
        if arg == "--":
            return False
        key = arg.split("=", 1)[0]
        if key in _SAFE_FORCE:
            continue
        if key.startswith("--") and len(key) >= 6 and "--force".startswith(key):
            return True
        if arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:]:
            return True
    return False


_PUSH_WITH_VALUE = frozenset({"--repo", "-o", "--push-option", "--receive-pack", "--exec"})


def _push_refspecs(args: tuple[str, ...]) -> list[str]:
    """`git push [<repository> [<refspec>...]]`; with `--repo` every operand is a refspec."""
    words = operands(args, _PUSH_WITH_VALUE)
    names_repo = any(a == "--repo" or a.startswith("--repo=") for a in args)
    return words if names_repo else words[1:]


@_git("push")
def _push_force(git: Git, ctx: RuleContext) -> str | None:
    args = git.args
    if long_flag(args, "--mirror"):
        return "git push --mirror"
    if _forces(args):
        return "git push --force"
    if any(r.startswith("+") or ":+" in r for r in _push_refspecs(args)):
        return "git push +refspec (force)"
    return None


@_git("push")
def _push_delete(git: Git, ctx: RuleContext) -> str | None:
    if short_flag(git.args, "d") or long_flag(git.args, "--delete"):
        return "git push --delete"
    if any(r.startswith(":") and len(r) > 1 for r in _push_refspecs(git.args)):
        return "git push :ref (delete)"
    return None


@_git("branch")
def _branch_force_delete(git: Git, ctx: RuleContext) -> str | None:
    args = git.args
    if short_flag(args, "D"):
        return "git branch -D"
    deleting = short_flag(args, "d") or long_flag(args, "--delete")
    forcing = short_flag(args, "f") or long_flag(args, "--force")
    return "git branch --delete --force" if deleting and forcing else None


@_git("tag")
def _tag_delete(git: Git, ctx: RuleContext) -> str | None:
    return "git tag -d" if short_flag(git.args, "d") or long_flag(git.args, "--delete") else None


@_git("reflog")
def _reflog_delete(git: Git, ctx: RuleContext) -> str | None:
    if git.args and git.args[0] == "delete":
        return "git reflog delete"
    if git.args and git.args[0] == "expire" and any(a.startswith("--expire") and a.endswith(("now", "all")) for a in git.args):
        return "git reflog expire --expire=now"
    return None


@_git(("filter-branch", "filter-repo"))
def _filter_history(git: Git, ctx: RuleContext) -> str | None:
    return f"git {git.subcommand}"


@_git("update-ref")
def _update_ref_delete(git: Git, ctx: RuleContext) -> str | None:
    return "git update-ref -d" if short_flag(git.args, "d") or long_flag(git.args, "--delete") else None


@_git(("rebase", "merge", "cherry-pick", "revert", "am"))
def _abort_in_progress(git: Git, ctx: RuleContext) -> str | None:
    return f"git {git.subcommand} --abort" if has(git.args, "--abort") else None


# --------------------------------------------------------------------------- #
# Config that hides commands or weakens git's own checks
# --------------------------------------------------------------------------- #


def _config_assignments(command: SimpleCommand) -> list[tuple[str, str]]:
    """(key, value) pairs set by `git -c k=v` or `git config [--global] k v`."""
    git = git_invocation(command)
    if git is None:
        return []
    pairs = [(c.partition("=")[0], c.partition("=")[2]) for c in git.config]
    if git.subcommand == "config":
        positional = operands(git.args, frozenset({"-f", "--file", "--blob", "--type", "--default",
                                                   "--comment", "--value"}))
        if positional and positional[0] == "set":
            positional = positional[1:]
        if len(positional) >= 2 and "." in positional[0]:
            pairs.append((positional[0].lower(), positional[1]))
    return pairs


def _hooks_path(command: SimpleCommand, ctx: RuleContext) -> str | None:
    for key, _ in _config_assignments(command):
        if key == "core.hookspath":
            return "git core.hooksPath"
    return None


def _shell_alias(command: SimpleCommand, ctx: RuleContext) -> str | None:
    for key, value in _config_assignments(command):
        if key.startswith("alias.") and value.lstrip().startswith("!"):
            return f"git {key} = !shell"
    return None


# Keys whose value is a program git runs. Set inline with `-c`, they execute
# an arbitrary command under an innocuous-looking git invocation.
_NETWORK_SUBCOMMANDS = frozenset({"clone", "fetch", "pull", "push", "ls-remote", "submodule",
                                  "remote", "archive"})
_PROGRAM_KEYS = frozenset({"core.sshcommand", "core.fsmonitor", "core.pager", "core.editor",
                           "core.askpass", "credential.helper", "diff.external", "sequence.editor"})


def _unsafe_override(command: SimpleCommand, ctx: RuleContext) -> str | None:
    for key, value in _config_assignments(command):
        if key == "http.sslverify" and value.lower() in ("false", "0", "no", "off"):
            return "git http.sslVerify=false"
    git = git_invocation(command)
    for item in git.config if git else ():
        key = item.partition("=")[0]
        # sshCommand only runs on network operations; the rest run on most commands.
        if key == "core.sshcommand" and git.subcommand not in _NETWORK_SUBCOMMANDS:
            continue
        if key in _PROGRAM_KEYS:
            return f"git -c {key}=..."
    return None


def _no_verify(command: SimpleCommand, ctx: RuleContext) -> str | None:
    git = git_invocation(command)
    if git and git.subcommand in ("commit", "push", "merge", "am", "rebase") and has(git.args, "--no-verify"):
        return f"git {git.subcommand} --no-verify"
    return None


_DISCARD_HINT = (
    "This discards uncommitted or unpushed work with no undo. Show the user what would be "
    "lost (`git status`, `git diff`, `git stash list`) and let them decide, or use the safe "
    "form: `git stash` to set changes aside, `git restore --staged` to unstage only."
)
_HISTORY_HINT = (
    "This rewrites or deletes history other people may have. Prefer `git push "
    "--force-with-lease`, `git branch -d` (refuses unmerged work), or ask the user to run it."
)

GIT_RULES: tuple[DangerRule, ...] = (
    DangerRule("git-reset-hard", HIGH, "git reset --hard/--merge discards uncommitted changes",
               _DISCARD_HINT, _reset_hard),
    DangerRule("git-checkout-discard", HIGH, "git checkout of paths or --force discards uncommitted changes",
               _DISCARD_HINT, _checkout_discard),
    DangerRule("git-switch-discard", HIGH, "git switch --force/--discard-changes discards local changes",
               _DISCARD_HINT, _switch_discard),
    DangerRule("git-restore-worktree", HIGH, "git restore of the working tree discards uncommitted changes",
               _DISCARD_HINT, _restore_worktree),
    DangerRule("git-clean-force", HIGH, "git clean -f deletes untracked files permanently",
               "Preview with `git clean -n` and let the user run the deletion.", _clean_force),
    DangerRule("git-rm-force", HIGH, "git rm -f deletes files with uncommitted changes",
               "Use `git rm --cached` to untrack, or plain `git rm` which refuses modified files.", _rm_force),
    DangerRule("git-stash-drop", HIGH, "dropping or clearing stashes loses stashed work",
               "Show `git stash list` and let the user drop it.", _stash_drop),
    DangerRule("git-worktree-remove-force", HIGH, "git worktree remove --force deletes uncommitted work",
               "Use `git worktree remove` without --force; it refuses dirty worktrees.", _worktree_remove_force),
    DangerRule("git-push-force", HIGH, "force push overwrites remote history",
               _HISTORY_HINT, _push_force),
    DangerRule("git-push-delete", HIGH, "git push --delete removes a remote branch or tag",
               _HISTORY_HINT, _push_delete),
    DangerRule("git-branch-force-delete", HIGH, "git branch -D deletes unmerged commits",
               _HISTORY_HINT, _branch_force_delete),
    DangerRule("git-tag-delete", HIGH, "deleting tags loses release markers", _HISTORY_HINT, _tag_delete),
    DangerRule("git-reflog-delete", HIGH, "deleting reflog entries removes the last way to recover commits",
               _HISTORY_HINT, _reflog_delete),
    DangerRule("git-filter-history", HIGH, "history rewriting tools rewrite every commit", _HISTORY_HINT,
               _filter_history),
    DangerRule("git-update-ref-delete", HIGH, "git update-ref -d deletes a ref directly", _HISTORY_HINT,
               _update_ref_delete),
    DangerRule("git-hooks-path", HIGH, "core.hooksPath redirects or disables git hooks",
               "Hooks are a control point; leave hook configuration to the user.", _hooks_path),
    DangerRule("git-shell-alias", HIGH, "a `!` alias runs an arbitrary shell command under a git name",
               "Run the command directly so it can be checked.", _shell_alias),
    DangerRule("git-unsafe-override", HIGH, "disables TLS verification or runs a program through git config",
               "Do not weaken transport security or inject programs via `-c`; fix the underlying issue.",
               _unsafe_override),
    DangerRule("git-no-verify", HIGH, "--no-verify skips the repository's hooks and checks",
               "Fix what the hook reports instead of skipping it, or ask the user.", _no_verify,
               decision="ask"),
    DangerRule("git-abort", STRICT, "--abort throws away an in-progress merge/rebase resolution",
               "Confirm with the user before discarding conflict resolution work.", _abort_in_progress,
               decision="ask"),
)
