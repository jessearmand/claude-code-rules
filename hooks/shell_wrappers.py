"""
Commands that run another command, and how to find the command they run.

Each table entry says which options take a separate argument, so that
`sudo -u root rm -rf /` resolves to `rm -rf /` instead of treating `root` as
the command. Options not listed are assumed to take no argument; an option
written as `--name=value` or `-uroot` carries its value with it either way.

Three shapes of wrapper are handled by shell_parse.py:

  PREFIX_WRAPPERS   the wrapped command follows the wrapper's options
                    (sudo, env, nice, timeout, xargs, ...)
  STRING_RUNNERS    the wrapped command is a single string argument that a
                    shell parses again (bash -c, su -c, watch, ssh host ...)
  SUBCOMMAND_RUNNERS  a tool subcommand that runs whatever follows it
                    (mise exec --, fnox exec --, uv run, direnv exec DIR)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WrapperSpec:
    """How to skip a wrapper's own arguments to reach the command it runs."""

    with_arg: frozenset[str] = frozenset()
    # Positional arguments that belong to the wrapper itself (timeout's
    # duration, flock's lock file, direnv's directory).
    positionals: int = 0
    # Options that turn the wrapper into a query that runs nothing
    # (`command -v rm`, `sudo -l`).
    no_exec: frozenset[str] = frozenset()
    # Options whose value is a shell command string (`su -c`, `flock --command`).
    command_options: frozenset[str] = frozenset()
    # Options that change directory before running the command (`env -C`, `sudo -D`).
    chdir_options: frozenset[str] = frozenset()


def _opts(*names: str) -> frozenset[str]:
    return frozenset(names)


PREFIX_WRAPPERS: dict[str, WrapperSpec] = {
    "sudo": WrapperSpec(
        _opts("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group",
              "--close-from", "--chdir", "--host", "--prompt", "--role", "--type", "--other-user",
              "--command-timeout"),
        no_exec=_opts("-l", "--list", "-v", "--validate", "-k", "-K", "--reset-timestamp",
                      "--remove-timestamp", "-V", "--version", "-e", "--edit"),
        chdir_options=_opts("-D", "--chdir"),
    ),
    "doas": WrapperSpec(_opts("-u", "-C"), no_exec=_opts("-C")),
    "pkexec": WrapperSpec(_opts("--user")),
    "run0": WrapperSpec(_opts("-u", "--user", "-g", "--group", "--chdir", "-D", "--unit",
                              "--property", "--slice", "--description", "--setenv"),
        chdir_options=_opts("-D", "--chdir")),
    "env": WrapperSpec(_opts("-u", "--unset", "-C", "--chdir", "-P", "--default-signal",
                             "--ignore-signal", "--block-signal", "-a", "--argv0"),
        chdir_options=_opts("-C", "--chdir")),
    "nice": WrapperSpec(_opts("-n", "--adjustment")),
    "ionice": WrapperSpec(_opts("-c", "--class", "-n", "--classdata", "-p", "--pid",
                                "-P", "--pgid", "-u", "--uid")),
    "timeout": WrapperSpec(_opts("-s", "--signal", "-k", "--kill-after"), positionals=1),
    "gtimeout": WrapperSpec(_opts("-s", "--signal", "-k", "--kill-after"), positionals=1),
    "nohup": WrapperSpec(),
    "setsid": WrapperSpec(),
    "stdbuf": WrapperSpec(_opts("-i", "--input", "-o", "--output", "-e", "--error")),
    "time": WrapperSpec(_opts("-f", "--format", "-o", "--output")),
    "command": WrapperSpec(no_exec=_opts("-v", "-V")),
    "builtin": WrapperSpec(),
    "exec": WrapperSpec(_opts("-a")),
    "caffeinate": WrapperSpec(_opts("-t", "-w")),
    "chronic": WrapperSpec(),
    "unbuffer": WrapperSpec(),
    "fakeroot": WrapperSpec(_opts("-l", "--lib", "--faked", "-s", "-i")),
    "proxychains": WrapperSpec(_opts("-f")),
    "proxychains4": WrapperSpec(_opts("-f")),
    "torsocks": WrapperSpec(_opts("-u", "-p", "-a", "-P")),
    "busybox": WrapperSpec(),
    "toybox": WrapperSpec(),
    "xargs": WrapperSpec(_opts("-a", "--arg-file", "-d", "--delimiter", "-E", "-e", "--eof",
                               "-I", "--replace", "-L", "--max-lines", "-n", "--max-args",
                               "-P", "--max-procs", "-s", "--max-chars", "--process-slot-var")),
    "flock": WrapperSpec(_opts("-w", "--timeout", "-E", "--conflict-exit-code"), positionals=1,
                         command_options=_opts("-c", "--command")),
    "chroot": WrapperSpec(_opts("--userspec", "--groups"), positionals=1),
    "unshare": WrapperSpec(_opts("--mount-proc", "-S", "--setuid", "-G", "--setgid",
                                 "-R", "--root", "-w", "--wd"),
        chdir_options=_opts("-w", "--wd")),
    "nsenter": WrapperSpec(_opts("-t", "--target", "-S", "--setuid", "-G", "--setgid",
                                 "-R", "--root", "-w", "--wd"),
        chdir_options=_opts("-w", "--wd")),
    "dbus-launch": WrapperSpec(),
}

