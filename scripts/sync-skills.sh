#!/usr/bin/env bash
# Sync skills/ and hooks/ from the canonical agent-config repo, then re-apply
# the Claude Code-specific skill overlays.
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
# A skill in either mode may also declare KEEP paths (see skill_excludes):
# files that exist only in this repo and must survive rsync --delete. Prefer a
# KEEP entry over a patch for whole standalone files, since patch does not
# preserve the executable bit.
#
# Skills not listed are left untouched: those that exist only in this repo
# (e.g. anywidget-generator, marimo-check).
#
# hooks/ mirrors the shared policy hooks from agent-config/hooks. Files that
# exist only here (HOOK_KEEPS) survive the mirror; omp-only files in the
# canonical tree (HOOK_SKIPS) are never copied.
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

COPY_SKILLS=(check commit fetching-docs grep-code-search lang-rust lang-swift lang-typescript process-pdf)
OVERLAY_SKILLS=(commit-staged lang-python xcode-build)

# Paths (relative to the skill directory) that exist only in this repo and must
# not be deleted by the mirror step.
skill_excludes() {
    case "$1" in
        grep-code-search) printf '%s\n' 'README.md' 'scripts/package.sh' ;;
    esac
}

if [[ ! -d "$SRC" ]]; then
    echo "error: canonical skills directory not found: $SRC" >&2
    echo "       set AGENT_CONFIG to the agent-config checkout" >&2
    exit 1
fi

sync_skill() {
    local skill="$1" keep
    local -a excludes=(--exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store')
    while IFS= read -r keep; do
        [[ -n "$keep" ]] && excludes+=(--exclude "/$keep")
    done < <(skill_excludes "$skill")
    rsync -a --delete "${excludes[@]}" "$SRC/$skill/" "$DST/$skill/"
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
HOOKS_SRC="${AGENT_CONFIG:-$HOME/Develop/agent-config}/hooks"
# Claude Code-only hook files that must survive the mirror.
HOOK_KEEPS=(bash_command_validator.py settings_wiring_test.py)
# omp-only hook files that stay in agent-config.
HOOK_SKIPS=(omp_guard.py omp_guard_test.py)
hook_excludes=(--exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store')
for name in "${HOOK_KEEPS[@]}" "${HOOK_SKIPS[@]}"; do
    hook_excludes+=(--exclude "/$name")
done
rsync -a --delete "${hook_excludes[@]}" "$HOOKS_SRC/" "$ROOT/hooks/"
echo "synced  hooks/"

echo "Done. Review with: git -C $ROOT status skills/ hooks/"
