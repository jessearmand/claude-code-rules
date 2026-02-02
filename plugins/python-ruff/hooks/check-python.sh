#!/bin/bash
# PostToolUse hook to run ruff diagnostics on Python files after Edit/Write
# Non-blocking: shows diagnostics but doesn't prevent Claude from continuing

set -euo pipefail

# Read hook input from stdin
input=$(cat)

# Extract the file path from tool_input
file_path=$(echo "$input" | jq -r '.tool_input.file_path // .tool_input.path // empty')

# Exit silently if no file path or not a Python file
if [[ -z "$file_path" ]]; then
    exit 0
fi

if [[ ! "$file_path" =~ \.(py|pyi)$ ]]; then
    exit 0
fi

# Check if file exists
if [[ ! -f "$file_path" ]]; then
    exit 0
fi

# Check if ruff is available
if ! command -v ruff &> /dev/null; then
    echo '{"systemMessage": "ruff not found in PATH. Install with: pip install ruff"}'
    exit 0
fi

# Run ruff check and capture output
diagnostics=$(ruff check "$file_path" --output-format=concise 2>&1) || true

# If there are diagnostics, output them for Claude
if [[ -n "$diagnostics" ]]; then
    # Escape for JSON
    escaped_diagnostics=$(echo "$diagnostics" | jq -Rs .)
    echo "{\"systemMessage\": \"Ruff diagnostics for $file_path:\\n$diagnostics\"}"
else
    # No issues found
    exit 0
fi
