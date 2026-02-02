---
description: Format Python files with ruff
argument-hint: [file-or-directory]
allowed-tools: Bash(ruff:*)
---

Format the specified Python file or directory using ruff format.

Target: $ARGUMENTS

If no target specified, format the current working directory.

First, preview changes with: `ruff format --diff $ARGUMENTS`

Show the diff to explain what formatting changes will be made.

Then ask if the user wants to apply the changes. If confirmed, run:
`ruff format $ARGUMENTS`

Report the files that were formatted.
