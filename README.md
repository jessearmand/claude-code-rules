## Repository Overview

This repository contains Claude Code hooks and configuration files designed to enhance the development experience when using Claude Code. The repository serves as a collection of utilities and validation tools rather than a traditional application codebase.

## Architecture

### Hook System
The repository implements Claude Code's hook system for validating and enhancing tool usage:

- **bash_command_validator.py**: A PreToolUse hook for the Bash tool that validates commands and suggests better alternatives:
  - Recommends `rg` (ripgrep) over `grep`
  - Suggests `rg --files` patterns over `find -name`
  - Recommends `ast-grep` for source code searching in Swift, Python, TypeScript, and Rust files

- **file_protection.py**: A file protection hook that prevents modification of sensitive files:
  - Blocks editing of `.env`, lock files (`package-lock.json`, `Package.resolved`, `bun.lock`, `Cargo.lock`), and `.git/` directory contents

### Configuration Files
- **ast-grep-rule.md**: Comprehensive documentation for ast-grep pattern syntax, including meta variables, pattern matching, and advanced usage examples

## Hook Configuration

To use these hooks in your Claude Code setup, add to your settings file:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "uv run $HOME/Develop/claude-code/hooks/bash_command_validator.py"
          }
        ]
      }
    ]
  }
}
```

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

