## Repository Overview

This repository contains Claude Code skills, hooks, and configuration files designed to enhance the development experience when using Claude Code. The repository serves as a collection of reusable skills, validation hooks, and utilities rather than a traditional application codebase.

## Architecture

### Skills

Skills are invocable via `/skill-name` in Claude Code and provide structured workflows. Each skill lives in `skills/<name>/SKILL.md` with YAML frontmatter for metadata.

- **commit-staged** (`/commit-staged`): Commits staged changes using Conventional Commits format. Runs `/check` first, then reviews the staged diff and creates a well-structured commit message. User-initiated only (`disable-model-invocation: true`).

- **check** (`/check`): Runs project-specific code quality and security checks (linting, type checking, tests, formatting, builds). Can be invoked directly or by other skills like `commit-staged`. Supports JavaScript/TypeScript, Python, Rust, Go, and Swift projects.

- **marimo-check** (`/marimo-check <notebook>`): Runs `uvx marimo check --fix` on a marimo notebook and fixes any issues found. Accepts a notebook path as an argument. See [Marimo Check: Hook vs Skill](#marimo-check-hook-vs-skill) for how this relates to the automatic hook.

### Hook System

The repository implements Claude Code's hook system for validating and enhancing tool usage:

- **bash_command_validator.py** (PreToolUse): Validates Bash commands and suggests better alternatives:
  - Recommends `rg` (ripgrep) over `grep`
  - Suggests `rg --files` patterns over `find -name`
  - Recommends `ast-grep` for source code searching in Swift, Python, TypeScript, and Rust files

- **file_protection.py** (PreToolUse): Prevents modification of sensitive files:
  - Blocks editing of `.env`, lock files (`package-lock.json`, `Package.resolved`, `bun.lock`, `Cargo.lock`), and `.git/` directory contents

- **marimo-check.sh** (PostToolUse): Automatically runs `uvx marimo check` after any Edit or Write operation on marimo notebooks. Blocks the tool if checks fail, prompting Claude to fix the issues. Located at `skills/marimo-check/scripts/marimo-check.sh` (co-located with the marimo-check skill for maintainability). See [Marimo Check: Hook vs Skill](#marimo-check-hook-vs-skill) for details.

### Configuration Files
- **ast-grep-rule.md**: Comprehensive documentation for ast-grep pattern syntax, including meta variables, pattern matching, and advanced usage examples

## Setup

### Initial Configuration

This repository includes a `.claude/settings.json` template that uses `${HOME}` substitution variables for portability. To set up the configuration on your machine:

```bash
# Run the setup script to replace ${HOME} with your actual home directory
uv run scripts/update_settings_paths.py
```

This script will:
- Create a backup of the original settings file (`.claude/settings.json.backup`)
- Replace all `${HOME}` variables with your actual home directory path
- Update the settings file with machine-specific paths

The configuration includes:
- Hook integrations for bash command validation and file protection
- Local marketplace and plugin setup (including the explanatory-output-style plugin)
- Environment variables and custom status line configuration

### Linking Hooks on Windows

The hook commands in `settings.json` reference `${HOME}/.claude/hooks/*.py`, but the
hook sources live in this repository under `hooks/`. Rather than copying the files,
link `~/.claude/hooks` to the repo's `hooks/` directory (the same approach used for
skills) so edits in the repo take effect immediately.

On Windows, prefer a **directory junction** over a symbolic link: a junction needs no
Administrator rights and no Developer Mode, and behaves identically for a local
directory. A symbolic link requires elevation or Developer Mode.

**PowerShell (recommended — directory junction):**

```powershell
$link   = "$env:USERPROFILE\.claude\hooks"
$target = "$env:USERPROFILE\claude-code-rules\hooks"
# Remove any existing (empty) directory or stale link first
if (Test-Path $link) { Remove-Item $link -Recurse -Force }
New-Item -ItemType Junction -Path $link -Target $target
```

**cmd.exe alternative (also no elevation needed):**

```cmd
mklink /J "%USERPROFILE%\.claude\hooks" "%USERPROFILE%\claude-code-rules\hooks"
```

**Symbolic link (requires Administrator or Developer Mode):**

```powershell
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.claude\hooks" -Target "$env:USERPROFILE\claude-code-rules\hooks"
```

Verify the link resolves the hook scripts:

```powershell
Get-Item "$env:USERPROFILE\.claude\hooks" | Select-Object LinkType, Target
Test-Path "$env:USERPROFILE\.claude\hooks\bash_command_validator.py"  # -> True
```

> **Note:** `New-Item -ItemType Junction` fails if the target path already exists, so
> the `Remove-Item` step above is required even when `~/.claude/hooks` is an empty
> directory. If the hooks fail with `can't open file '.../.claude/hooks/...py'`, the
> link is missing or points at the wrong target.

The same junction approach links the other repo directories that Claude Code reads
from `~/.claude`:

```powershell
foreach ($d in "skills","agents","hooks") {
    $link   = "$env:USERPROFILE\.claude\$d"
    $target = "$env:USERPROFILE\claude-code-rules\$d"
    if (Test-Path $link) { Remove-Item $link -Recurse -Force }
    New-Item -ItemType Junction -Path $link -Target $target
}
```

### Linking Single Files on Windows

Junctions only work for **directories**. To mirror an individual file — for example
`~/claude-edit.cmd` pointing at this repo's `scripts/claude-edit.cmd` — use a
**hard link** instead. Like a junction, a hard link needs no elevation, but it
requires the link and target to live on the **same volume**:

```powershell
$link   = "$env:USERPROFILE\claude-edit.cmd"
$target = "$env:USERPROFILE\claude-code-rules\scripts\claude-edit.cmd"
if (Test-Path $link) { Remove-Item $link -Force }
New-Item -ItemType HardLink -Path $link -Target $target
```

> **Caveat:** A hard link is a second name for the same file data, so editing through
> either path updates both. However, if an editor saves by *replacing* the file
> (write-to-temp then rename) rather than editing in place, the two names diverge —
> re-run the command to relink. If you need replace-safe behavior, use a file
> symbolic link instead (`New-Item -ItemType SymbolicLink`), which requires
> Administrator rights or Developer Mode.

### Plugin Structure

This repository includes a local plugin marketplace for Claude Code customizations. The structure follows Claude Code's plugin system:

```
.claude-plugin/
  marketplace.json          # Marketplace definition
plugins/
  explanatory-output-style/ # Plugin directory
    .claude-plugin/
      plugin.json           # Plugin metadata
    hooks/
      hooks.json            # Hook definitions
    hooks-handlers/
      session-start.sh      # SessionStart hook implementation
    README.md               # Plugin documentation
```

All plugins are defined and maintained locally in this repository, making it easy to customize and extend functionality without depending on external repositories.

### Hook Configuration

The settings file configures these hooks for your Claude Code setup:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${HOME}/Develop/claude-code/hooks/bash_command_validator.py"
          }
        ]
      },
      {
        "matcher": "Edit|MultiEdit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${HOME}/Develop/claude-code/hooks/file_protection.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "${HOME}/Develop/claude-code/skills/marimo-check/scripts/marimo-check.sh"
          }
        ]
      }
    ]
  }
}
```

## Marimo Check: Hook vs Skill

The marimo-check functionality has two complementary components that serve different purposes:

| Concern | Hook (`marimo-check.sh`) | Skill (`/marimo-check`) |
|---------|--------------------------|------------------------|
| Trigger | Automatic on every Edit/Write | Manual invocation |
| Purpose | Guard: catch issues immediately | Fix: run `marimo check --fix` |
| Scope | All marimo notebooks, always | Specific file via argument |
| Configuration | PostToolUse hook in settings.json | Invoked with `/marimo-check <path>` |

**The hook** runs automatically after every Edit or Write operation. It detects whether the modified file is a marimo notebook (by checking for `import marimo` and `@app.cell`) and runs `uvx marimo check`. If the check fails (non-zero exit), it blocks the operation and tells Claude to fix the issue.

**The skill** is invoked manually when you want to run `uvx marimo check --fix` on a specific notebook. It shows the check output and, only if issues are found, reads the file and applies fixes.

Both are needed: the hook provides always-on validation, while the skill provides on-demand fixing. The hook script is co-located at `skills/marimo-check/scripts/marimo-check.sh` for maintainability, but must be configured as a PostToolUse hook in `settings.json` to function (see [Hook Configuration](#hook-configuration)).

## Development Commands

This repository doesn't contain traditional build/test commands as it's primarily a configuration repository. The Python hooks can be executed directly:

```bash
# Test bash command validator
echo '{"tool_name": "Bash", "tool_input": {"command": "grep pattern file.txt"}}' | uv run hooks/bash_command_validator.py

# Test file protection
echo '{"tool_input": {"file_path": ".env"}}' | uv run hooks/file_protection.py
```

## Tool Recommendations

The hooks enforce these tool preferences:
- Use `rg` (ripgrep) instead of `grep` for text searching
- Use `ast-grep` or `sg` for structural code searching and refactoring
- Use `rg --files` patterns instead of `find -name` for file discovery
- Leverage ast-grep for language-aware code analysis in Swift, Python, TypeScript, and Rust

## Hook Exit Codes

The hooks use specific exit codes for different behaviors:
- `0`: Allow tool execution
- `1`: Show error to user but not to Claude
- `2`: Block tool execution and show error to Claude

