---
name: lang-python
description: Python development with modern linting and type checking. Use when writing or reviewing Python code.
compatibility: Examples use uv+ruff+ty; adapt to pip/poetry and mypy/pyright as needed.
---

# Python Development

Write type-safe Python code with modern tooling.

## Core Principles

- Prefer `uv` for package management
- Prefer `httpx` over `requests` for HTTP
- Use types everywhere possible

## Validation Workflow

After implementing Python code:

```bash
# 1. Lint and format
uvx ruff check
uvx ruff format

# 2. Type check
uvx ty check
```

## If uv/ruff/ty Aren’t Available

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

## Package Management with uv

### Initialize Project

```bash
uv init example
cd example
uv add ruff
```

### Add Dependencies

```bash
uv add httpx
uv add --dev pytest
```

### Run Scripts

```bash
uv run script.py

# With ad-hoc dependency
uv run --with rich script.py

# With version constraint
uv run --with 'rich>12,<13' script.py
```

### Lock & Sync

```bash
uv lock
uv sync
```

## Linting with ruff

```bash
# Check for issues
uvx ruff check

# Auto-fix
uvx ruff check --fix

# Format code
uvx ruff format
```

## Type Checking with ty

```bash
# Check all files
uvx ty check

# Check specific file
uvx ty check example.py
```

`ty` runs on all Python files in the working directory or project (starting from `pyproject.toml`).

## Quick Reference

| Task | Command |
|------|---------|
| New project | `uv init project_name` |
| Add package | `uv add package_name` |
| Run script | `uv run script.py` |
| Lint | `uvx ruff check` |
| Format | `uvx ruff format` |
| Type check | `uvx ty check` |
