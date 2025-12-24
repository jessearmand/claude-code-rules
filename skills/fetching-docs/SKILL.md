---
name: fetching-docs
description: Fetch library documentation and analyze Git repositories. Use Context7 MCP for library/API docs, gitingest for Git repos, grepgithub for cross-repo code search. Use when generating code, setup steps, or exploring unfamiliar codebases.
---

# Fetching Documentation

Use this skill to quickly gather trustworthy information needed to implement or debug code.

## General Workflow

1. Identify the exact question (API surface, config, version constraints, examples).
2. Prefer primary sources: official docs, release notes, and the library’s own repository.
3. Cross-check version compatibility with the target project.
4. Keep provenance: URLs, versions, and/or commit hashes for anything non-obvious.

## Library/API Documentation

- If your agent/runtime provides a documentation retrieval tool, use it to fetch the relevant API pages for the exact library + version you need.
- If you have Context7 available, the common flow is:
  - Resolve a library ID (e.g., `mcp__context7__resolve-library-id`)
  - Fetch focused docs (e.g., `mcp__context7__get-library-docs`)
- Otherwise, use the library’s official docs site (or web search) and verify the version matches the project.

## Repository Analysis

### Local repositories

- Use fast code search (e.g., `rg`, `git grep`, IDE search) to find relevant symbols.
- Read `README`, `CHANGELOG`, and `docs/` before diving into implementation.

### Remote repositories (no local checkout)

- If you have a “repo-to-text” ingester (e.g., `gitingest`), generate a digest to reduce context switching.
- Otherwise, clone the repository locally and inspect it with normal tooling.

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

Use cross-repo search to find real-world usage patterns (configuration snippets, migration examples, gotchas).

- Be specific: search for exact imports, error messages, or function names.
- Expect rate limits and result caps; refine queries iteratively.

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
