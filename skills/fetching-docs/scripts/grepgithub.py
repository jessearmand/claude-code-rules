#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests>=2.28.0",
#     "beautifulsoup4>=4.11.0",
#     "lxml>=4.9.0",
# ]
# ///
"""
grepgithub - Search across half a million GitHub repos using grep.app API

Usage:
    uv run grepgithub.py -q "search query" [options]

Examples:
    uv run grepgithub.py -q "useEffect cleanup"
    uv run grepgithub.py -q "async fn main" -flang Rust
    uv run grepgithub.py -q "import torch" -flang Python -json

Based on: https://github.com/popovicn/grepgithub
"""

import argparse
import json
import os
import re
import sys
import time
import uuid

import bs4
import requests

C_BANNER = "\033[35;1m"
C_REPO = "\033[37;1m"
C_LINE_NUM = "\033[31m"
C_LINE = "\033[37;2m"
C_MARK = "\033[32m"
C_RST = "\033[0m"

BANNER = """
   {bc}____ ____ ____ ___  {r}____ _ ___ _  _ _  _ ___ {r}
   {bc}| __ |__/ |___ |__] {r}| __ |  |  |__| |  | |__] {r}
   {bc}|__| |  \\ |___ |   {r} |__| |  |  |  | |__| |__] {r}

""".format(bc=C_BANNER, r=C_RST)


class OutStream:
    def __init__(self, output_file=None):
        self.output_file = open(output_file, "w") if output_file else None

    def write(self, content, *args):
        if args:
            print(content.format(*args))
            if self.output_file:
                self.output_file.write(content.format(*args) + "\n")
        else:
            print(content)
            if self.output_file:
                self.output_file.write(content + "\n")

    def close(self):
        if self.output_file:
            self.output_file.close()


class Hits:

    def __init__(self, monochrome=False):
        self.mark_start_placeholder = str(uuid.uuid4())
        self.mark_end_placeholder = str(uuid.uuid4())
        self.hits = {}
        self.monochrome = monochrome

    def _parse_snippet(self, snippet):
        matches = {}
        soup = bs4.BeautifulSoup(snippet, "lxml")
        for tr in soup.select("tr"):
            lineno_divs = tr.select("div.lineno")
            if not lineno_divs:
                continue
            line_num = lineno_divs[0].text.strip()
            pre_elements = tr.select("pre")
            if not pre_elements:
                continue
            line = pre_elements[0].decode_contents()
            if "<mark" not in line:
                continue
            else:
                line = re.sub(r"<mark[^<]*>", self.mark_start_placeholder, line)
                line = line.replace("</mark>", self.mark_end_placeholder)
                line = bs4.BeautifulSoup(line, "lxml").text
                if self.monochrome:
                    line = line.replace(self.mark_start_placeholder, "")
                    line = line.replace(self.mark_end_placeholder, "")
                else:
                    line = line.replace(self.mark_start_placeholder, C_RST + C_MARK)
                    line = line.replace(self.mark_end_placeholder, C_RST + C_LINE)
                matches[line_num] = line
        return matches

    def add_hit(self, repo, path, snippet):
        if repo not in self.hits:
            self.hits[repo] = {}
        if path not in self.hits[repo]:
            self.hits[repo][path] = {}
        for line_num, line in self._parse_snippet(snippet).items():
            self.hits[repo][path][line_num] = line

    def merge(self, hits2):
        for hit_repo, path_data in hits2.hits.items():
            if hit_repo not in self.hits:
                self.hits[hit_repo] = {}
            for path, lines in path_data.items():
                if path not in self.hits[hit_repo]:
                    self.hits[hit_repo][path] = {}
                for line_num, line in lines.items():
                    self.hits[hit_repo][path][line_num] = line


def fail(error_msg):
    print(f"Error: {error_msg}\033[0m", file=sys.stderr)
    sys.exit(1)


def fetch_grep_app(page, args, monochrome=False):
    params = {"q": args.query, "page": page}
    url = "https://grep.app/api/search"

    if args.use_regex:
        params["regexp"] = "true"
    elif args.whole_words:
        params["words"] = "true"

    if args.case_sensitive:
        params["case"] = "true"
    if args.repo_filter:
        params["f.repo.pattern"] = args.repo_filter
    if args.path_filter:
        params["f.path.pattern"] = args.path_filter
    if args.lang_filter:
        params["f.lang"] = args.lang_filter.split(",")

    try:
        response = requests.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        fail(f"Request failed: {e}")

    if response.status_code != 200:
        fail(f"HTTP {response.status_code} {url}")

    data = response.json()
    count = data["hits"]["total"]
    hits = Hits(monochrome=monochrome)
    for hit_data in data["hits"]["hits"]:
        # Handle both old format (nested dict) and new format (direct string)
        repo = hit_data["repo"]["raw"] if isinstance(hit_data["repo"], dict) else hit_data["repo"]
        path = hit_data["path"]["raw"] if isinstance(hit_data["path"], dict) else hit_data["path"]
        snippet = hit_data["content"]["snippet"]
        hits.add_hit(repo, path, snippet)

    if count > 10 * page:
        return page + 1, hits, count
    else:
        return None, hits, count


