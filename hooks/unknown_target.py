"""
Writes whose target path cannot be worked out, judged by what the command does.

`> "$OUT"` with OUT never set on the line could be any file, an agent's
settings included. Asking for every such write would interrupt ordinary
logging (`echo done >> "$LOG"`), so the command decides:

- removing, moving, linking, re-permissioning or copying something into an
  unknown place asks: these replace or destroy whatever is there;
- writers that copy content from elsewhere (cp, install, rsync, dd, tee of a
  download, curl -o, archive extraction, in-place editors) ask;
- a redirect or `tee` asks when the text written comes from a download, a
  decoder or a file copy, or reads like agent configuration or a shell
  startup/persistence entry (in the command's arguments or heredoc);
- anything else, such as program output or a status line, passes.

A path under a known directory other than the root, home or the current one
(`/tmp/$NAME`) is left to the ordinary path rules. `$(mktemp)` is resolved by
the parser.
"""

from __future__ import annotations

import os
import re

from shell_parse import ParseResult, SimpleCommand
from shell_writes import WRITE, PathWrite

_UNRESOLVED = re.compile(r"\$|`")
# Text that configures an agent or runs at login/startup.
_CONFIG_TEXT = re.compile(
    r"hooks|PreToolUse|PostToolUse|UserPromptSubmit|permissions|mcpServers|bypassPermissions|"
    r"dangerously|apiKeyHelper|statusLine|allowedTools|settings\.json|\.mcp\.json|CLAUDE\.md|AGENTS\.md|"
    r"\balias\s+\w+=|\bexport\s+\w+=|\bPATH=|\bsource\s|authorized_keys|LaunchAgents|LaunchDaemons|"
    r"@reboot|crontab|systemctl|\.bashrc|\.zshrc|\.profile|\.zshenv",
    re.IGNORECASE,
)
# Programs whose output is content brought from elsewhere.
_CONTENT_SOURCES = frozenset({
    "curl", "wget", "fetch", "aria2c", "http", "https", "xh", "nc", "ncat", "socat",
    "base64", "base32", "xxd", "openssl", "uudecode", "gunzip", "zcat", "bzcat", "xzcat", "unzip",
})
_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})


def is_unknown(path: str) -> bool:
    """Where `path` lands cannot be told apart from an agent's configuration:
    nothing before its first computed part is known (`$OUT`, `$DIR/x.json`),
    or only the root, the home or the current directory is (`~/$X`, `./$X`),
    which is where agent configuration lives. `/tmp/$NAME` and `logs/$NAME`
    stay in a known directory."""
    segments = path.split("/")
    first = next((i for i, segment in enumerate(segments) if _UNRESOLVED.search(segment)), None)
    if first is None:
        return False
    known = "/".join(segments[:first])
    return known in ("", ".", "~") or os.path.expanduser(known) == os.path.expanduser("~")


def reason(command: SimpleCommand, write: PathWrite, parsed: ParseResult) -> str | None:
    """Why this write to an unknown target deserves a look, or None if it does not."""
    if write.action != WRITE:
        return f"the target is not known, and `{command.name}` {_ACTION_WORDS.get(write.action, 'changes')} it"
    stream = write.path in {r.target for r in command.redirects if r.op in _REDIRECTS} or command.name == "tee"
    if not stream:
        return f"the target is not known, and `{command.name}` replaces its contents"
    for producer in _producers(command, parsed):
        if producer.name in _CONTENT_SOURCES or (producer.name == "cat" and _file_operands(producer)):
            return f"the target is not known, and the text written comes from `{producer.name}`"
        if any(_CONFIG_TEXT.search(text) for text in _texts(producer)):
            return "the target is not known, and the text written reads like agent configuration or a startup file"
    return None


_ACTION_WORDS = {
    "delete": "deletes",
    "delete-tree": "deletes everything under",
    "move": "moves away",
    "permissions": "changes the permissions of",
    "link": "links",
    "into-dir": "copies files into",
}


def _producers(command: SimpleCommand, parsed: ParseResult) -> list[SimpleCommand]:
    """The command writing the text, and for `tee` or a bare `cat`, the commands
    piping into it."""
    found = [command]
    current = command
    while current.name in ("tee", "cat") and not _file_operands(current):
        previous = parsed.previous_stage(current)
        if previous is None:
            break
        found.append(previous)
        current = previous
    return found


def _file_operands(command: SimpleCommand) -> list[str]:
    return [a for a in command.args if not a.startswith("-") or a == "-"] if command.name == "cat" else []


def _texts(command: SimpleCommand) -> list[str]:
    """The arguments and heredoc bodies of `command`: what it may write."""
    return [" ".join(command.argv), *(r.body for r in command.redirects if r.body)]
