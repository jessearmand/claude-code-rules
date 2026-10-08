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
    short_flag,
)
from shell_interpreters import interpreter_kind
from shell_parse import SimpleCommand
from shell_wrappers import SHELLS

# --------------------------------------------------------------------------- #
# Disks and devices
# --------------------------------------------------------------------------- #

_HARMLESS_DEVICES = re.compile(r"^/dev/(?:null|zero|u?random|stdout|stderr|stdin|tty\w*|fd/\d+|pts/\d+)$")
_BLOCK_DEVICE = re.compile(r"^/dev/(?:sd|hd|vd|xvd|nvme|mmcblk|disk|rdisk|md|dm-|mapper/|loop)")


def _device_write(command: SimpleCommand, ctx: RuleContext) -> str | None:
    for redirect in command.redirects:
        if redirect.op in (">", ">>", ">|", "&>") and _BLOCK_DEVICE.match(redirect.target):
            return f"redirect into {redirect.target}"
    if command.name == "dd":
        for arg in command.args:
            if arg.startswith("of=/dev/") and not _HARMLESS_DEVICES.match(arg[3:]):
                return f"dd {arg}"
    return None


_FORMATTERS = re.compile(r"^(?:mkfs(?:\.\w+)?|mke2fs|mkswap|newfs(?:_\w+)?|wipefs|blkdiscard|sfdisk|cfdisk)$")
_DISKUTIL_DESTRUCTIVE = frozenset({"eraseDisk", "eraseVolume", "partitionDisk", "zeroDisk",
                                   "randomDisk", "secureErase", "reformat", "deleteVolume",
                                   "apfs"})


def _disk_format(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if _FORMATTERS.match(command.name):
        return command.name
    if command.name in ("fdisk", "parted", "gdisk", "sgdisk") and not (short_flag(command.args, "l") or has(command.args, "print")):
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
        if short_flag(args, "c"):
            return False
        # `-s` reads the script from stdin; anything after it is $1, $2, ...
        return short_flag(args, "s") or not operands(args) or operands(args)[0] == "-"
    if interpreter_kind(name):
        script_args = operands(args, frozenset({"-W", "-X", "-m", "-r", "-I", "-M"}))
        return not script_args or script_args[0] == "-"
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
    return None


def _decode_and_execute(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if not _executes_stdin(command):
        return None
    for source in _pipeline_sources(command, ctx):
        decodes = source.name in _DECODERS and (
            short_flag(source.args, "dDr") or long_flag(source.args, "--decode") or
            source.name in ("uudecode", "gunzip", "zcat", "rev"))
        if decodes:
            return f"{source.name} (decode) ... | {command.name}"
    return None


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #

_WORLD_WRITABLE = re.compile(r"^(?:[0-7]?[0-7][0-7][2367]|(?:[ugoa]*[ao][ugoa]*|)\+[rwxXst]*w)")
_SETUID = re.compile(r"^(?:[4267][0-7]{3}|[ugoa]*\+[rwxX]*s)")


def _chmod_dangerous(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name != "chmod":
        return None
    words = operands(command.args, frozenset({"--reference"}))
    if len(words) < 2:
        return None
    mode, targets = words[0], words[1:]
    recursive = short_flag(command.args, "R") or long_flag(command.args, "--recursive")
    if _SETUID.match(mode):
        return f"chmod {mode} (setuid/setgid)"
    if _WORLD_WRITABLE.match(mode) and (recursive or any(is_broad_target(t, ctx.cwd) for t in targets)):
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
    targets = operands(command.args, frozenset({"--reference", "--from"}))[1:]
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
    if command.name == "systemctl" and any(a in ("enable", "link", "preset", "edit", "set-property")
                                           for a in operands(command.args)[:1]):
        return f"systemctl {operands(command.args)[0]}"
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
    words = operands(command.args)
    if not words or words[0] not in verbs:
        return None
    if command.name == "yarn" and words[0] == "npm" and (len(words) < 2 or words[1] != "publish"):
        return None
    if has(command.args, "--dry-run", "-n") or long_flag(command.args, "--dry-run"):
        return None
    return f"{command.name} {words[0]}"


def _push_artifact(command: SimpleCommand, ctx: RuleContext) -> str | None:
    words = operands(command.args)
    if command.name in ("docker", "podman", "buildah") and words[:1] == ["push"]:
        return f"{command.name} push"
    if command.name == "gh" and words[:2] in (["release", "create"], ["release", "delete"], ["repo", "delete"]):
        return "gh " + " ".join(words[:2])
    return None


# --------------------------------------------------------------------------- #
# Bulk deletion that is not `rm -rf`
# --------------------------------------------------------------------------- #


def _find_starting_points(args: tuple[str, ...]) -> list[str]:
    points = []
    for arg in args:
        if arg.startswith(("-", "(", "!", ")")):
            break
        points.append(arg)
    return points or ["."]


def _find_delete(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name not in ("find", "gfind") or "-delete" not in command.args:
        return None
    points = _find_starting_points(command.args)
    follows = any(a in ("-L", "-follow", "-H") for a in command.args)
    outside = [p for p in points if not is_inside(expand(p, ctx.cwd), ctx.cwd) or is_broad_target(p, ctx.cwd)]
    if outside or follows:
        return f"find {' '.join(points)} -delete (outside the working directory)"
    return None


def _find_delete_local(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name in ("find", "gfind") and "-delete" in command.args:
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


def _kill_everything(command: SimpleCommand, ctx: RuleContext) -> str | None:
    if command.name == "kill" and any(a in ("-1", "1") for a in operands(command.args)):
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
    stops = command.args[:1] and command.args[0] in ("stop", "disable", "mask", "unload", "bootout")
    if command.name in ("systemctl", "launchctl") and stops and any(
        _SECURITY_SERVICES.match(os.path.basename(a).removesuffix(".service").removesuffix(".plist"))
        for a in command.args[1:]
    ):
        return f"{command.name} {command.args[0]} (security service)"
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
    DangerRule("decode-and-execute", CRITICAL, "decodes and runs hidden code", _FETCH_HINT,
               _decode_and_execute),
    DangerRule("fork-bomb", CRITICAL, "exhausts process limits", _DISK_HINT, _fork_bomb),
    DangerRule("chmod-dangerous", HIGH, "makes files world-writable, setuid, or re-permissions a broad tree",
               "Grant the narrowest permission that works (e.g. `chmod u+x script.sh`).", _chmod_dangerous),
    DangerRule("chown-broad", HIGH, "changes ownership of a system or home tree",
               "Change ownership of the specific files that need it.", _chown_broad),
    DangerRule("crontab-remove", HIGH, "crontab -r deletes every scheduled job without confirmation",
               "Use `crontab -l` to show the table and let the user edit it.", _crontab_remove),
    DangerRule("crontab-install", HIGH, "installing a crontab schedules code to run later", _PERSIST_HINT,
               _crontab_install),
    DangerRule("publish-package", HIGH, "publishes or unpublishes a package", _PUBLISH_HINT, _publish),
    DangerRule("find-delete-outside", HIGH, "find -delete outside the working directory or following symlinks",
               "Run with -print first and let the user delete.", _find_delete),
    DangerRule("shred", HIGH, "irrecoverably destroys file contents",
               "Use plain `rm` for files the task owns; let the user shred anything else.", _shred),
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
