"""
Rule table for config_guard.py: the files an agent must not change about itself.

An agent that can edit its own settings, hooks or MCP servers can switch every
other guard off, grant itself permissions, or plant something that runs in
the next session (the CHAINDROP npm worm persisted through
`.claude/settings.json`). These rules cover the agent harnesses this repo
configures (Claude Code, Codex, omp, pi, opencode) and the places a process
can make itself run again later (shell startup files, git hooks, launchd and
systemd user units).

Patterns are matched against a `/`-separated path. Most are anchored on a
path suffix (`(?:^|/)\\.claude/settings...`) so they hold for `~/...`,
`$HOME/...`, an absolute path, or a project-relative one alike.

Levels:
  critical  the agent's own permission and hook configuration     -> deny
  high      other harness config and persistence locations        -> deny
  Set CONFIG_GUARD_ASK=1 to prompt instead of denying.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CRITICAL = "critical"
HIGH = "high"


@dataclass(frozen=True)
class ConfigRule:
    rule_id: str
    level: str
    pattern: re.Pattern[str]
    reason: str


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


_EDIT_YOURSELF = (
    "Tell the user exactly what to change and let them edit it outside the agent, "
    "or have them run the command themselves with `! <command>`."
)

CONFIG_RULES: tuple[ConfigRule, ...] = (
    # ---- critical: the running agent's own permissions, hooks and tools ----
    ConfigRule("claude-settings", CRITICAL,
               _rx(r"(?:^|/)\.claude/settings(?:\.local)?\.json$"),
               "Claude Code settings hold permissions, hooks and env for the agent itself"),
    ConfigRule("claude-managed-settings", CRITICAL,
               _rx(r"(?:^|/)(?:ClaudeCode|claude-code)/managed-(?:settings|mcp)\.json$"),
               "managed Claude Code policy"),
    ConfigRule("claude-user-state", CRITICAL, _rx(r"(?:^|/)\.claude\.json$"),
               "~/.claude.json holds MCP servers, trusted projects and allowed tools"),
    ConfigRule("claude-hooks", CRITICAL, _rx(r"(?:^|/)\.claude/hooks(?:/|$)"),
               "hook scripts are the guards themselves"),
    ConfigRule("claude-plugins", CRITICAL, _rx(r"(?:^|/)\.claude/plugins(?:/|$)"),
               "installed plugins can register hooks and MCP servers"),
    ConfigRule("mcp-config", CRITICAL, _rx(r"(?:^|/)\.mcp\.json$"),
               "project MCP servers run with the agent's privileges"),
    ConfigRule("codex-config", CRITICAL,
               _rx(r"(?:^|/)\.codex/(?:config\.toml|[^/]*\.config\.toml|hooks\.json|hooks/|rules/)"),
               "Codex approval, sandbox and hook configuration"),
    ConfigRule("omp-config", CRITICAL,
               _rx(r"(?:^|/)\.omp/(?:agent/)?(?:config\.ya?ml|settings\.json|extensions(?:/|$)|plugins(?:/|$)|hooks(?:/|$))"),
               "omp configuration, extensions and hooks"),
    ConfigRule("pi-config", CRITICAL,
               _rx(r"(?:^|/)\.pi/(?:agent/)?(?:settings\.json|extensions(?:/|$)|hooks(?:/|$)|packages(?:/|$))"),
               "pi configuration, extensions and hooks"),
    ConfigRule("opencode-config", CRITICAL,
               _rx(r"(?:^|/)(?:\.config/opencode/|\.opencode/)(?:opencode\.jsonc?$|plugins?(?:/|$))"
                   r"|(?:^|/)opencode\.jsonc?$"),
               "opencode configuration and plugins"),

    # ---- high: places that make a process run again later ----
    ConfigRule("shell-startup", HIGH,
               _rx(r"(?:^|/)\.(?:bashrc|bash_profile|bash_login|profile|zshrc|zshenv|zprofile|zlogin"
                   r"|kshrc|mkshrc|inputrc)$|(?:^|/)\.config/fish/(?:config\.fish|conf\.d/)"),
               "shell startup files run on every new shell"),
    ConfigRule("git-hooks", HIGH, _rx(r"(?:^|/)\.git/hooks(?:/|$)|(?:^|/)\.git/config$"),
               "git hooks and .git/config (core.hooksPath, aliases) run code on git commands"),
    ConfigRule("launch-agents", HIGH,
               _rx(r"(?:^|/)Library/Launch(?:Agents|Daemons)(?:/|$)"),
               "launchd jobs persist across reboots"),
    ConfigRule("systemd-units", HIGH,
               _rx(r"(?:^|/)\.config/systemd/user(?:/|$)|^/etc/systemd/"),
               "systemd units persist across reboots"),
    ConfigRule("cron", HIGH, _rx(r"^/(?:etc/cron|var/spool/cron|usr/lib/cron)"),
               "cron tables run commands on a schedule"),
    ConfigRule("ssh-config", HIGH, _rx(r"(?:^|/)\.ssh/(?:config|rc|environment)$"),
               "ssh client config can run commands (ProxyCommand, LocalCommand)"),
    ConfigRule("direnv-mise-config", HIGH,
               _rx(r"(?:^|/)\.config/(?:direnv|mise)/|(?:^|/)\.config/fnox/"),
               "global direnv/mise/fnox config runs or exposes code in every project"),
)

# Directories whose removal, move or permission change would take a protected
# path with it. `~` is expanded by the guard.
PROTECTED_ROOTS: tuple[str, ...] = (
    "~/.claude",
    "~/.codex",
    "~/.omp",
    "~/.pi",
    "~/.config/opencode",
    "~/.ssh",
    "~/Library/LaunchAgents",
    "~/.config/systemd/user",
)
# Relative to the session's working directory.
PROJECT_PROTECTED_ROOTS: tuple[str, ...] = (".claude", ".git/hooks")
