# Repository Guidelines

## Project Structure & Module Organization
- `hooks/`: Claude Code hooks (Python) for command validation and file protection.
- `docs/`: Internal guides (ast-grep, TypeScript, Python, Swift, MCP notes).
- `commands/`: Quick how-to docs for common CLI tasks.
- Root files: `README.md`, `CLAUDE.md`, `claude_desktop_mcp_config_converter.py`.

## Build, Test, and Development Commands
- Run hook locally (uses uv):
  - `echo '{"tool_name":"Bash","tool_input":{"command":"grep foo file"}}' | uv run hooks/bash_command_validator.py`
  - `echo '{"tool_input":{"file_path":".env"}}' | uv run hooks/file_protection.py`
- Convert Claude Desktop MCP config:
  - `uv run claude_desktop_mcp_config_converter.py > /dev/null`
- Source search:
  - Structural: `sg --pattern 'func $NAME($$) {$$}' --lang ts`
  - Text: `rg "pattern" path/ -n --hidden -g '!node_modules'`

## Coding Style & Naming Conventions
- Indentation: 4 spaces.
- Python: follow `docs/python-guidelines.md`; snake_case for files (`bash_command_validator.py`).
- TypeScript/JS (in docs/examples): follow `docs/typescript-guide.md`; lowerCamelCase for vars, PascalCase for types.
- Keep modules small; avoid inline styles; factor long functions into helpers.

## Testing Guidelines
- This repo is config/tooling focused; no formal test suite.
- Smoke-test hooks via the echo + `uv run` examples above.
- If adding Python tests, use `tests/test_*.py` pattern and `pytest -q`; target pure functions and error paths.
- Document sample inputs/outputs for new hooks in `commands/`.

## Commit & Pull Request Guidelines
- Commits: imperative mood, scope prefix when applicable.
  - Examples: `hooks: block find -name`, `docs: add ast-grep cheatsheet link`.
- PRs must include:
  - Summary, rationale, and linked issues.
  - Before/after examples (CLI output or JSON snippets).
  - Local test commands used to validate changes.

## Security & Configuration Tips
- File protection hook blocks edits to `.env`, lockfiles, and `.git/`.
- Prefer `rg` over `grep`; use `ast-grep/sg` for language-aware code search.
- Keep secrets out of the repo; reference via env vars or local config.
