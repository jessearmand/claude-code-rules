#!/usr/bin/env python3
"""
Render settings.template.json with ${HOME} replaced by the actual home
directory.

The template is never modified. Without --output the result goes to stdout so
it can be reviewed or merged by hand; with --output it is written to that path,
and an existing file there is backed up first.

Usage:
    python scripts/update_settings_paths.py
    python scripts/update_settings_paths.py --output ~/.claude/settings.json
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "settings.template.json"


def substitute_home_in_value(value: str, home_dir: str) -> str:
    """Substitute ${HOME} with actual home directory in a string."""
    return value.replace("${HOME}", home_dir)


def substitute_home_recursive(obj: dict | list | str, home_dir: str) -> dict | list | str:
    """Recursively substitute ${HOME} in a JSON object."""
    if isinstance(obj, dict):
        return {key: substitute_home_recursive(val, home_dir) for key, val in obj.items()}
    elif isinstance(obj, list):
        return [substitute_home_recursive(item, home_dir) for item in obj]
    elif isinstance(obj, str):
        return substitute_home_in_value(obj, home_dir)
    else:
        return obj


def render(template: Path, home_dir: str) -> str:
    """Return the template as JSON text with ${HOME} substituted."""
    with open(template) as f:
        settings = json.load(f)
    return json.dumps(substitute_home_recursive(settings, home_dir), indent=2) + "\n"


def write_with_backup(output: Path, text: str) -> None:
    """Write text to output, keeping any existing file as <name>.backup."""
    if output.exists():
        backup_file = output.with_name(output.name + ".backup")
        shutil.copy(output, backup_file)
        print(f"Created backup: {backup_file}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    print(f"Wrote {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--template", type=Path, default=TEMPLATE, help="settings template to render")
    parser.add_argument("--output", type=Path, help="destination file (default: stdout)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.template.exists():
        print(f"Error: {args.template} not found", file=sys.stderr)
        sys.exit(1)

    text = render(args.template, os.path.expanduser("~"))
    if args.output is None:
        sys.stdout.write(text)
        return
    write_with_backup(args.output.expanduser(), text)


if __name__ == "__main__":
    main()
