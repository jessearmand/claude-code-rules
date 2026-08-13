#!/usr/bin/env bash
# Sync skills/ from the canonical agent-config repo, then re-apply the
# Claude Code-specific overlays.
#
# Canonical source: agent-config/skills (harness-neutral; see its AGENTS.md
# "Skill conventions"). This repo is a downstream overlay: most skills mirror
# the canonical copy verbatim, and a few carry Claude Code-specific additions
# (frontmatter such as allowed-tools/disable-model-invocation, pointers to
# Claude Code plugin skills, and the Xcode MCP guide).
#
# Per-skill modes:
#   COPY_SKILLS    - verbatim mirror of the canonical skill
#   OVERLAY_SKILLS - mirror, then apply scripts/skill-overlays/<skill>.patch
#                    (a patch may also create Claude-only files, e.g.
#                    xcode-build/xcode-mcp.md)
#
# Skills not listed are left untouched:
#   - skills that exist only in this repo (e.g. anywidget-generator,
#     marimo-check)
#   - grep-code-search, whose Claude-flavored wording is maintained here while
#     agent-config maintains the harness-generic wording; the substantive
#     content (scripts/, API reference) is kept aligned manually
#
# If a patch fails to apply, the canonical skill changed in an overlaid
# region. Resolve skills/<skill> by hand, then regenerate the overlay:
#   diff -ruN <agent-config>/skills/<skill> skills/<skill> \
#       > scripts/skill-overlays/<skill>.patch

set -euo pipefail

SRC="${AGENT_CONFIG:-$HOME/Develop/agent-config}/skills"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DST="$ROOT/skills"
OVERLAYS="$ROOT/scripts/skill-overlays"

COPY_SKILLS=(check fetching-docs lang-rust lang-swift lang-typescript process-pdf)
OVERLAY_SKILLS=(commit-staged lang-python xcode-build)

if [[ ! -d "$SRC" ]]; then
    echo "error: canonical skills directory not found: $SRC" >&2
    echo "       set AGENT_CONFIG to the agent-config checkout" >&2
    exit 1
fi

sync_skill() {
    rsync -a --delete \
        --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
        "$SRC/$1/" "$DST/$1/"
}

for skill in "${COPY_SKILLS[@]}"; do
    sync_skill "$skill"
    echo "synced  $skill"
done

for skill in "${OVERLAY_SKILLS[@]}"; do
    sync_skill "$skill"
    if ! patch -d "$DST/$skill" -p1 -s -N < "$OVERLAYS/$skill.patch"; then
        echo "error: overlay failed for $skill; resolve skills/$skill by hand" >&2
        echo "       and regenerate scripts/skill-overlays/$skill.patch" >&2
        exit 1
    fi
    echo "overlay $skill"
done

echo "Done. Review with: git -C $ROOT status skills/"
