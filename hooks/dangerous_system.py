"""
System-level dangerous commands: disks, fetch-and-execute, permissions,
persistence, package publishing, bulk deletion, and switching off security
controls.

Sources: dcg system.disk / system.permissions / package_managers packs,
karanb192 block-dangerous-commands, sh-guard's download->execute taint rule,
cc-safety-net device/find rules, and the cancelli rubric axes for
persistence, weakened controls and fetch-and-execute.
"""

from __future__ import annotations

import os
import re

from danger_rule import (
    CRITICAL,
    HIGH,
    DangerRule,
    RuleContext,
    expand,
    has,
    is_broad_target,
    is_inside,
    long_flag,
    operands,
    positional_words,
    short_flag,
)
from shell_interpreters import interpreter_kind
from shell_parse import SimpleCommand, parse
from shell_wrappers import SHELLS, STDIN_PATHS, shell_invocation
from shell_writes import WRITE, written_paths

# --------------------------------------------------------------------------- #
# Disks and devices
# --------------------------------------------------------------------------- #

_HARMLESS_DEVICES = re.compile(r"^/dev/(?:null|zero|u?random|stdout|stderr|stdin|tty\w*|fd/\d+|pts/\d+)$")
_BLOCK_DEVICE = re.compile(r"^/dev/(?:sd|hd|vd|xvd|nvme|mmcblk|disk|rdisk|md|dm-|mapper/|loop)")


def _device_write(command: SimpleCommand, ctx: RuleContext) -> str | None:
    redirected = {r.target for r in command.redirects}
    for write in written_paths(command):
        if write.action != WRITE or write.dynamic:
            continue
        for target in _device_paths(write.path, command):
            # `dd of=` takes any device but the harmless ones; other writers
            # (`tee /dev/sda`, `cp image /dev/sda`, `> /dev/disk2`) a block device.
            raw_device = command.name == "dd" and not _HARMLESS_DEVICES.match(target)
            if _BLOCK_DEVICE.match(target) or (raw_device and write.path not in redirected):
                if write.path in redirected:
                    return f"redirect into {target}"
                return f"dd of={target}" if command.name == "dd" else f"{command.name} writes {target}"
    return None


def _device_paths(path: str, command: SimpleCommand) -> list[str]:
    """The /dev paths `path` may name: `cd /dev && dd of=sda` writes /dev/sda."""
    found: list[str] = []
    for directory in command.possible_cwds:
        if directory is None and not path.startswith(("/", "~")):
            continue
        target = os.path.normpath(os.path.join(directory or "/", os.path.expanduser(path)))
        if target.startswith("/dev/") and target not in found:
            found.append(target)
    return found


_FORMATTERS = re.compile(r"^(?:mkfs(?:\.\w+)?|mke2fs|mkswap|newfs(?:_\w+)?|wipefs|blkdiscard|sfdisk|cfdisk)$")
_DISKUTIL_DESTRUCTIVE = frozenset({"eraseDisk", "eraseVolume", "partitionDisk", "zeroDisk",
                                   "randomDisk", "secureErase", "reformat", "deleteVolume",
                                   "apfs"})


def _names_device(command: SimpleCommand) -> bool:
    """A /dev path, from any directory the command may run in (`cd /dev && mkfs
    sdb1`), or a computed one; formatting an image file is harmless."""
    return any(a.startswith("/dev/") or "$" in a or "`" in a or _device_paths(a, command)
               for a in operands(command.args))


# parted commands that only report; any other command (or none: interactive) may write.
_PARTED_READ_ONLY = frozenset({"print", "p", "help", "h", "version", "v", "unit", "align-check", "quit", "q"})
_PARTED_UNITS = frozenset({"s", "B", "kB", "MB", "MiB", "GB", "GiB", "TB", "TiB", "%", "cyl", "chs", "compact"})


