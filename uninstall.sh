#!/usr/bin/env bash
# uninstall.sh — remove symlinks created by install.sh.
#
# Source files are left untouched. Placeholders ({{HARNESS_ROOT}} etc.) live
# verbatim in the source per Amendment A4 (no install-time substitution), so
# there is no template state to restore.

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"

echo "python-lib-dev uninstaller"

for d in "$HARNESS_ROOT/skills/"*/; do
    name="$(basename "$d")"
    link="$CLAUDE_SKILLS_DIR/$name"
    if [[ -L "$link" ]]; then
        target="$(readlink -f "$link" || true)"
        if [[ "$target" == "$(readlink -f "$d")" ]]; then
            rm "$link"
            echo "  removed: ~/.claude/skills/$name"
        else
            echo "  skipped: ~/.claude/skills/$name (points elsewhere: $target)"
        fi
    fi
done

# Drop the local-only hooks setting so a fresh re-clone isn't blocked by a
# stale config; install.sh re-applies it next time.
if [[ -d "$HARNESS_ROOT/.git" ]]; then
    git -C "$HARNESS_ROOT" config --unset core.hooksPath 2>/dev/null || true
fi

echo "done. Source tree at $HARNESS_ROOT was left as-is."