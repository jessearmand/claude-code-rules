#!/usr/bin/env python3
"""
Claude Code Hook: rm -rf Guard
==============================
PreToolUse hook for the Bash tool. Asks the user before any `rm` invocation
that combines recursive (-r/-R/--recursive) with force (-f/--force). In a
permission mode where no one can be asked, the ask becomes a deny (see
hook_protocol.for_permission_mode), and Claude is told not to work around it.

The reason includes a per-target analysis so the user (or Claude, when it is
denied) can see the least destructive command that still does the job:

    plain `rm <file>`   for regular files
    `rm -r <dir>`       for directories
    `rm -rf <dir>`      only after the user explicitly confirms

Decision protocol: https://code.claude.com/docs/en/hooks#pretooluse-decision-control
Exit 0 with a `permissionDecision: "ask"` payload shows `permissionDecisionReason`
to the user, who confirms or declines. Exit 0 with no payload defers to the
normal permission flow.

Wire-up (~/.claude/settings.json):

{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/rm_rf_guard.py"
          }
        ]
      }
    ]
  }
}
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import shell_sources  # noqa: E402
from hook_protocol import (  # noqa: E402
    Decision,
    emit,
    for_permission_mode,
    read_input,
    tool_input,
)
from shell_parse import SimpleCommand, SourceResolver, parse  # noqa: E402

# Long options, matched by unambiguous prefix the way GNU coreutils does.
_LONG_RECURSIVE = "--recursive"
_LONG_FORCE = "--force"

_GLOB_METACHARACTERS = re.compile(r"[*?\[\]]")
_UNEXPANDED_VARIABLE = re.compile(r"\$\{?\w+")

# Fallback detector used only when the command cannot be parsed completely
# (an unterminated quote or substitution). Deliberately loose: a detection
# failure here would let an `rm -rf` through unexamined.
_UNPARSEABLE_FALLBACK = re.compile(
    r"(?:^|[;&|`(]|\s)rm\s+(?:-\w*\s+)*-\w*(?:r\w*f|f\w*r)|(?:^|[;&|`(]|\s)rm\s+(?:[^;&|]*\s)?"
    r"(?:--recursive\s+(?:[^;&|]*\s)?--force|--force\s+(?:[^;&|]*\s)?--recursive)",
    re.IGNORECASE,
)

# A command name computed at run time that still says "rm": `$(which rm)`,
# `$RM`, `${RM_CMD}`. With recursive and force flags it is treated as rm.
_DYNAMIC_RM = re.compile(r"\brm\b|\$\{?RM", re.IGNORECASE)

HOOK_NAME = "rm-rf-guard"

_SYSTEM_ROOTS = frozenset(
    {
        "/Applications",
        "/Library",
        "/System",
        "/Users",
        "/Volumes",
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/opt",
        "/private",
        "/sbin",
        "/srv",
        "/usr",
        "/var",
    }
)


@dataclass
class RmInvocation:
    """A single parsed `rm` command line."""

    recursive: bool = False
    force: bool = False
    flags: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    # How the parser reached it (`bash -c`, `script ./clean.sh`), outermost first.
    origin: str = ""
    # Found inside a file the command runs rather than typed in this call.
    indirect: bool = False
    # Directory it runs in after any earlier `cd`; None when that is computed.
    cwd: str | None = None
    # An `rm` whose arguments the parser could not fully work out (the parse
    # was cut short): it may be recursive and forced.
    uncertain: bool = False

    @property
    def is_recursive_force(self) -> bool:
        return self.recursive and self.force

    def rendered(self) -> str:
        text = " ".join(["rm", *self.flags, *self.targets])
        return f"{text}    (via {self.origin})" if self.origin else text


@dataclass
class TargetAnalysis:
    """What a single `rm` target actually points at, and how to remove it safely."""

    raw: str
    kind: str  # file | directory | symlink | missing | glob | unresolved
    detail: str
    safer_command: str | None
    severity: str  # critical | high | normal


def _is_rm(command: SimpleCommand) -> bool:
    if command.dynamic_head:
        return bool(command.argv) and bool(_DYNAMIC_RM.search(command.argv[0]))
    return command.name == "rm"


def _parse_rm_arguments(arguments: list[str]) -> RmInvocation:
    """Parse `rm` arguments into recursive/force flags and target paths."""
    invocation = RmInvocation()
    options_ended = False

    for argument in arguments:
        if options_ended:
            invocation.targets.append(argument)
            continue

        if argument == "--":
            options_ended = True
            invocation.flags.append(argument)
            continue

        if argument.startswith("--") and len(argument) > 2:
            invocation.flags.append(argument)
            if _LONG_RECURSIVE.startswith(argument):
                invocation.recursive = True
            elif _LONG_FORCE.startswith(argument):
                invocation.force = True
            continue

        if argument.startswith("-") and len(argument) > 1:
            invocation.flags.append(argument)
            for letter in argument[1:]:
                if letter in ("r", "R"):
                    invocation.recursive = True
                elif letter == "f":
                    invocation.force = True
            continue

        invocation.targets.append(argument)

    return invocation


def find_recursive_force_invocations(
    command: str, cwd: str = "", sources: SourceResolver | None = None
) -> tuple[list[RmInvocation], bool]:
    """Return every `rm -rf`-equivalent invocation a command would run.

    The parser sees through wrappers (`sudo -u root`, `timeout 5`, `xargs`,
    `find -exec`), re-parsed strings (`bash -c`, `eval`, `ssh host`, `$(...)`,
    heredocs fed to a shell) and, with `sources`, files the command executes.

    The second element is True when the command could not be parsed
    completely and the regex fallback had to be used, in which case
    flags/targets are unavailable.
    """
    parsed = parse(command, cwd, sources)
    invocations: list[RmInvocation] = []
    for simple in parsed.commands:
        if not _is_rm(simple):
            continue
        invocation = _parse_rm_arguments(list(simple.args))
        if invocation.is_recursive_force:
            invocation.origin = simple.origin()
            invocation.indirect = simple.indirect
            invocation.cwd = simple.cwd
            invocations.append(invocation)

    incomplete = parsed.unparseable or parsed.truncated
    if not invocations and incomplete and _UNPARSEABLE_FALLBACK.search(command):
        return [RmInvocation(recursive=True, force=True)], True
    if not invocations and parsed.truncated:
        for simple in parsed.commands:
            if _is_rm(simple):
                invocation = _parse_rm_arguments(list(simple.args))
                invocation.origin, invocation.cwd, invocation.uncertain = simple.origin(), simple.cwd, True
                return [invocation], False
    return invocations, False


def _resolve(target: str, cwd: str) -> str:
    expanded = os.path.expanduser(target)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return os.path.normpath(expanded)


def _severity_for(resolved: str, cwd: str) -> tuple[str, str | None]:
    """Rate how catastrophic removing `resolved` would be."""
    home = os.path.expanduser("~")

    if resolved == os.sep:
        return "critical", "this is the filesystem root"
    if resolved == home:
        return "critical", "this is your entire home directory"
    if resolved in _SYSTEM_ROOTS:
        return "critical", "this is a system directory"
    if cwd == resolved or cwd.startswith(resolved.rstrip(os.sep) + os.sep):
        return "critical", "this is the working directory or one of its ancestors"
    if os.path.dirname(resolved) == os.sep:
        return "high", "this is a top-level directory"
    if not resolved.startswith(home + os.sep):
        return "high", "this is outside your home directory"
    return "normal", None


def _analysis_cwd(invocation: RmInvocation, cwd: str) -> str:
    """Where relative targets resolve: after any `cd`, or `/` (worst case) when unknown."""
    if invocation.cwd is not None:
        return invocation.cwd
    return os.sep if invocation.targets else cwd


def analyze_target(target: str, cwd: str) -> TargetAnalysis:
    """Describe a target and name the least destructive command that removes it."""
    if _UNEXPANDED_VARIABLE.search(target):
        return TargetAnalysis(
            raw=target,
            kind="unresolved",
            detail=(
                "contains an unexpanded variable, so the real target is unknown. "
                "If it expands to an empty string this deletes the wrong tree"
            ),
            safer_command=None,
            severity="critical",
        )

    if _GLOB_METACHARACTERS.search(target):
        return TargetAnalysis(
            raw=target,
            kind="glob",
            detail=f"is a glob; run `ls -d {shlex.quote(target)}` first to see exactly what it matches",
            safer_command=None,
            severity="high",
        )

    resolved = _resolve(target, cwd)
    severity, danger = _severity_for(resolved, cwd)
    quoted = shlex.quote(target)

    if os.path.islink(resolved):
        detail = "is a symlink (plain `rm` removes the link, not its target)"
        analysis = TargetAnalysis(target, "symlink", detail, f"rm {quoted}", severity)
    elif os.path.isdir(resolved):
        try:
            entries = len(os.listdir(resolved))
            count = f"{entries} immediate " + ("entry" if entries == 1 else "entries")
        except OSError:
            count = "unreadable contents"
        detail = f"is a directory ({count}) at {resolved}"
        analysis = TargetAnalysis(target, "directory", detail, f"rm -r {quoted}", severity)
    elif os.path.isfile(resolved):
        detail = f"is a regular file at {resolved}"
        analysis = TargetAnalysis(target, "file", detail, f"rm {quoted}", severity)
    else:
        detail = (
            f"does not exist ({resolved}); removing it is a no-op, so if `-f` was "
            "only there to suppress 'No such file or directory', drop the command entirely"
        )
        analysis = TargetAnalysis(target, "missing", detail, None, "normal")

    if danger:
        analysis.detail = f"{analysis.detail} — {danger}"
    return analysis


def _format_target_section(analyses: list[TargetAnalysis]) -> list[str]:
    lines: list[str] = []
    for analysis in analyses:
        marker = {"critical": "  [CRITICAL] ", "high": "  [CAUTION]  ", "normal": "  "}[analysis.severity]
        lines.append(f"{marker}{analysis.raw} {analysis.detail}")
        if analysis.safer_command:
            lines.append(f"      safer: {analysis.safer_command}")
    return lines


def build_ask_reason(invocations: list[RmInvocation], cwd: str, unparseable: bool) -> str:
    """What the user sees when asked to confirm, and Claude sees if it is declined
    or denied because no one can be asked."""
    sections: list[str] = [
        "rm-rf-guard: this command runs `rm` with both recursive and force, which deletes "
        "without stopping on anything. It runs only if the user confirms.",
        "",
    ]

    if unparseable:
        sections += [
            "The command could not be parsed completely, but it appears to contain a recursive "
            "forced `rm`.",
            "",
        ]
    else:
        sections.append("Invocation(s):")
        sections += [f"  {invocation.rendered()}" for invocation in invocations]
        sections.append("")

        analyses = [
            analyze_target(target, _analysis_cwd(invocation, cwd))
            for invocation in invocations
            for target in invocation.targets
        ]
        if analyses:
            sections.append("Target analysis:")
            sections += _format_target_section(analyses)
            sections.append("")

    sections += [
        "Less destructive alternatives:",
        "  `rm <file>`      removes a single file — no flags needed",
        "  `rm -r <dir>`    removes a directory tree, but stops on write-protected entries "
        "instead of silently forcing them",
        "",
        "If this is declined or denied: do NOT retry it and do NOT route around this hook — "
        "no `sudo`, no `find -delete`, no `git clean -xdf`, no Python `shutil.rmtree`, no "
        "`trash`/`mv` to a temp dir. Use a 'safer:' command above if one fits, otherwise ask "
        "the user how to proceed (for example, they can run `! rm -rf <target>` themselves), "
        "and report it in your reply. Never silently skip a removal the task depended on.",
    ]

    return "\n".join(sections)


def build_indirect_reason(invocations: list[RmInvocation], cwd: str) -> str:
    """Feedback for an `rm -rf` found only inside a file the command runs."""
    lines = [
        "rm-rf-guard: this command runs a script or recipe that contains a recursive "
        "forced `rm`. The agent did not type it here, so the user decides:",
        "",
    ]
    lines += [f"  {invocation.rendered()}" for invocation in invocations]
    analyses = [analyze_target(t, _analysis_cwd(invocation, cwd))
                for invocation in invocations for t in invocation.targets]
    if analyses:
        lines += ["", "Target analysis:", *_format_target_section(analyses)]
    lines += [
        "",
        "If the user declines, do not edit the script to get around this; report it and "
        "ask how to proceed.",
    ]
    return "\n".join(lines)


def run(data: dict) -> Decision | None:
    """Ask before recursive forced removals, typed in the command or run from files."""
    if data.get("tool_name") != "Bash":
        return None

    command = tool_input(data).get("command", "")
    if not isinstance(command, str) or not command:
        return None

    cwd = os.path.normpath(data.get("cwd") or os.getcwd())
    invocations, unparseable = find_recursive_force_invocations(command, cwd, shell_sources.resolve)
    if not invocations:
        return None

    if all(invocation.uncertain for invocation in invocations) and not unparseable:
        return Decision(
            "ask",
            "rm-rf-guard: this command runs `rm` with arguments that could not all be worked out "
            "(too many possible values to check every combination):\n\n"
            + "\n".join(f"  {invocation.rendered()}" for invocation in invocations)
            + "\n\nThe user decides whether it may run.",
            "rm-rf-guard: an `rm` whose flags could not be fully determined.",
            HOOK_NAME,
        )
    direct = [invocation for invocation in invocations if not invocation.indirect]
    if direct or unparseable:
        return Decision(
            "ask",
            build_ask_reason(direct, cwd, unparseable),
            "rm-rf-guard: confirm the `rm -rf` before it runs.",
            HOOK_NAME,
        )
    return Decision(
        "ask",
        build_indirect_reason(invocations, cwd),
        "rm-rf-guard: the command runs a script containing `rm -rf`.",
        HOOK_NAME,
    )


def main() -> None:
    data = read_input("rm_rf_guard")
    emit(for_permission_mode(run(data), data))


if __name__ == "__main__":
    main()
