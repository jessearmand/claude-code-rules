---
name: lang-python
description: Python development with modern linting and type checking. Use when writing or reviewing Python code.
compatibility: Examples use uv+ruff+ty; adapt to pip/poetry and mypy/pyright as needed.
---

# Python Development

Write type-safe Python code with modern tooling.

## Core Principles

- Prefer `httpx` over `requests` for HTTP
- Use types everywhere possible
- Follow project conventions for tooling

## Tooling Reference

For detailed documentation on Python tooling, see the Astral skills:

- **`astral:uv`** - Package and project management (replaces pip, pipx, poetry, pyenv)
- **`astral:ruff`** - Linting and formatting (replaces flake8, black, isort)
- **`astral:ty`** - Type checking (replaces mypy, pyright)

## Validation Workflow

After implementing Python code:

```bash
# 1. Lint and format
uvx ruff check --fix
uvx ruff format

# 2. Type check
uvx ty check
```

## Fallback for Non-Astral Projects

If uv/ruff/ty aren't available, translate to project tooling:

| Astral Tool | Alternatives |
|-------------|-------------|
| `uv` | pip, poetry, pdm |
| `ruff` | black + flake8 + isort |
| `ty` | mypy, pyright |
