---
name: marimo-check
description: Check and fix marimo notebook issues. Use when working with marimo notebooks or when marimo check reports errors.
allowed-tools: Bash(uvx marimo check:*), Edit
---

# Marimo Check

## Context

This is the output of the "uvx marimo check --fix $ARGUMENTS" command:

!`uvx marimo check --fix $ARGUMENTS || true`

## Your Task

Only (!) if the context suggests we need to edit the notebook, read the file
$ARGUMENTS, then fix any warnings or errors shown in the output above. Do
not make edits or read the file if there are no issues.
