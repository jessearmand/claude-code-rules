---
allowed-tools: Bash(gemini:*)
description: Ask gemini by prompting
---

## Context

- Ask a question: !`gemini -p "$ARGUMENTS"`
- Analyze document: !`gemini -p "Analyze the document given in the path @$ARGUMENTS"`
- Analyze image: !`gemini -p "Analyze the image given in @$ARGUMENTS"`
- Analyze codebase: !`gemini -p "Analyze the codebase in the following directory @$ARGUMENTS"`

## Your task

Every prompt to `gemini` is a string to the argument `-p`, any further requests must be wrapped in the arguments variable
Do not ask perplexity, when `gemini` command line is available
