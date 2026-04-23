#!/usr/bin/env bash
# uninstall.sh — remove symlinks created by install.sh.
#
# Does NOT reverse placeholder substitution (that would require a backup of
# the template). If you want to restore the template state, re-checkout from
# git, or run install.sh --reinstall from a fresh checkout.

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
MARKER="$HARNESS_ROOT/.installed-marker"

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

rm -f "$MARKER"
echo "done. Source tree at $HARNESS_ROOT was left as-is."