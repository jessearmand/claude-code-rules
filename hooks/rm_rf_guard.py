#!/usr/bin/env python3
"""
Claude Code Hook: rm -rf Guard
==============================
PreToolUse hook for the Bash tool. Denies any `rm` invocation that combines
recursive (-r/-R/--recursive) with force (-f/--force), and tells Claude to
escalate via the AskUserQuestion tool instead of retrying or working around it.

The denial reason includes a per-target analysis so Claude can propose the
least destructive command that still does the job:

    plain `rm <file>`   for regular files
    `rm -r <dir>`       for directories
    `rm -rf <dir>`      only after the user explicitly confirms

Decision protocol: https://code.claude.com/docs/en/hooks#pretooluse-decision-control
Exit 0 with a `permissionDecision: "deny"` payload blocks the call and shows
`permissionDecisionReason` to Claude. Exit 0 with no payload defers to the
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

from hook_protocol import Decision, decide, read_input  # noqa: E402

# Tokens that terminate one simple command and begin another. `rm` is looked
# for at the head of each resulting segment, so `echo rm -rf x` is ignored
# while `foo && rm -rf x` and `... | xargs rm -rf` are both caught.
_COMMAND_SEPARATORS = frozenset(
    {"&&", "||", ";", "|", "|&", "&", "(", ")", "{", "}", "\n", "!", "xargs", "-exec", "-execdir", "then", "do", "else"}
)

# Wrappers that delegate to the command that follows them.
_COMMAND_PREFIXES = frozenset(
    {"sudo", "doas", "command", "builtin", "exec", "time", "nohup", "env", "nice", "ionice", "eval", "xargs"}
)

# Long options, matched by unambiguous prefix the way GNU coreutils does.
_LONG_RECURSIVE = "--recursive"
_LONG_FORCE = "--force"

_GLOB_METACHARACTERS = re.compile(r"[*?\[\]]")
_UNEXPANDED_VARIABLE = re.compile(r"\$\{?\w+")

# Fallback detector used only when the command cannot be tokenized. Deliberately
# loose: a detection failure here would let an `rm -rf` through unexamined.
_UNPARSEABLE_FALLBACK = re.compile(
    r"(?:^|[;&|]|\s)rm\s+(?:-\w*\s+)*-\w*(?:r\w*f|f\w*r)|(?:^|[;&|]|\s)rm\s+(?:[^;&|]*\s)?"
    r"(?:--recursive\s+(?:[^;&|]*\s)?--force|--force\s+(?:[^;&|]*\s)?--recursive)",
    re.IGNORECASE,
)

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

    @property
    def is_recursive_force(self) -> bool:
        return self.recursive and self.force

    def rendered(self) -> str:
        return " ".join(["rm", *self.flags, *self.targets])


@dataclass
class TargetAnalysis:
    """What a single `rm` target actually points at, and how to remove it safely."""

    raw: str
    kind: str  # file | directory | symlink | missing | glob | unresolved
    detail: str
    safer_command: str | None
    severity: str  # critical | high | normal


def _tokenize(command: str) -> list[str] | None:
    """Split a shell command into tokens, or None if it cannot be tokenized."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _split_into_simple_commands(tokens: list[str]) -> list[list[str]]:
    """Break a token stream on shell operators into individual simple commands."""
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _COMMAND_SEPARATORS or set(token) <= {"&", "|", ";"} and token:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _strip_command_prefixes(segment: list[str]) -> list[str]:
    """Drop `sudo`, `env FOO=bar`, and similar wrappers from the head of a command."""
    index = 0
    while index < len(segment):
        token = segment[index]
        if token in _COMMAND_PREFIXES or re.fullmatch(r"\w+=.*", token):
            index += 1
            continue
        # Skip flags belonging to the wrapper (e.g. `sudo -u root`, `xargs -0 -n1`).
        if index > 0 and token.startswith("-"):
            index += 1
            continue
        break
    return segment[index:]


