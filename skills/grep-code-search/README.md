# grep-code-search skill

A packaged [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
that lets Claude search real-world code across 1M+ public GitHub repositories with
[Grep by Vercel](https://grep.app/) — usable in **Claude Desktop** and **Cowork**.

It wraps both faces of grep.app:
- the **`mcp.grep.app` MCP server** (`searchGitHub` tool), and
- the **grep.app frontend** (browser automation + the JSON API).

## Layout

```
grep-code-search/
├── SKILL.md                  # skill manifest + instructions (loaded by Claude)
├── README.md                 # this file
├── scripts/
│   ├── grep_search.py        # zero-dependency CLI for the JSON API (Python 3.8+)
│   └── package.sh            # build the distributable .zip
└── references/
    ├── grep-api.md           # JSON API contract
    ├── mcp-setup.md          # connect the MCP server in any agent runtime
    └── browser-mode.md       # drive the grep.app UI with browser automation
```

## Install

### Claude Desktop / Cowork (as a Skill)

1. Build the archive:
   ```bash
   bash scripts/package.sh
   ```
2. In Claude Desktop: **Settings → Capabilities → Skills → Upload** and select
   `../grep-code-search.zip`. (Cowork: add it as a skill the same way.)
3. (Recommended) Also connect the MCP server for the most reliable path — see
   `references/mcp-setup.md`.

### Claude Code (local)

Place the `grep-code-search/` folder under `.claude/skills/` in your project (or
`~/.claude/skills/` for all projects). Connect the MCP server separately — either
`claude mcp add --transport http grep https://mcp.grep.app` or a project `.mcp.json`
(see `references/mcp-setup.md`).

## Quick try

```bash
python3 scripts/grep_search.py 'useState(' --lang TypeScript --lang TSX --limit 5
```

If you get HTTP 429, grep.app's WAF is blocking the plain HTTP client — use the MCP
server or browser mode instead (both documented in `SKILL.md`).
