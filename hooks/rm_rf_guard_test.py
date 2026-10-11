#!/usr/bin/env python3
"""Tests for rm_rf_guard.py. Run: python3 hooks/rm_rf_guard_test.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rm_rf_guard import analyze_target, find_recursive_force_invocations  # noqa: E402

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_GUARD = os.path.join(_HOOKS_DIR, "rm_rf_guard.py")
# A real project directory to stand in for the session cwd: this repo's root, so the
# end-to-end cases resolve relative targets the same way on any machine or checkout.
_REPO_ROOT = os.path.dirname(_HOOKS_DIR)

SHOULD_BLOCK = [
    "rm -rf build",
    "rm -fr build",
    "rm -Rf build",
    "rm -rvf build",
    "rm -r -f build",
    "rm -f -R build",
    "rm --recursive --force build",
    "rm --force --recursive build",
    "rm -r --force build",
    "sudo rm -rf /var/cache/foo",
    "npm ci && rm -rf node_modules",
    "cd /tmp; rm -rf scratch",
    "find . -name '*.tmp' -exec rm -rf {} +",
    "ls | xargs rm -rf",
    "/bin/rm -rf build",
    "rm -rf -- --weird-dir",
    "rm -rf $PROJECT/dist",
    "rm -rf ~/Library/Caches/foo",
    "test -d out && rm -rf out || echo skipped",
    # Newlines separate commands; shlex used to read this as one `echo`.
    "echo hi\nrm -rf build",
    "git status\n  rm -rf ~",
    # `#` only starts a comment at the start of a word.
    "echo a#b; rm -rf build",
    # Wrappers whose options take an argument.
    "sudo -u root rm -rf /",
    "sudo -Eu root rm -rf /",
    "timeout 5 rm -rf build",
    "nice -n 5 rm -rf build",
    "xargs -I{} rm -rf {}",
    "busybox rm -rf build",
    "mise exec -- rm -rf build",
    # Strings a shell parses again.
    "bash -c 'rm -rf ~'",
    "zsh -c 'rm -rf ~'",
    "sudo sh -c 'rm -rf /'",
    "bash -lc \"eval 'rm -rf ~'\"",
    "eval 'rm -rf ~'",
    "env -S 'rm -rf build'",
    "su -c 'rm -rf /' root",
    "ssh -p 22 host 'rm -rf /srv'",
    "watch -n 1 rm -rf build",
    "flock /tmp/lock -c 'rm -rf build'",
    "echo 'rm -rf ~' | bash",
    "bash <<'EOF'\nrm -rf ~\nEOF",
    "bash <<< 'rm -rf build'",
    # Command substitution runs its body.
    "`rm -rf build`",
    "echo \"$(rm -rf build)\"",
    "cat <<EOF\n$(rm -rf build)\nEOF",
    # Computed command names.
    "X=rm; $X -rf ~",
    "T=$HOME; rm -rf \"$T\"",
    "X=rm; echo $($X -rf ~/victim)",
    # Subshells keep their own variables; eval shares the caller's (PR review).
    "X=rm; echo $(X=echo); $X -rf ~/victim",
    "X=rm; (X=echo); $X -rf ~/victim",
    "X=echo; eval X=rm; $X -rf ~/victim",
    "X=rm; X=echo true; $X -rf ~/victim",  # a prefix assignment is temporary (PR review)
    "X='rm -rf ~' sh -c '$X'",  # ...but is in the environment of what the command runs
    # Runner global options before the subcommand; long command options (PR review).
    "uv --directory . run rm -rf build",
    "poetry -C . run rm -rf build",
    "pnpm --dir . exec rm -rf build",
    "flock lockfile --command 'rm -rf build'",
    "flock lockfile --command='rm -rf build'",
    "su --command='rm -rf /'",
    # fish command options and substitutions inside arithmetic (PR review).
    "fish -C 'rm -rf ~/victim'",
    "fish --command='rm -rf ~/victim'",
    "fish --init-command='rm -rf ~/victim' script.fish",
    "echo $(( $(rm -rf ~/victim) + 0 ))",
    "$(which rm) -rf build",
    "f(){ rm -rf ~; }; f",
    "$'\\x72m' -rf build",
]

SHOULD_ALLOW = [
    "rm build.log",
    "rm -r build",
    "rm -R build",
    "rm --recursive build",
    "rm -f build.log",
    "rm --force build.log",
    "rm -i -r build",
    'echo "rm -rf /"',
    "grep -rf patterns.txt src/",
    "echo rm -rf | wc -c",
    "git rm -r --cached build",
    "rsync -rf src/ dst/",
    "rm -rd build",
    "rm -- -rf",
    "printf '%s' 'rm -rf build'",
    "cat <<EOF\nrm -rf build\nEOF",
    "cat <<'EOF'\n$(rm -rf build)\nEOF",
    "git commit -m 'drop rm -rf build from the docs'",
    "command -v rm",
    "ssh host",
    "$GREP -rf patterns.txt src/",
]


def _run_hook(command: str, cwd: str = _REPO_ROOT) -> dict | None:
    """Invoke the hook end-to-end; return its parsed JSON payload, or None if it deferred."""
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": cwd,
        }
    )
    result = subprocess.run(
        [sys.executable, _GUARD], input=payload, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"unexpected exit {result.returncode}: {result.stderr}"
    payload = json.loads(result.stdout) if result.stdout.strip() else {}
    return payload or None


def _decision(payload: dict | None) -> str | None:
    return payload["hookSpecificOutput"]["permissionDecision"] if payload else None


def _write(directory: str, name: str, text: str) -> None:
    with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
        handle.write(text)


def _check(condition: bool, label: str, failures: list[str]) -> None:
    if condition:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}")
        failures.append(label)


def main() -> int:
    failures: list[str] = []

    print("Detection — must block:")
    for command in SHOULD_BLOCK:
        invocations, _ = find_recursive_force_invocations(command)
        _check(bool(invocations), command, failures)

    print("\nDetection — must not block:")
    for command in SHOULD_ALLOW:
        invocations, _ = find_recursive_force_invocations(command)
        _check(not invocations, command, failures)

    print("\nTarget analysis:")
    with tempfile.TemporaryDirectory() as tmp:
        directory = os.path.join(tmp, "adir")
        os.mkdir(directory)
        open(os.path.join(directory, "child"), "w").close()
        file_path = os.path.join(tmp, "afile")
        open(file_path, "w").close()
        link = os.path.join(tmp, "alink")
        os.symlink(file_path, link)

        cases = [
            (file_path, "file", f"rm {file_path}"),
            (directory, "directory", f"rm -r {directory}"),
            (link, "symlink", f"rm {link}"),
            (os.path.join(tmp, "nope"), "missing", None),
            ("$UNSET/dist", "unresolved", None),
            ("build/*", "glob", None),
        ]
        for target, expected_kind, expected_safer in cases:
            analysis = analyze_target(target, tmp)
            _check(
                analysis.kind == expected_kind and analysis.safer_command == expected_safer,
                f"{target} -> kind={analysis.kind} safer={analysis.safer_command}",
                failures,
            )

        _check(analyze_target("/", tmp).severity == "critical", "/ is critical", failures)
        _check(
            analyze_target(os.path.expanduser("~"), tmp).severity == "critical",
            "~ is critical",
            failures,
        )
        _check(analyze_target("/usr", tmp).severity == "critical", "/usr is critical", failures)
        _check(
            analyze_target("..", directory).severity == "critical",
            ".. from cwd is critical (ancestor of cwd)",
            failures,
        )

    print("\nEnd-to-end hook protocol:")
    denied = _run_hook("rm -rf build")
    _check(
        denied is not None
        and denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        and denied["hookSpecificOutput"]["hookEventName"] == "PreToolUse",
        "rm -rf build -> permissionDecision: deny",
        failures,
    )
    _check(
        denied is not None and "AskUserQuestion" in denied["hookSpecificOutput"]["permissionDecisionReason"],
        "denial reason instructs AskUserQuestion",
        failures,
    )
    _check(
        denied is not None and "rm -r " in denied["hookSpecificOutput"]["permissionDecisionReason"],
        "denial reason suggests rm / rm -r",
        failures,
    )
    _check(_run_hook("rm -r build") is None, "rm -r build -> defer", failures)
    _check(_run_hook("ls -la") is None, "ls -la -> defer", failures)

    result = subprocess.run(
        [sys.executable, _GUARD],
        input=json.dumps({"tool_name": "Edit", "tool_input": {"new_text": "rm -rf /"}}),
        capture_output=True,
        text=True,
        check=False,
    )
    _check(
        result.returncode == 0 and json.loads(result.stdout or "{}") == {},
        "non-Bash tool -> defer",
        failures,
    )

    reason = (_run_hook("cd ~ && rm -rf .") or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    _check("entire home directory" in reason, "target analysis follows `cd` (cd ~ && rm -rf .)", failures)

    print("\nPackage scripts in the selected workspace (PR review):")
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "packages", "app"))
        os.makedirs(os.path.join(root, "packages", "lib"))
        os.makedirs(os.path.join(root, "sub"))
        _write(root, "package.json", json.dumps({"workspaces": ["packages/*"], "scripts": {"clean": "echo root"}}))
        _write(root, os.path.join("packages", "app", "package.json"),
               json.dumps({"name": "@me/app", "scripts": {"clean": "rm -rf ~/victim"}}))
        _write(root, os.path.join("packages", "lib", "package.json"),
               json.dumps({"name": "lib", "scripts": {"build": "tsc"}}))
        _write(root, os.path.join("sub", "package.json"), json.dumps({"scripts": {"x": "rm -rf ~/a"}}))
        for command in ("npm -w packages/app run clean", "npm --workspace=@me/app run clean",
                        "npm -ws run clean", "pnpm --filter @me/app clean", "yarn workspace @me/app clean",
                        "npm -w unknown-name run clean", "npm --prefix sub run x", "pnpm -C sub x"):
            decision = _decision(_run_hook(command, root))
            _check(decision == "ask", f"{command} -> {decision}", failures)
        for command in ("npm run clean", "npm -w lib run build"):
            decision = _decision(_run_hook(command, root))
            _check(decision is None, f"{command} -> {decision}", failures)

    print("\nCommands run from files the agent did not type here -> ask:")
    with tempfile.TemporaryDirectory() as project:
        _write(project, "clean.sh", "#!/usr/bin/env bash\nset -e\nrm -rf ~/stuff\n")
        _write(project, "notes.txt", "rm -rf ~\n")
        _write(project, "package.json", json.dumps({"scripts": {"prebuild": "rm -rf dist", "build": "tsc"}}))
        _write(project, "Makefile", "all: build\nbuild: clean\n\t@echo built\nclean:\n\t-rm -rf out\n")
        os.makedirs(os.path.join(project, "tools"))
        _write(project, os.path.join("tools", "wipe.sh"), "#!/bin/sh\nrm -rf ~/stuff\n")
        for command in ("./clean.sh", "bash clean.sh", "source clean.sh", "npm run build", "make", "make clean"):
            decision = _decision(_run_hook(command, project))
            _check(decision == "ask", f"{command} -> {decision}", failures)
        for command in ("bash < clean.sh", "sh -s -- --verbose < clean.sh", "cat clean.sh | bash"):
            decision = _decision(_run_hook(command, project))
            _check(decision == "ask", f"script on stdin (PR review): {command} -> {decision}", failures)
        _check(_decision(_run_hook("cd tools || cd other; ./wipe.sh", project)) == "ask",
               "script found in a directory a conditional cd may leave (PR review)", failures)
        os.makedirs(os.path.join(project, "sub"))
        _write(project, os.path.join("sub", "Makefile"), "clean:\n\trm -rf ~/victim\n")
        for command in ("make -Csub clean", "make --directory=sub clean", "make -fsub/Makefile clean",
                        "make --file=sub/Makefile clean"):
            decision = _decision(_run_hook(command, project))
            _check(decision == "ask", f"make attached/= options (PR review): {command} -> {decision}", failures)
        decision = _decision(_run_hook("bash -n < clean.sh", project))
        _check(decision is None, f"bash -n < clean.sh only checks syntax -> {decision}", failures)
        for command in ("cat notes.txt", "./notes.txt", "npm run lint"):
            decision = _decision(_run_hook(command, project))
            _check(decision is None, f"{command} -> {decision}", failures)
        appended = "printf 'rm -rf ~\\n' > x.sh; printf 'echo ok\\n' >> x.sh; bash x.sh"
        decision = _decision(_run_hook(appended, project))
        _check(decision == "deny", f"`>>` adds to the remembered script (PR review) -> {decision}", failures)
        decision = _decision(_run_hook("printf 'echo ok\\n' >> clean.sh; ./clean.sh", project))
        _check(decision == "ask", f"`>>` keeps the file's existing body -> {decision}", failures)
        decision = _decision(_run_hook("./clean.sh && rm -rf build", project))
        _check(decision == "deny", f"typed rm -rf beside a script still denies -> {decision}", failures)

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
