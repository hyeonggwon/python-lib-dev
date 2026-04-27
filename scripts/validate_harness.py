#!/usr/bin/env python3
"""Validate cross-file invariants in the python-lib-dev harness.

The harness has several coupled surfaces that must stay in sync:

  * scripts/prompts/<stage>.md  ↔  STAGE_TOOLS allowlist in run.py
  * scripts/prompts/<stage>.md  ↔  placeholder substitution map in run.py
  * Gate rewrite feedback paths ↔  each prompt's Inputs section
  * s5_review.md verdict schema ↔  fields run.py actually consumes
  * s5_review.md verdict labels ↔  VALID_VERDICT_LABELS in run.py
  * STAGE_REQUIRED_AUX_OUTPUTS  ↔  the stage prompt's Outputs section
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


def parse_run_py() -> tuple[dict[str, list[str]], set[str], dict[str, list[str]], set[str]]:
    """Pull structured constants out of run.py.

    Returns:
        stage_tools: STAGE_TOOLS dict (stage → list of allowlist entries).
        sub_keys: placeholder names used in the .replace("{X}", ...) chain.
        aux_outputs: STAGE_REQUIRED_AUX_OUTPUTS dict (stage → list of run-relative
            paths the orchestrator demands in addition to the completion marker).
        verdict_labels: VALID_VERDICT_LABELS set literal (e.g. {"PASS", "MINOR", ...}).
    """
    text = RUN_PY.read_text()

    stage_tools: dict[str, list[str]] = {}
    aux_outputs: dict[str, list[str]] = {}
    verdict_labels: set[str] = set()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        target: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        value = node.value
        if name == "STAGE_TOOLS" and isinstance(value, ast.Dict):
            for k, v in zip(value.keys, value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                    stage_tools[k.value] = [p.strip() for p in v.value.split(",") if p.strip()]
        elif name == "STAGE_REQUIRED_AUX_OUTPUTS" and isinstance(value, ast.Dict):
            for k, v in zip(value.keys, value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.List):
                    items: list[str] = []
                    for elt in v.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            items.append(elt.value)
                    aux_outputs[k.value] = items
        elif name == "VALID_VERDICT_LABELS" and isinstance(value, ast.Set):
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    verdict_labels.add(elt.value)

    # The substitution chain in call_headless is the only place run.py uses
    # .replace("{X}", ...) so a regex sweep is sufficient and avoids encoding
    # call_headless's exact AST shape here.
    sub_keys = set(re.findall(r'\.replace\("\{([A-Za-z_][A-Za-z0-9_]*)\}"', text))
    return stage_tools, sub_keys, aux_outputs, verdict_labels


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


def check_verdict_labels_consistent(verdict_labels: set[str]) -> list[str]:
    """`VALID_VERDICT_LABELS` in run.py must equal the enum in s5_review.md.

    The schema's `verdict:` line carries the labels in an inline comment:
        verdict: PASS            # PASS | MINOR | MAJOR | CRITICAL
    Drift here is silent and asymmetric: a label only in the prompt is
    rejected at runtime by load_verdict's enum check (the LLM sees no error
    until s6 escalates); a label only in run.py would never be emitted, so
    the loop logic for it would be dead code.
    """
    if not S5_REVIEW.exists():
        return [f"[verdict-labels] {S5_REVIEW.name} missing"]
    if not verdict_labels:
        return [
            "[verdict-labels] could not locate `VALID_VERDICT_LABELS = {...}` set "
            "literal in run.py (parse_run_py only matches a top-level set assignment)"
        ]
    text = S5_REVIEW.read_text()
    m = re.search(r"```yaml\s*\n(.*?)```", text, re.DOTALL)
    if not m:
        return [f"[verdict-labels] {S5_REVIEW.name} has no ```yaml fenced schema block"]
    schema_block = m.group(1)
    label_line = next(
        (ln for ln in schema_block.splitlines() if ln.lstrip().startswith("verdict:")),
        None,
    )
    if label_line is None:
        return [f"[verdict-labels] {S5_REVIEW.name} schema block has no `verdict:` field"]
    comment_match = re.search(r"#\s*([A-Z_][A-Z_| ]+)", label_line)
    if not comment_match:
        return [
            f"[verdict-labels] {S5_REVIEW.name} `verdict:` line has no inline `# A | B | C` "
            "enum comment — cannot extract the prompt's enum to compare"
        ]
    prompt_labels = {tok.strip() for tok in comment_match.group(1).split("|") if tok.strip()}
    issues: list[str] = []
    only_prompt = prompt_labels - verdict_labels
    only_runpy = verdict_labels - prompt_labels
    if only_prompt:
        issues.append(
            f"[verdict-labels] s5_review.md schema enum has {sorted(only_prompt)} but "
            f"run.py VALID_VERDICT_LABELS does not — load_verdict() would reject those "
            f"verdicts the LLM is told it may emit"
        )
    if only_runpy:
        issues.append(
            f"[verdict-labels] run.py VALID_VERDICT_LABELS has {sorted(only_runpy)} but "
            f"the s5_review.md schema enum does not — those run.py branches are unreachable"
        )
    return issues


def check_aux_outputs_referenced(aux_outputs: dict[str, list[str]]) -> list[str]:
    """Every `STAGE_REQUIRED_AUX_OUTPUTS[stage]` path must appear in that prompt.

    run.py treats a missing aux output as "stage cheated the marker" and
    aborts. If a future prompt edit drops the requirement, every run will
    fail here with no hint that the prompt itself is the cause. The simplest
    structural guard is to require the path string to appear *somewhere* in
    the prompt body.
    """
    issues: list[str] = []
    for stage, paths in aux_outputs.items():
        prompt_path = PROMPTS_DIR / f"{stage}.md"
        if not prompt_path.exists():
            issues.append(
                f"[aux-output] STAGE_REQUIRED_AUX_OUTPUTS has {stage!r} but "
                f"scripts/prompts/{stage}.md is missing"
            )
            continue
        text = prompt_path.read_text()
        for path in paths:
            # Match the path's basename or full run-relative form. Prompts
            # typically write `{run_dir}/s2/api_stubs.py` so the basename
            # `api_stubs.py` is the most reliable anchor.
            basename = path.rsplit("/", 1)[-1]
            if path not in text and basename not in text:
                issues.append(
                    f"[aux-output] {prompt_path.name}: STAGE_REQUIRED_AUX_OUTPUTS "
                    f"demands {path!r} but the prompt never mentions it. The "
                    "stage will write its completion marker, run.py will then "
                    "abort the run with 'required auxiliary output(s) missing'."
                )
    return issues


def check_gate_decision_fields_consumed() -> list[str]:
    """Mirror of check_escalation_fields_consumed for gate-decision templates.

    Same class of bug: stage_headless_with_gate's `options = (...)` /
    `options += (...)` blocks invite the user to type `key: ...` fields into
    `<gate>.decision.md`, but if run.py never reads `decision[<key>]` /
    `dec[<key>]` the input gets silently dropped (it lands in
    `state.user_input` which no prompt reads). Verify every templated key
    has a consumer.
    """
    run_text = RUN_PY.read_text()
    issues: list[str] = []
    # Walk each `options = (` / `options += (` opener and find the matching
    # `)` by paren-balance (string-literal-aware). A naive `.*?` regex stops
    # at the first `)` inside a string like `"(evolve only; ...)\n"` and
    # misses the rest of the template — exactly the kind of silent miss
    # this validator is meant to catch.
    blocks: list[str] = []
    for opener in re.finditer(r"options\s*(?:=|\+=)\s*\(", run_text):
        idx = opener.end()
        depth = 1
        in_str: str | None = None
        escape = False
        start = idx
        while idx < len(run_text) and depth > 0:
            c = run_text[idx]
            if in_str:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == in_str:
                    in_str = None
            else:
                if c in ("'", '"'):
                    in_str = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        blocks.append(run_text[start:idx])
                        break
            idx += 1
    if not blocks:
        return ["[gate-decision] could not locate `options = (...)` template blocks in run.py"]
    fields: set[str] = set()
    for body in blocks:
        for raw in body.splitlines():
            line = raw.strip()
            # Each templated line is a Python string literal: "key: value\n".
            m = re.match(r'^"([a-z_][a-z_0-9]*)\s*:', line)
            if m:
                fields.add(m.group(1))
    fields.discard("decision")  # always consumed by the if/elif on `decision.get("decision")`
    for f_name in sorted(fields):
        patterns = [
            f'decision["{f_name}"]', f"decision['{f_name}']",
            f'decision.get("{f_name}"', f"decision.get('{f_name}'",
            f'dec["{f_name}"]', f"dec['{f_name}']",
            f'dec.get("{f_name}"', f"dec.get('{f_name}'",
        ]
        if not any(p in run_text for p in patterns):
            issues.append(
                f"[gate-decision] gate template invites '{f_name}: ...' but run.py "
                f"never reads decision[{f_name!r}] or dec[{f_name!r}]. The user's "
                f"input would be silently dropped (state.user_input is not read by "
                f"any stage prompt)."
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

    stage_tools, sub_keys, aux_outputs, verdict_labels = parse_run_py()
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

    # 5b. s5_review.md verdict label enum ↔ VALID_VERDICT_LABELS in run.py.
    # load_verdict() rejects any label not in the run.py set, so a prompt-only
    # label is a silent contract break (the LLM is told it may emit it; the
    # orchestrator escalates). A run.py-only label is dead code.
    issues.extend(check_verdict_labels_consistent(verdict_labels))

    # 5c. STAGE_REQUIRED_AUX_OUTPUTS ↔ stage prompt body.
    # stage_headless_with_gate aborts the run if a registered aux output is
    # missing after the marker is written. A prompt edit that drops the
    # auxiliary file requirement would surface as repeated mid-run aborts
    # with no obvious cause; require the path to appear in the prompt.
    issues.extend(check_aux_outputs_referenced(aux_outputs))

    # 6. escalation request template ↔ handle_escalation_decision consumers.
    # The escalation.md template invites the user to write `feedback: |` etc.
    # If run.py never reads `dec["<field>"]`, the user's input is dropped on
    # the floor — silent functional bug. Validate by parsing the template
    # fenced block and grepping run.py for each non-action field.
    issues.extend(check_escalation_fields_consumed())

    # 7. gate-decision template ↔ stage_headless_with_gate consumers.
    # Same class of silent-drop bug as #6 but for gateA/B request templates
    # (the `options = (...)` / `options += (...)` blocks). Catches e.g. a
    # `breaking_notes: |` field added to the template without a corresponding
    # `decision.get("breaking_notes")` consumer.
    issues.extend(check_gate_decision_fields_consumed())

    # 8. README install commands ↔ tooling reality.
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
