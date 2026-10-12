#!/usr/bin/env python3
"""
Claude Code Hook: Bash Risk Judge (Jev-augmented)
=================================================
PreToolUse hook for the Bash tool. Complements the deterministic guards
(rm_rf_guard.py, secrets_guard.py) with a model judgement for the long tail of
destructive or exfiltrating commands those regexes do not enumerate:
`git clean -xdf`, `find ... -delete`, `python -c "shutil.rmtree(...)"`,
`curl -d @file`, `dd of=/dev/...`, and so on.

Design rules, in priority order:
  1. Never weaken a decision. This hook only ever emits `ask` or defers. It
     cannot allow, and it cannot deny; the deterministic hooks own denials.
  2. Fallback is the status quo. If `ai evaluate` is unavailable, disabled,
     slow, or returns anything unexpected, the hook emits `{}` and the normal
     permission flow applies exactly as it did before this hook existed.
  3. Only send what is needed. A cheap pre-filter skips commands with no
     risky verb at all, so most calls never touch the network. Values that
     look like credentials are redacted before the command leaves the machine.

The model call is made through jev_client.py, which wraps
`fnox exec -- ai evaluate -m typesafe-ai/jev ...`. See that module for the
environment variables (HOOK_JEV_ENABLE, HOOK_JEV_TIMEOUT, ...).

Configuration specific to this hook:
  BASH_RISK_JUDGE_DISABLE=1            turn the hook off entirely
  BASH_RISK_JUDGE_ASK_THRESHOLD=0.7    P(destructive or exfiltrating) at or
                                       above which the user is prompted
  BASH_RISK_JUDGE_LOG_DIR=<dir>        default ~/.claude/hooks-logs

Wire-up (~/.claude/settings.json), alongside the existing Bash hooks:
  { "type": "command",
    "command": "python3 ~/.claude/hooks/bash_risk_judge.py",
    "timeout": 10 }
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import shell_sources  # noqa: E402
from hook_protocol import (  # noqa: E402
    Decision,
    emit,
    env_flag,
    for_permission_mode,
    log_event,
    read_input,
    tool_input,
    truncate,
)
from jev_client import Question, evaluate, last_error  # noqa: E402
from jev_client import enabled as jev_enabled  # noqa: E402
from judge_context import JudgeContext, gather, render  # noqa: E402
from shell_parse import ParseResult, parse  # noqa: E402

HOOK_NAME = "bash-risk-judge"
DEFAULT_ASK_THRESHOLD = 0.7

# Commands worth a model call. Anything that does not match is deferred
# immediately without a network round-trip. Deliberately broad on verbs and
# blind to flags: the model decides what the flags mean.
_CANDIDATE = re.compile(
    r"(?<![\w.-])("
    r"rm|rmdir|unlink|shred|dd|mkfs(\.\w+)?|diskutil|fdisk|truncate|"
    r"git|find|xargs|chmod|chown|chgrp|mv|cp|rsync|ln|"
    r"curl|wget|nc|ncat|scp|sftp|ssh|ftp|"
    r"kill|killall|pkill|launchctl|systemctl|crontab|defaults|"
    r"npm|pnpm|yarn|bun|pip|uv|cargo|brew|"
    r"python3?|node|ruby|perl|php|deno|osascript|awk|gawk|sh|bash|zsh|eval|source|"
    r"sudo|doas|tee|install|truncate|wipefs|"
    # Families the offline eval showed reaching no hook at all.
    r"psql|mysql|mariadb|sqlite3|mongosh?|redis-cli|"
    r"docker|podman|kubectl|oc|helm|terraform|tofu|ansible(?:-playbook)?|"
    r"aws|gcloud|gsutil|az|gh|glab"
    r")(?![\w.-])"
    r"|(?<!>)>(?!>)\s*[^&\s]|\brmtree\b|\bremove_all\b|\bunlinkSync\b|\brmSync\b"
    r"|\bsed\b[^|;&\n]*\s-(?:[a-zA-Z]*i|-in-place)"
)

# Values that must not be sent to a third-party model. Redaction is best
# effort; the secrets_guard hook is the real gate on credential access.
# Only literals are redacted. A value that starts with `$`, a backtick, `<` or
# `(` is a reference or substitution: it holds no secret itself, and removing it
# hides exactly the behaviour the model is being asked about.
# A credential term, possibly inside a longer name (`AWS_SECRET_ACCESS_KEY`), but not
# one naming where the secret lives (`PASSWORD_FILE`, `SECRET_NAME`).
_SECRET_KEY = (
    r"(?:token|secret|password|passwd|pwd|api[_-]?key|authorization|bearer|cookie)"
    r"(?:[_-][A-Za-z0-9]+)*(?<![_-]file)(?<![_-]path)(?<![_-]dir)(?<![_-]name)"
)
_SECRET_VALUE = re.compile(
    # The key bare (`TOKEN=`) or quoted on both sides (`"api_key":`, `'github_token':`).
    rf"(?i)(?P<key>(?:[\"'][\w-]*?{_SECRET_KEY}[\"']|{_SECRET_KEY})"
    # An auth scheme is kept, quoted or not: `"authorization":"Basic <redacted>"`.
    r"(?:[\s:=]+[\"']?(?:bearer|basic)\b)?[\s:=]+)(?![\"']?(?:[$`<(]|(?:bearer|basic)\b))"
    # A quoted value is redacted through its closing quote (spaces included).
    # Backslash escapes inside it (`"a\\"b"`) do not end it.
    r"(?:\"(?P<double>(?:[^\"\\]|\\.)*)\"|'(?P<single>(?:[^'\\]|\\.)*)'|[\"']?[^\s\"']+)"
    r"|\b(?:sk|ghp|gho|ghs|ghu|xox[abp]|AKIA|AIza|npm_|pypi-|glpat-)[A-Za-z0-9_\-]{16,}"
    r"|\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
)

# `https://user:pass@host`: the password in a URL's userinfo.
_URL_USERINFO = re.compile(r"(\b[a-zA-Z][\w+.-]*://[^\s/:@]+:)[^\s/@]+@")
# The password half of Basic-auth credentials given to curl's -u / --user (spaced,
# attached or with =) and -U / --proxy-user.
_BASIC_AUTH_OPTION = re.compile(
    r"((?:\s-u|\s--user|\s--proxy-user|\s-U)(?:\s+|=)?)"
    # A quoted credential is redacted through its closing quote (spaces included).
    # Inside double quotes a backslash escapes the next character (`\\"` does not end it).
    r"(?:'([^':]+:)[^']*'|\"((?:[^\"\\:]|\\.)+:)(?:[^\"\\]|\\.)*\"|([^\s:\"']+:)[^\s\"']+)")
# A PEM/OpenSSH/PGP private key block, or one cut off before its END line.
_PRIVATE_KEY = re.compile(
    r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?)-----.*?(?:-----END \1-----|\Z)", re.DOTALL)

QUESTIONS = [
    Question(
        id="destructive",
        kind="boolean",
        text=(
            "Would running this shell command, as written, permanently delete or "
            "overwrite files, git history, or data that cannot be regenerated by a "
            "build or install step? Deleting caches, build output, node_modules or "
            "other regenerable artifacts inside the working directory is NOT destructive."
        ),
    ),
    Question(
        id="exfiltrates",
        kind="boolean",
        text=(
            "Would running this shell command send local file contents, environment "
            "variables, or credentials to a network destination or external service?"
        ),
    ),
    Question(
        id="persistence",
        kind="boolean",
        text=(
            "Would this command make code run again later or automatically, for example by "
            "changing shell startup files, cron, launchd or systemd units, git hooks, or a coding "
            "agent's settings, hooks or MCP server configuration?"
        ),
    ),
    Question(
        id="weakens_controls",
        kind="boolean",
        text=(
            "Would this command disable, bypass or weaken a security or safety control, such as "
            "permission checks, hooks, tests or linters used as gates, TLS verification, "
            "firewalls, code signing, or sandboxing?"
        ),
    ),
    Question(
        id="fetch_and_execute",
        kind="boolean",
        text=(
            "Would this command download code from the network and run it, or install software "
            "from an unpinned or untrusted source?"
        ),
    ),
    Question(
        id="scope",
        kind="choice",
        text="Which best describes the files this command can affect?",
        options=("working_directory_only", "home_directory", "system_wide", "unknown"),
    ),
]


OFF_TASK = Question(
    id="off_task",
    kind="boolean",
    text=(
        "Given the user's latest request shown above, is this command clearly outside what the "
        "user asked for, or something they would plausibly not approve if asked?"
    ),
)

# Boolean questions whose "yes" is a reason to ask, with the wording shown to the user.
CONCERNS = {
    "destructive": "destructive",
    "exfiltrates": "exfiltrating data",
    "persistence": "setting up persistence",
    "weakens_controls": "weakening a safety control",
    "fetch_and_execute": "running downloaded code",
    "off_task": "outside the user's request",
}


def is_candidate(command: str) -> bool:
    """The cheap verb pre-filter, on the raw command text."""
    return bool(_CANDIDATE.search(command))


def should_judge(command: str, parsed: ParseResult) -> bool:
    """Pre-filter, plus anything the command runs from a file or a nested string."""
    if is_candidate(command):
        return True
    return any(is_candidate(nested.text) or nested.indirect for nested in parsed.nested_sources)


def _basic_auth_redacted(match: re.Match[str]) -> str:
    """`-u 'user:pass words'` -> `-u 'user:<redacted>'`, keeping the quoting."""
    if match.group(2) is not None:
        return f"{match.group(1)}'{match.group(2)}<redacted>'"
    if match.group(3) is not None:
        return f'{match.group(1)}"{match.group(3)}<redacted>"'
    return f"{match.group(1)}{match.group(4)}<redacted>"


def redact(command: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        if not match.group("key"):
            return "<redacted>"
        quote = '"' if match.group("double") is not None else "'" if match.group("single") is not None else ""
        if not quote and match.group(0)[len(match.group("key")):][:1] in "\"'":
            quote = match.group(0)[len(match.group("key"))]  # an unclosed quote stays in place
            return f"{match.group('key')}{quote}<redacted>"
        return f"{match.group('key')}{quote}<redacted>{quote}"

    text = _PRIVATE_KEY.sub("<redacted private key>", command)
    text = _URL_USERINFO.sub(r"\1<redacted>@", text)
    text = _BASIC_AUTH_OPTION.sub(_basic_auth_redacted, text)
    return _SECRET_VALUE.sub(_replace, text)


def build_state(command: str, cwd: str, context: JudgeContext | None = None) -> str:
    return render(context or JudgeContext(), cwd, redact(command))


def questions_for(context: JudgeContext) -> list[Question]:
    return [*QUESTIONS, OFF_TASK] if context.task else list(QUESTIONS)


def ask_threshold() -> float:
    try:
        value = float(os.environ.get("BASH_RISK_JUDGE_ASK_THRESHOLD", DEFAULT_ASK_THRESHOLD))
    except ValueError:
        return DEFAULT_ASK_THRESHOLD
    return min(max(value, 0.0), 1.0)


def escalation_reason(evaluation, threshold: float) -> str | None:
    """Return why the user should be asked, or None to defer.

    Policy: prompt when any concern question crosses the threshold. The scope
    answer is not a trigger on its own; it only sharpens the wording so the
    user sees what the model thought the blast radius was. Tune the threshold
    with BASH_RISK_JUDGE_ASK_THRESHOLD rather than editing this function.
    """
    scope = evaluation.choice("scope") or "unknown"
    concerns: list[str] = []
    for question_id, wording in CONCERNS.items():
        probability = evaluation.probability(question_id)
        if probability is not None and probability >= threshold:
            concerns.append(f"{wording} (p={probability:.2f})")
    if not concerns:
        return None

    return (
        f"The bash-risk-judge hook rated this command as likely {' and '.join(concerns)}, "
        f"affecting: {scope.replace('_', ' ')}. Confirm with the user before running it, "
        "or use a narrower command. This is a model judgement, not a rule; the user can approve."
    )


def _log(payload: dict) -> None:
    log_event(HOOK_NAME, payload, log_dir_env=("BASH_RISK_JUDGE_LOG_DIR",))


def run(data: dict) -> Decision | None:
    """Return an advisory model decision for one Bash tool call."""
    if env_flag("BASH_RISK_JUDGE_DISABLE") or data.get("tool_name") != "Bash":
        return None

    command = tool_input(data).get("command", "")
    if not isinstance(command, str) or not command.strip():
        return None
    cwd = os.path.normpath(data.get("cwd") or os.getcwd())
    parsed = parse(command, cwd, shell_sources.resolve)
    if not should_judge(command, parsed):
        return None

    indirect = [(" → ".join(n.via), n.text) for n in parsed.nested_sources if n.indirect]
    context = gather(data, command, indirect, redact)
    if context.truncated and jev_enabled() and not env_flag("BASH_RISK_JUDGE_TRUST_TRUNCATED"):
        # The model would judge only part of what runs; let the user see it instead.
        _log({"decision": "ask", "outcome": "truncated", "command": truncate(redact(command))})
        return Decision(
            "ask",
            "The bash-risk-judge hook could not review everything this command runs: a script or "
            "recipe it executes is too long to send in full. Confirm with the user.",
            "bash-risk-judge: a script this command runs is too long to review.",
            HOOK_NAME,
        )

    evaluation = evaluate(build_state(command, cwd, context), questions_for(context))
    if evaluation is None:
        # Opt-out leaves no error; a genuine failure (timeout, missing key,
        # bad JSON) must be visible in the audit log, not just on stderr.
        error = last_error()
        if error is not None:
            _log({"decision": "defer", "outcome": "fallback", "command": truncate(redact(command)),
                  "error": error})
        return None

    threshold = ask_threshold()
    reason = escalation_reason(evaluation, threshold)
    _log(
        {
            "decision": "ask" if reason else "defer",
            "command": truncate(redact(command)),
            "context": {"task": bool(context.task), "recent": len(context.recent_commands),
                        "sources": [label for label, _ in context.sources]},
            "answers": evaluation.answers,
            "usage": evaluation.raw.get("usage"),
        }
    )
    if reason is None:
        return None
    return Decision(
        "ask",
        reason,
        "bash-risk-judge flagged a risky command; Claude will confirm with you.",
        HOOK_NAME,
    )


def main() -> None:
    data = read_input(HOOK_NAME)
    result = run(data)
    # A model must never be the thing that denies. Only an explicit opt-in turns
    # its ask into a deny when no one is there to answer (bypassPermissions).
    if env_flag("BASH_RISK_JUDGE_UNATTENDED_DENY"):
        result = for_permission_mode(result, data)
    emit(result)


if __name__ == "__main__":
    main()
