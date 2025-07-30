import json
import sys

data = json.load(sys.stdin)
path = data.get("tool_input", {}).get("file_path", "")
sys.exit(
    2
    if any(
        p in path
        for p in [
            ".env",
            "package-lock.json",
            ".git/",
            "Package.resolved",
            "bun.lock",
            "Cargo.lock",
        ]
    )
    else 0
)
