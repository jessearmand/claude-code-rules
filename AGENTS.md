# Repository Guidelines

## Project Structure & Module Organization
- `hooks/`: Claude Code hooks (Python) for command validation and file protection.
- `docs/`: Internal guides (ast-grep, TypeScript, Python, Swift, MCP notes).
- `commands/`: Quick how-to docs for common CLI tasks.
- `agents/`: Role/specialist prompts (e.g., code review, debugging, research) used as subagents.
- `plugins/`: Local Claude Code plugins (e.g., `explanatory-output-style`) with marketplace metadata under each plugin's `.claude-plugin/` directory.
- `.claude/`: Local Claude settings and template that integrates hooks and local marketplace.
- `scripts/`: Utility scripts (e.g., `update_settings_paths.py`).
- Root files: `README.md`, `CLAUDE.md`, `claude_desktop_mcp_config_converter.py`.

## Build, Test, and Development Commands
- Update local Claude settings paths from template (`.claude/settings.json.backup` → `.claude/settings.json`):
  - `uv run scripts/update_settings_paths.py`
- Run hooks locally (uses uv):
  - `echo '{"tool_name":"Bash","tool_input":{"command":"grep foo file"}}' | uv run hooks/bash_command_validator.py`
  - `echo '{"tool_input":{"file_path":".env"}}' | uv run hooks/file_protection.py`
- Convert Claude Desktop MCP config:
  - `uv run claude_desktop_mcp_config_converter.py > /dev/null`
- Source search:
  - Structural: `sg --pattern 'func $NAME($$) {$$}' --lang ts`
  - Text: `rg "pattern" path/ -n --hidden -g '!node_modules'`
- Checks (project-dependent; see `commands/check.md` for process):
  - JavaScript/TypeScript: `npm run check` / `yarn check` / `bun run check`, `bun run lint`
  - Python: `black`, `isort`, `flake8`, `mypy`
  - Rust: `cargo check`, `cargo clippy`
  - Go: `go vet`, `golint`
  - Swift: `swift-format`, `swiftlint`

## Coding Style & Naming Conventions
- Indentation: 4 spaces.
- Python: follow `docs/python-guide.md`; snake_case for files (`bash_command_validator.py`).
- TypeScript/JS (in docs/examples): follow `docs/typescript-guide.md`; lowerCamelCase for vars, PascalCase for types.
- Keep modules small; avoid inline styles; factor long functions into helpers.

## Testing Guidelines
- This repo is config/tooling focused; no formal test suite.
- Smoke-test hooks via the echo + `uv run` examples above.
- If adding Python tests, use `tests/test_*.py` pattern and `pytest -q`; target pure functions and error paths.
- Document sample inputs/outputs for new hooks in `commands/`.

## Agent Operating Guidelines
- Implement general-purpose, robust solutions; do not hard-code to tests.
- Ask for clarification if requirements are ambiguous or infeasible.
- Prefer principled algorithms and maintainable design over quick hacks.
- Use `rg` for text searches and `ast-grep/sg` for structural searches.
- Follow language guides in `docs/` (Python/TypeScript/Swift) and use formatters/linters where applicable.

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

## Subagents and Plugins
- Subagents live in `agents/` (e.g., `researcher.md`, `debugger.md`, `reviewer.md`, and specialist variants). Use them as role references or to generate task-specific assistants.
- Local plugin marketplace is configured in `.claude/settings.json` under `marketplaces` and `plugins`. See `README.md` for structure and development details.
- Example plugin: `plugins/explanatory-output-style` provides an Explanatory output style via a SessionStart hook. Enable/disable via `.claude/settings.json`.
