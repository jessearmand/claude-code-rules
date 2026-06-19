#!/usr/bin/env python3
"""
Search public GitHub code via the grep.app JSON API (Grep by Vercel).

This is the same backend that powers https://grep.app/ and the
https://mcp.grep.app MCP server. It needs no API key and no dependencies
beyond the Python standard library, so it works anywhere Python 3.8+ runs.

grep.app does LITERAL (grep-style) matching by default, not keyword search.
Search for code that literally appears in files (e.g. 'useState(',
'import React from'), not natural-language questions.

Examples:
    # Literal substring
    grep_search.py 'createServer('

    # Regex across multiple lines, TypeScript + TSX only
    grep_search.py '(?s)useEffect\\(\\(\\) => \\{.*removeEventListener' \\
        --regexp --lang TypeScript --lang TSX

    # Case-sensitive, scoped to one org and path
    grep_search.py 'CORS(' --case --repo vercel/ --path src/

    # Machine-readable output for further processing
    grep_search.py 'def main' --lang Python --json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

API_URL = "https://grep.app/api/search"
GITHUB_BLOB = "https://github.com/{repo}/blob/{branch}/{path}"


class _SnippetParser(HTMLParser):
    """Turn a grep.app highlight-table snippet (HTML) into (line_no, text) rows."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, str]] = []
        self._line: str | None = None
        self._in_lineno = False
        self._in_code = False
        self._lineno_buf: list[str] = []
        self._code_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._line = attrs.get("data-line")
            self._lineno_buf, self._code_buf = [], []
        elif tag == "div" and attrs.get("class") == "lineno":
            self._in_lineno = True
        elif tag == "pre":
            self._in_code = True

    def handle_endtag(self, tag):
        if tag == "div" and self._in_lineno:
            self._in_lineno = False
        elif tag == "pre":
            self._in_code = False
        elif tag == "tr" and self._line is not None:
            line_no = ("".join(self._lineno_buf).strip()
                       or (self._line or "").strip())
            self.rows.append((line_no, "".join(self._code_buf)))
            self._line = None

    def handle_data(self, data):
        if self._in_lineno:
            self._lineno_buf.append(data)
        elif self._in_code:
            self._code_buf.append(data)


def _snippet_to_lines(snippet: str) -> list[tuple[str, str]]:
    parser = _SnippetParser()
    parser.feed(snippet or "")
    return parser.rows


def build_url(args: argparse.Namespace) -> str:
    params: list[tuple[str, str]] = [("q", args.query)]
    if args.regexp:
        params.append(("regexp", "true"))
    if args.case:
        params.append(("case", "true"))
    if args.words:
        params.append(("words", "true"))
    for lang in args.lang or []:
        params.append(("f.lang", lang))
    if args.repo:
        # `.pattern` matches partial owner/name (e.g. 'vercel/' or 'react')
        params.append(("f.repo.pattern", args.repo))
    if args.path:
        params.append(("f.path.pattern", args.path))
    return API_URL + "?" + urllib.parse.urlencode(params)


def fetch(url: str, timeout: float = 30.0) -> dict:
    # grep.app throttles non-browser clients hard; mimic a browser request.
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://grep.app/",
            "Origin": "https://grep.app",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def render(data: dict, limit: int) -> str:
    hits = (data.get("hits") or {})
    total = hits.get("total", 0)
    items = hits.get("hits", [])[:limit]
    out: list[str] = [f"{total:,} total results (showing {len(items)})", ""]

    for h in items:
        repo = h.get("repo", "")
        branch = h.get("branch", "main")
        path = h.get("path", "")
        matches = h.get("total_matches", "")
        url = GITHUB_BLOB.format(repo=repo, branch=branch, path=path)
        header = f"{repo}  —  {path}"
        if matches:
            header += f"  ({matches} matches)"
        out.append(header)
        out.append(url)
        for line_no, text in _snippet_to_lines(h.get("content", {}).get("snippet", "")):
            out.append(f"  {line_no:>6} | {text}")
        out.append("")

    # Facets help the model narrow a too-broad search.
    facets = data.get("facets") or {}
    langs = facets.get("lang", {}).get("buckets", [])
    if langs:
        top = ", ".join(f"{b['val']} ({b['count']:,})" for b in langs[:8])
        out.append(f"Top languages: {top}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Search public GitHub code via the grep.app API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("query", help="Literal code pattern to search for (grep-style).")
    p.add_argument("--regexp", action="store_true",
                   help="Interpret query as a regular expression. Prefix '(?s)' to span lines.")
    p.add_argument("--case", action="store_true", help="Case-sensitive match.")
    p.add_argument("--words", action="store_true", help="Match whole words only.")
    p.add_argument("--lang", action="append", metavar="LANG",
                   help="Filter by language (repeatable): TypeScript, TSX, Python, Go, ...")
    p.add_argument("--repo", metavar="OWNER/NAME",
                   help="Filter by repo; partial ok ('vercel/' matches the org).")
    p.add_argument("--path", metavar="PATH",
                   help="Filter by file path; partial ok ('/route.ts' matches at any level).")
    p.add_argument("--limit", type=int, default=10, help="Max results to show (default 10).")
    p.add_argument("--json", action="store_true", help="Emit raw JSON from the API.")
    args = p.parse_args(argv)

    url = build_url(args)
    try:
        data = fetch(url)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(
                "grep.app returned 429 (Too Many Requests).\n"
                "Its WAF fingerprints clients and blocks plain HTTP libraries\n"
                "(curl/urllib) even when a browser on the same IP succeeds.\n"
                "Use the grep.app MCP server (searchGitHub tool) or run the query\n"
                "from inside a browser page instead — see SKILL.md 'Browser mode'.\n"
                f"Attempted URL: {url}",
                file=sys.stderr,
            )
        else:
            print(f"grep.app HTTP {e.code}: {e.reason}\nURL: {url}", file=sys.stderr)
        return 1
    except Exception as e:  # network / JSON errors surface plainly
        print(f"Error querying grep.app: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(render(data, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
