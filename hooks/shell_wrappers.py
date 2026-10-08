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
    # Short option whose value is a shell command string (`su -c`, `flock -c`).
    command_option: str | None = None


def _opts(*names: str) -> frozenset[str]:
    return frozenset(names)


PREFIX_WRAPPERS: dict[str, WrapperSpec] = {
    "sudo": WrapperSpec(
        _opts("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group",
              "--close-from", "--chdir", "--host", "--prompt", "--role", "--type", "--other-user",
              "--command-timeout"),
        no_exec=_opts("-l", "--list", "-v", "--validate", "-k", "-K", "--reset-timestamp",
                      "--remove-timestamp", "-V", "--version", "-e", "--edit"),
    ),
    "doas": WrapperSpec(_opts("-u", "-C"), no_exec=_opts("-C")),
    "pkexec": WrapperSpec(_opts("--user")),
    "run0": WrapperSpec(_opts("-u", "--user", "-g", "--group", "--chdir", "-D", "--unit",
                              "--property", "--slice", "--description", "--setenv")),
    "env": WrapperSpec(_opts("-u", "--unset", "-C", "--chdir", "-P", "--default-signal",
                             "--ignore-signal", "--block-signal", "-a", "--argv0")),
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
                         command_option="-c"),
    "chroot": WrapperSpec(_opts("--userspec", "--groups"), positionals=1),
    "unshare": WrapperSpec(_opts("--mount-proc", "-S", "--setuid", "-G", "--setgid",
                                 "-R", "--root", "-w", "--wd")),
    "nsenter": WrapperSpec(_opts("-t", "--target", "-S", "--setuid", "-G", "--setgid",
                                 "-R", "--root", "-w", "--wd")),
    "dbus-launch": WrapperSpec(),
}

# Shells: `-c STRING` runs STRING; a bare positional is a script file; no
# positional at all means the program is read from stdin.
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "yash", "posh"})
SHELL_OPTIONS_WITH_ARG = _opts("-o", "+o", "-O", "+O", "--rcfile", "--init-file")

# Wrappers whose command is a string the shell parses again.
STRING_RUNNERS: dict[str, WrapperSpec] = {
    "su": WrapperSpec(_opts("-s", "--shell", "-g", "--group", "-G", "--supp-group",
                            "-w", "--whitelist-environment"), command_option="-c"),
    "runuser": WrapperSpec(_opts("-u", "--user", "-g", "--group", "-G", "--supp-group",
                                 "-s", "--shell", "-w"), command_option="-c"),
    "script": WrapperSpec(_opts("-E", "--echo", "-I", "--log-in", "-O", "--log-out",
                                "-B", "--log-io", "-T", "--log-timing", "-m", "--logging-format",
                                "-o", "--output-limit"), command_option="-c"),
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


SUBCOMMAND_RUNNERS: dict[str, SubcommandRunner] = {
    "mise": SubcommandRunner(_opts("exec", "x"), _opts("-C", "--cd", "-E", "--env", "-j",
                                                      "--jobs", "-c", "--command")),
    "fnox": SubcommandRunner(_opts("exec"), _opts("-c", "--config", "-p", "--profile",
                                                  "--if-missing")),
    "direnv": SubcommandRunner(_opts("exec"), positionals=1),
    "uv": SubcommandRunner(_opts("run"), _opts("--with", "--with-editable", "--with-requirements",
                                               "-p", "--python", "--project", "--directory",
                                               "--package", "--extra", "--group", "--env-file",
                                               "--index", "--default-index", "--index-url",
                                               "--extra-index-url", "-f", "--find-links",
                                               "--config-file", "--cache-dir", "--only-group",
                                               "--no-group")),
    "poetry": SubcommandRunner(_opts("run"), _opts("-C", "--directory", "-P", "--project")),
    "pnpm": SubcommandRunner(_opts("exec", "dlx"), _opts("-C", "--dir", "--filter", "-F")),
    "bundle": SubcommandRunner(_opts("exec")),
    "pipenv": SubcommandRunner(_opts("run")),
    "conda": SubcommandRunner(_opts("run"), _opts("-n", "--name", "-p", "--prefix")),
}

# Words that start a compound command or negate a pipeline; the command proper
# follows them.
RESERVED_PREFIXES = frozenset({"!", "{", "}", "if", "then", "else", "elif", "fi", "do", "done",
                               "while", "until", "time", "coproc"})
