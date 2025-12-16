#!/bin/bash

# Read JSON input from stdin
input=$(cat)

# Extract basic information
username=$(whoami)
current_dir=$(echo "$input" | jq -r '.workspace.current_dir // .cwd')
dir_name=$(basename "$current_dir")
model_name=$(echo "$input" | jq -r '.model.display_name // "Claude"')

# Get git branch if in a git repository
git_branch=""
if git -C "$current_dir" rev-parse --git-dir >/dev/null 2>&1; then
    git_branch=$(git -C "$current_dir" --no-optional-locks branch --show-current 2>/dev/null || echo "detached")
    if [ -n "$git_branch" ]; then
        git_branch=" ($git_branch)"
    fi
fi

# Format and output the status line with dimmed colors
printf "\033[33m%s@%s%s | %s\033[0m" "$username" "$dir_name" "$git_branch" "$model_name"