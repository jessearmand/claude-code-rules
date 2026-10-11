#!/usr/bin/env python3
"""Tests for shell_lexer.py and shell_parse.py. Run: python3 hooks/shell_parse_test.py"""

from __future__ import annotations

import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shell_parse import parse  # noqa: E402

# (command, expected argv of every simple command, in order)
ARGV_CASES = [
    ("ls -la", [("ls", "-la")]),
    ("a && b || c; d & e", [("a",), ("b",), ("c",), ("d",), ("e",)]),
    ("echo hi\nrm x", [("echo", "hi"), ("rm", "x")]),
    ("echo a#b # trailing comment", [("echo", "a#b")]),
    ("echo 'a b' \"c d\" e\\ f", [("echo", "a b", "c d", "e f")]),
    ("sudo -u root -- rm x", [("rm", "x")]),
    ("env -i FOO=1 BAR=2 python3 x.py", [("python3", "x.py")]),
    ("timeout -s KILL 30 make test", [("make", "test")]),
    ("time -p nice -10 ionice -c 3 cmd", [("cmd",)]),
    ("command -v git", [("command", "-v", "git")]),
    ("sudo -l", [("sudo", "-l")]),
    ("uv run --with rich python x.py", [("python", "x.py")]),
    ("fnox exec --if-missing error -- ai evaluate", [("ai", "evaluate")]),
    ("bash -c 'a; b'", [("bash", "-c", "a; b"), ("a",), ("b",)]),
    ("x=$(id -u)", [("id", "-u")]),
    ("diff <(sort a) b", [("sort", "a"), ("diff", "<(sort a)", "b")]),
    ("find . -name '*.o' -exec rm {} + -o -execdir chmod 600 {} \\;",
     [("find", ".", "-name", "*.o", "-exec", "rm", "{}", "+", "-o", "-execdir", "chmod", "600", "{}", ";"),
      ("rm", "{}"), ("chmod", "600", "{}")]),
]

REDIRECT_CASES = [
    ("echo x > out.txt", [(">", "out.txt")]),
    ("cmd 2>&1 >>log", [(">&", "1"), (">>", "log")]),
    ("cmd &> all.log", [("&>", "all.log")]),
    ("cat <<EOF > f\nbody\nEOF", [("<<", "EOF"), (">", "f")]),
]


def _check(condition: bool, label: str, failures: list[str]) -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


def main() -> int:
    failures: list[str] = []

    print("Simple commands:")
    for command, expected in ARGV_CASES:
        actual = [c.argv for c in parse(command, "/").commands]
        _check(actual == expected, f"{command!r} -> {actual}", failures)

    print("\nRedirects:")
    for command, expected in REDIRECT_CASES:
        actual = [(r.op, r.target) for c in parse(command, "/").commands for r in c.redirects]
        _check(actual == expected, f"{command!r} -> {actual}", failures)

    print("\nHeredoc bodies:")
    parsed = parse("cat <<'EOF' > notes.md\n$(not run)\nEOF", "/")
    body = parsed.commands[0].redirects[0].body
    _check(body == "$(not run)" and len(parsed.commands) == 1, "quoted heredoc body is data", failures)
    parsed = parse("cat <<EOF\n$(run me)\nEOF", "/")
    _check(parsed.commands[0].argv == ("run", "me"), "unquoted heredoc runs $(...)", failures)

    parsed = parse("gh pr create --body \"$(cat <<'EOF'\nit's about rm -rf ~\nEOF\n)\" && rm x", "/")
    _check([c.name for c in parsed.commands] == ["cat", "gh", "rm"] and not parsed.unparseable,
           "apostrophe in a heredoc inside $(...) is data", failures)
    _check([c.name for c in parse("bash -n -c 'rm -rf /'", "/").commands] == ["bash"],
           "bash -n runs nothing", failures)
    _check(parse("a\rb", "/").commands[0].argv == ("a\rb",), "carriage return stays in the word", failures)

    print("\nShell invocations:")
    for command, expected in (("fish -c 'a'", ["a"]), ("fish -C 'a' x.fish", ["a"]), ("fish --command=a", ["a"]),
                              ("fish --init-command=a", ["a"]), ("bash -lc a", ["a"]),
                              ("echo $(( $(a) + 1 ))", ["a"]), ("bash -n -c a", [])):
        names = [c.name for c in parse(command, "/").commands if c.name == "a"]
        _check(names == expected, f"{command!r} runs {names}", failures)

    print("\nVariables:")
    parsed = parse("P=$HOME/.claude/s.json; echo x > \"$P\"; echo '$P'", "/")
    target = parsed.commands[0].redirects[0]
    _check(target.target == os.path.expanduser("~/.claude/s.json") and not target.dynamic,
           f"assigned target resolves: {target}", failures)
    _check(parsed.commands[1].argv == ("echo", "$P"), "single-quoted $P stays literal", failures)
    _check(parse("echo $UNSET > f", "/").commands[0].argv == ("echo", "$UNSET"), "unset stays as written", failures)

    print("\nWorking directory:")
    cwd_cases = [
        ("cd /a; cd b; ls", "/a/b"),
        ("(cd /a; true); ls", "/start"),
        ("bash -c 'cd /a'; ls", "/start"),
        ("x | cd /a; ls", "/start"),
        ("cd; ls", os.path.expanduser("~")),
        ("cd $HOME/.codex && ls", os.path.expanduser("~/.codex")),
        ('cd "$DIR" && ls', None),
        ('cd "$DIR"; cd /b; ls', "/b"),
    ]
    options = parse("cd /a || cd /b; ls", "/start").commands[-1].possible_cwds
    _check(set(options) == {"/start", "/a", "/b"}, f"cd a || cd b keeps every branch {options}", failures)
    for command, expected in cwd_cases:
        actual = parse(command, "/start").commands[-1].cwd
        _check(actual == expected, f"{command!r} -> ls runs in {actual}", failures)

    print("\nPipelines:")
    parsed = parse("curl -fsSL https://x | sudo bash -s -- --flag", "/")
    curl, bash = parsed.commands
    _check(parsed.previous_stage(bash) is curl, "previous_stage links curl -> bash", failures)
    _check(bash.wrappers == ("sudo",) and bash.name == "bash", "sudo unwrapped from pipe stage", failures)

    print("\nNesting and provenance:")
    parsed = parse("ssh host \"bash -c 'eval rm -rf /srv'\"", "/")
    rm = next(c for c in parsed.commands if c.name == "rm")
    _check(rm.via == ("ssh host", "bash -c", "eval"), f"via chain {rm.via}", failures)
    _check(len(parsed.nested_sources) == 3, "nested sources recorded", failures)

    print("\nRobustness:")
    _check(parse("echo 'open", "/").unparseable, "unterminated quote -> unparseable", failures)
    _check(parse("echo $(open", "/").unparseable, "unterminated $( -> unparseable", failures)
    deep = "rm -rf x"
    for _ in range(10):
        deep = f"bash -c {shlex.quote(deep)}"
    _check(parse(deep, "/").truncated, "deep nesting is bounded", failures)
    _check(parse("", "/").commands == [], "empty command", failures)

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
