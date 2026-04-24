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

    # The harness runs every tool via `uv run ...` against this repo (gates.py
    # and all stage prompts). A repo without a pyproject.toml — or one with a
    # pyproject.toml but no `[project]` table (e.g., legacy setup.py-only, or
    # non-PEP-621 poetry configs) — will fail opaquely deep inside gates.py.
    # Catch it here with a clear message so the user can decide whether to
    # adapt the repo or use a different harness.
    pyproject = p / "pyproject.toml"
    if not pyproject.exists():
        errors.append(
            f"target_repo_path has no pyproject.toml: {p}\n"
            "    this harness drives tooling via `uv run ...`, which requires a\n"
            "    PEP 621 pyproject.toml. Legacy setup.py-only repos are not supported."
        )
    else:
        text = pyproject.read_text(errors="replace")
        if "[project]" not in text and "[tool.poetry]" not in text:
            errors.append(
                f"target_repo_path pyproject.toml has no recognizable [project] table: {p}\n"
                "    `uv run` needs a PEP 621 [project] table (or tool.poetry that uv can read)."
            )
        elif "[tool.poetry]" in text and "[project]" not in text:
            # Poetry-only projects sometimes work with uv, sometimes don't —
            # warn rather than hard-fail so the user isn't blocked needlessly.
            errors.append(
                f"target_repo_path appears to be poetry-only (no [project] table): {p}\n"
                "    uv may not handle this cleanly. If `uv run pytest` fails later,\n"
                "    migrate to a PEP 621 [project] table or use a different harness."
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
