# python-ruff

Python language server plugin for Claude Code using [ruff](https://docs.astral.sh/ruff/).

## Features

- **Real-time diagnostics**: See linting errors and warnings as you edit
- **Code formatting**: Format Python files with ruff's fast formatter
- **Code actions**: Quick fixes for common issues
- **Project configuration**: Respects your `pyproject.toml` ruff settings

## Prerequisites

Install ruff (version 0.8.0+ required for `ruff server`):

```bash
# Using uv (recommended)
uv tool install ruff

# Using pip
pip install ruff

# Using pipx
pipx install ruff

# Using Homebrew
brew install ruff
```

Verify installation:

```bash
ruff --version
# Should show 0.8.0 or later
```

## Installation

```bash
claude /plugin install ~/.claude/plugins/python-ruff
```

Or test locally during development:

```bash
claude --plugin-dir ~/.claude/plugins/python-ruff
```

## Commands

| Command | Description |
|---------|-------------|
| `/python-ruff:check` | Run ruff check on current file or project |
| `/python-ruff:format` | Format current file with ruff |

## Configuration

The plugin respects your project's ruff configuration in `pyproject.toml` or `ruff.toml`. Example:

```toml
# pyproject.toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "W"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"
```

## Behavior

- **LSP integration**: Claude receives real-time diagnostics after each edit
- **PostToolUse hook**: Automatically shows ruff diagnostics after Python file edits (non-blocking)
- **Project-aware**: Uses your project's ruff configuration

## License

MIT
