#!/usr/bin/env python3
"""
Claude Code Hook: Config Guard
==============================
PreToolUse hook for Write, Edit, MultiEdit, NotebookEdit and Bash. Stops the
agent from changing its own configuration: settings and permissions, hook
scripts, MCP servers, other harnesses' config (Codex, omp, pi, opencode), and
places that make code run again later (shell rc files, git hooks, launchd,
systemd, cron).

Without it, every other guard is one `sed -i` away from being switched off.

Bash writes are found structurally (shell_parse + shell_writes): redirects,
tee, sed/perl -i, cp/mv/install/rsync/ln destinations, rm/unlink/shred,
chmod/chown, dd of=, curl -o / wget -O. Removing, moving or re-permissioning
a directory that contains a protected path counts too (`rm -r ~/.claude`).
Writes found only inside a script the command runs are asked about, not
denied: the agent did not type them in this call.

Paths are checked as written and after resolving `~`, `$HOME`, the working
directory and symlinks, so a symlinked `~/.claude/hooks` protects the
directory it points to.

Configuration (environment variables, set in the hook command):
  CONFIG_GUARD_DISABLE=1     turn the hook off entirely
  CONFIG_GUARD_ASK=1         prompt instead of denying
  CONFIG_GUARD_LOG_DIR=<dir> default ~/.claude/hooks-logs
  CONFIG_GUARD_LIVE_DIRS=<dir>:<dir>
      extra directories that hold running guard code (e.g. the omp
      extension checkout). Writes there are asked about, not denied.
  CLAUDE_CONFIG_DIR          honoured as an extra protected root

Live guard sources. The guards usually run from a git checkout that
~/.claude/hooks links to, or (omp) straight from this repo. Editing that
checkout changes the running guards, but it is also where they are
developed, so a write there is asked about rather than denied. The live
directories are this script's own directory, the target of ~/.claude/hooks,
and CONFIG_GUARD_LIVE_DIRS.

Wire-up (~/.claude/settings.json):
  { "matcher": "Edit|MultiEdit|Write|NotebookEdit|Bash",
    "hooks": [{ "type": "command",
                "command": "python3 ~/.claude/hooks/config_guard.py",
                "timeout": 10 }] }
"""

from __future__ import annotations

import glob
import itertools
import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import shell_sources  # noqa: E402
import unknown_target
from config_guard_rules import (  # noqa: E402
    CONFIG_RULES,
    PROJECT_PROTECTED_ROOTS,
    PROTECTED_ROOTS,
    ConfigRule,
)
from hook_protocol import (  # noqa: E402
    WRITE_TOOLS,
    Decision,
    action_verb,
    emit,
    env_flag,
    for_permission_mode,
    log_event,
    paths_from,
    read_input,
    tool_input,
    truncate,
)
from landing_paths import landing_paths
from shell_parse import SimpleCommand, parse  # noqa: E402
from shell_writes import (  # noqa: E402
    DELETE_TREE,
    INTO_DIR,
    MOVE,
    PERMISSIONS,
    WRITE,
    PathWrite,
    written_paths,
)

HOOK_NAME = "config-guard"
HANDLED_TOOLS = WRITE_TOOLS | {"Bash"}
_HOME_VARIABLE = re.compile(r"^(?:\$HOME|\$\{HOME\})(?=/|$)")
_GLOB = re.compile(r"[*?\[]")
# Actions that take everything below a directory with them.
_TREE_ACTIONS = frozenset({DELETE_TREE, MOVE, PERMISSIONS})
CONFIG_DIR_RULE = ConfigRule(
    "agent-config-dir",
    "high",
    re.compile(r"(?!)"),
    "copying into an agent configuration directory can add or replace its settings, hooks or plugins",
)
UNKNOWN_TARGET_RULE = ConfigRule(
    "unknown-target",
    "high",
    re.compile(r"(?!)"),
    "the path is computed at run time and could be an agent's configuration",
)
LIVE_SOURCE_RULE = ConfigRule(
    "guard-sources",
    "live",
    re.compile(r"(?!)"),
    "this directory holds the guard code that is running now",
)


