# grep.app JSON API contract

The undocumented-but-stable JSON API behind https://grep.app/ and the
`https://mcp.grep.app` MCP server.

## Endpoint

```
GET https://grep.app/api/search
```

Returns `application/json`. No API key. **WAF-gated:** plain HTTP clients
(`curl`, Python `urllib`) frequently get **HTTP 429**, while requests issued from a
real browser page (`fetch()` on a grep.app tab) succeed. Prefer the MCP server or
browser mode for reliable automation.

## Query parameters

| Param | Meaning | Example |
|---|---|---|
| `q` | Query (literal by default) | `q=useState(` |
| `regexp` | Treat `q` as regex (`true`) | `regexp=true` |
| `case` | Case-sensitive (`true`) | `case=true` |
| `words` | Whole-word match (`true`) | `words=true` |
| `f.lang` | Language filter — **repeatable** | `f.lang=TypeScript&f.lang=TSX` |
| `f.repo.pattern` | Repo filter, partial match | `f.repo.pattern=vercel/` |
| `f.path.pattern` | Path filter, partial match | `f.path.pattern=src/` |
| `format` | `json` (optional; JSON is default) | `format=json` |

Notes:
- `f.lang` must be repeated once per language. Exact bare `f.repo=owner/name` and
  `f.path=...` also filter, but `.pattern` variants allow partial matches and are
  what the script uses.
- Regex uses RE2-style syntax; prefix `(?s)` so `.` matches newlines (multi-line).

## Response shape

```jsonc
{
  "time": 179,                       // server time, ms
  "facets": {
    "path": { "buckets": [{ "val": "src/", "count": 201358 }, ...] },
    "repo": { "buckets": [{ "val": "keenthemes/reui", "count": 1503,
                            "owner_id": "34410960" }, ...] },
    "lang": { "buckets": [{ "val": "TSX", "count": 418066 }, ...] }
  },
  "hits": {
    "total": 646000,                 // total matching files (approx., rounded)
    "hits": [
      {
        "owner_id": "69631",
        "repo": "facebook/react-native",
        "branch": "main",
        "path": "packages/.../ScrollViewExample.js",
        "total_matches": "26",       // matches in this file (string)
        "content": { "snippet": "<table class=\"highlight-table\">...</table>" }
      }
    ]
  }
}
```

- `facets.*.buckets` power the language / repo / path sidebar; use them to decide
  how to narrow a broad search.
- `content.snippet` is **HTML**: a `<table class="highlight-table">` whose rows are
  `<tr data-line="N">` with a `.lineno` cell and a syntax-highlighted `<pre>` cell.
  Strip the tags to recover `(line_number, code_text)` pairs (see
  `scripts/grep_search.py` `_SnippetParser`).
- Build the GitHub source URL as
  `https://github.com/{repo}/blob/{branch}/{path}`.