def main():
    parser = argparse.ArgumentParser(
        description="Search across GitHub repos using grep.app API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run grepgithub.py -q "useEffect cleanup"
  uv run grepgithub.py -q "async fn main" -flang Rust
  uv run grepgithub.py -q "import torch" -flang Python -json
  uv run grepgithub.py -q "def test_" -frepo "pytest-dev/pytest"
        """,
    )
    parser.add_argument("-q", dest="query", help="Query string (required)", required=True)
    parser.add_argument("-c", dest="case_sensitive", action="store_true", help="Case sensitive search")
    parser.add_argument("-r", dest="use_regex", action="store_true", help="Use regex query (cannot use with -w)")
    parser.add_argument("-w", dest="whole_words", action="store_true", help="Search whole words (cannot use with -r)")
    parser.add_argument("-frepo", dest="repo_filter", help="Filter by repository (e.g., facebook/react)")
    parser.add_argument("-fpath", dest="path_filter", help="Filter by path pattern")
    parser.add_argument(
        "-flang", dest="lang_filter", help="Filter by language (e.g., Python,Rust,JavaScript). Comma-separated"
    )
    parser.add_argument("-json", dest="json_output", action="store_true", help="Output as JSON")
    parser.add_argument("-o", dest="output_file", help="Output file path")
    parser.add_argument("-m", dest="monochrome", action="store_true", help="Monochrome output (no colors)")
    parser.add_argument(
        "--max-pages", dest="max_pages", type=int, default=100, help="Maximum pages to fetch (default: 100)"
    )
    args = parser.parse_args()

    if args.use_regex and args.whole_words:
        fail("Cannot use -r (regex) and -w (whole words) together")

    out_stream = OutStream(output_file=args.output_file)

    c_banner = "" if args.monochrome else C_BANNER
    c_repo = "" if args.monochrome else C_REPO
    c_line_num = "" if args.monochrome else C_LINE_NUM
    c_line = "" if args.monochrome else C_LINE
    c_mark = "" if args.monochrome else C_MARK
    c_rst = "" if args.monochrome else C_RST

    # Force monochrome for JSON output to avoid color codes in data
    use_monochrome = args.monochrome or args.json_output

    if not args.json_output:
        if not args.monochrome:
            out_stream.write(BANNER)
        out_stream.write("> Fetching 10/?")

    next_page, hits, count = fetch_grep_app(page=1, args=args, monochrome=use_monochrome)
    while next_page and next_page <= args.max_pages:
        time.sleep(1)
        if not args.json_output:
            out_stream.write("> Fetching {}/{}", 10 * next_page, count)
        next_page, page_hits, _ = fetch_grep_app(page=next_page, args=args, monochrome=use_monochrome)
        hits.merge(page_hits)

    if args.json_output:
        json_out = json.dumps(hits.hits, indent=2)
        out_stream.write(json_out)
    else:
        try:
            cli_w = os.get_terminal_size().columns
        except OSError:
            cli_w = 80

        separator = "_" * cli_w
        repo_ct = 0
        file_ct = 0
        line_ct = 0

        for repo, path_data in hits.hits.items():
            repo_ct += 1
            out_stream.write(separator)
            out_stream.write("")
            out_stream.write("{}{}{}", c_repo, repo, c_rst)
            for path, lines in path_data.items():
                file_ct += 1
                out_stream.write("    /{}", path)
                for line_num, line in lines.items():
                    line_ct += 1
                    num_fmt = str(line_num).rjust(4)
                    out_stream.write("      {}{}:{} {}{}{}", c_line_num, num_fmt, c_rst, c_line, line, c_rst)

        out_stream.write(separator)
        out_stream.write("")
        out_stream.write("> Repositories  {}{}{}", c_mark, repo_ct, c_rst)
        out_stream.write("> Files         {}{}{}", c_mark, file_ct, c_rst)
        out_stream.write("> Matched lines {}{}{}", c_mark, line_ct, c_rst)

    out_stream.close()


if __name__ == "__main__":
    main()