@dataclass(frozen=True)
class Finding:
    rule: ConfigRule
    target: str
    action: str
    indirect: bool = False
    # Why the target is only possibly protected (an unknown directory, an
    # unknown file name). Such a finding asks instead of denying.
    uncertain: str | None = None


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #


def _expand(path: str, cwd: str) -> str:
    path = _HOME_VARIABLE.sub(lambda _: os.path.expanduser("~"), path)
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.normpath(path)


_MAX_GLOB_MATCHES = 10_000


def glob_overflows(path: str, cwd: str) -> bool:
    """True when a glob matches more files than candidate_paths inspects."""
    expanded = _expand(path, cwd)
    return bool(_GLOB.search(expanded)) and \
        sum(1 for _ in itertools.islice(glob.iglob(expanded), _MAX_GLOB_MATCHES + 1)) > _MAX_GLOB_MATCHES


def candidate_paths(path: str, cwd: str) -> list[str]:
    """The path as written, expanded, symlink-resolved, and glob-expanded."""
    expanded = _expand(path, cwd)
    found = [path, expanded]
    if _GLOB.search(expanded):
        found += itertools.islice(glob.iglob(expanded), _MAX_GLOB_MATCHES)
    resolved = [os.path.realpath(p) for p in found[1:]]
    unique: list[str] = []
    for value in (*found, *resolved):
        normalized = value.replace(os.sep, "/")
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def _protected_roots(cwd: str) -> list[str]:
    roots = [_expand(root, cwd) for root in PROTECTED_ROOTS]
    roots += [os.path.join(cwd, root) for root in PROJECT_PROTECTED_ROOTS]
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        roots.append(_expand(config_dir, cwd))
    resolved = [os.path.realpath(root) for root in roots]
    return list(dict.fromkeys(os.path.normpath(r) for r in (*roots, *resolved)))


def _is_inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _is_inside_config_dir(path: str) -> bool:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    return bool(config_dir) and _is_inside(path, os.path.realpath(os.path.expanduser(config_dir)))


def live_source_dirs() -> list[str]:
    """Directories holding the guard code that runs for this harness."""
    dirs = [os.path.dirname(os.path.realpath(__file__)), os.path.realpath(os.path.expanduser("~/.claude/hooks"))]
    extra = os.environ.get("CONFIG_GUARD_LIVE_DIRS", "")
    dirs += [os.path.realpath(os.path.expanduser(d)) for d in extra.split(os.pathsep) if d.strip()]
    return list(dict.fromkeys(dirs))


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def check_path(path: str, cwd: str, action: str = "write") -> ConfigRule | None:
    """The rule that protects `path` from `action`, if any."""
    candidates = candidate_paths(path, cwd)
    for candidate in candidates:
        for rule in CONFIG_RULES:
            if rule.pattern.search(candidate):
                return rule
        if _is_inside_config_dir(candidate):
            return CONFIG_RULES[0]
    if action in _TREE_ACTIONS:
        rule = _check_tree(candidates, cwd)
        if rule:
            return rule
    live = live_source_dirs()
    for candidate in candidates:
        if candidate.startswith("/") and any(
            _is_inside(candidate, root) or (action in _TREE_ACTIONS and _is_inside(root, candidate))
            for root in live
        ):
            return LIVE_SOURCE_RULE
    return None


def _check_tree(candidates: list[str], cwd: str) -> ConfigRule | None:
    """A delete/move/chmod of a directory that holds a protected root."""
    for candidate in candidates:
        if not candidate.startswith("/"):
            continue
        prefix = candidate.rstrip("/") + "/"
        for root in _protected_roots(cwd):
            if root == candidate or root.startswith(prefix):
                return next(
                    (rule for rule in CONFIG_RULES if rule.pattern.search(root + "/x")),
                    CONFIG_RULES[0],
                )
    return None