def _parted(command: SimpleCommand) -> str | None:
    """`parted -l` and `parted DEV print` only read; `parted DEV mklabel gpt print`
    writes a new label before printing it, and bare `parted DEV` is interactive."""
    if short_flag(command.args, "l") or long_flag(command.args, "--list"):
        return None
    words = operands(command.args, frozenset({"-a", "--align"}))[1:]  # after the device
    commands = [w for w in words if not w.isdigit() and w not in _PARTED_UNITS and w not in ("minimal", "optimal")]
    if commands and all(w in _PARTED_READ_ONLY for w in commands):
        return None
    return "parted"


def _disk_format(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if _FORMATTERS.match(command.name) and _names_device(command):
        return command.name
    if command.name == "parted":
        return _parted(command)
    if command.name in ("fdisk", "gdisk", "sgdisk") and not short_flag(command.args, "l"):
        return command.name
    if command.name == "diskutil" and command.args and command.args[0] in _DISKUTIL_DESTRUCTIVE:
        # `diskutil apfs list` is harmless; `diskutil apfs deleteContainer` is not.
        apfs_destructive = command.args[1:2] and command.args[1].startswith(("delete", "erase"))
        if command.args[0] != "apfs" or apfs_destructive:
            return f"diskutil {command.args[0]}"
    return None


# --------------------------------------------------------------------------- #
# Fetch-and-execute and decode-and-execute
# --------------------------------------------------------------------------- #

_FETCHERS = frozenset({"curl", "wget", "fetch", "aria2c", "http", "https", "xh", "xhs", "httpie",
                       "nc", "ncat", "netcat", "socat", "lwp-request", "GET"})
_DECODERS = frozenset({"base64", "base32", "xxd", "openssl", "uudecode", "gunzip", "zcat", "rev"})
_FETCH_IN_ARG = re.compile(r"(?:\$\(|<\(|`)\s*(?:\S*/)?(?:curl|wget|fetch|aria2c|http|https|xh)\b")


def _executes_stdin(command: SimpleCommand) -> bool:
    """A shell or interpreter that will run whatever arrives on stdin."""
    name, args = command.name, command.args
    if name in SHELLS:
        shell = shell_invocation(name, args)
        return shell.reads_stdin and not shell.syntax_only
    if interpreter_kind(name):
        script_args = operands(args, frozenset({"-W", "-X", "-m", "-r", "-I", "-M"}))
        return not script_args or script_args[0] in STDIN_PATHS
    return name in ("eval", "source", ".")


def _pipeline_sources(command: SimpleCommand, ctx: RuleContext) -> list[SimpleCommand]:
    found: list[SimpleCommand] = []
    current = ctx.parsed.previous_stage(command)
    while current is not None:
        found.append(current)
        current = ctx.parsed.previous_stage(current)
    return found


def _fetch_and_execute(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if _executes_stdin(command):
        for source in _pipeline_sources(command, ctx):
            if source.name in _FETCHERS:
                return f"{source.name} ... | {command.name}"
    if command.name in SHELLS or command.name in ("eval", "source", ".") or interpreter_kind(command.name):
        for arg in command.args:
            if _FETCH_IN_ARG.search(arg):
                return f"{command.name} runs downloaded code: {arg[:60]}"
        # `bash < <(curl -fsSL URL)`: the download arrives as stdin.
        if _executes_stdin(command):
            for redirect in command.redirects:
                if redirect.op == "<" and _FETCH_IN_ARG.search(redirect.target):
                    return f"{command.name} < downloaded code: {redirect.target[:60]}"
    return None


def _executed_files(command: SimpleCommand, ctx: RuleContext) -> list[str]:
    """Files `command` runs as a program: a script argument, `./tool`, `source f`,
    or a file a shell or interpreter reads on stdin (`< f`, `cat f | bash`)."""
    files: list[str] = []
    if command.name in SHELLS:
        shell = shell_invocation(command.name, command.args)
        if shell.script and not shell.syntax_only:
            files.append(shell.script)
    elif interpreter_kind(command.name):
        script = operands(command.args, frozenset({"-W", "-X", "-m", "-r", "-I", "-M", "-c", "-e"}))[:1]
        files += [s for s in script if s not in STDIN_PATHS]
    elif command.name in ("source", ".") and command.args:
        files.append(command.args[0])
    elif "/" in (command.argv[0] if command.argv else "") and not command.dynamic_head:
        files.append(command.argv[0])
    if _executes_stdin(command):
        files += [r.target for r in command.redirects if r.op == "<" and not r.dynamic]
        for source in _pipeline_sources(command, ctx):
            if source.name == "cat" and not source.dynamic_head:
                files += [f for f in operands(source.args, frozenset()) if f not in STDIN_PATHS]
    return files


def _absolute(path: str, directory: str | None) -> str | None:
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return None if directory is None else os.path.normpath(os.path.join(directory, expanded))


def _run_downloaded(command: SimpleCommand, ctx: RuleContext) -> str | None:
    """`curl -o x.sh URL && bash x.sh`: running a file this same line downloaded."""
    executed = {a for f in _executed_files(command, ctx) for d in command.possible_cwds
                if (a := _absolute(f, d))}
    if not executed:
        return None
    for earlier in ctx.parsed.commands:
        if earlier is command:
            break
        if earlier.name not in _FETCHERS:
            continue
        for write in written_paths(earlier):
            for directory in earlier.possible_cwds:
                if _absolute(write.path, directory) in executed:
                    return f"{earlier.name} downloads {write.path}, then {command.name} runs it"
    return None


def _uninspected_program(command: SimpleCommand, ctx: RuleContext) -> str | None:
    """A script, Makefile or recipe set this command runs that could not be read
    (too large, binary, or past a resolver limit)."""
    for skipped, label in ctx.parsed.skipped_sources:
        if skipped is command:
            return f"{command.name} runs {label} (could not be inspected)"
    return None


def _incomplete_analysis(command: SimpleCommand, ctx: RuleContext) -> str | None:
    """The parser stopped early (nesting depth, wrapper chain, expansion limits):
    what it did not see may be anything. Reported once, on the first command."""
    if ctx.parsed.truncated and ctx.parsed.commands and command is ctx.parsed.commands[0]:
        return "the command is nested or expanded beyond what the guards analyse"
    return None


def _decode_and_execute(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.dynamic_head and command.argv:
        # `$(base64 -d <<< cm0=) -rf x`: the program to run is decoded text.
        body = re.fullmatch(r"\$\((.*)\)|`(.*)`", command.argv[0], re.DOTALL)
        if body:
            for source in parse(next(g for g in body.groups() if g is not None), "/").commands:
                if _decodes(source):
                    return f"$({source.name} (decode) ...) as the command"
    if not _executes_stdin(command):
        return None
    for source in _pipeline_sources(command, ctx):
        if _decodes(source):
            return f"{source.name} (decode) ... | {command.name}"
    return None


def _decodes(source: SimpleCommand) -> bool:
    return source.name in _DECODERS and (
        short_flag(source.args, "dDr") or long_flag(source.args, "--decode") or
        source.name in ("uudecode", "gunzip", "zcat", "rev"))


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #

_WORLD_WRITABLE = re.compile(r"^(?:[0-7]?[0-7][0-7][2367]|(?:[ugoa]*[ao][ugoa]*|)\+[rwxXst]*w)")
_SETUID = re.compile(r"^(?:[4267][0-7]{3}|[ugoa]*\+[rwxX]*s)")


def _chmod_dangerous(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name != "chmod":
        return None
    words = operands(command.args, frozenset({"--reference"}))
    if any(a == "--reference" or a.startswith("--reference=") for a in command.args):
        # `chmod --reference=RFILE FILE...`: the mode comes from RFILE; every operand is a target.
        mode, targets = "", words
    elif len(words) < 2:
        return None
    else:
        mode, targets = words[0], words[1:]
    if not targets:
        return None
    recursive = short_flag(command.args, "R") or long_flag(command.args, "--recursive")
    if mode and _SETUID.match(mode):
        return f"chmod {mode} (setuid/setgid)"
    if mode and _WORLD_WRITABLE.match(mode) and (recursive or any(is_broad_target(t, ctx.cwd) for t in targets)):
        return f"chmod {'-R ' if recursive else ''}{mode}"
    if recursive and any(is_broad_target(t, ctx.cwd) for t in targets):
        return f"chmod -R on {targets[0]}"
    return None


def _chmod_world_single(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name != "chmod":
        return None
    words = operands(command.args)
    return f"chmod {words[0]}" if len(words) >= 2 and _WORLD_WRITABLE.match(words[0]) else None


def _chown_broad(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name not in ("chown", "chgrp"):
        return None
    recursive = short_flag(command.args, "R") or long_flag(command.args, "--recursive")
    words = operands(command.args, frozenset({"--reference", "--from"}))
    # `chown --reference=RFILE FILE...`: no owner operand, every operand is a target.
    by_reference = any(a == "--reference" or a.startswith("--reference=") for a in command.args)
    targets = words if by_reference else words[1:]
    if recursive and any(is_broad_target(t, ctx.cwd) for t in targets):
        return f"{command.name} -R {targets[0]}"
    return None


# --------------------------------------------------------------------------- #
# Persistence: scheduled jobs and services
# --------------------------------------------------------------------------- #


def _crontab_remove(command: SimpleCommand, ctx: RuleContext) -> str | None:
    return "crontab -r" if command.name == "crontab" and short_flag(command.args, "r") else None


def _crontab_install(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name != "crontab" or short_flag(command.args, "lr"):
        return None
    return "crontab (install a new table)"


def _schedule(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name in ("at", "batch") and not short_flag(command.args, "lrdc"):
        return command.name
    if command.name == "launchctl" and command.args and command.args[0] in (
            "load", "bootstrap", "enable", "submit", "kickstart"):
        return f"launchctl {command.args[0]}"
    if command.name == "systemctl" and positional_words(command)[:1] in (
            ["enable"], ["link"], ["preset"], ["edit"], ["set-property"]):
        return f"systemctl {positional_words(command)[0]}"
    return None


# --------------------------------------------------------------------------- #
# Publishing packages and artifacts
# --------------------------------------------------------------------------- #

_PUBLISH = {
    "npm": ("publish", "unpublish", "deprecate", "dist-tag"),
    "pnpm": ("publish",),
    "yarn": ("publish", "npm"),
    "bun": ("publish",),
    "cargo": ("publish", "yank", "owner"),
    "gem": ("push", "yank", "owner"),
    "twine": ("upload",),
    "uv": ("publish",),
    "poetry": ("publish",),
    "flit": ("publish",),
    "hatch": ("publish",),
    "pdm": ("publish",),
    "vsce": ("publish", "unpublish"),
    "ovsx": ("publish",),
    "pod": ("trunk",),
    "dotnet": ("nuget",),
    "mvn": ("deploy",),
    "gradle": ("publish",),
    "goreleaser": ("release",),
}


def _publish(command: SimpleCommand, ctx: RuleContext) -> str | None:
    verbs = _PUBLISH.get(command.name)
    if not verbs:
        return None
    words = positional_words(command)
    if not words or words[0] not in verbs:
        return None
    if command.name == "yarn" and words[0] == "npm" and (len(words) < 2 or words[1] != "publish"):
        return None
    if has(command.args, "--dry-run", "-n") or long_flag(command.args, "--dry-run"):
        return None
    return f"{command.name} {words[0]}"


def _push_artifact(command: SimpleCommand, ctx: RuleContext) -> str | None:
    words = positional_words(command)
    if command.name in ("docker", "podman", "buildah") and words[:1] == ["push"]:
        return f"{command.name} push"
    if command.name == "gh" and words[:2] in (["release", "create"], ["release", "delete"], ["repo", "delete"]):
        return "gh " + " ".join(words[:2])
    return None


# --------------------------------------------------------------------------- #
# Bulk deletion that is not `rm -rf`
# --------------------------------------------------------------------------- #


# find primaries that consume the next word (so `-name -delete` is a pattern).
_FIND_ARITY_ONE = frozenset({
    "-amin", "-anewer", "-atime", "-Bmin", "-Bnewer", "-Btime", "-cmin", "-cnewer", "-context",
    "-ctime", "-flags", "-fls", "-fprint", "-fprint0", "-fstype", "-gid", "-group", "-ilname",
    "-iname", "-inum", "-ipath", "-iregex", "-iwholename", "-links", "-lname", "-maxdepth",
    "-mindepth", "-mmin", "-mnewer", "-mtime", "-name", "-newer", "-path", "-perm", "-printf",
    "-regex", "-regextype", "-samefile", "-size", "-type", "-uid", "-used", "-user", "-wholename",
    "-xattrname", "-xtype", "-files0-from",
})
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_TEMP_ROOTS = ("/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp", "/var/folders")


def _find_deletes(args: tuple[str, ...]) -> bool:
    """True when `-delete` is a find action, not an argument of another primary."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _FIND_EXEC:
            while index < len(args) and args[index] not in (";", "+"):
                index += 1
        elif arg == "-fprintf":
            index += 2
        elif arg in _FIND_ARITY_ONE or arg.startswith("-newer"):
            index += 1
        elif arg == "-delete":
            return True
        index += 1
    return False


def _is_temp(path: str, cwd: str) -> bool:
    if path.startswith(("$TMPDIR", "${TMPDIR}")):
        return True
    resolved = os.path.realpath(expand(path, cwd))
    return any(is_inside(resolved, os.path.realpath(root)) and resolved != os.path.realpath(root)
               for root in _TEMP_ROOTS)


def _find_starting_points(args: tuple[str, ...]) -> list[str]:
    points = []
    for arg in args:
        if arg.startswith(("-", "(", "!", ")")):
            break
        points.append(arg)
    return points or ["."]


def _find_delete(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name not in ("find", "gfind") or not _find_deletes(command.args):
        return None
    points = _find_starting_points(command.args)
    follows = any(a in ("-L", "-follow", "-H") for a in command.args)
    outside = [p for p in points
               if is_broad_target(p, ctx.cwd)
               or not (is_inside(expand(p, ctx.cwd), ctx.cwd) or _is_temp(p, ctx.cwd))]
    if outside or follows:
        return f"find {' '.join(points)} -delete (outside the working directory)"
    return None


def _find_delete_local(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name in ("find", "gfind") and _find_deletes(command.args):
        return f"find {' '.join(_find_starting_points(command.args))} -delete"
    return None


def _shred(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name in ("shred", "srm") and operands(command.args):
        return command.name
    if command.name == "mv" and operands(command.args)[-1:] == ["/dev/null"]:
        return "mv ... /dev/null"
    return None


# --------------------------------------------------------------------------- #
# System state and security controls
# --------------------------------------------------------------------------- #


def _power(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name in ("shutdown", "reboot", "halt", "poweroff"):
        return command.name
    if command.name in ("init", "telinit") and operands(command.args)[:1] in (["0"], ["6"]):
        return f"{command.name} {command.args[0]}"
    if command.name == "systemctl" and any(a in ("poweroff", "reboot", "halt", "kexec") for a in command.args):
        return "systemctl poweroff/reboot"
    return None


def _kill_targets(args: tuple[str, ...]) -> list[str]:
    """PIDs passed to kill. `kill [-s SIG | -n NUM | -SIG] pid...`: only the first
    dash word is a signal, so in `kill -9 -1` the `-1` is the PID for everything."""
    index = 0
    if args[:1] and args[0] in ("-l", "-L", "--list"):
        return []
    if args[:1] and args[0] in ("-s", "-n", "--signal"):
        index = 2
    elif args[:1] and args[0].startswith("-") and args[0] != "--":
        index = 1
    if index < len(args) and args[index] == "--":
        index += 1
    return list(args[index:])


def _kill_everything(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name == "kill" and any(pid in ("-1", "1") for pid in _kill_targets(command.args)):
        return "kill -1 / kill 1 (every process / init)"
    if command.name in ("killall5",):
        return command.name
    return None


_SECURITY_OFF = (
    ("csrutil", ("disable",)),
    ("spctl", ("--master-disable", "--global-disable", "--disable")),
    ("setenforce", ("0", "Permissive", "permissive")),
    ("ufw", ("disable",)),
    ("pfctl", ("-d",)),
)
_SECURITY_SERVICES = re.compile(r"^(?:firewalld|ufw|apparmor|auditd|selinux|fail2ban|clamav\S*|"
                                r"com\.apple\.(?:alf|security)\S*)$")


def _weaken_security(command: SimpleCommand, ctx: RuleContext) -> str | None:
    for name, args in _SECURITY_OFF:
        if command.name == name and any(a in args for a in command.args):
            return f"{name} {' '.join(a for a in command.args if a in args)}"
    if command.name in ("iptables", "ip6tables") and (short_flag(command.args, "F") or long_flag(command.args, "--flush")
                                                      or (has(command.args, "-P", "--policy") and "ACCEPT" in command.args)):
        return f"{command.name} flush/accept-all"
    if command.name == "nft" and has(command.args, "flush") and has(command.args, "ruleset"):
        return "nft flush ruleset"
    verb = positional_words(command)[:1]
    stops = verb and verb[0] in ("stop", "disable", "mask", "unload", "bootout")
    if command.name in ("systemctl", "launchctl") and stops and any(
        _SECURITY_SERVICES.match(os.path.basename(a).removesuffix(".service").removesuffix(".plist"))
        for a in command.args[1:]
    ):
        return f"{command.name} {verb[0]} (security service)"
    if command.name == "defaults" and "LSQuarantine" in command.args:
        return "defaults write LSQuarantine"
    if command.name in ("npm", "pnpm", "yarn") and has(command.args, "strict-ssl") and has(command.args, "false"):
        return f"{command.name} config strict-ssl false"
    if command.name in ("pip", "pip3") and any("trusted-host" in a for a in command.args) and has(command.args, "config"):
        return "pip config trusted-host"
    return None


def _clear_history(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name == "history" and short_flag(command.args, "cw"):
        return "history -c"
    if command.name == "unset" and has(command.args, "HISTFILE"):
        return "unset HISTFILE"
    if command.name in ("rm", "shred", "truncate") and any(
            os.path.basename(a) in (".bash_history", ".zsh_history", ".zhistory", ".python_history")
            for a in command.args):
        return f"{command.name} shell history"
    return None


_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}")


def _fork_bomb(command: SimpleCommand, ctx: RuleContext) -> str | None:
    # A fork bomb is a function definition; look at the source text it came from.
    if command.name == ":" and command.stage == 1:
        source = ctx.parsed.previous_stage(command)
        if source is not None and source.name == ":":
            return "fork bomb"
    return None


_DISK_HINT = "This can destroy a whole disk or filesystem. Never run it from an agent; describe it to the user."
_FETCH_HINT = (
    "Do not pipe downloaded code into a shell or interpreter. Download to a file, show the user "
    "what it does (or its checksum and source), and let them run it."
)
_PERSIST_HINT = "This makes code run later or on a schedule. Describe the change and let the user make it."
_PUBLISH_HINT = "Publishing is public and often irreversible. Run with --dry-run and let the user publish."

SYSTEM_RULES: tuple[DangerRule, ...] = (
    DangerRule("device-write", CRITICAL, "writes directly to a block device", _DISK_HINT, _device_write),
    DangerRule("disk-format", CRITICAL, "formats, partitions or wipes a disk", _DISK_HINT, _disk_format),
    DangerRule("fetch-and-execute", CRITICAL, "runs code downloaded from the network", _FETCH_HINT,
               _fetch_and_execute),
    DangerRule("run-downloaded", CRITICAL, "runs a file downloaded earlier in the same command", _FETCH_HINT,
               _run_downloaded),
    DangerRule("uninspected-program", HIGH, "runs program text the guards could not read",
               "Confirm with the user, or run the steps it needs directly so they can be checked.",
               _uninspected_program, decision="ask"),
    DangerRule("incomplete-analysis", HIGH, "could not be analysed completely",
               "Simplify the command (fewer nested shells, wrappers or expansions) so it can be checked, "
               "or confirm with the user.", _incomplete_analysis, decision="ask"),
    DangerRule("decode-and-execute", CRITICAL, "decodes and runs hidden code", _FETCH_HINT,
               _decode_and_execute),
    DangerRule("fork-bomb", CRITICAL, "exhausts process limits", _DISK_HINT, _fork_bomb),
    DangerRule("chmod-dangerous", HIGH, "makes files world-writable, setuid, or re-permissions a broad tree",
               "Grant the narrowest permission that works (e.g. `chmod u+x script.sh`).", _chmod_dangerous),
    DangerRule("chown-broad", HIGH, "changes ownership of a system or home tree",
               "Change ownership of the specific files that need it.", _chown_broad),
    DangerRule("crontab-remove", HIGH, "crontab -r deletes every scheduled job without confirmation",
               "Use `crontab -l` to show the table and let the user edit it.", _crontab_remove, decision="ask"),
    DangerRule("crontab-install", HIGH, "installing a crontab schedules code to run later", _PERSIST_HINT,
               _crontab_install),
    DangerRule("publish-package", HIGH, "publishes or unpublishes a package", _PUBLISH_HINT, _publish),
    DangerRule("find-delete-outside", HIGH, "find -delete outside the working directory or following symlinks",
               "Run with -print first and let the user delete.", _find_delete, decision="ask"),
    DangerRule("shred", HIGH, "irrecoverably destroys file contents",
               "Use plain `rm` for files the task owns; let the user shred anything else.", _shred, decision="ask"),
    DangerRule("power-state", HIGH, "shuts down or reboots the machine",
               "Ask the user to do this.", _power),
    DangerRule("kill-everything", HIGH, "signals every process or init", "Kill specific PIDs.", _kill_everything),
    DangerRule("weaken-security", HIGH, "disables a security control (SIP, Gatekeeper, SELinux, firewall, TLS checks)",
               "Do not switch off security controls; fix the underlying problem or ask the user.",
               _weaken_security),
    # Common and often legitimate: confirm rather than block.
    DangerRule("chmod-world-writable", HIGH, "makes a file world-writable",
               "Prefer `chmod u+w` or `chmod g+w`.", _chmod_world_single, decision="ask"),
    DangerRule("schedule-job", HIGH, "schedules or enables something that runs later", _PERSIST_HINT,
               _schedule, decision="ask"),
    DangerRule("push-artifact", HIGH, "pushes an image or release, or deletes a repository", _PUBLISH_HINT,
               _push_artifact, decision="ask"),
    DangerRule("find-delete", HIGH, "find -delete removes every match with no undo",
               "Run the same find with -print first and show the user.", _find_delete_local, decision="ask"),
    DangerRule("clear-history", HIGH, "erases shell history (covers tracks)",
               "Leave shell history alone.", _clear_history, decision="ask"),
)
