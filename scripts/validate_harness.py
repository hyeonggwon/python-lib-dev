#!/usr/bin/env python3
"""Validate cross-file invariants in the python-lib-dev harness.

The harness has several coupled surfaces that must stay in sync:

  * scripts/prompts/<stage>.md  ↔  STAGE_TOOLS allowlist in run.py
  * scripts/prompts/<stage>.md  ↔  placeholder substitution map in run.py
  * Gate rewrite feedback paths ↔  each prompt's Inputs section

When these drift, the headless either runs without the tool it was told to
use, or receives un-substituted `{placeholder}` text, or loses the user's
rewrite feedback. Catching this structurally avoids the recurring "find 10
more bugs every review" cycle.

Exit code: 0 = clean, 1 = drift detected, 2 = validator itself errored.
"""
from __future__ import annotations

import ast
import fnmatch
import re
import sys
from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = HARNESS_ROOT / "scripts" / "prompts"
RUN_PY = HARNESS_ROOT / "scripts" / "run.py"

# Prompts that receive rewrite/loop feedback at {run_dir}/<stem>/feedback.md.
# Must match the directory naming used by run.py:stage_headless_with_gate
# (rewrite path) and run.py:preserve_loop_feedback (MINOR/MAJOR loop path).
STAGES_WITH_FEEDBACK = {"s0_survey", "s1_plan", "s2_design", "s4_implement"}

# Shell builtins Claude Code permits without a Bash(...) allowlist entry.
# `cd` in particular shows up in compound commands (`cd X && uv run ...`)
# and would otherwise produce noisy false positives.
SHELL_BUILTINS = {"cd", "true", "false", "echo", "exit", "pwd", "set"}


def parse_run_py() -> tuple[dict[str, list[str]], set[str]]:
    """Pull STAGE_TOOLS and the .replace("{X}", ...) keys out of run.py."""
    text = RUN_PY.read_text()

    stage_tools: dict[str, list[str]] = {}
    tree = ast.parse(text)
    for node in ast.walk(tree):
        target: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if not (isinstance(target, ast.Name) and target.id == "STAGE_TOOLS"):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for k, v in zip(node.value.keys, node.value.values):
            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                stage_tools[k.value] = [p.strip() for p in v.value.split(",") if p.strip()]

    # The substitution chain in call_headless is the only place run.py uses
    # .replace("{X}", ...) so a regex sweep is sufficient and avoids encoding
    # call_headless's exact AST shape here.
    sub_keys = set(re.findall(r'\.replace\("\{([A-Za-z_][A-Za-z0-9_]*)\}"', text))
    return stage_tools, sub_keys


def allowlist_patterns(entries: list[str]) -> list[str]:
    """Strip the Bash(...) wrapper, leaving the inner glob."""
    out: list[str] = []
    for entry in entries:
        m = re.fullmatch(r"Bash\((.+)\)", entry)
        if m:
            out.append(m.group(1))
    return out


def split_compound(cmd: str) -> list[str]:
    """Break a bash line on &&, ||, |, ; and strip subshell parens / backticks."""
    parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd)
    cleaned: list[str] = []
    for p in parts:
        p = p.strip()
        while p.startswith(("(", "`", "{")):
            p = p[1:].lstrip()
        while p.endswith((")", "`", "}")):
            p = p[:-1].rstrip()
        if p:
            cleaned.append(p)
    return cleaned


def extract_bash_commands(text: str) -> list[str]:
    """Non-comment, non-empty lines from ```bash / ```sh fences."""
    cmds: list[str] = []
    for block in re.findall(r"```(?:bash|sh)\s*\n(.*?)```", text, re.DOTALL):
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            cmds.append(line)
    return cmds


def first_token(cmd: str) -> str:
    return cmd.split(None, 1)[0] if cmd else ""


def command_allowed(cmd: str, patterns: list[str]) -> bool:
    """True if any allowlist glob fnmatches the command (case-sensitive)."""
    return any(fnmatch.fnmatchcase(cmd, p) for p in patterns)


def extract_placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", text))


def main() -> int:
    if not RUN_PY.exists():
        print(f"error: {RUN_PY} not found", file=sys.stderr)
        return 2
    if not PROMPTS_DIR.is_dir():
        print(f"error: {PROMPTS_DIR} not found", file=sys.stderr)
        return 2

    stage_tools, sub_keys = parse_run_py()
    issues: list[str] = []
    used_placeholders: set[str] = set()
    prompt_stems: set[str] = set()

    for prompt_path in sorted(PROMPTS_DIR.glob("*.md")):
        stage = prompt_path.stem
        prompt_stems.add(stage)
        text = prompt_path.read_text()

        # 1. Bash command ↔ STAGE_TOOLS
        if stage in stage_tools:
            patterns = allowlist_patterns(stage_tools[stage])
            for cmd in extract_bash_commands(text):
                for piece in split_compound(cmd):
                    if first_token(piece) in SHELL_BUILTINS:
                        continue
                    if not command_allowed(piece, patterns):
                        issues.append(
                            f"[bash-allowlist] {prompt_path.name}: command not covered by "
                            f"STAGE_TOOLS[{stage!r}]: {piece!r}"
                        )

        # 2. {placeholder} ↔ substitution map
        used = extract_placeholders(text)
        used_placeholders |= used
        for ph in sorted(used - sub_keys):
            issues.append(
                f"[placeholder] {prompt_path.name}: {{{ph}}} used but not in run.py "
                f"substitution map (known: {', '.join(sorted(sub_keys))})"
            )

        # 3. Gate rewrite / loop feedback path
        if stage in STAGES_WITH_FEEDBACK:
            expected = f"{{run_dir}}/{stage}/feedback.md"
            if expected not in text:
                issues.append(
                    f"[feedback] {prompt_path.name}: missing reference to '{expected}' in Inputs "
                    "(rewrite/loop feedback would not reach the headless)"
                )

    # 4. Cross-checks
    for stage in stage_tools:
        if stage not in prompt_stems:
            issues.append(
                f"[stage-tools] STAGE_TOOLS has {stage!r} but scripts/prompts/{stage}.md is missing"
            )
    for stage in prompt_stems:
        if stage not in stage_tools:
            issues.append(
                f"[stage-tools] scripts/prompts/{stage}.md has no STAGE_TOOLS entry "
                "(headless would inherit unrestricted tool access)"
            )
    for k in sorted(sub_keys - used_placeholders):
        issues.append(
            f"[placeholder] run.py substitution key {{{k}}} is not used by any prompt"
        )

    if not issues:
        print("validate_harness: clean ✓")
        return 0

    print(f"validate_harness: {len(issues)} drift issue(s)\n")
    for line in issues:
        print(f"  - {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
