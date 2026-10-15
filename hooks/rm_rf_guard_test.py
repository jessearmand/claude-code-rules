#!/usr/bin/env python3
"""Tests for rm_rf_guard.py. Run: python3 hooks/rm_rf_guard_test.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shell_sources  # noqa: E402
from rm_rf_guard import analyze_target, find_recursive_force_invocations  # noqa: E402

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_GUARD = os.path.join(_HOOKS_DIR, "rm_rf_guard.py")
# A real project directory to stand in for the session cwd: this repo's root, so the
# end-to-end cases resolve relative targets the same way on any machine or checkout.
_REPO_ROOT = os.path.dirname(_HOOKS_DIR)

SHOULD_BLOCK = [
    "BASH_ENV=<(printf 'rm -rf victim') bash -c :",
    "ruby -e '%x(rm -rf victim)'",
    "node --eval='require(\"child_process\").execSync(\"rm -rf victim\")'",
    "source -- <(printf 'rm -rf victim')",
    ". -- <(printf 'rm -rf victim')",
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
    "strace -o /dev/null rm -rf victim",
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
    # Branches keep every value; pipeline stages are subshells (PR review).
    "X=rm || X=echo; $X -rf ~/victim",
    "X=rm; X=echo | cat; $X -rf ~/victim",
    "X=rm; true && X=echo; $X -rf ~/victim",
    # Assignments after a wrapper; a shell reading /dev/stdin (PR review).
    "env X=rm sh -c '$X -rf ~/victim'",
    "sudo X=rm sh -c '$X -rf ~/victim'",
    "bash /dev/stdin <<< 'rm -rf ~/victim'",
    "sh /dev/fd/0 <<EOF\nrm -rf ~/victim\nEOF",
    # Process substitution as the script; brace expansion (PR review).
    "source <(printf 'rm -rf ~/victim')",
    ". <(echo rm -rf ~/victim)",
    "bash <(printf 'rm -rf ~/victim')",
    "bash < <(echo 'rm -rf ~/victim')",
    "{rm,-rf,$HOME/victim}",
    # Quote boundaries inside a word (PR review).
    "X=r; $X'm' -rf victim",
    "X=r; \"$X\"m -rf victim",
    "X='rm -rf victim'; $X''",
    # Parameter operators on known values; xargs input; brace overflow (PR review).
    "X=rm; ${X:0:2} -rf victim",
    "X=RM; ${X,,} -rf victim",
    "X=rmdir; ${X%dir} -rf victim",
    "printf '%s\\n' '-rf victim' | xargs rm",
    "echo -rf victim | xargs rm",
    "printf 'rm -rf victim\\n' | xargs -I{} sh -c '{}'",
    "rm {" + ",".join(["x"] * 70) + ",-rf} victim",
    # Every possible value is kept; background commands are subshells (PR review).
    "X=rm; " + "; ".join(f"true || X=echo{i}" for i in range(10)) + "; $X -rf victim",
    "X=rm; X=echo & $X -rf victim",
    "X=CMD; CMD=rm; ${!X} -rf victim",  # indirect expansion (PR review)
    "printf -v X rm; $X -rf victim",
    "function cleanup { rm -rf victim; }; cleanup",
    # Pathname expansion of the command word; positional parameters (PR review).
    "/bin/r[m] -rf victim",
    "set -- rm -rf victim; \"$@\"",
    "set -- x rm; ${2} -rf victim",
    "read X <<< rm; $X -rf victim",  # read from a here-string (PR review)
    # Loop variables; function bodies apply their effects when called (PR review).
    "for X in rm; do \"$X\" -rf victim; done",
    "X=rm; function f { X=echo; }; $X -rf victim",
    "X=echo; f() { X=rm; }; f; $X -rf victim",
    # A call runs the body with its arguments; read from <(...) (PR review).
    "f(){ \"$1\" -rf victim; }; f rm",
    "function g { \"$@\"; }; g rm -rf victim",
    "read X < <(echo rm); \"$X\" -rf victim",
    "npm exec -c 'rm -rf victim'",  # npm exec command strings (PR review)
    "read -a X <<< r; \"${X[0]}m\" -rf victim",  # arrays from read -a; $@ inside a word (PR review)
    "set -- r; \"$@\"m -rf victim",
    "set -- m x; r\"$@\" -rf victim",  # "$@" keeps one word per parameter (PR review)
    "read X <<<'r\\m'; \"$X\" -rf victim",  # read strips backslashes without -r (PR review)
    "X=echo; f(){ local X; \"${X:-r}m\" -rf victim; }; f",  # bare local is unset
    "X=$(printf %b '\\162\\155'); \"$X\" -rf victim",  # printf escapes
    ": ${X:=r}; \"${X}m\" -rf victim",  # ${X:=word} assigns (PR review)
    "echo ${X:=r}; \"$X\"m -rf victim",
    "IFS=x; read A B <<< rxm; \"$A$B\" -rf victim",  # read splits by IFS (PR review)
    "IFS=, read A B <<< r,m; \"$A$B\" -rf victim",
    "IFS=,; X=rm,-rf,victim; $X",  # word splitting by IFS
    "set -- r m; IFS=; \"$*\" -rf victim",  # "$*" joins with IFS's first character
    "bash < <(printf r; printf 'm -rf victim')",  # producers write one stream (PR review)
    "source <(printf 'cd /; r'; printf 'm -rf victim')",
    "bash -O expand_aliases -c $'alias x=\"/bin/r[m]\"\\nx -rf victim'",  # aliases (PR review)
    "alias x='rm -rf'; x victim",
    "alias x='cd / && rm'; x -rf victim",
    "ln -s /bin/rm x; ./x -rf victim",  # a link or copy run as a program (PR review)
    "cp /bin/rm x; ./x -rf victim",
    "install -m 755 /bin/rm y; ./y -rf victim",
    "X=r; X+=m; declare -n Y=X; \"$Y\" -rf victim",  # declare attributes (PR review)
    "declare -l X=RM; $X -rf victim",
    "f(){ local -n r=$1; r=rm; }; f X; $X -rf victim",
    "X=(r); X[0]+=m; \"${X[0]}\" -rf victim",  # compound array assignments (PR review)
    "X=(echo); X+=(rm); ${X[1]} -rf victim",
    "declare -a X=([3]=rm); ${X[3]} -rf victim",
    "X=r export X; X+=m; \"$X\" -rf victim",  # prefix assignments kept by export (PR review)
    "X=r :; X+=m; \"$X\" -rf victim",  # and by any special builtin in POSIX shells
    "set -- echo r; shift; \"$1\"m -rf victim",  # shift moves the parameters (PR review)
    "read -n 1 X <<< rx; \"${X}m\" -rf victim",  # read -n/-N limits (PR review)
    "read -rN2 X <<< rmx; \"$X\" -rf victim",
    "trap \"$(printf %b '\\162\\155 -rf victim')\" EXIT",  # trap handlers run later (PR review)
    "mapfile -C \"$(printf %b '\\162\\155 -rf victim #')\" -c 1 < <(printf x)",  # mapfile callbacks
    "readarray -t -C'rm -rf victim #' A < /dev/null",
    "read X <<'EOF'\nr\\\nm\nEOF\n\"$X\" -rf victim",  # backslash-newline joins lines (PR review)
    "$'\\u0072m' -rf victim",  # ANSI-C Unicode escapes (PR review)
    "$'\\U00000072m' -rf victim",
    "mapfile -t X < <(printf r; printf m); \"${X[0]}\" -rf victim",  # mapfile fills its array (PR review)
    "readarray -t X <<< rm; ${X[0]} -rf victim",
    "set -- -r; getopts r X; \"$X\"m -rf victim",  # getopts sets its variable (PR review)
    "hash -p /bin/r[m] x; x -rf victim",  # hash -p names a program (PR review)
    "X=echo; f(){ X=rm; return; X=echo; }; f; \"$X\" -rf victim",  # nothing after return runs (PR review)
    "X=echo; f(){ X=rm; true && return; X=ls; }; f; \"$X\" -rf victim",
    "X=rm; trap 'X=echo' EXIT; \"$X\" -rf victim",  # a trap handler runs later (PR review)
    "X=echo; trap '\"$X\" -rf victim' EXIT; X=rm",  # it sees the state the shell exits with (PR review)
    "X=echo; f(){ X=rm; if true; then return; fi; X=echo; }; f; \"$X\" -rf victim",  # return after `then`
    "X=echo; f(){ X=rm; command return; X=echo; }; f; \"$X\" -rf victim",
    "X=echo; source <(printf 'X=rm\\nreturn\\nX=echo\\n'); \"$X\" -rf victim",  # return ends a sourced script
    "X=echo; f(){ g(){ return; }; X=rm; }; f; \"$X\" -rf victim",  # a nested declaration's return is its own
    "CMD=$'\\x72m'; X='a[$(\"$CMD\" -rf victim)]'; echo \"$((X))\"",  # arithmetic evaluates X (PR review)
    "X='a[$(rm -rf victim)]'; (( X ))",
    "X='\\x72\\x6d'; \"${X@E}\" -rf victim",  # ${X@E} expands escapes (PR review)
    "X=RM; ${X@L} -rf victim",
    "shopt -s xpg_echo; bash < <(echo '\\0162\\0155 -rf victim')",  # xpg_echo (PR review)
    "shopt -s xpg_echo; X=$(echo '\\0162\\0155'); $X -rf victim",
    "sh -c \"echo '\\0162\\0155 -rf victim' | sh\"",  # sh's echo expands escapes
    "sqlite3 db <<< '.system rm -rf victim'",  # sqlite3 .shell/.system (PR review)
    "printf '.shell rm -rf victim\\n' | sqlite3 :memory:",
    "printf '.shell rm -rf victim\\n' | cat | sqlite3 :memory:",
    "printf '\\\\! rm -rf victim\\n' | psql",
    "psql -c'\\! rm -rf victim'",  # attached option value (PR review)
    "git clone 'ext::sh -c rm% -rf% victim' out",  # git-remote-ext runs the URL's command (PR review)
    "printf '.shell rm -rf victim\\n' > i.sql; sqlite3 -init i.sql :memory:",  # written here, then run
    "printf '.shell rm -rf victim\\n' > r.sql; sqlite3 :memory: '.read r.sql'",  # an included file (PR review)
    "printf '\\\\! rm -rf victim\\n' > r.sql; psql -c '\\i r.sql'",
    "printf 'system rm -rf victim\\n' > r.sql; mysql -e 'source r.sql'",
    "sqlcmd -Q '!! rm -rf victim'",  # sqlcmd and cockroach shell escapes
    "cockroach sql -e '\\| rm -rf victim'",
    "shopt -s lastpipe; printf r | read X; \"${X}m\" -rf victim",  # lastpipe (PR review)
    "read X <<<echo <<< r''m; \"$X\" -rf victim",  # the last stdin redirect wins (PR review)
    "X=r''m; read -t0 X <<<echo; \"$X\" -rf victim",  # read -t 0 assigns nothing (PR review)
    "X=rm; read -t \"$T\" X <<<echo; $X -rf victim",
    "X=echo; trap '\"$X\" -rf victim' EXIT; X=r; X+=m; exit; X=echo",  # exit ends the shell (PR review)
    "f(){ false && exit; }; f; rm -rf victim",
    "sqlite3 :memory: '.read \"|rm -rf victim\"'",  # a `|CMD` file name runs CMD (PR review)
    "sqlite3 app.db '.once |rm -rf victim'",
    "psql -c '\\! rm -rf victim'",  # psql's shell escape (PR review)
    "exec 3<<<'rm -rf victim'; bash <&3",  # a shell reading a descriptor exec opened
    "X=(r m); \"${X[0]}${X[1&1]}\" -rf victim",  # any bash arithmetic in a subscript (PR review)
    "printf -v X %q r''m; \"$X\" -rf victim",  # printf %q (PR review)
    "X=rM; \"${X,,M}\" -rf victim",  # patterned case conversion (PR review)
    "printf r > cmd; printf m >> cmd; \"$(cat cmd)\" -rf victim",  # cat of a file the line wrote (PR review)
    "X=r; X+=m; \"${X/m/&}\" -rf victim",  # `&` is the matched text (PR review)
    "readonly X=$'\\x72m'; printf -v X %s echo; \"$X\" -rf victim",  # readonly keeps its value (PR review)
    "X=rM; \"${X,,[[:upper:]]}\" -rf victim",  # bash character classes (PR review)
    "hash -p /bin/rm x; x(){ echo ok; }; command x -rf victim",  # command skips functions (PR review)
    "shopt -s extglob; X=rM; \"${X,,+([[:upper:]])}\" -rf victim",  # extglob patterns (PR review)
    "X=rm; declare -n R=X; declare -r R; printf -v R %s echo; \"$X\" -rf victim",  # attributes via a nameref
    "X=foo; declare -n R=X; declare -l R; R=RM; \"$X\" -rf victim",
    "touch -- -rf; rm -* victim",  # a glob sees files made earlier on the line (PR review)
    "PROMPT_COMMAND='rm -rf victim' bash --noprofile --norc -i <<< exit",  # interactive bash (PR review)
    "X=echo; f(){ declare -g X=rm; }; f; \"$X\" -rf victim",  # declare -g sets the global (PR review)
    "X=echo; trap 'X=rm' DEBUG; \"$X\" -rf victim",  # DEBUG runs before each command (PR review)
    "X=echo; trap 'X=rm' USR1; kill -USR1 $$; \"$X\" -rf victim",  # a signal handler's assignments
    "mkdir -- -rf; rm -* victim",
    "X=; f(){ X+=$2; }; mapfile -t -C f -c 1 A <<< $'r\\nm'; \"$X\" -rf victim",  # -C per line (PR review)  # an exit that may not run ends nothing (PR review)
    "X=echo; trap '\"$X\" -rf victim' USR1; X=r; X+=m; kill -USR1 $$; X=echo",  # signal traps (PR review)
    "X=r; X+=m; coproc X=echo; \"$X\" -rf victim",  # coproc runs on its own (PR review)
    "X=r; X+=m; coproc name { X=echo; }; \"$X\" -rf victim",
    "X=(r m); ${X[-2]}${X[-1]} -rf victim",  # negative and arithmetic subscripts (PR review)
    "i=1; X=(a rm); ${X[i]} -rf victim",
    "X=(rm -rf victim); \"${X[@]}\"",  # every element as its own word
    "exec 3<<<$'\\x72m'; read -u 3 X; \"$X\" -rf victim",  # descriptors exec opened (PR review)
    "exec 3<<<$'echo\\nrm'; read -u 3 A; read -u 3 B; \"$B\" -rf victim",
    "exec 3<<<$'erm\\necho\\n'; read -u3 -n1 D; read -u3 X; \"$X\" -rf victim",  # -n K takes K characters
    "exec 3<<<$'e\\\\rm\\n'; read -u3 -N1 D; read -u3 X; \"$X\" -rf victim",
    # Definitions in a branch that may not run, or in a subshell (PR review).
    "X=r''m; declare -u X; \"$X\" -rf victim",  # -u converts on assignment only
    "hash -p /bin/r''m x; false && hash -p /bin/echo x; x -rf victim",
    "exec 3<<<r''m; false && exec 3<<<echo; read -u3 X; \"$X\" -rf victim",
    "shopt -s expand_aliases; alias x=rm; false && alias x=echo; x -rf victim",
    "shopt -s expand_aliases; alias x=rm; (alias x=echo); x -rf victim",
    "f(){ rm \"$@\"; }; false && f(){ echo; }; f -rf victim",
    "f(){ rm \"$@\"; }; (f(){ echo; }); f -rf victim",
    "declare -n R=X; false && declare -n R=Y; X=rm; Y=echo; \"$R\" -rf victim",
    "X[0]=r; X[0]+=m; f(){ local X; X[0]=echo; }; f; \"${X[0]}\" -rf victim",  # locals restore elements
    "X=rm; f(){ local -u X; X=echo; }; f; $X -rf victim",
    "exec 3<<<$'echo\\nr'$'m\\n'; exec 4<&3; read -u4 A; read -u3 X; \"$X\" -rf victim",  # shared offset
    "exec 3<<<$'echo\\nrm\\n'; exec 4<&3 5<&3; exec 3<&-; read -u4 A; read -u5 X; \"$X\" -rf victim",
    "exec <<< rm; read X; $X -rf victim",
    "exec 4<<<rm; read X <&4; $X -rf victim",
    "X[0]=echo; read 'X[0]' <<< rm; \"${X[0]}\" -rf victim",  # read into an element (PR review)
    "R=r; M=m; X[0]=echo; unset 'X[0]'; \"${X[0]:-$R$M}\" -rf victim",  # unset an element (PR review)
    "X=(echo ls); unset X; \"${X[1]:-rm}\" -rf victim",
    "X='a[$(rm -rf victim)]'; Y=X; let Y+1",
    "printf -v 'X[0]' %b '\\162\\155'; \"${X[0]}\" -rf victim",  # printf -v into an element (PR review)
    "X=r; X+=m; f(){ local X=echo; }; f; \"$X\" -rf victim",  # local restored on return
    "X[1]=rm; ${X[1]} -rf victim",
    "printf 'rm\\n' > q; read X < q; \"$X\" -rf victim",  # read from a file written on the line
    "npm x --call='rm -rf victim'",
    "$(printf rm) -rf victim",  # literal command substitution (PR review)
    "X=rm; true || unset X; $X -rf victim",
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
    # While `set -x` traces, bash expands $PS4 before each command (PR review).
    "PS4='$(rm -rf victim)'; set -x; true",
    "set -o xtrace; PS4='`rm -rf victim`'; ls",
    "PS4='$(rm -rf victim)'; set -ex; set +x",  # `set +x` is itself traced
    "PS4='$(rm -rf victim)' bash -xc true",
]

SHOULD_ALLOW = [
    "PS4='$(rm -rf victim)'; true",  # PS4 is only expanded while tracing
    "PS4='$(rm -rf victim)' bash -c true",
    "PS4='+ ${LINENO}: '; set -x; ls",
    "bash <<< 'rm -rf victim' <<< :",  # only the last stdin redirect is read (PR review)
    "read -r X <<<'r\\m'; \"$X\" -rf victim",
    "npm exec -- eslint .",
    "pnpm --dir . exec sh -c 'echo hi'",
    "f(){ \"$1\" -rf victim; }; f echo",
    "f(){ f; }; f",  # recursion stops
    "X=echo; f() { X=rm; }; $X -rf victim",  # declared, never called
    "printf '%s\\n' a.txt b.txt | xargs rm",
    "mkdir -p src/{lib,bin} && echo '{rm,-rf,x}'",  # quoted braces stay one word
    "source <(kubectl completion bash)",
    "X=echo; env X=rm true; $X -rf build",  # env's assignment is for `true` only
    "X=rm; X=echo; $X -rf build",  # the later assignment always runs
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


def _run_hook(command: str, cwd: str = _REPO_ROOT, mode: str | None = None) -> dict | None:
    """Invoke the hook end-to-end; return its parsed JSON payload, or None if it deferred."""
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": cwd,
            **({"permission_mode": mode} if mode else {}),
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


# The start of the reason for an `rm -rf` typed in the command (not run from a file).
_TYPED = "this command runs `rm` with both recursive and force"


def _reason(payload: dict | None) -> str:
    return payload["hookSpecificOutput"]["permissionDecisionReason"] if payload else ""


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

    with tempfile.TemporaryDirectory() as tmp:
        # A Makefile written earlier in the same line is what make runs (PR review).
        command = "cat > Makefile <<'EOF'\nx:\n\trm -rf victim\nEOF\nmake x"
        invocations, _ = find_recursive_force_invocations(command, tmp, shell_sources.resolve)
        _check(bool(invocations) and not invocations[0].indirect, "same-line Makefile recipe", failures)
        invocations, _ = find_recursive_force_invocations("make --eval='x:;rm -rf victim' x", tmp,
                                                          shell_sources.resolve)
        _check(bool(invocations) and not invocations[0].indirect, "make --eval recipe", failures)
        with open(os.path.join(tmp, "latin.sh"), "wb") as handle:
            handle.write(b"#!/bin/sh\n# caf\xe9\nrm -rf victim\n")
        os.chmod(os.path.join(tmp, "latin.sh"), 0o755)
        invocations, _ = find_recursive_force_invocations("./latin.sh", tmp, shell_sources.resolve)
        _check(bool(invocations), "script with a non-UTF-8 byte is still read", failures)
        # A package.json written earlier in the line is what npm runs (PR review).
        command = "cat > package.json <<'EOF'\n{\"scripts\":{\"boom\":\"rm -rf victim\"}}\nEOF\nnpm run boom"
        invocations, _ = find_recursive_force_invocations(command, tmp, shell_sources.resolve)
        _check(bool(invocations) and not invocations[0].indirect, "same-line package.json script", failures)
        # A command-word glob with more matches than are listed still runs its first match.
        os.mkdir(os.path.join(tmp, "bin"))
        os.symlink("/bin/rm", os.path.join(tmp, "bin", "rm"))
        for i in range(70):
            open(os.path.join(tmp, "bin", f"s{i}"), "w").close()
        invocations, _ = find_recursive_force_invocations("bin/[r-z]* -rf victim", tmp, shell_sources.resolve)
        _check(bool(invocations), "overflowing command-word glob keeps its first match", failures)
        # `source FILE ARGS` binds ARGS as the file's $1..$n (PR review).
        with open(os.path.join(tmp, "positional.sh"), "w") as handle:
            handle.write('"$1" -rf victim\n')
        invocations, _ = find_recursive_force_invocations("source positional.sh rm", tmp, shell_sources.resolve)
        _check(bool(invocations), "source binds its arguments", failures)
        invocations, _ = find_recursive_force_invocations("source positional.sh echo", tmp, shell_sources.resolve)
        _check(not invocations, "source with harmless arguments", failures)
        for builtin in ("source", "."):
            for argument, expected in (("rm", True), ("echo", False)):
                command = f"{builtin} -- positional.sh {argument}"
                invocations, _ = find_recursive_force_invocations(command, tmp, shell_sources.resolve)
                _check(bool(invocations) == expected, f"source terminator binds its arguments: {command}", failures)
        # Appends join byte for byte: no newline is added between printf outputs (PR review).
        command = "printf r > run.sh; printf m >> run.sh; printf ' -rf victim' >> run.sh; bash run.sh"
        invocations, _ = find_recursive_force_invocations(command, tmp, shell_sources.resolve)
        _check(bool(invocations), "script built from exact printf bytes", failures)

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
    # Too many possible values to pair every one: the `rm` asks (PR review).
    overflow = ("A=rm; B=-rf; " + "; ".join(f"true || A=a{i}" for i in range(8)) + "; "
                + "; ".join(f"true || B=b{i}" for i in range(7)) + "; $A $B victim")
    asked = _run_hook(overflow)
    _check(asked is not None and asked["hookSpecificOutput"]["permissionDecision"] == "ask",
           "rm with uncheckable argument combinations -> ask", failures)
    asked = _run_hook("rm -rf build")
    _check(
        asked is not None
        and asked["hookSpecificOutput"]["permissionDecision"] == "ask"
        and asked["hookSpecificOutput"]["hookEventName"] == "PreToolUse",
        "rm -rf build -> permissionDecision: ask",
        failures,
    )
    _check(
        asked is not None and "do NOT route around this hook" in asked["hookSpecificOutput"]["permissionDecisionReason"],
        "the reason says not to work around a declined removal",
        failures,
    )
    _check(
        asked is not None and "rm -r " in asked["hookSpecificOutput"]["permissionDecisionReason"],
        "the reason suggests rm / rm -r",
        failures,
    )
    _check(_decision(_run_hook("rm -rf build", mode="bypassPermissions")) == "deny",
           "rm -rf build when no one can be asked -> deny", failures)
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
        # A script's arguments are its $1.. and Make's `$@` is the target (PR review).
        _write(project, "run.sh", '#!/bin/sh\n"$1" -rf victim\n')
        os.makedirs(os.path.join(project, "auto"))
        _write(project, os.path.join("auto", "Makefile"), "rm:; $@ -rf victim\nout/x: a b\n\techo $(@D) $^ $$HOME\n")
        for command in ("bash run.sh rm", "./run.sh rm", "sh -x run.sh rm", "make -C auto rm"):
            decision = _decision(_run_hook(command, project))
            _check(decision == "ask", f"arguments reach the script: {command} -> {decision}", failures)
        for command in ("bash run.sh echo", "make -C auto out/x"):
            decision = _decision(_run_hook(command, project))
            _check(decision is None, f"{command} -> {decision}", failures)
        # Make runs `$(shell …)` and `!=` while reading the Makefile, whatever the
        # target, and recipes run what their variables hold (PR review).
        expansions = {
            "shell": "X := $(shell rm -rf victim)\nall:\n\t@echo hi\n",
            "braces": "X = ${shell rm -rf victim}\nall:\n\t@echo hi\n",
            "bang": "X != rm -rf victim\nall:\n\t@echo hi\n",
            "conditional": "ifeq ($(shell rm -rf victim),)\nendif\nall:\n\t@echo hi\n",
            "variable": "CLEAN = rm -rf\nTARGET := victim\nall:\n\t$(CLEAN) $(TARGET)\n",
            "nested": "A = $(B)\nB = rm -rf victim\nall:\n\t$(A)\n",
            "appended": "FLAGS = -r\nFLAGS += -f\nall:\n\trm ${FLAGS} victim\n",
            "recipe-shell": "all:\n\techo $(shell rm -rf victim)\n",
        }
        for name, text in expansions.items():
            os.makedirs(os.path.join(project, f"mk-{name}"))
            _write(project, os.path.join(f"mk-{name}", "Makefile"), text)
            decision = _decision(_run_hook(f"make -C mk-{name}", project))
            _check(decision == "ask", f"make expansion '{name}' -> {decision}", failures)
        # A `$$` is the shell's `$`, not a Make reference; unknown variables stay as written.
        for name, text in {"escaped": "all:\n\techo $$(shell rm -rf victim) $(UNSET)\n",
                           "safe-shell": "REV := $(shell git rev-parse HEAD)\nall:\n\techo $(REV)\n"}.items():
            os.makedirs(os.path.join(project, f"mk-{name}"))
            _write(project, os.path.join(f"mk-{name}", "Makefile"), text)
            decision = _decision(_run_hook(f"make -C mk-{name}", project))
            _check(decision is None, f"make expansion '{name}' -> {decision}", failures)
        decision = _decision(_run_hook("bash -n < clean.sh", project))
        _check(decision is None, f"bash -n < clean.sh only checks syntax -> {decision}", failures)
        # `source NAME` looks NAME up in PATH first (PR review).
        os.makedirs(os.path.join(project, "bin"))
        _write(os.path.join(project, "bin"), "cleanup_guard_test", "rm -rf victim\n")
        decision = _decision(_run_hook(f"PATH={project}/bin:$PATH; source cleanup_guard_test", project))
        _check(decision == "ask", f"source NAME found in PATH -> {decision}", failures)
        # A non-interactive bash runs $BASH_ENV first (PR review).
        decision = _decision(_run_hook("printf %b '\\162\\155 -rf victim\\n' > env.sh; "
                                       "BASH_ENV=\"$PWD/env.sh\" bash -c :", project))
        _check(decision == "ask", f"BASH_ENV startup file -> {decision}", failures)
        decision = _decision(_run_hook("printf %b '\\162\\155 -rf victim\\n' > env.sh; "
                                       "BASH_ENV='$PWD/env.sh' bash -c :", project))
        _check(decision == "ask", f"bash expands BASH_ENV itself (PR review) -> {decision}", failures)
        _write(project, "evilrc", "rm -rf victim\n")
        _write(project, "saferc", "echo ok\n")
        for command, expected in (
            ("bash --rcfile evilrc -i <<<'exit'", "ask"),
            ("bash --init-file evilrc -ic ':'", "ask"),
            ("printf 'rm -rf victim' > typedrc; bash --rcfile typedrc -i <<<'exit'", "ask"),
            ("bash --rcfile saferc -i <<<'exit'", None),
            ("bash --rcfile evilrc --init-file saferc -i <<<'exit'", None),
            ("bash --rcfile evilrc --norc -i <<<'exit'", None),
            ("bash --rcfile evilrc -c ':'", None),
            ("bash --rcfile evilrc --posix -i <<<'exit'", None),
            ("bash --rcfile evilrc -li <<<'exit'", None),
        ):
            decision = _decision(_run_hook(command, project))
            _check(decision == expected, f"explicit Bash rc: {command} -> {decision}", failures)
        for option in ("--rcfile", "--init-file"):
            invocations, _ = find_recursive_force_invocations(
                f"printf 'rm -rf victim' > typedrc; bash {option} typedrc -i <<<'exit'",
                project, shell_sources.resolve)
            _check(bool(invocations) and not invocations[0].indirect,
                   f"{option} uses the same-line file snapshot as typed code", failures)
        _write(project, "largerc", "#" + "x" * (256 * 1024) + "\nrm -rf victim\n")
        from shell_parse import parse
        parsed = parse("bash --rcfile largerc -i <<<'exit'", project, shell_sources.resolve)
        _check(parsed.truncated, "oversized explicit rc file marks analysis incomplete", failures)
        os.makedirs(os.path.join(project, "safe"))
        _write(os.path.join(project, "safe"), "cleanup_guard_test", "echo ok\n")
        decision = _decision(_run_hook("PATH=bin; false && PATH=safe; source cleanup_guard_test", project))
        _check(decision == "ask", f"source searches every PATH the line may have (PR review) -> {decision}", failures)
        decision = _decision(_run_hook("cd safe; trap 'source ./cleanup_guard_test' TERM; cd ../bin; kill -TERM $$",
                                       project))
        _check(decision == "ask", f"a signal handler is checked in the directory it runs in (PR review) -> {decision}",
               failures)
        # Database clients run shell escapes from command files too (PR review).
        _write(project, "init.sql", ".shell rm -rf victim\n")
        _write(project, "safe.sql", "SELECT 1;\n")
        _write(project, "q.sql", "\\! rm -rf victim\n")
        for command in ("sqlite3 -init init.sql :memory:", "sqlite3 --init init.sql :memory:", "sqlite3 :memory: < init.sql",
                        "cat init.sql | cat | sqlite3 :memory:",
                        "psql -f q.sql", "psql --file=q.sql", "psql -fq.sql", "psql < q.sql"):
            decision = _decision(_run_hook(command, project))
            _check(decision == "ask", f"shell escape in a command file: {command} -> {decision}", failures)
        # ... and from the files those include, followed in turn (PR review).
        _write(project, "outer.sql", ".read init.sql\n")
        _write(project, "safe-outer.sql", ".read safe.sql\n")
        for command in ("sqlite3 :memory: '.read init.sql'", "sqlite3 -init outer.sql :memory:",
                        "duckdb -c '.read outer.sql'", "psql -c '\\i q.sql'", "psql -c '\\include q.sql'"):
            decision = _decision(_run_hook(command, project))
            _check(decision == "ask", f"shell escape in an included file: {command} -> {decision}", failures)
        for command in ("sqlite3 -init safe.sql :memory:", "psql -f safe.sql", "sqlite3 -init missing.sql :memory:",
                        "printf 'SELECT 1;' | sqlite3 :memory:",
                        "printf '.shell rm -rf victim\\n' | sqlite3 :memory: <<< 'SELECT 1;'",
                        "sqlite3 :memory: '.read safe-outer.sql'", "psql -c '\\i safe.sql'"):
            decision = _decision(_run_hook(command, project))
            _check(decision is None, f"{command} -> {decision}", failures)
        for command in ("cat notes.txt", "./notes.txt", "npm run lint"):
            decision = _decision(_run_hook(command, project))
            _check(decision is None, f"{command} -> {decision}", failures)
        appended = "printf 'rm -rf ~\\n' > x.sh; printf 'echo ok\\n' >> x.sh; bash x.sh"
        reason = _reason(_run_hook(appended, project))
        _check(_TYPED in reason, f"`>>` adds to the remembered script (PR review) -> {reason[:60]!r}", failures)
        decision = _decision(_run_hook("printf 'echo ok\\n' >> clean.sh; ./clean.sh", project))
        _check(decision == "ask", f"`>>` keeps the file's existing body -> {decision}", failures)
        reason = _reason(_run_hook("./clean.sh && rm -rf build", project))
        _check(_TYPED in reason, f"typed rm -rf beside a script is reported as typed -> {reason[:60]!r}", failures)

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
