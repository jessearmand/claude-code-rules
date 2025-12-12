---
name: fetching-docs
description: Fetch library documentation and analyze Git repositories. Use Context7 MCP for library/API docs, gitingest for Git repos. Use when generating code, setup steps, or exploring unfamiliar codebases.
---

# Fetching Documentation

## Context7 MCP (Library/API Docs)

Use Context7 MCP tools when working with library/API documentation:

1. **Resolve library ID**: `mcp__context7__resolve-library-id`
2. **Get documentation**: `mcp__context7__get-library-docs`

Automatically use these tools when generating new code, setup steps, or configuration from library documentation.

## gitingest (Git Repository Analysis)

Transform Git repositories into LLM-friendly text digests:

```bash
# GitHub repository
uv tool run gitingest https://github.com/user/repo

# Local directory
uv tool run gitingest /path/to/directory

# Specific branch
uv tool run gitingest https://github.com/user/repo -b main

# Private repositories
uv tool run gitingest https://github.com/user/repo --token $GITHUB_TOKEN
```

**Key options:**
- `-o, --output` - Output file path (default: `<repo>.txt`)
- `-e, --exclude-pattern` - Patterns to exclude
- `-i, --include-pattern` - Patterns to include
- `-b, --branch` - Specific branch to ingest
- `-t, --token` - GitHub PAT for private repos