def _check_from_unknown_dir(path: str, cwd: str, action: str) -> ConfigRule | None:
    """A relative write after `cd "$DIR"`: protected if it would be from any protected root."""
    if os.path.isabs(os.path.expanduser(path)):
        return check_path(path, cwd, action)
    for root in _protected_roots(cwd):
        rule = check_path(os.path.join(root, path), cwd, action)
        if rule is not None:
            return rule
    return None


_INTO_PROTECTED = "copied into a protected directory under names that cannot be known"
_HOLDS_PROTECTED = "copied or extracted into a directory holding protected config, with contents that cannot be listed"


def _check_into_dir(write: PathWrite, path: str, cwd: str,
                    made_earlier: frozenset[str] = frozenset()) -> tuple[ConfigRule | None, str | None]:
    """A copy or extraction into a directory: (rule, why it is uncertain).

    Into a protected or live directory, the names do not matter. Into a
    directory that holds one (`tar xf x.tar` in a project or in ~), the files
    it lands are listed and each is checked; when they cannot be listed, ask.
    """
    roots = _protected_roots(cwd)
    for candidate in candidate_paths(path, cwd):
        if not candidate.startswith("/"):
            continue
        if any(_is_inside(candidate, root) for root in roots):
            return CONFIG_DIR_RULE, _INTO_PROTECTED
        if any(_is_inside(candidate, root) for root in live_source_dirs()):
            return LIVE_SOURCE_RULE, _INTO_PROTECTED
        held = [root for root in roots if _is_inside(root, candidate)]
        if not held:
            continue
        rule = _check_landed(write, candidate, held, cwd, made_earlier)
        if rule is not None:
            return rule
    return None, None


def _check_landed(write: PathWrite, destination: str, held: list[str], cwd: str,
                  made_earlier: frozenset[str]) -> tuple[ConfigRule | None, str | None] | None:
    def descend(relative: str) -> bool:
        # Only directories on the way to, or inside, a protected root matter.
        target = os.path.join(destination, relative)
        return any(_is_inside(root, target) or _is_inside(target, root) for root in held)

    landed = landing_paths(write.contents, cwd, descend, lambda name: os.path.basename(name) in made_earlier)
    if landed is None:
        return CONFIG_DIR_RULE, _HOLDS_PROTECTED
    for relative in landed:
        target = os.path.normpath(os.path.join(destination, relative))
        rule = check_path(target, cwd, WRITE)
        if rule is not None:
            return rule, None  # a named file the command writes: as certain as `cp x target`
    return None


_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _inherited(path: str) -> str:
    """`$NAME` the command line does not set comes from the environment the agent
    runs in, which this hook shares: `> "$CLAUDE_CONFIG_DIR/settings.json"`.
    Resolved here, for the path check only, so values never reach a message."""
    def value(match: re.Match[str]) -> str:
        found = os.environ.get(match.group(1) or match.group(2))
        return match.group(0) if found is None else found
    return _VARIABLE.sub(value, path) if "$" in path else path


def _through_links(path: str, links: dict[str, str]) -> str:
    """`path` after following links created earlier in the same command line."""
    for _ in range(8):
        for link, target in links.items():
            if path == link or path.startswith(link + "/"):
                path = target + path[len(link):]
                break
        else:
            return path
    return path


def _finding_for(write: PathWrite, directory: str | None, cwd: str, simple: SimpleCommand,
                 links: dict[str, str] | None = None, made_earlier: frozenset[str] = frozenset()) -> Finding | None:
    """Check one write as if its command runs in `directory` (None: unknown)."""
    uncertain = None
    path = _inherited(write.path)
    if directory is None:
        rule = _check_from_unknown_dir(path, cwd, write.action)
        uncertain = "relative to a directory set by a computed `cd`"
    elif write.action == INTO_DIR:
        rule, uncertain = _check_into_dir(write, path, directory, made_earlier)
    else:
        rule = check_path(path, directory, write.action)
        if rule is None and links:
            absolute = os.path.normpath(os.path.join(directory, os.path.expanduser(path)))
            linked = _through_links(absolute, links)
            if linked != absolute:
                rule = check_path(linked, directory, write.action)
        if rule is None and glob_overflows(path, directory):
            rule, uncertain = CONFIG_DIR_RULE, "a glob matching more files than can be checked"
    return Finding(rule, write.path, write.action, simple.indirect, uncertain) if rule else None


