# Browser mode — driving the grep.app frontend

Use browser automation to operate https://grep.app/ as a real user. This gives you
the interactive UI (facet sidebar, full-file context, links to GitHub) and is a
reliable way to reach the JSON API when grep.app's WAF blocks plain HTTP clients
but allows `fetch()` issued from within a grep.app page.

Works with any browser automation your agent supports:

- **chrome-devtools MCP** — `navigate_page`, `evaluate_script`
- **agent-browser CLI** — `agent-browser open`, `agent-browser eval`
- **Playwright / Puppeteer** — `page.goto`, `page.evaluate`
- **browser-use** or similar agent browser tools

## A. Structured results via in-page fetch (recommended)

1. Navigate to any grep.app page (so the origin is loaded):
   `https://grep.app/`
2. Run the query with JavaScript evaluation in the page context:

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

### agent-browser example

```bash
agent-browser open https://grep.app/
agent-browser eval "fetch('https://grep.app/api/search?q=' + encodeURIComponent('createServer(') + '&f.lang=TypeScript').then(r => r.json())"
```

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

Prefer accessibility snapshots or DOM text extraction over screenshots when reading
results. Result pages can be large; read output in chunks if truncated.

## Notes

- The page itself fetches results via React Server Components (`?_rsc=` requests),
  so the visible network tab may not show a clean JSON call — use approach **A** to
  get JSON deterministically.
- No login or cookies are required; the API works anonymously from the page.
