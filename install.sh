#!/usr/bin/env bash
# install.sh — python-lib-dev harness installer.
#
# Creates symlinks in ~/.claude/skills/ pointing at this harness's skills/.
# Does NOT modify any file contents. All {{HARNESS_ROOT}} placeholders in
# skill and doc files are left intact; the main session resolves HARNESS_ROOT
# at runtime by inspecting the symlink target (see each SKILL.md section 0).
#
# Safe to re-run after moving the harness directory: existing symlinks that
# point at the old location are detected (broken or stale) and replaced with
# fresh ones pointing at the current path.

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"

echo "python-lib-dev installer"
echo "  HARNESS_ROOT = $HARNESS_ROOT"

mkdir -p "$CLAUDE_SKILLS_DIR"

for d in "$HARNESS_ROOT/skills/"*/; do
    name="$(basename "$d")"
    link="$CLAUDE_SKILLS_DIR/$name"
    target="$(cd "$d" && pwd)"

    if [[ -L "$link" ]]; then
        existing_target="$(readlink -f "$link" 2>/dev/null || true)"
        if [[ "$existing_target" == "$target" ]]; then
            echo "  ok: $name (already linked)"
            continue
        fi
        echo "  replacing: ~/.claude/skills/$name (was -> ${existing_target:-<broken>})"
        rm "$link"
    elif [[ -e "$link" ]]; then
        echo "  error: $link exists and is not a symlink; remove manually" >&2
        exit 1
    fi

    ln -s "$d" "$link"
    echo "  linked: ~/.claude/skills/$name -> $d"
done

# Activate the tracked pre-commit hook for this clone (idempotent).
# .git/hooks/ is local-only and not pushed; .githooks/ is tracked, and
# core.hooksPath points git at it so every clone gets the same drift checks.
if [[ -d "$HARNESS_ROOT/.git" ]]; then
    git -C "$HARNESS_ROOT" config core.hooksPath .githooks
    echo "  hooks: core.hooksPath = .githooks (validate_harness.py runs on commit)"
fi

echo ""
echo "install complete."
echo "Main sessions resolve HARNESS_ROOT at runtime from the symlink target."
echo "After 'mv' of this harness directory, just re-run this script."
