#!/usr/bin/env python3
"""Validate cross-file invariants in the python-lib-dev harness.

The harness has several coupled surfaces that must stay in sync:

  * scripts/prompts/<stage>.md  ↔  STAGE_TOOLS allowlist in run.py
  * scripts/prompts/<stage>.md  ↔  placeholder substitution map in run.py
  * Gate rewrite feedback paths ↔  each prompt's Inputs section
  * s5_review.md verdict schema ↔  fields run.py actually consumes
  * escalation request fields    ↔  handle_escalation_decision consumers
  * README install commands      ↔  flags modern uv/pip actually require

When these drift, the headless either runs without the tool it was told to
use, or receives un-substituted `{placeholder}` text, or loses the user's
rewrite feedback, or emits a schema field that the orchestrator silently
ignores. Catching this structurally avoids the recurring "find 10 more
bugs every review" cycle.

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
S5_REVIEW = PROMPTS_DIR / "s5_review.md"
README_FILES = [HARNESS_ROOT / "README.md", HARNESS_ROOT / "README.en.md"]

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


def extract_yaml_top_level_keys(yaml_text: str) -> set[str]:
    """Scan a YAML-ish snippet and return top-level (column-0) keys.

    Skips comment lines, empty lines, and indented continuations. Lines
    annotated with a trailing `# context-only` comment are excluded — those
    are fields the schema declares for human-readable context (dumped wholesale
    via `verdict.yaml.read_text()` into escalation/summary blocks) and are
    intentionally not consumed via dict access by the orchestrator.

    We don't use a real YAML parser because the snippets in prompt files
    often mix template tokens like `{lib_name}` that aren't valid YAML.
    """
    keys: set[str] = set()
    for raw in yaml_text.splitlines():
        if not raw or raw[0] in (" ", "\t", "#", "-"):
            continue
        line = raw.rstrip()
        if ":" not in line:
            continue
        if re.search(r"#\s*context-only\b", line):
            continue
        key = line.split(":", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            keys.add(key)
    return keys


def check_verdict_schema_consumed() -> list[str]:
    if not S5_REVIEW.exists():
        return [f"[verdict-schema] {S5_REVIEW.name} missing"]
    text = S5_REVIEW.read_text()
    # Find the first ```yaml fenced block — that's the verdict.yaml schema.
    m = re.search(r"```yaml\s*\n(.*?)```", text, re.DOTALL)
    if not m:
        return [f"[verdict-schema] {S5_REVIEW.name} has no ```yaml fenced schema block"]
    schema_keys = extract_yaml_top_level_keys(m.group(1))
    # `verdict` itself is read as `verdict["verdict"]` (Python dict key) — different
    # spelling than `.replace`/`.get`. Search for any of the common access patterns.
    run_text = RUN_PY.read_text()
    issues: list[str] = []
    for key in sorted(schema_keys):
        patterns = [
            f'verdict["{key}"]',
            f"verdict['{key}']",
            f'verdict.get("{key}"',
            f"verdict.get('{key}'",
        ]
        if not any(p in run_text for p in patterns):
            issues.append(
                f"[verdict-schema] s5_review.md mandates verdict.yaml field "
                f"'{key}' but run.py never reads verdict[{key!r}] / verdict.get({key!r}, ...). "
                f"Either drop the field from the schema or have run.py consume it."
            )
    return issues


def check_escalation_fields_consumed() -> list[str]:
    run_text = RUN_PY.read_text()
    # Find write_escalation_request / equivalent template in run.py — the
    # escalation.md body is built from f-strings that contain `feedback: |`
    # etc. Look for those literal field tokens.
    template_match = re.search(r"action: abort.*?```", run_text, re.DOTALL)
    if not template_match:
        return ["[escalation] could not locate escalation.md template body in run.py"]
    body = template_match.group(0)
    # Top-level keys in the template (excluding `action` which is always read).
    fields = set()
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith('f"') or line.startswith('"'):
            line = line.strip('f').strip('"').strip()
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if re.fullmatch(r"[a-z_][a-z_]*", key):
            fields.add(key)
    fields.discard("action")  # always consumed
    issues: list[str] = []
    for f_name in sorted(fields):
        patterns = [
            f'dec["{f_name}"]',
            f"dec['{f_name}']",
            f'dec.get("{f_name}"',
            f"dec.get('{f_name}'",
        ]
        if not any(p in run_text for p in patterns):
            issues.append(
                f"[escalation] escalation.md template invites '{f_name}: ...' but run.py "
                f"never reads dec[{f_name!r}]. The user's input would be silently dropped."
            )
    return issues


# Patterns of install commands that are known broken / require flags.
# Each entry: (regex matched against a single line, hint shown on hit).
README_INSTALL_TRAPS: list[tuple[str, str]] = [
    (
        r"\buv pip install\b(?![^\n]*--system)(?![^\n]*-r\s)",
        "modern uv (>=0.4) refuses `uv pip install` without an active venv or `--system` "
        "(would fail with 'No virtual environment found'). Use `uv pip install --system <pkg>` "
        "or `pip install --user <pkg>` for global one-shot installs.",
    ),
]


def check_readme_install_commands() -> list[str]:
    issues: list[str] = []
    for readme in README_FILES:
        if not readme.exists():
            continue
        for lineno, line in enumerate(readme.read_text().splitlines(), 1):
            stripped = line.strip()
            # Only check lines that look like commands the user is supposed
            # to run — bash fence content typically. Comment-only lines are
            # fine; we want to catch instructions, not prose.
            if not stripped or stripped.startswith("#"):
                continue
            for pattern, hint in README_INSTALL_TRAPS:
                if re.search(pattern, line):
                    issues.append(
                        f"[readme-install] {readme.name}:{lineno}: `{stripped}` — {hint}"
                    )
    return issues


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

        # 3b. Detect the easy-to-make typo: writing the *output* directory
        # short-name (s1, s2, s4) when referring to feedback. Output dirs
        # (s1/plan.md, s2/design.md, s4/changes.patch) and feedback dirs
        # (s1_plan/feedback.md, s2_design/feedback.md, s4_implement/feedback.md)
        # diverge by design — see CLAUDE.md §4. A reference to
        # `{run_dir}/<short>/feedback.md` is silently wrong because run.py
        # writes to the long form, so the headless never sees it.
        for long_name in STAGES_WITH_FEEDBACK:
            short_name = long_name.split("_", 1)[0]
            if short_name == long_name:
                continue
            wrong = f"{{run_dir}}/{short_name}/feedback.md"
            if wrong in text:
                issues.append(
                    f"[feedback] {prompt_path.name}: references '{wrong}' but run.py writes "
                    f"to '{{run_dir}}/{long_name}/feedback.md' "
                    "(stage_headless_with_gate / preserve_loop_feedback use the long-name dir)"
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

    # 5. s5_review.md verdict schema ↔ run.py consumers.
    # Any top-level field the verdict.yaml schema example mandates must be
    # read by run.py somewhere. Otherwise the LLM is asked to fill a field
    # the orchestrator silently ignores — a class of drift this harness has
    # already been bitten by (loop_target was unenforced for ages).
    issues.extend(check_verdict_schema_consumed())

    # 6. escalation request template ↔ handle_escalation_decision consumers.
    # The escalation.md template invites the user to write `feedback: |` etc.
    # If run.py never reads `dec["<field>"]`, the user's input is dropped on
    # the floor — silent functional bug. Validate by parsing the template
    # fenced block and grepping run.py for each non-action field.
    issues.extend(check_escalation_fields_consumed())

    # 7. README install commands ↔ tooling reality.
    # Modern uv (>=0.4) refuses `uv pip install` without an active venv or
    # `--system`. The harness is meant to install pyyaml globally for the
    # operator, so a documented `uv pip install pyyaml` (no flag) breaks
    # first-time setup. Catch this and similar known-broken forms.
    issues.extend(check_readme_install_commands())

    if not issues:
        print("validate_harness: clean ✓")
        return 0

    print(f"validate_harness: {len(issues)} drift issue(s)\n")
    for line in issues:
        print(f"  - {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
