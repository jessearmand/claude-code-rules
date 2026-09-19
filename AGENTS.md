# Repository Guidelines

## Project Structure & Module Organization
- `hooks/`: Claude Code hooks (Python) for command validation and file protection.
- `docs/`: Internal guides (ast-grep, TypeScript, Python, Swift, MCP notes).
- `skills/`: Agent skills. Most mirror the canonical harness-neutral copies in the `agent-config`
  repo; see "Skills sync" below.
- `agents/`: Role/specialist prompts (e.g., code review, debugging, research) used as subagents.
- `plugins/`: Local Claude Code plugins (e.g., `explanatory-output-style`) with marketplace metadata under each plugin's `.claude-plugin/` directory.
- `settings.template.json`: Claude settings template that integrates hooks and local marketplace. It is not loaded by Claude Code; render it with `scripts/update_settings_paths.py`.
- `scripts/`: Utility scripts (e.g., `update_settings_paths.py`, `sync-skills.sh` and the
  overlay patches under `scripts/skill-overlays/`).
- Root files: `README.md`, `CLAUDE.md`, `claude_desktop_mcp_config_converter.py`.

## Build, Test, and Development Commands
- Render the settings template with `${HOME}` expanded (stdout, or `--output <path>` which backs up an existing file):
  - `uv run scripts/update_settings_paths.py`
- Run hooks locally (uses uv):
  - `echo '{"tool_name":"Bash","tool_input":{"command":"grep foo file"}}' | uv run hooks/bash_command_validator.py`
  - `echo '{"tool_input":{"file_path":".env"}}' | uv run hooks/file_protection.py`
- Convert Claude Desktop MCP config:
  - `uv run claude_desktop_mcp_config_converter.py > /dev/null`
- Sync skills from the canonical `agent-config` repo (override the source with `AGENT_CONFIG=`):
  - `./scripts/sync-skills.sh`
- Source search:
  - Structural: `ast-grep run --pattern 'function $NAME($$$) { $$$ }' --lang ts`
  - Text: `rg "pattern" path/ -n --hidden -g '!node_modules'`
- Checks: follow the `check` skill; language tooling lives in the `lang-*` skills
  (`lang-python`, `lang-rust`, `lang-swift`, `lang-typescript`).

## Skills Sync
- `agent-config/skills/` is the canonical, harness-neutral source. Edit shared skill content
  there, then run `./scripts/sync-skills.sh` here; do not hand-edit a mirrored skill.
- `scripts/sync-skills.sh` mirrors each listed skill with `rsync --delete`, then re-applies the
  Claude Code overlays in `scripts/skill-overlays/<skill>.patch` (frontmatter such as
  `allowed-tools`/`disable-model-invocation`, plugin-skill pointers, and Claude-only files such
  as `xcode-build/xcode-mcp.md`). Running it twice is a no-op.
- Rules-only files inside a mirrored skill are listed in the script's `skill_excludes` so
  `--delete` keeps them (e.g. `grep-code-search/README.md`, `grep-code-search/scripts/package.sh`).
- Skills that exist only here (`anywidget-generator`, `marimo-check`) are untouched by the sync.
- If a patch fails to apply, the canonical skill changed inside an overlaid region: resolve
  `skills/<skill>` by hand, then regenerate the patch with
  `diff -ruN <agent-config>/skills/<skill> skills/<skill> > scripts/skill-overlays/<skill>.patch`.

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
- Use `rg` for text searches and `ast-grep` for structural searches.
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
- Prefer `rg` over `grep`; use `ast-grep` for language-aware code search.
- Keep secrets out of the repo; reference via env vars or local config.

## Subagents and Plugins
- Subagents live in `agents/` (e.g., `researcher.md`, `debugger.md`, `reviewer.md`, and specialist variants). Use them as role references or to generate task-specific assistants.
- Local plugin marketplace is configured in `settings.template.json` under `marketplaces` and `plugins`. See `README.md` for structure and development details.
- Example plugin: `plugins/explanatory-output-style` provides an Explanatory output style via a SessionStart hook. Enable/disable via `settings.template.json`.
