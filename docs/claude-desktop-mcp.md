## Configuring Claude MCP Servers with mise/asdf Node Installations

02 Mar 2025

When integrating Claude’s [Model Context Protocol](https://www.claudemcp.com/servers) (MCP) servers with _alternative_ Node.js installation methods like [mise](https://mise.jdx.dev/) or [asdf](https://asdf-vm.com/), you might encounter configuration challenges not addressed in the official documentation. This affects all Node.js-based MCP servers, including the filesystem, web browsing via Puppeteer, and other servers (and analogously for other languages managed with one of these tools).

## Following the docs

The Claude MCP [documentation](https://www.claudemcp.com/servers/filesystem) suggests this configuration for the filesystem server:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [\
        "-y",\
        "@modelcontextprotocol/server-filesystem",\
        "/Users/username/Desktop",\
        "/path/to/other/allowed/dir"\
      ]
    }
  }
}

```

The configuration is stored as JSON at this path on macOS `~/Library/Application\ Support/Claude/claude_desktop_config.json` and you need to restart Claude Desktop everytime you update it.

## The problem

However, when using mise (or similar tools like asdf), you might encounter this error in the logs:

```bash
2025-03-02T18:02:57.517Z [filesystem] [error] spawn npx ENOENT {"context":"connection","stack":"Error: spawn npx ENOENT
at ChildProcess._handle.onexit (node:internal/child_process:285:19)
at onErrorNT (node:internal/child_process:483:16)
at process.processTicksAndRejections (node:internal/process/task_queues:82:21)"}

```

This occurs because Claude MCP cannot find the `npx` executable in your PATH, as it’s managed by mise/asdf rather than installed globally. The default configuration assumes `npx` is available system-wide, but environment managers like mise and asdf isolate these tools in version-specific directories that aren’t automatically accessible to applications launched outside your shell environment.

(btw, you can find Claude’s log files at `/Library/Logs/Claude/*.log` on macOS)

## The work-around

For mise/asdf Node installations, you need to first install the MCP filesystem server globally.

```bash
npm install -g @modelcontextprotocol/server-filesystem

```

Then, update the MCP configuration to:

1. Point `command` directly to your Node executable
2. Set the first item in `args` to the full path of the MCP filesystem server script
3. Add any allowed directories as additional items in the `args` array

Here’s a working configuration:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "/Users/username/.local/share/mise/installs/node/18.20.2/bin/node",
      "args": [\
        "/Users/username/.local/share/mise/installs/node/18.20.2/lib/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js",\
        "/path/to/other/allowed/dir"\
      ]
    }
  }
}

```

### Finding Your Paths (with mise)

To locate your mise/asdf Node paths:

```bash
# For the node executable
which node

# For the MCP server module
find ~/.local/share/mise -name "index.js" | grep server-filesystem/dist/index.js

```

With this configuration, you should be able to successfully connect Claude MCP to your filesystem with version-managed Node installations.

## Note

This converter will not work with playwright MCP server,
in that case maybe run playwright with a docker container or mcp connector
