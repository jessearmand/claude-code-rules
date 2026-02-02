---
description: Run ruff linting checks on Python files
argument-hint: [file-or-directory]
allowed-tools: Bash(ruff:*)
---

Run ruff check on the specified Python file or directory.

Target: $ARGUMENTS

If no target specified, run on the current working directory.

Execute: `ruff check $ARGUMENTS --output-format=grouped`

Analyze the output and:
1. Summarize the issues found by category (errors, warnings, style)
2. Highlight the most critical issues that should be fixed first
3. For each issue type, explain what it means and how to fix it
4. If there are auto-fixable issues, mention that `ruff check --fix` can resolve them
