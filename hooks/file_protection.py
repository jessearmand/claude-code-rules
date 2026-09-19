#!/usr/bin/env python3
"""
Claude Code Hook: File Edit Protection
======================================
PreToolUse hook for Edit / MultiEdit / Write / NotebookEdit. Blocks writes to
files that a tool owns and regenerates, so they must never be hand-edited.

Reads are deliberately NOT blocked: inspecting a lockfile to see which version
resolved is normal and useful. Only writes are refused.

This is the "generated files" category. Secrets and environment variables are a
separate concern handled by secrets_guard.py — .env is not listed here.

Covered ecosystems: npm, yarn, pnpm, bun, deno, Cargo, SwiftPM, uv, poetry,
pipenv, bundler (Ruby), composer, Go, Dart/Flutter, Nix, CocoaPods, Gradle,
plus git internals and node_modules.

Decision protocol: https://code.claude.com/docs/en/hooks#pretooluse-decision-control
A match emits `permissionDecision: "deny"` naming the command that should
regenerate the file instead. A non-match emits `{}` (defer).

Configuration (environment variables, set in the hook command):
  FILE_PROTECTION_DISABLE=1     turn the hook off entirely
  FILE_PROTECTION_ASK=true      prompt the user instead of denying outright
  FILE_PROTECTION_LOG_DIR=<dir> default ~/.claude/hooks-logs

Wire-up (~/.claude/settings.json):
  {
    "matcher": "Edit|MultiEdit|Write|NotebookEdit",
    "hooks": [{ "type": "command",
                "command": "python3 ~/.claude/hooks/file_protection.py",
                "timeout": 10 }]
  }
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from hook_protocol import (  # noqa: E402
    WRITE_TOOLS,
    Decision,
    action_verb,
    emit,
    env_flag,
    log_event,
    paths_from,
    read_input,
    tool_input,
    truncate,
)

HOOK_NAME = "file-protection"


@dataclass(frozen=True)
class ProtectedFile:
    """A path pattern that a tool owns, plus how to regenerate it properly."""

    rule_id: str
    pattern: re.Pattern[str]
    ecosystem: str
    reason: str
    regenerate: str


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _lock(rule_id: str, filenames: str, ecosystem: str, regenerate: str) -> ProtectedFile:
    """A lockfile rule, matched on basename."""
    return ProtectedFile(
        rule_id=rule_id,
        pattern=_rx(rf"(?:^|/)(?:{filenames})$"),
        ecosystem=ecosystem,
        reason="lockfile is generated and carries integrity hashes",
        regenerate=regenerate,
    )


# Order matters only for reporting: the first match wins, so keep the specific
# rules ahead of the broad directory rules.
PROTECTED_FILES: tuple[ProtectedFile, ...] = (
    # ---- JavaScript / TypeScript ----
    _lock("npm-lock", r"package-lock\.json|npm-shrinkwrap\.json", "npm",
          "`npm install` (or `npm ci` to install from the lockfile unchanged)"),
    _lock("yarn-lock", r"yarn\.lock", "yarn",
          "`yarn install`, or `yarn up <pkg>` to move a single dependency"),
    _lock("pnpm-lock", r"pnpm-lock\.yaml", "pnpm",
          "`pnpm install`, or `pnpm update <pkg>` for one dependency"),
    _lock("bun-lock", r"bun\.lock|bun\.lockb", "bun",
          "`bun install`, or `bun update <pkg>` for one dependency"),
    _lock("deno-lock", r"deno\.lock", "deno",
          "`deno install`, or `deno cache --reload <entrypoint>` to refresh. "
          "Edit `deno.json` imports instead — that file is yours to change"),

    # ---- Rust / Swift / Go ----
    _lock("cargo-lock", r"Cargo\.lock", "cargo",
          "`cargo update -p <crate>` for one crate, or `cargo build` to resolve. "
          "Edit `Cargo.toml` instead"),
    _lock("swiftpm-resolved", r"Package\.resolved", "swiftpm",
          "`swift package resolve`, or `swift package update <dep>`. "
          "Edit `Package.swift` instead"),
    _lock("go-sum", r"go\.sum", "go",
          "`go mod tidy`, or `go get <module>@<version>`. Edit `go.mod` instead"),

    # ---- Python ----
    _lock("uv-lock", r"uv\.lock", "uv",
          "`uv lock` to re-resolve, or `uv add <pkg>` / `uv remove <pkg>`. "
          "Edit `pyproject.toml` instead"),
    _lock("poetry-lock", r"poetry\.lock", "poetry",
          "`poetry lock`, or `poetry add <pkg>` / `poetry update <pkg>`. "
          "Edit `pyproject.toml` instead"),
    _lock("pipenv-lock", r"Pipfile\.lock", "pipenv",
          "`pipenv lock`, or `pipenv install <pkg>`. Edit `Pipfile` instead"),

    # ---- Ruby / PHP ----
    _lock("bundler-lock", r"Gemfile\.lock|gems\.locked", "bundler",
          "`bundle install`, or `bundle update <gem>` for one gem. "
          "Edit `Gemfile` instead — that file is yours to change"),
    _lock("composer-lock", r"composer\.lock", "composer",
          "`composer update`, or `composer require <pkg>`. Edit `composer.json` instead"),

    # ---- Other ecosystems ----
    _lock("dart-lock", r"pubspec\.lock", "pub",
          "`dart pub get` or `flutter pub get`. Edit `pubspec.yaml` instead"),
    _lock("nix-flake-lock", r"flake\.lock", "nix",
          "`nix flake update`, or `nix flake lock --update-input <input>`"),
    _lock("cocoapods-lock", r"Podfile\.lock", "cocoapods",
          "`pod install`, or `pod update <pod>`. Edit `Podfile` instead"),
    _lock("gradle-lock", r"gradle\.lockfile|gradle/verification-metadata\.xml", "gradle",
          "`./gradlew dependencies --write-locks`"),

    # ---- Tool-managed directories ----
    ProtectedFile(
        "git-internals",
        _rx(r"(?:^|/)\.git/"),
        "git",
        "files under .git/ are repository internals",
        "use a git command (`git config`, `git update-ref`, `git rebase`, …). "
        "Hand-editing .git/ can corrupt the repository",
    ),
    ProtectedFile(
        "node-modules",
        _rx(r"(?:^|/)node_modules/"),
        "npm",
        "node_modules/ is installed content, not source",
        "change the dependency and reinstall. To alter a package's behaviour, use a "
        "patch tool (`patch-package`, `pnpm patch`) so the change survives reinstall",
    ),
)


def check_path(path: str) -> ProtectedFile | None:
    """Return the rule that forbids writing `path`, if any."""
    if not path:
        return None
    normalized = path.replace(os.sep, "/")
    for rule in PROTECTED_FILES:
        if rule.pattern.search(normalized):
            return rule
    return None


def build_reason(rule: ProtectedFile, tool_name: str, path: str) -> str:
    return "\n".join(
        [
            f"📦 BLOCKED by file-protection [{rule.rule_id}] — cannot {action_verb(tool_name)}: "
            f"{rule.reason}.",
            f"    target: {truncate(path)}",
            "",
            f"Regenerate it with {rule.ecosystem} instead: {rule.regenerate}.",
            "",
            "Hand-edited lockfiles drift from the manifest and break reproducible "
            "installs on other machines and in CI. Reading this file is allowed — only "
            "writing is blocked.",
        ]
    )


def run(data: dict) -> Decision | None:
    """Return the generated-file policy decision for one tool call."""
    if env_flag("FILE_PROTECTION_DISABLE"):
        return None

    tool_name = data.get("tool_name", "")
    if tool_name not in WRITE_TOOLS:
        return None

    finding = next(
        (
            (rule, path)
            for path in paths_from(tool_input(data))
            if (rule := check_path(path))
        ),
        None,
    )
    if not finding:
        return None

    rule, path = finding
    decision = "ask" if env_flag("FILE_PROTECTION_ASK") else "deny"
    log_event(
        HOOK_NAME,
        {
            "outcome": decision,
            "rule": rule.rule_id,
            "ecosystem": rule.ecosystem,
            "tool": tool_name,
            "target": path[:200],
            "session_id": data.get("session_id"),
            "cwd": data.get("cwd"),
            "permission_mode": data.get("permission_mode"),
        },
        log_dir_env=("FILE_PROTECTION_LOG_DIR",),
    )
    return Decision(
        decision,
        build_reason(rule, tool_name, path),
        f"file-protection [{rule.rule_id}]: {rule.reason}",
        HOOK_NAME,
    )


def main() -> None:
    emit(run(read_input(HOOK_NAME)))


if __name__ == "__main__":
    main()
