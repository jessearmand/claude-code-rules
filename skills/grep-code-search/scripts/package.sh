#!/usr/bin/env bash
# Package the grep-code-search skill into a .zip for Claude Desktop / Cowork.
#
# Claude Desktop: Settings -> Capabilities -> Skills -> Upload, then pick the zip.
# The zip's top-level entry must be the skill folder containing SKILL.md.
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skill_name="$(basename "$skill_dir")"
parent_dir="$(dirname "$skill_dir")"
out="${1:-$parent_dir/$skill_name.zip}"

if [[ ! -f "$skill_dir/SKILL.md" ]]; then
  echo "error: SKILL.md not found in $skill_dir" >&2
  exit 1
fi

rm -f "$out"
# Zip from the parent so paths are prefixed with the skill folder name.
# Exclude caches and any previously built archive.
( cd "$parent_dir" && zip -r -q "$out" "$skill_name" \
    -x "*/__pycache__/*" "*.pyc" "*/.DS_Store" "*.zip" )

echo "Packaged: $out"
unzip -l "$out"