def check_command(command: str, cwd: str) -> Finding | None:
    """The first protected write in `command`.

    Each relative path is resolved in every directory its command might run
    in: after a `cd`, and also as if that `cd` failed or sat in a branch
    that did not run (`cd a || cd b`).
    """
    parsed = parse(command, cwd, shell_sources.resolve)
    softer: Finding | None = None
    made: set[str] = set()  # names of files written earlier on the line (`curl -o a.tgz URL`)
    for simple in parsed.commands:
        writes = written_paths(simple)
        made_earlier = frozenset(made)
        made.update(os.path.basename(w.path.rstrip("/")) for w in writes)
        for write in writes:
            for directory in simple.possible_cwds:
                finding = _finding_for(write, directory, cwd, simple, parsed.links, made_earlier)
                if finding is None:
                    continue
                if not (finding.indirect or finding.uncertain):
                    return finding
                softer = softer or finding
            if softer is None and unknown_target.is_unknown(_inherited(write.path)):
                # `cp x "$OUT"`: where it lands is not known, so what the command does decides.
                why = unknown_target.reason(simple, write, parsed)
                if why:
                    softer = Finding(UNKNOWN_TARGET_RULE, write.path, write.action, simple.indirect, why)
    return softer


def evaluate(tool_name: str, inputs: dict, cwd: str) -> Finding | None:
    if tool_name == "Bash":
        command = inputs.get("command", "")
        return check_command(command, cwd) if isinstance(command, str) and command else None
    for path in paths_from(inputs):
        rule = check_path(path, cwd)
        if rule:
            return Finding(rule, path, "write")
    return None


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def build_reason(finding: Finding, tool_name: str, decision: str) -> str:
    verb = "BLOCKED" if decision == "deny" else "Confirm"
    source = (" (inside a script, or an interpreter program's string, this command runs)" if finding.indirect
              else f" ({finding.uncertain})" if finding.uncertain else "")
    return "\n".join(
        [
            f"🧷 {verb} by config-guard [{finding.rule.rule_id}] — cannot "
            f"{action_verb(tool_name)} ({finding.action}){source}: {finding.rule.reason}.",
            f"    target: {truncate(finding.target)}",
            "",
            "An agent must not change its own configuration, hooks or persistence "
            "locations: that is how guards get switched off. Do not route around this "
            "(another tool, a copy-and-rename, a script). Tell the user exactly what to "
            "change and let them make the edit, or have them run it with `! <command>`.",
        ]
    )


def run(data: dict) -> Decision | None:
    if env_flag("CONFIG_GUARD_DISABLE"):
        return None
    tool_name = data.get("tool_name", "")
    if tool_name not in HANDLED_TOOLS:
        return None

    cwd = os.path.normpath(data.get("cwd") or os.getcwd())
    finding = evaluate(tool_name, tool_input(data), cwd)
    if finding is None:
        return None

    asks = (env_flag("CONFIG_GUARD_ASK") or finding.indirect or finding.uncertain is not None
            or finding.rule is LIVE_SOURCE_RULE)
    decision = "ask" if asks else "deny"
    log_event(
        HOOK_NAME,
        {
            "outcome": decision,
            "rule": finding.rule.rule_id,
            "level": finding.rule.level,
            "action": finding.action,
            "indirect": finding.indirect,
            "tool": tool_name,
            "target": finding.target[:200],
            "session_id": data.get("session_id"),
            "cwd": data.get("cwd"),
            "permission_mode": data.get("permission_mode"),
        },
        log_dir_env=("CONFIG_GUARD_LOG_DIR",),
    )
    return Decision(
        decision,
        build_reason(finding, tool_name, decision),
        f"config-guard [{finding.rule.rule_id}]: {finding.rule.reason}",
        HOOK_NAME,
    )


def main() -> None:
    data = read_input(HOOK_NAME)
    emit(for_permission_mode(run(data), data))


if __name__ == "__main__":
    main()
