# Connecting the grep.app MCP server

The server exposes one tool, `searchGitHub`, over HTTP at `https://mcp.grep.app`.
No authentication required.

## Tool: `searchGitHub`

| Argument | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | Literal code pattern (or regex if `useRegexp`). Required. |
| `matchCase` | boolean | `false` | Case-sensitive match. |
| `matchWholeWords` | boolean | `false` | Whole-word match. |
| `useRegexp` | boolean | `false` | Treat `query` as regex; prefix `(?s)` for multi-line. |
| `repo` | string | — | Repo filter; partial ok (`vercel/` matches the org). |
| `path` | string | — | Path filter; partial ok (`/route.ts` at any level). |
| `language` | string[] | — | e.g. `["TypeScript","TSX"]`, `["Python"]`. |

## Generic HTTP MCP config

Most agents accept a remote MCP server over HTTP. Add this block to your agent's
MCP configuration (exact file and schema vary by runtime):

```json
{
  "mcpServers": {
    "grep": {
      "type": "http",
      "url": "https://mcp.grep.app"
    }
  }
}
```

Some runtimes use `"transport": "http"` instead of `"type": "http"`. Check your
agent's MCP docs if the server does not connect.

## Runtime-specific locations

| Runtime | Config location | Notes |
|---|---|---|
| **Cursor** | Project `.cursor/mcp.json` or global MCP settings | Restart or reload MCP after saving. |
| **Claude Code** | Project `.mcp.json` or `claude mcp add --transport http grep https://mcp.grep.app` | CLI: `claude mcp list` to verify. |
| **Claude Desktop** | `claude_desktop_config.json` → `mcpServers` | Settings → Developer → Edit Config. |
| **Codex / OpenAI agents** | Agent MCP config (e.g. `~/.codex/config.toml` or project MCP JSON) | Use HTTP transport when available. |
| **Grok Build** | Project or user MCP config per Grok docs | Same HTTP endpoint. |
| **VS Code (Copilot / MCP)** | `.vscode/mcp.json` or user MCP settings | Follow VS Code MCP extension schema. |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | Same `url` field. |

After adding the server, restart the agent or reload MCP servers. The
`searchGitHub` tool should appear in the tool list.

## Verify connectivity

1. Confirm `searchGitHub` is listed among available MCP tools.
2. Run a small query: `query: "useState("`, `language: ["TypeScript"]`.
3. If the tool is missing, check transport type (`http` vs `stdio`) and that the
   URL is exactly `https://mcp.grep.app`.

## Programmatic use (optional)

If you build agent apps with the Vercel AI SDK, you can wrap the same server with
an MCP client or call the JSON API directly (see `references/grep-api.md`). The MCP
server is the most reliable path when WAF blocks plain HTTP clients.
