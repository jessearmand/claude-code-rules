---
name: commit-staged
description: Commit staged changes with clear description and body using Conventional Commits format.
disable-model-invocation: true
allowed-tools: Bash(git diff:*), Bash(git status:*), Bash(git log:*), Bash(git commit:*), Bash(git add:*), Read, Glob, Grep, Skill(check)
---

# Commit Staged

Commit staged changes with clear description and body

## Primary Task

Review the staged changes above, then commit with a clear description and body.
Describe the purpose of the changes, prioritize clarity, readability, and minimize ambiguity.

## Commit Message Guidelines (Conventional Commits)

- Use the format `<type>[optional scope]: <description>`.
- Use `feat` for new features and `fix` for bug fixes; other allowed types include `docs`, `refactor`, `chore`, `test`, `perf`, `ci`, `build`, and `style`.
- Add a scope in parentheses when it clarifies the area affected, e.g. `feat(parser):`.
- Keep the description within 100 column width limit, and immediately after the colon and space.
- Add a body when extra context is useful; start it one blank line after the description.
- Add footers after another blank line using trailer style (e.g. `Refs: #123`).
- Indicate breaking changes with a `BREAKING CHANGE:` footer.

Example:
```
feat(commands): add dry-run support for commit workflow

Explain how dry-run skips git writes and reports intended actions.

Refs: #412
```

## Important

- DO NOT add any other files that are not staged
- Focus on the staged changes identified by the diff above
- EXCEPTION: when there are modified files from the process of checking, ONLY add those modified files

## Process

1. Perform checks with /check or refer to project setup for running checks, tests, or linting
2. After checks are resolved, proceed with reviewing the staged diff above
3. Review modified files from the process of checking, ONLY add those modified files
4. Run git diff --staged and review the output
5. ONLY commit the staged changes