def _is_rm(token: str) -> bool:
    return token == "rm" or token.endswith("/rm")


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


def find_recursive_force_invocations(command: str) -> tuple[list[RmInvocation], bool]:
    """Return every `rm -rf`-equivalent invocation in a command.

    The second element is True when the command could not be tokenized and a
    regex fallback had to be used, in which case flags/targets are unavailable.
    """
    tokens = _tokenize(command)
    if tokens is None:
        if _UNPARSEABLE_FALLBACK.search(command):
            return [RmInvocation(recursive=True, force=True)], True
        return [], True

    invocations: list[RmInvocation] = []
    for segment in _split_into_simple_commands(tokens):
        stripped = _strip_command_prefixes(segment)
        if not stripped or not _is_rm(stripped[0]):
            continue
        invocation = _parse_rm_arguments(stripped[1:])
        if invocation.is_recursive_force:
            invocations.append(invocation)

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


def build_denial_reason(invocations: list[RmInvocation], cwd: str, unparseable: bool) -> str:
    """Compose the feedback Claude sees in place of running the command."""
    sections: list[str] = [
        "BLOCKED by the rm-rf-guard hook: `rm` with both recursive and force is never "
        "allowed to run, and this hook has no override.",
        "",
    ]

    if unparseable:
        sections += [
            "The command could not be tokenized, but it appears to contain a recursive "
            "forced `rm`. Rewrite it so the removal is a plain, quoted command.",
            "",
        ]
    else:
        sections.append("Blocked invocation(s):")
        sections += [f"  {invocation.rendered()}" for invocation in invocations]
        sections.append("")

        analyses = [
            analyze_target(target, cwd)
            for invocation in invocations
            for target in invocation.targets
        ]
        if analyses:
            sections.append("Target analysis:")
            sections += _format_target_section(analyses)
            sections.append("")

    sections += [
        "Do this instead, in order:",
        "",
        "1. Reach for the least destructive command that actually does the job:",
        "     `rm <file>`      removes a single file — no flags needed",
        "     `rm -r <dir>`    removes a directory tree, but still stops on "
        "write-protected entries instead of silently forcing them",
        "   Use the 'safer:' commands above where they are listed. If a target does not "
        "exist, drop it rather than adding `-f` to hide the error.",
        "",
        "2. If `rm -r` genuinely will not work (write-protected files, or the path may be "
        "absent and the removal must not fail), do NOT retry and do NOT route around this "
        "hook — no `sudo`, no `find -delete`, no `git clean -xdf`, no Python `shutil.rmtree`, "
        "no `trash`/`mv` to a temp dir. Instead call the AskUserQuestion tool and let the "
        "user decide, e.g.:",
        "",
        '     question: "`rm -rf` is blocked. How should I remove <target>?"',
        '     header:   "Removal"',
        "     options:",
        '       - "Try `rm -r <target>`" — non-forced recursive delete; stops and reports '
        "write-protected entries",
        '       - "You run `rm -rf <target>`" — paste `! rm -rf <target>` in the prompt so '
        "the destructive step is yours, not mine",
        '       - "Leave it in place" — skip the removal and continue with the rest of the task',
        "",
        "3. Report the block to the user in your reply. Never silently skip a removal the "
        "task depended on.",
    ]

    return "\n".join(sections)


def run(data: dict) -> Decision | None:
    """Return a denial for recursive forced removal commands."""
    if data.get("tool_name") != "Bash":
        return None

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        return None

    invocations, unparseable = find_recursive_force_invocations(command)
    if not invocations:
        return None

    cwd = os.path.normpath(data.get("cwd") or os.getcwd())
    return Decision(
        "deny",
        build_denial_reason(invocations, cwd, unparseable),
        "rm-rf-guard blocked an `rm -rf`. Claude will ask you how to proceed.",
        "rm-rf-guard",
    )


def main() -> None:
    result = run(read_input("rm_rf_guard"))
    if result:
        decide(result.decision, result.reason, result.system_message)


if __name__ == "__main__":
    main()
