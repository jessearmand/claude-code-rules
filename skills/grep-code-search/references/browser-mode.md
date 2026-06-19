# Browser mode — driving the grep.app frontend

Use the **chrome-devtools** MCP tools to operate https://grep.app/ as a real
browser. This is the way to get the interactive UI (facet sidebar, full-file
context, links to GitHub) and the only reliable way to reach the JSON API from
outside the MCP server, because grep.app's WAF blocks plain HTTP clients but allows
`fetch()` issued from within a grep.app page.

## A. Structured results via in-page fetch (recommended)

1. Navigate to any grep.app page (so the origin is loaded):
   `navigate_page` → `https://grep.app/`
2. Run the query with `evaluate_script`:

   ```js
   async () => {
     const params = new URLSearchParams();
     params.set('q', 'createServer(');        // literal pattern
     // params.set('regexp', 'true');         // optional flags
     // params.set('case', 'true');
     // params.set('words', 'true');
     for (const l of ['TypeScript', 'TSX']) params.append('f.lang', l);
     // params.set('f.repo.pattern', 'vercel/');
     // params.set('f.path.pattern', 'src/');
     const r = await fetch('https://grep.app/api/search?' + params.toString());
     return await r.json();   // { facets, hits: { total, hits: [...] } }
   }
   ```

   See `references/grep-api.md` for the response shape and how to turn each hit's
   HTML `content.snippet` into `(line, code)` pairs and a GitHub blob URL.

## B. Visual UI navigation

- Search by URL: `https://grep.app/search?q=<url-encoded-query>` (the textbox is
  prefilled). The page renders matched files with repo, path, and match counts.
- Toggle buttons in the search bar: **Match case**, **Match whole words**,
  **Use regular expression**.
- Left sidebar facets, each filterable:
  - **Repository** — list with match counts; a filter textbox.
  - **Path** — top path prefixes (`src/`, `packages/`, `app/`, ...).
  - **Language** — checkboxes (`TSX`, `JavaScript`, `Python`, ...) with counts.
- Each result row links the file path to its GitHub blob
  (`https://github.com/{repo}/blob/{branch}/{path}`) for full context.
- A view selector toggles result density (e.g. "Compact").

Prefer `take_snapshot` over screenshots to read results as text. Result pages can
be large; read snapshots in chunks if truncated.

## Notes

- The page itself fetches results via React Server Components (`?_rsc=` requests),
  so the visible network tab may not show a clean JSON call — use approach **A** to
  get JSON deterministically.
- No login or cookies are required; the API works anonymously from the page.
