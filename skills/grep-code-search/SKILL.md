---
name: grep-code-search
description: Search real-world code across 1M+ public GitHub repositories using Grep by Vercel (grep.app). Use when you need real usage examples for an API/library, want to verify correct syntax or configuration, look for implementation patterns, or find how developers actually use a function, hook, or snippet. Searches literal code patterns (grep-style) and regular expressions, with filters for language, repository, and file path. Works in any coding agent via the grep.app MCP server, browser automation, or a zero-dependency Python CLI.
---

# Grep Code Search

Search literal code across more than a million public GitHub repositories using
[Grep by Vercel](https://grep.app/). This is **code search, not keyword search**:
match what literally appears in files (`useState(`, `import React from`), not
natural-language questions (`react tutorial`).

## When to use this skill

- Implementing an unfamiliar API/library and need real usage patterns.
- Unsure of correct syntax, parameters, or configuration for something specific.
- Looking for production examples and idioms across many codebases.
- Understanding how libraries/frameworks are wired together in practice.

Map questions to literal patterns before searching:

| Question | Search |
|---|---|
| How is auth done in Next.js apps? | `getServerSession` + lang TypeScript, TSX |
| Common React error boundary patterns? | `ErrorBoundary` + lang TSX |
| Real `useEffect` cleanup examples? | `(?s)useEffect\(\(\) => \{.*removeEventListener` (regexp) |
| How is CORS configured in Flask? | `CORS(` + case-sensitive + lang Python |

Search syntax cheatsheet:
- **Literal (default):** `useState(`, `export function`, `createServer(`.
- **Regex** (`--regexp` / `useRegexp: true`): prefix `(?s)` to match across lines,
  e.g. `(?s)try \{.*await`.
- **Filters:** language (e.g. `TypeScript`, `TSX`, `Python`, `Go`), repository
  (`facebook/react`, or partial `vercel/`), file path (`src/`, `/route.ts`).
- **Flags:** match case, match whole words.

## Access paths

Pick the first one available in your environment. They hit the same backend.

### 1. MCP server (preferred — programmatic, no browser)

The grep.app MCP server exposes a single tool, **`searchGitHub`**. It is the most
reliable programmatic path and needs no browser.

- Endpoint: `https://mcp.grep.app` (HTTP / StreamableHTTP transport).
- Tool: `searchGitHub` with arguments
  `{ query, matchCase, matchWholeWords, useRegexp, repo, path, language[] }`.
- If the tool is already connected in your agent, call it directly.
- To add it, see `references/mcp-setup.md` for your runtime (Cursor, Codex, Grok,
  Claude Code, VS Code, Windsurf, etc.).

### 2. Browser mode (the grep.app frontend)

Use when you want the interactive UI, full-file context, the language/repo/path
facet sidebar, or to follow results to GitHub — or when direct HTTP is blocked
(see note below). Drive a real browser with whatever automation your agent provides
(chrome-devtools MCP, `agent-browser`, Playwright, Puppeteer, etc.).

- Navigate the UI directly with query in the URL:
  `https://grep.app/search?q=<url-encoded-query>`
- Or get structured JSON by running `fetch()` **from inside the page** (this passes
  grep.app's WAF, which blocks plain HTTP clients):

  ```js
  // Run via browser evaluate_script / page.evaluate on a grep.app tab
  async () => {
    const r = await fetch('https://grep.app/api/search?q=' +
      encodeURIComponent('createServer(') + '&f.lang=TypeScript');
    return await r.json();
  }
  ```

See `references/browser-mode.md` for UI selectors and the full recipe.

### 3. Direct API script (best-effort)

`scripts/grep_search.py` queries the JSON API directly using only the Python
standard library (3.8+), so it runs anywhere without `pip install`. Resolve the
script path relative to this skill directory (e.g. `skills/grep-code-search/scripts/`
or `~/.agents/skills/grep-code-search/scripts/`).

```bash
# Literal search, limited to TypeScript
python3 scripts/grep_search.py 'createServer(' --lang TypeScript

# Regex across lines, two languages, scoped to an org and path
python3 scripts/grep_search.py '(?s)useEffect\(\(\) => \{.*removeEventListener' \
  --regexp --lang TypeScript --lang TSX --repo vercel/ --path src/

# Case-sensitive, whole word, raw JSON for further processing
python3 scripts/grep_search.py 'CORS(' --case --words --lang Python --json
```

Flags: `--regexp`, `--case`, `--words`, `--lang` (repeatable), `--repo`, `--path`,
`--limit`, `--json`.

> **WAF note:** grep.app fingerprints clients and may return **HTTP 429** to plain
> HTTP libraries (`curl`, `urllib`) even when a browser on the same IP succeeds. If
> the script reports 429, fall back to the **MCP server** (path 1) or **browser
> mode** (path 2). The script already mimics browser headers and reports this
> clearly when blocked.

## Recommended workflow

1. Turn the question into a literal pattern (or regex). Add `(?s)` for multi-line.
2. Run the search via the best available path (MCP → browser → script).
3. If too many results, narrow with `language`, `repo`, and/or `path` filters; the
   response's language facets show where matches concentrate.
4. Read the matched lines, then open the GitHub blob URL for full context.

## Reference files

- `references/grep-api.md` — JSON API contract: endpoint, parameters, response shape.
- `references/mcp-setup.md` — connecting the grep.app MCP server in any agent runtime.
- `references/browser-mode.md` — driving the grep.app UI with browser automation.