# Shells: `-c STRING` runs STRING; a bare positional is a script file; no
# positional at all means the program is read from stdin.
# Paths that name the process's own stdin: `bash /dev/stdin <<< CMD` runs CMD.
STDIN_PATHS = frozenset({"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "yash", "posh"})
SHELL_OPTIONS_WITH_ARG = _opts("-o", "+o", "-O", "+O", "--rcfile", "--init-file")
# Options whose value is itself a command string the shell runs. fish takes
# them as `-c CMD`, `--command=CMD`, `-C CMD`, `--init-command=CMD`.
SHELL_COMMAND_OPTIONS: dict[str, frozenset[str]] = {
    "fish": _opts("-c", "--command", "-C", "--init-command"),
}
# Other value-taking options, per shell.
SHELL_EXTRA_OPTIONS_WITH_ARG: dict[str, frozenset[str]] = {
    "fish": _opts("-d", "--debug", "-o", "--debug-output", "-p", "--profile", "-f", "--features",
                  "--profile-startup"),
}


@dataclass(frozen=True)
class ShellInvocation:
    """What a shell command line will run."""

    # Command strings run directly: `bash -c STR`, `fish -C STR --command=STR`.
    commands: tuple[str, ...] = ()
    # Positional arguments after a `-c` string: $0, $1, ...
    command_args: tuple[str, ...] = ()
    script: str | None = None  # `bash script.sh`
    reads_stdin: bool = False  # `bash`, `bash -s args`, `bash -`
    syntax_only: bool = False  # `-n`: parse and check, run nothing


def shell_invocation(name: str, args: list[str] | tuple[str, ...]) -> ShellInvocation:
    """Parse a shell's own arguments. The single source of truth for every guard."""
    command_options = SHELL_COMMAND_OPTIONS.get(name, frozenset())
    with_arg = SHELL_OPTIONS_WITH_ARG | SHELL_EXTRA_OPTIONS_WITH_ARG.get(name, frozenset())
    commands: list[str] = []
    dash_c = reads_stdin = syntax_only = False
    command_given = False  # fish `-c CMD`: the string came with the option
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        key = arg.split("=", 1)[0] if arg.startswith("--") else arg
        if key in command_options:
            # `-c`/`--command` replace the script; `-C`/`--init-command` run before it.
            if key in ("-c", "--command"):
                dash_c = command_given = True
            if arg.startswith("--") and "=" in arg:
                commands.append(arg.split("=", 1)[1])
                index += 1
            else:
                if index + 1 < len(args):
                    commands.append(args[index + 1])
                index += 2
            continue
        if key in with_arg:
            index += 1 if "=" in arg else 2
            continue
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 1:
            letters = arg[1:]
            syntax_only = syntax_only or "n" in letters
            dash_c = dash_c or "c" in letters
            reads_stdin = reads_stdin or "s" in letters
            index += 1
            continue
        if arg.startswith(("+", "--")) and arg not in ("+", "--"):
            index += 1
            continue
        break
    positionals = list(args[index:])
    command_args: tuple[str, ...] = ()
    script = None
    if dash_c and not command_given and positionals:
        commands.append(positionals[0])  # `bash -c STR $0 $1 ...`
        command_args = tuple(positionals[1:])
    elif dash_c:
        command_args = tuple(positionals)
    elif not reads_stdin and positionals and positionals[0] not in STDIN_PATHS:
        script = positionals[0]
    stdin = reads_stdin or (not dash_c and script is None)
    return ShellInvocation(tuple(commands), command_args, script, stdin, syntax_only)

# Wrappers whose command is a string the shell parses again.
STRING_RUNNERS: dict[str, WrapperSpec] = {
    "su": WrapperSpec(_opts("-s", "--shell", "-g", "--group", "-G", "--supp-group",
                            "-w", "--whitelist-environment"), command_options=_opts("-c", "--command")),
    "runuser": WrapperSpec(_opts("-u", "--user", "-g", "--group", "-G", "--supp-group",
                                 "-s", "--shell", "-w"), command_options=_opts("-c", "--command")),
    "script": WrapperSpec(_opts("-E", "--echo", "-I", "--log-in", "-O", "--log-out",
                                "-B", "--log-io", "-T", "--log-timing", "-m", "--logging-format",
                                "-o", "--output-limit"), command_options=_opts("-c", "--command")),
    "watch": WrapperSpec(_opts("-n", "--interval", "-d", "--differences", "-q", "--equexit",
                               "--chgexit")),
}

SSH_OPTIONS_WITH_ARG = _opts("-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L",
                             "-l", "-m", "-O", "-o", "-P", "-p", "-Q", "-R", "-S", "-W", "-w")


@dataclass(frozen=True)
class SubcommandRunner:
    """`tool sub [opts] [--] command...`, e.g. `mise exec -- rm -rf x`."""

    subcommands: frozenset[str]
    with_arg: frozenset[str] = frozenset()
    positionals: int = 0
    # Options that change directory before running the command (`uv --directory`).
    chdir_options: frozenset[str] = frozenset()
    # Options whose value is a command string run through a shell (`npm exec -c CMD`).
    command_options: frozenset[str] = frozenset()


SUBCOMMAND_RUNNERS: dict[str, SubcommandRunner] = {
    "mise": SubcommandRunner(_opts("exec", "x"), _opts("-C", "--cd", "-E", "--env", "-j",
                                                      "--jobs", "-c", "--command"),
        chdir_options=_opts("-C", "--cd"), command_options=_opts("-c", "--command")),
    "npm": SubcommandRunner(_opts("exec", "x"), _opts("-p", "--package", "-w", "--workspace", "--prefix",
                                                     "-c", "--call"),
        chdir_options=_opts("--prefix"), command_options=_opts("-c", "--call")),
    "fnox": SubcommandRunner(_opts("exec"), _opts("-c", "--config", "-p", "--profile",
                                                  "--if-missing")),
    "direnv": SubcommandRunner(_opts("exec"), positionals=1),
    "uv": SubcommandRunner(_opts("run"), _opts("--with", "--with-editable", "--with-requirements",
                                               "-p", "--python", "--project", "--directory",
                                               "--package", "--extra", "--group", "--env-file",
                                               "--index", "--default-index", "--index-url",
                                               "--extra-index-url", "-f", "--find-links",
                                               "--config-file", "--cache-dir", "--only-group",
                                               "--no-group"),
        chdir_options=_opts("--directory")),
    "poetry": SubcommandRunner(_opts("run"), _opts("-C", "--directory", "-P", "--project"),
        chdir_options=_opts("-C", "--directory")),
    "pnpm": SubcommandRunner(_opts("exec", "dlx"), _opts("-C", "--dir", "--filter", "-F"),
        chdir_options=_opts("-C", "--dir"), command_options=_opts("-c", "--shell-mode")),
    "bundle": SubcommandRunner(_opts("exec")),
    "pipenv": SubcommandRunner(_opts("run")),
    "conda": SubcommandRunner(_opts("run"), _opts("-n", "--name", "-p", "--prefix")),
}

# Words that start a compound command or negate a pipeline; the command proper
# follows them.
RESERVED_PREFIXES = frozenset({"!", "{", "}", "if", "then", "else", "elif", "fi", "do", "done",
                               "while", "until", "time", "coproc"})


# Options that take a separate value, per CLI, wherever they appear: before
# the subcommand (`npm -w web publish`, `helm -n prod uninstall`) or after
# it. Without them the value reads as the subcommand and the rule misses.
CLI_OPTIONS_WITH_VALUE: dict[str, frozenset[str]] = {
    "npm": frozenset({"-w", "--workspace", "--registry", "--prefix", "--userconfig",
                      "--globalconfig", "--cache", "--tag", "--access", "--otp", "--loglevel",
                      "--scope", "--auth-type", "--include", "--omit"}),
    "pnpm": frozenset({"-C", "--dir", "-F", "--filter", "--registry", "--tag", "--access",
                       "--otp", "--loglevel", "--publish-branch", "--report-summary"}),
    "yarn": frozenset({"--cwd", "--registry", "--tag", "--access", "--otp", "--new-version"}),
    "bun": frozenset({"--cwd", "-c", "--config", "--registry", "--tag", "--access", "--otp"}),
    "cargo": frozenset({"-Z", "--config", "--color", "-C", "--manifest-path", "--registry",
                        "--index", "--token", "-p", "--package", "-j", "--jobs", "--target",
                        "--target-dir", "-F", "--features"}),
    "gem": frozenset({"-k", "--key", "--host", "--otp"}),
    "twine": frozenset({"-r", "--repository", "--repository-url", "-u", "--username", "-p",
                        "--password", "-c", "--comment", "-i", "--identity", "--sign-with",
                        "--config-file", "--cert", "--client-cert"}),
    "uv": frozenset({"--directory", "--project", "--config-file", "--cache-dir", "--index",
                     "--publish-url", "-u", "--username", "-p", "--password", "-t", "--token",
                     "--trusted-publishing", "--check-url"}),
    "poetry": frozenset({"-C", "--directory", "-P", "--project", "-r", "--repository", "-u",
                         "--username", "-p", "--password", "--cert", "--client-cert"}),
    "pdm": frozenset({"-p", "--project", "-r", "--repository", "-u", "--username", "-P",
                      "--password"}),
    "hatch": frozenset({"-r", "--repo", "-u", "--user", "-a", "--auth", "-e", "--env"}),
    "mvn": frozenset({"-f", "--file", "-s", "--settings", "-gs", "--global-settings", "-P",
                      "--activate-profiles", "-pl", "--projects", "-rf", "--resume-from", "-T",
                      "--threads", "-t", "--toolchains"}),
    "gradle": frozenset({"-p", "--project-dir", "-c", "--settings-file", "-g", "--gradle-user-home",
                         "-I", "--init-script", "-x", "--exclude-task"}),
    "docker": frozenset({"-H", "--host", "--context", "-c", "--config", "-l", "--log-level",
                         "--tlscacert", "--tlscert", "--tlskey"}),
    "podman": frozenset({"--url", "--connection", "-c", "--identity", "--log-level", "--root",
                         "--runroot"}),
    "helm": frozenset({"-n", "--namespace", "--kube-context", "--kubeconfig", "--kube-apiserver",
                       "--kube-token", "--registry-config", "--repository-config",
                       "--repository-cache"}),
    "gh": frozenset({"-R", "--repo", "--hostname"}),
}
CLI_OPTIONS_WITH_VALUE["gradlew"] = CLI_OPTIONS_WITH_VALUE["gradle"]
CLI_OPTIONS_WITH_VALUE["buildah"] = CLI_OPTIONS_WITH_VALUE["podman"]
CLI_OPTIONS_WITH_VALUE["systemctl"] = frozenset({"-H", "--host", "-M", "--machine", "-t", "--type",
                                                 "-p", "--property", "--state", "-n", "--lines",
                                                 "-o", "--output", "--root", "--signal", "-s"})
