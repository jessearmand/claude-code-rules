---
name: fetching-docs
description: Fetch library documentation and analyze Git repositories. Use Context7 MCP for library/API docs, gitingest for Git repos, grepgithub for cross-repo code search. Use when generating code, setup steps, or exploring unfamiliar codebases.
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

## grepgithub (Cross-Repository Code Search)

Be as specific as possible when using `grepgithub` because it enables searching across half a million GitHub repositories using the grep.app API. Useful for finding usage examples, implementation patterns, and how other projects solve similar problems.

```bash
# Basic search
uv run skills/fetching-docs/scripts/grepgithub.py -q "#[allow(clippy::fn_params_excessive_bools)]"

# Filter by language
uv run skills/fetching-docs/scripts/grepgithub.py -q "use rerun::{Color, GraphEdges, GraphNodes};" -flang Rust

# Filter by repository
uv run skills/fetching-docs/scripts/grepgithub.py -q "ProviderTransform" -frepo "sst/opencode"

# JSON output for parsing
uv run skills/fetching-docs/scripts/grepgithub.py -q "from ray.serve.llm import LLMConfig, build_openai_app" -flang Python -json

# Regex search
uv run skills/fetching-docs/scripts/grepgithub.py -q "def test_.*async" -r -flang Python
```

**Key options:**
- `-q QUERY` - Search query (required)
- `-c` - Case sensitive search
- `-r` - Use regex query
- `-w` - Search whole words
- `-frepo REPO` - Filter by repository (e.g., `facebook/react`)
- `-fpath PATH` - Filter by path pattern
- `-flang LANG` - Filter by language (comma-separated: `Python,Rust,JavaScript`)
- `-json` - Output as JSON
- `-o FILE` - Output to file
- `-m` - Monochrome output (no colors)
- `--max-pages N` - Limit pages fetched (default: 100, max 1000 results)

**Note:** API returns max 1000 matches. Make queries specific for best results.
