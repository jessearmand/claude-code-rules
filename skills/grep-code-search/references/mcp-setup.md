# Connecting the grep.app MCP server

The server exposes one tool, `searchGitHub`, over StreamableHTTP at
`https://mcp.grep.app`. No authentication required.

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

## Claude Desktop

Add to the MCP servers config (Settings → Developer → Edit Config, i.e.
`claude_desktop_config.json`):

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

Restart Claude Desktop. The `searchGitHub` tool then appears and the model can call
it directly.

## Cowork / Claude Code (`.mcp.json`)

In the project root, add a `.mcp.json`:

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

## CLI quick check (Claude Code)

```bash
claude mcp add --transport http grep https://mcp.grep.app
```

## Using the generated AI SDK wrapper (programmatic)

This repo also ships a typed Vercel AI SDK wrapper for the same server under
`mcps/mcp.grep.app/` (`mcpGrepTools.searchGitHub`). Import it directly in an AI SDK
app instead of configuring an MCP client by hand.
