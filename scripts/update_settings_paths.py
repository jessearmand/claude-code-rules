#!/usr/bin/env python3
"""
Script to update ${HOME} substitution variables in .claude/settings.json
with the actual home directory path.

Usage:
    python scripts/update_settings_paths.py
"""

import json
import os
import shutil
from pathlib import Path


def substitute_home_in_value(value: str, home_dir: str) -> str:
    """Recursively substitute ${HOME} with actual home directory in a string."""
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


def main() -> None:
    # Get the home directory
    home_dir = os.path.expanduser("~")

    # Define the settings file path
    settings_file = Path(".claude/settings.json")

    if not settings_file.exists():
        print(f"Error: {settings_file} not found")
        return

    # Create a backup
    backup_file = settings_file.with_suffix(".json.backup")
    shutil.copy(settings_file, backup_file)
    print(f"Created backup: {backup_file}")

    # Read the settings file
    with open(settings_file) as f:
        settings = json.load(f)

    # Substitute ${HOME} with actual home directory
    updated_settings = substitute_home_recursive(settings, home_dir)

    # Write the updated settings
    with open(settings_file, "w") as f:
        json.dump(updated_settings, f, indent=2)
        f.write("\n")  # Add trailing newline

    print(f"Updated paths in {settings_file}")
    print(f"Substituted ${{HOME}} with: {home_dir}")
    print("\nTo restore the original file with ${HOME} variables:")
    print(f"  cp {backup_file} {settings_file}")


if __name__ == "__main__":
    main()
