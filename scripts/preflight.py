#!/usr/bin/env python3
"""Preflight checks for the python-lib-dev harness.

Verifies required tooling and (for evolve mode) target_repo_path state.
Exit code 0 on success, 1 on failure. Writes a short diagnostic to stderr.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


UV_INSTALL_HINT = "curl -LsSf https://astral.sh/uv/install.sh | sh"
CLAUDE_HINT = "install Claude Code CLI: https://docs.anthropic.com/claude/docs/claude-code"


def check_tool(name: str, install_hint: str = "") -> list[str]:
    if shutil.which(name) is None:
        msg = f"missing tool: {name}"
        if install_hint:
            msg += f"  (install: {install_hint})"
        return [msg]
    return []


def check_target(path_str: str) -> list[str]:
    errors: list[str] = []
    p = Path(path_str).expanduser().resolve()

    if not p.is_dir():
        return [f"target_repo_path not a directory: {p}"]
    if not (p / ".git").exists():
        return [f"target_repo_path is not a git repo: {p}"]

    status = subprocess.run(
        ["git", "-C", str(p), "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    if status.returncode != 0:
        errors.append(f"git status failed at {p}: {status.stderr.strip()}")
    elif status.stdout.strip():
        errors.append(
            f"target_repo_path has uncommitted changes: {p}\n"
            "    commit, stash, or clean before running the harness."
        )

    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["new", "evolve"])
    ap.add_argument("--target-repo-path", default=None)
    args = ap.parse_args()

    errors: list[str] = []
    errors += check_tool("uv", UV_INSTALL_HINT)
    errors += check_tool("git")
    errors += check_tool("claude", CLAUDE_HINT)

    if args.mode == "evolve":
        if not args.target_repo_path:
            errors.append("evolve mode requires --target-repo-path")
        else:
            errors += check_target(args.target_repo_path)

    if errors:
        print("preflight failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print("preflight ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
