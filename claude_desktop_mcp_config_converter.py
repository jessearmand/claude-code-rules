#!/usr/bin/env python3
"""
Convert all MCP server entries that use `npx` into the
“node <package‑dist‑index.js> …” form required when the
`npx` executable is not on the PATH (e.g. when using mise/asdf).

The script now also:
  • Installs each required MCP server globally via `npm install -g`.
  • Locates the `node` binary with `which node`.
  • Finds the server’s `dist/index.js` using the `find … | grep` pattern
    you specified.

Note:
This converter will not work with playwright MCP server,
in that case maybe run playwright with a docker container or mcp connector
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List

# --------------------------------------------------------------------------- #
# Paths (relative to the repository root)
# --------------------------------------------------------------------------- #
CONFIG_PATH = Path("claude_desktop_config.json")
OUTPUT_PATH = Path("claude_desktop_config_converted.json")


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def run_shell(command: str) -> str:
    """
    Execute a shell command and return its stdout (stripped).
    Raises subprocess.CalledProcessError on failure.
    """
    # Using `bash -lc` ensures the user's shell init files are sourced,
    # which is important for tools like `mise` that modify PATH.
    result = subprocess.check_output(
        ["bash", "-lc", command], text=True, stderr=subprocess.STDOUT
    )
    return result.strip()


def locate_node_executable() -> str:
    """
    Return an absolute path to the `node` binary.
    First try `which node` via a shell (covers mise/asdf), then fall back
    to a hard‑coded path.
    """
    try:
        node_path = run_shell("which node")
        if node_path:
            return node_path
    except subprocess.CalledProcessError:
        pass

    # Fallback – adjust if your mise/asdf install lives elsewhere
    fallback = "/Users/jeesearmand/.local/share/mise/installs/node/22.14.0/bin/node"
    if Path(fallback).exists():
        return fallback

    raise FileNotFoundError(
        "Unable to locate the `node` executable. Ensure it is on your PATH "
        "or adjust the fallback path in `locate_node_executable()`."
    )


def install_npx_server(package_name: str) -> None:
    """
    Install the given MCP server globally using npm.
    This mirrors the work‑around step:
        npm install -g <package>
    """
    print(f"[INFO] Installing MCP server package '{package_name}' globally …")
    subprocess.run(
        ["npm", "install", "-g", package_name],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    print(f"[INFO] Installation of '{package_name}' completed.")


def resolve_global_package_entrypoint(pkg_name: str) -> Path:
    """
    Locate the `dist/index.js` file for a globally‑installed MCP server.
    Uses the pattern you supplied:

        find ~/.local/share/mise -name "index.js" | grep server-filesystem/dist/index.js

    The function works for any package name by grepping for
    `<pkg_name>/dist/index.js`.
    """
    # Build the grep pattern – we escape any forward slashes in the pkg name.
    grep_pattern = f"{pkg_name}/dist/index.js"

    # The `find` command searches under the typical mise directory.
    find_cmd = (
        f'find ~/.local/share/mise -name "index.js" | grep "{grep_pattern}" | head -n 1'
    )
    try:
        entrypoint_str = run_shell(find_cmd)
        if not entrypoint_str:
            raise FileNotFoundError
        entrypoint = Path(entrypoint_str)
        if not entrypoint.is_file():
            raise FileNotFoundError
        return entrypoint
    except subprocess.CalledProcessError:
        raise FileNotFoundError(
            f"Could not locate entry point for package '{pkg_name}'. "
            f"Tried command: {find_cmd}"
        )


def convert_npx_server(server_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform a single MCP server configuration that uses `npx` into the
    node‑script form, installing the package first and locating the
    appropriate binaries.
    """
    # ------------------------------------------------------------------- #
    # 1️⃣ Detect the `npx` command
    # ------------------------------------------------------------------- #
    command_path = Path(server_cfg["command"])
    if command_path.name != "npx":
        # Not an npx‑based server – return unchanged
        return server_cfg

    # ------------------------------------------------------------------- #
    # 2️⃣ Parse original args to extract the package name and extra args
    # ------------------------------------------------------------------- #
    original_args: List[str] = server_cfg.get("args", [])
    if not original_args:
        raise ValueError("npx‑based server has no arguments to work with.")

    # Pattern can be ["-y", "<package>", ...] or ["<package>", ...]
    if original_args[0] == "-y":
        package_name = original_args[1]
        extra_args = original_args[2:]
    else:
        package_name = original_args[0]
        extra_args = original_args[1:]

    # ------------------------------------------------------------------- #
    # 3️⃣ Ensure the package is installed globally
    # ------------------------------------------------------------------- #
    install_npx_server(package_name)

    # ------------------------------------------------------------------- #
    # 4️⃣ Locate the node binary
    # ------------------------------------------------------------------- #
    node_exe = locate_node_executable()

    # ------------------------------------------------------------------- #
    # 5️⃣ Resolve the package's entry point (dist/index.js)
    # ------------------------------------------------------------------- #
    entrypoint = resolve_global_package_entrypoint(package_name)

    # ------------------------------------------------------------------- #
    # 6️⃣ Build the new configuration dictionary
    # ------------------------------------------------------------------- #
    new_cfg: Dict[str, Any] = {
        "command": node_exe,
        "args": [str(entrypoint)] + extra_args,
    }

    # Preserve any environment dict present in the original config
    if "env" in server_cfg:
        new_cfg["env"] = server_cfg["env"]

    return new_cfg


# --------------------------------------------------------------------------- #
# Main driver
# --------------------------------------------------------------------------- #
def main() -> None:
    # Load the original configuration
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)

    # Transform each server entry
    transformed_servers: Dict[str, Any] = {}
    for name, srv in config.get("mcpServers", {}).items():
        try:
            transformed_servers[name] = convert_npx_server(srv)
        except Exception as exc:
            # If something goes wrong we keep the original entry and
            # surface the problem for the user.
            print(f"[WARN] Could not convert server '{name}': {exc}")
            transformed_servers[name] = srv

    # Assemble the new top‑level config
    new_config = {
        "globalShortcut": config.get("globalShortcut"),
        "mcpServers": transformed_servers,
    }

    # Pretty‑print to stdout
    print(json.dumps(new_config, indent=2))

    # Write the transformed configuration to a file
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(new_config, f, indent=2)
    print(f"\nConverted configuration written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
