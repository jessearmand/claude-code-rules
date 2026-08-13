---
name: lang-python
description: Python development with modern linting and type checking. Use when writing or reviewing Python code. Examples use uv+ruff+ty; adapt to pip/poetry and mypy/pyright as needed.
---

# Python Development

Write type-safe Python code with modern tooling.

## Core Principles

- Prefer `uv` for package management
- Prefer `httpx` over `requests` for HTTP
- Use types everywhere possible

## Tooling Reference

For detailed documentation on Python tooling, see the Astral skills:

- **`astral:uv`** - Package and project management (replaces pip, pipx, poetry, pyenv)
- **`astral:ruff`** - Linting and formatting (replaces flake8, black, isort)
- **`astral:ty`** - Type checking (replaces mypy, pyright)

## Validation Workflow

After implementing Python code:

```bash
# 1. Lint and format
uvx ruff check
uvx ruff format

# 2. Type check
uvx ty check
```

## If uv/ruff/ty Aren't Available

Translate the workflow to whatever the repository uses:

- Package management: `python -m pip`, Poetry, PDM
- Lint/format: Ruff, Black, Flake8
- Type checking: `ty`, mypy, pyright

Example equivalents:

```bash
# Install tools (one option)
python -m pip install ruff mypy

# Lint/format
ruff check && ruff format

# Type check
mypy .
```
