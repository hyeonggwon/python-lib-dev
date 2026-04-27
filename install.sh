#!/usr/bin/env bash
# install.sh — python-lib-dev harness setup.
#
# Points git at the tracked pre-commit hook so every clone runs
# validate_harness.py before commit. Skills are project-local
# (.claude/skills/) and need no installation step.

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "python-lib-dev installer"
echo "  HARNESS_ROOT = $HARNESS_ROOT"

if [[ -d "$HARNESS_ROOT/.git" ]]; then
    git -C "$HARNESS_ROOT" config core.hooksPath .githooks
    echo "  hooks: core.hooksPath = .githooks (validate_harness.py runs on commit)"
else
    echo "  hooks: skipped (not a git working tree)"
fi

echo ""
echo "install complete."
echo "Skills are project-local; they load when Claude Code runs inside this repo."
