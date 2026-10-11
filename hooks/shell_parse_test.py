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
    ("echo a{b,c}d {x,{y,z}}! '{p,q}' \"{r\",s} {} {t}", [("echo", "abd", "acd", "x!", "y!", "z!", "{p,q}",
                                                       "{r,s}", "{}", "{t}")]),
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

    order = [c.name for c in parse("a $(b) && c $(d $(e))", "/").commands]
    _check(order == ["b", "a", "e", "d", "c"], f"substitutions run with their command, inner first: {order}", failures)

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
    argv = parse("X=r; Y='a b'; echo $X'm' \"$X\"m '$X'm $Y'' \"$Y\"", "/").commands[-1].argv
    _check(argv == ("echo", "rm", "rm", "$Xm", "a", "b", "a b"), f"quote boundaries and splitting: {argv}", failures)

    argv = parse("X=hello.tar.gz; echo ${X:1} ${#X} ${X/l/L} ${X//l/L} ${X^} ${X%%.*} ${X%.*} ${X#*.} "
                 "${X##*.} ${X: -2} ${X:1:-2} ${!X}", "/").commands[-1].argv
    _check(argv[1:] == ("ello.tar.gz", "12", "heLlo.tar.gz", "heLLo.tar.gz", "Hello.tar.gz", "hello",
                        "hello.tar", "tar.gz", "gz", "gz", "ello.tar.", "${!X}"),
           f"parameter operators on a known value: {argv}", failures)

    argv = [c.argv for c in parse('set -- a "b c"; echo "$@" "$*" $# $2; (set -- z); echo $1', "/").commands]
    _check(argv[1] == ("echo", "a", "b c", "a b c", "2", "b", "c") and argv[-1] == ("echo", "a"),
           f"positional parameters from set --: {argv}", failures)
    links = parse("ln -s /etc/hosts h; ln -st /tmp /a/b", "/w").links
    _check(links == {"/w/h": "/etc/hosts", "/tmp/b": "/a/b"}, f"links recorded: {links}", failures)

    argv = parse('set -- a b c; echo x"$@"y', "/").commands[-1].argv
    _check(argv == ("echo", "xa", "b", "cy"), f'"$@" inside a word: {argv}', failures)

    argv = [c.argv for c in parse(': ${X:=r} ${Y=a}; echo $X $Y; Z=k; : ${Z:=q}; echo $Z', "/").commands]
    _check(argv[1] == ("echo", "r", "a") and argv[-1] == ("echo", "k"), f"${{X:=word}} assigns: {argv}", failures)
    argv = [c.argv for c in parse('IFS=,; X=a,b; echo $X "$X"; IFS=: read A B <<< "c:d"; echo $A $B',
                                  "/").commands]
    _check(argv[0] == ("echo", "a", "b", "a,b") and argv[-1] == ("echo", "c", "d"),
           f"IFS splits expansions and read: {argv}", failures)
    argv = [c.argv for c in parse("alias a=b b='a x'; a; alias ll='ls -l'; ll; unalias ll; ll", "/").commands]
    _check(argv[1::2] == [("a", "x"), ("ls", "-l"), ("ll",)], f"aliases expand once, unalias removes: {argv}", failures)
    argv = [c.argv for c in parse("declare -u X; X=rm; declare -i N=5; echo $X $N", "/").commands]
    _check(argv[-1] == ("echo", "RM", "$N"), f"declare -u and -i: {argv}", failures)
    parsed = parse("f(){ local -n r=$1; r=rm; }; f X; echo $X", "/")
    _check(parsed.commands[-1].argv == ("echo", "rm") and not parsed.truncated,
           f"local -n in a function: {[c.argv for c in parsed.commands]}", failures)
    argv = parse("X=(a 'b c' $Y [5]=d); X+=(e); echo \"${X[1]}\" ${X[5]} ${X[6]} $X; X=(z); echo ${X[1]}",
                 "/").commands
    _check([c.argv for c in argv] == [("echo", "b c", "d", "e", "a"), ("echo", "${X[1]}")],
           f"compound array assignments: {[c.argv for c in argv]}", failures)
    argv = parse("X=a; X=b true; echo $X; X=c export X; echo $X", "/").commands
    _check(argv[1].argv == ("echo", "a") and argv[-1].argv == ("echo", "c"),
           f"prefix assignments: temporary, kept by export: {[c.argv for c in argv]}", failures)
    argv = [c.argv for c in parse("set -- a b c; shift 5; echo $#; shift 2; echo $# $1", "/").commands]
    _check(argv[2] == ("echo", "3") and argv[-1] == ("echo", "1", "c"), f"shift: {argv}", failures)
    argv = [c.argv for c in parse('read -N 3 A B <<< "a b c"; echo "$A" "[$B]"; read -n 3 A B <<< "a b c"; '
                                  'echo $A $B', "/").commands]
    _check(argv[1] == ("echo", "a b", "[]") and argv[-1] == ("echo", "a", "b"), f"read -N / -n: {argv}", failures)
    argv = [c.argv for c in parse("trap - EXIT; trap '' INT; trap -p; trap EXIT", "/").commands]
    _check(len(argv) == 4, f"trap resets, ignores and listings run nothing: {argv}", failures)
    argv = [c.argv for c in parse("X=(q w e); mapfile -O 1 -t X <<< z; echo ${X[0]} ${X[1]} ${X[2]}; "
                                  "mapfile -s 1 -n 1 -t Y < <(printf '1\\n2\\n3\\n'); echo ${Y[0]} ${Y[1]}", "/").commands]
    _check(argv[1] == ("echo", "q", "z", "e") and argv[-1] == ("echo", "2", "${Y[1]}"),
           f"mapfile -O / -s / -n / -t: {argv}", failures)
    argv = [c.argv for c in parse("set -- -rf -x val z; getopts rfx: O; echo $O $OPTIND; getopts rfx: O; "
                                  "echo $O $OPTIND; getopts rfx: O; echo $O $OPTARG $OPTIND; getopts rfx: O; "
                                  "echo $O $OPTIND; getopts :ab Q -c; echo $Q $OPTARG; OPTIND=1; getopts :ab Q -c; echo $Q $OPTARG",
                                  "/").commands if c.name == "echo"]
    _check(argv == [("echo", "r", "1"), ("echo", "f", "2"), ("echo", "x", "val", "4"), ("echo", "?", "4"),
                    ("echo", "?", "$OPTARG"), ("echo", "?", "c")], f"getopts as bash runs it: {argv}", failures)
    argv = [c.argv for c in parse("hash -p /bin/rm x; hash -r; x; f(){ :; }; hash -p /bin/rm f; f", "/").commands]
    _check(argv[2] == ("x",) and argv[-2] == ("f",), f"hash -r forgets, a function comes first: {argv}", failures)
    argv = [c.argv for c in parse("echo $((1+2)) ok; (( n++ )); ((cd /tmp; ls))", "/").commands]
    _check(argv == [("echo", "$((1+2))", "ok"), ("(( n++ ))",), ("cd", "/tmp"), ("ls",)],
           f"$((...)) closes at `))`; ((expr)) is arithmetic, ((cmd; cmd)) subshells: {argv}", failures)
    parsed = parse("X=abc; echo ${X@U} ${X@Q} ${X@P}", "/")
    _check(parsed.commands[0].argv == ("echo", "ABC", "'abc'", "${X@P}") and parsed.truncated,
           f"@ transformations; @P is not modelled: {parsed.commands[0].argv}", failures)
    argv = parse("X=(a 'b c' d); echo ${#X[@]} \"${X[@]}\" ${X[*]} ${X[1+1]} ${X[-1]}", "/").commands[-1].argv
    _check(argv == ("echo", "3", "a", "b c", "d", "a", "b", "c", "d", "d", "d"), f"array subscripts: {argv}", failures)
    argv = [c.argv for c in parse("X=echo; (exit); X=ls; exit; X=rm; echo $X", "/").commands]
    _check(argv == [("exit",), ("exit",)], f"exit ends the shell, ( exit ) only the subshell: {argv}", failures)
    argv = [c.argv for c in parse("read X 3<<< no <<< yes; echo $X; exec 3<<<a; exec 3<&-; read -u 3 Y; echo $Y",
                                  "/").commands if c.name == "echo"]
    _check(argv == [("echo", "yes"), ("echo", "$Y")], f"descriptors: 3<<< is not stdin; 3<&- closes: {argv}", failures)
    argv = [c.argv for c in parse("exec 3<<<$'a\\\\bcd\\nefg'; read -u3 -n2 X; read -u3 Y; read -u3 -N2 Z; echo $X $Y $Z",
                                  "/").commands if c.name == "echo"]
    _check(argv == [("echo", "ab", "cd", "ef")], f"read -n/-N take K characters, an escape counts once: {argv}", failures)
    argv = [c.argv for c in parse("X=a; declare -u X; echo $X; X+=b; echo $X; (declare -l X); X=c; echo $X; "
                                  "unset X; X=d; echo $X", "/").commands if c.name == "echo"]
    _check(argv == [("echo", "a"), ("echo", "AB"), ("echo", "C"), ("echo", "d")],
           f"declare -u converts later assignments; a subshell's attribute stays there: {argv}", failures)
    argv = [c.argv for c in parse("alias x=ls; false && alias x=pwd; x; f(){ id; }; (f(){ who; }); f; unset -f f; f",
                                  "/").commands if c.name not in ("alias", "false", "unset")]
    _check(("ls",) in argv and ("pwd",) in argv and ("who",) not in argv[-3:] and argv[-1] == ("f",),
           f"aliases and functions follow branches, subshells and unset -f: {argv}", failures)
    argv = parse("bash <(printf 'echo '; printf hi)", "/").commands[-1].argv
    _check(argv == ("echo", "hi"), f"process substitution output is one stream: {argv}", failures)

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
