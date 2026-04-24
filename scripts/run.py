#!/usr/bin/env python3
"""python-lib-dev harness orchestrator.

Reads outputs/<run-id>/state.json and drives the pipeline:
    preflight -> di -> (s0) -> s1 -> s2 -> s3 -> s4 -> s5 -> s6 -> (loop | s7) -> s7 -> s8

Invokes headless Claude Code sessions for each content-producing stage (s0-s7)
by shelling out to `claude -p`. The headless session is given a short wrapper
instruction that tells it to read the full system prompt from
    scripts/prompts/<stage>.md
which is the authoritative spec for that stage.

Usage:
    python run.py --run-id <id>            # start/continue a run
    python run.py --resume <id>            # same thing; accepted for clarity

The script is idempotent-per-stage: running it repeatedly picks up wherever
state.json says `current_stage` points, and stops on any user-facing gate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    print("error: PyYAML is required. Install with: uv pip install pyyaml", file=sys.stderr)
    sys.exit(1)


HARNESS_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = HARNESS_ROOT / "scripts"
PROMPTS_DIR = SCRIPTS_DIR / "prompts"
CONFIG_PATH = SCRIPTS_DIR / "config.yaml"


# ---------- state ----------

def load_state(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "state.json").read_text())


def save_state(run_dir: Path, state: dict[str, Any]) -> None:
    (run_dir / "state.json").write_text(json.dumps(state, indent=2))


def load_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text())


# ---------- cross-run evidence (0-5) ----------

def append_index_entry(state: dict[str, Any], final_status: str, escalation_trigger: str | None = None) -> None:
    """Append one line to outputs/.index.jsonl with this run's outcome.

    See harness-builder SKILL.md §0-5: cross-run pattern accumulation is the
    evidence layer that lets a human (not the harness) patch prompts/caps.
    """
    index_path = HARNESS_ROOT / "outputs" / ".index.jsonl"
    index_path.parent.mkdir(exist_ok=True)
    entry = {
        "run_id": state["run_id"],
        "mode": state.get("mode"),
        "final_status": final_status,
        "counters": state.get("counters", {}),
        "escalation_trigger": escalation_trigger,
        "completed_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_recent_index_entries(limit: int = 10) -> list[dict[str, Any]]:
    """Return the last `limit` entries from outputs/.index.jsonl, newest last.

    Malformed lines are skipped silently — the index is advisory evidence, not
    load-bearing state. Absence of the file (first-ever run) returns [].
    """
    index_path = HARNESS_ROOT / "outputs" / ".index.jsonl"
    if not index_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-limit:]


def format_cross_run_pattern_block(current_run_id: str, limit: int = 10) -> str:
    """Build a markdown block summarizing recent run outcomes for escalation.md.

    Shows the last N runs (excluding the current run) with their final_status
    and escalation_trigger counts, so the user can spot repeated failure modes
    before deciding how to patch prompts/caps.
    """
    # In the current call order, `escalate()` calls this *before*
    # `append_index_entry`, so the current run is never in the index yet and
    # the filter is a no-op today. Kept as a guard in case a future caller
    # reorders those calls (e.g. writing an index entry at stage boundaries).
    entries = [e for e in read_recent_index_entries(limit=limit)
               if e.get("run_id") != current_run_id]
    if not entries:
        return "_no prior runs indexed — this is the first recorded run._\n"

    trigger_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for e in entries:
        status = e.get("final_status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        trig = e.get("escalation_trigger")
        if trig:
            trigger_counts[trig] = trigger_counts.get(trig, 0) + 1

    lines = [f"Looking at the previous {len(entries)} run(s) (newest last):", ""]
    for e in entries:
        lines.append(
            f"- `{e.get('run_id', '?')}` ({e.get('mode', '?')}) → "
            f"{e.get('final_status', '?')}"
            + (f" [trigger: {e.get('escalation_trigger')}]" if e.get("escalation_trigger") else "")
        )
    lines.append("")
    lines.append("**Status counts:** " + ", ".join(f"{k}={v}" for k, v in status_counts.items()))
    if trigger_counts:
        lines.append("**Escalation triggers:** " + ", ".join(f"{k}={v}" for k, v in trigger_counts.items()))
    return "\n".join(lines) + "\n"


# ---------- gates ----------

def gate_pending(run_dir: Path, gate: str) -> bool:
    request = run_dir / f"{gate}.request.md"
    decision = run_dir / f"{gate}.decision.md"
    return request.exists() and not decision.exists()


def write_gate_request(run_dir: Path, gate: str, title: str, context: str, options: str) -> None:
    # Deliberately no textwrap.dedent + f-string indentation: interpolated multi-line
    # values (options, JSON, etc.) include flush-left lines that force the common
    # leading-whitespace prefix to 0, defeating dedent and leaving the surrounding
    # literal lines with 4+ spaces of indent — which Markdown renders as a code
    # block, breaking headers and the decision snippet. Keep triple-quoted content
    # flush-left in source so the file on disk is real Markdown.
    body = (
        f"# Gate: {title}\n"
        f"\n"
        f"## Context\n"
        f"\n"
        f"{context}\n"
        f"\n"
        f"## Expected decision\n"
        f"\n"
        f"Write `{gate}.decision.md` next to this file with one of:\n"
        f"\n"
        f"```\n"
        f"{options.rstrip()}\n"
        f"```\n"
    )
    (run_dir / f"{gate}.request.md").write_text(body)


def read_gate_decision(run_dir: Path, gate: str) -> dict[str, str] | None:
    p = run_dir / f"{gate}.decision.md"
    if not p.exists():
        return None
    text = p.read_text()
    # Very lightweight parser: line `key: value` or `key: |` followed by indented block.
    result: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#"):
            i += 1
            continue
        if ":" in line:
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "|":
                # YAML-style block: consume subsequent lines that are either
                # indented (2+ spaces or tab) or blank. Preserve relative
                # indentation by collecting raw lines and running textwrap.dedent
                # at the end — avoids flattening code snippets that the user
                # pastes into feedback.
                buf: list[str] = []
                i += 1
                while i < len(lines):
                    raw = lines[i]
                    if raw.startswith("  ") or raw.startswith("\t") or not raw.strip():
                        buf.append(raw)
                        i += 1
                    else:
                        break
                result[key] = textwrap.dedent("\n".join(buf)).strip()
                continue
            else:
                result[key] = rest
        i += 1
    return result


# ---------- headless invocation ----------

# Stage-level tool allowlists (harness-builder SKILL.md §0-3).
# Structural enforcement of tool boundaries: s0/s5 are read-only investigators
# by design, other stages get progressively wider access. Unmapped stages fall
# back to unrestricted (no flag passed) — add an entry if you introduce a new
# stage rather than silently inheriting full access.
STAGE_TOOLS: dict[str, str] = {
    # Survey: read-only codebase investigator. Must not mutate target_repo_path.
    "s0_survey":    "Read,Grep,Glob,Bash(rg *),Bash(grep *),Bash(find *),Bash(head *),Bash(cat *),Bash(ls *),Bash(tree *),Bash(wc *),Bash(git log *),Bash(git show *),Bash(git diff *),Bash(git status *)",
    # Plan / design: write planning artifacts into run_dir. No code execution needed.
    "s1_plan":      "Read,Grep,Glob,Write,Edit",
    "s2_design":    "Read,Grep,Glob,Write,Edit",
    # Tests: scaffold uv workspace (new mode) + write test files. No git commits.
    # `uv` narrowed to non-publishing subcommands (no `uv publish`, no `uv build`).
    "s3_tests":     "Read,Grep,Glob,Write,Edit,Bash(uv init *),Bash(uv add *),Bash(uv sync *),Bash(uv lock *),Bash(uv run *),Bash(mkdir *),Bash(ls *),Bash(cat *),Bash(cp *)",
    # Implementation: uv run loop + git commits on harness branch (evolve).
    # `uv` narrowed same as s3 (no publish/build). Git narrowed to non-destructive
    # subcommands (no reset/clean/push/rebase/branch). Both bare (`git <sub>`) and
    # `git -C <path> <sub>` forms are allowed — the s4 prompt uses -C to target
    # {target_repo_path} for patch generation (diff/merge-base/rev-parse).
    "s4_implement": "Read,Grep,Glob,Write,Edit,Bash(uv init *),Bash(uv add *),Bash(uv sync *),Bash(uv lock *),Bash(uv run *),Bash(git add *),Bash(git commit *),Bash(git status *),Bash(git diff *),Bash(git log *),Bash(git show *),Bash(git merge-base *),Bash(git rev-parse *),Bash(git -C * add *),Bash(git -C * commit *),Bash(git -C * status *),Bash(git -C * diff *),Bash(git -C * log *),Bash(git -C * show *),Bash(git -C * merge-base *),Bash(git -C * rev-parse *),Bash(mkdir *),Bash(ls *),Bash(cat *),Bash(cp *),Bash(mv *)",
    # Independent review: writes review.md/verdict.yaml. Reads gates/*.json for
    # mechanical results — does not execute them itself (0-2 clean separation).
    # No Edit (must not modify source under review), no Bash(uv run *) (gates
    # are authoritative). Git read-only for inspecting evolve diffs.
    "s5_review":    "Read,Grep,Glob,Write,Bash(git log *),Bash(git diff *),Bash(git show *),Bash(git status *)",
    # Docs: writes documentation, may run uv to verify snippets compile.
    # Needs `git -C <target_repo_path> diff` for the evolve-mode docs-diff.patch.
    "s7_docs":      "Read,Grep,Glob,Write,Edit,Bash(uv run *),Bash(git diff *),Bash(git -C * diff *)",
}


def call_headless(stage: str, run_dir: Path, extra: str = "") -> int:
    template_path = PROMPTS_DIR / f"{stage}.md"
    if not template_path.exists():
        print(f"error: prompt not found: {template_path}", file=sys.stderr)
        return 1

    # Resolve placeholders into a run-local copy.
    # Only values that must appear literally in the prompt's instructions get
    # substituted here; other fields (pypi_slug, python_min, overrides) are
    # read by the headless directly from interview/mode.json when needed.
    # str.replace (not str.format) so curly braces in example code are safe.
    mode_json = run_dir / "interview" / "mode.json"
    mode_data: dict[str, Any] = json.loads(mode_json.read_text()) if mode_json.exists() else {}
    target_repo_path = mode_data.get("target_repo_path") or ""
    lib_name = mode_data.get("lib_name") or ""

    resolved_text = (
        template_path.read_text()
        .replace("{HARNESS_ROOT}", str(HARNESS_ROOT))
        .replace("{run_dir}", str(run_dir))
        .replace("{run_id}", run_dir.name)
        .replace("{target_repo_path}", str(target_repo_path))
        .replace("{lib_name}", str(lib_name))
    )
    resolved_dir = run_dir / ".prompts"
    resolved_dir.mkdir(exist_ok=True)
    prompt_path = resolved_dir / f"{stage}.md"
    prompt_path.write_text(resolved_text)

    wrapper = textwrap.dedent(f"""\
        You are operating as a headless stage worker in the python-lib-dev harness.

        Open and read the full system prompt at:
            {prompt_path}

        That file is your authoritative spec for this stage. Follow it exactly.
        All paths in that file are already absolute.

        Harness root: {HARNESS_ROOT}
        Run directory: {run_dir}
        Stage: {stage}

        {extra}

        When you finish, print a single final line matching the pattern defined in the prompt
        (for example: `{stage.upper()}_DONE: <path>`). Do not emit anything after that line.
    """)

    # Non-interactive headless call. Two flags enforce 0-3 (tool boundary):
    #   --allowed-tools : structural whitelist of tools this stage may use.
    #   --permission-mode acceptEdits : auto-accept Edit/Write without prompting
    #     (required for -p mode; Bash is already gated by the allowlist patterns).
    # Stages missing from STAGE_TOOLS fall back to unrestricted tool access — add
    # an explicit entry when introducing a new stage rather than inheriting full
    # access silently.
    cmd = ["claude", "-p", wrapper, "--permission-mode", "acceptEdits"]
    allowed = STAGE_TOOLS.get(stage)
    if allowed is not None:
        cmd.extend(["--allowed-tools", allowed])
    print(f"[run.py] invoking headless for stage={stage} (tools={'restricted' if allowed else 'unrestricted'})", file=sys.stderr)
    r = subprocess.run(cmd, check=False)
    return r.returncode


# ---------- stage implementations ----------

def stage_preflight(run_dir: Path, state: dict[str, Any]) -> bool:
    if state.get("preflight_done"):
        return True
    mode_path = run_dir / "interview" / "mode.json"
    if not mode_path.exists():
        print("error: interview/mode.json missing. Run deep-interview first.", file=sys.stderr)
        return False
    mode = json.loads(mode_path.read_text())
    state.update({
        "mode": mode["mode"],
        "target_repo_path": mode.get("target_repo_path"),
        "lib_name": mode.get("lib_name"),
        "pypi_slug": mode.get("pypi_slug"),
        "python_min": mode.get("python_min") or "3.10",
        "branch_name": mode.get("branch_name"),
        "overrides": mode.get("overrides", {}),
    })
    preflight = [
        sys.executable, str(SCRIPTS_DIR / "preflight.py"),
        "--mode", state["mode"],
    ]
    if state["mode"] == "evolve":
        preflight += ["--target-repo-path", state["target_repo_path"]]
    r = subprocess.run(preflight, check=False)
    if r.returncode != 0:
        return False
    # evolve: create harness branch (default naming or user-chosen)
    if state["mode"] == "evolve":
        branch = state.get("branch_name") or f"harness/{state['run_id']}"
        try:
            subprocess.run(
                ["git", "-C", state["target_repo_path"], "checkout", "-b", branch],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            print(
                f"error: failed to create branch '{branch}' in {state['target_repo_path']}:\n"
                f"  {(e.stderr or e.stdout or '').strip()}\n"
                f"  hint: branch may already exist; delete it or choose a different branch_name "
                f"in interview/mode.json and rerun.",
                file=sys.stderr,
            )
            return False
        state["branch_name"] = branch
    state["preflight_done"] = True
    # Advance out of the initial "di" sentinel (set by init_run.py). s0 is
    # self-skipping for new mode (handled in main loop), so this single value
    # works for both modes.
    if state.get("current_stage") == "di":
        state["current_stage"] = "s0"
    save_state(run_dir, state)
    return True


def stage_headless_with_gate(
    run_dir: Path, state: dict[str, Any], stage: str, gate: str | None,
    next_stage_on_approve: str, output_marker: Path, title: str,
) -> str:
    """Run a headless stage (if output missing), then handle gate (if any).

    Returns next current_stage: either continues to next stage, or returns
    `stage` unchanged if blocked on a gate.
    """
    if not output_marker.exists():
        rc = call_headless(stage, run_dir)
        if rc != 0:
            print(f"[run.py] headless stage {stage} failed rc={rc}", file=sys.stderr)
            sys.exit(rc)
        state["counters"]["total_stages"] += 1
        save_state(run_dir, state)

    if gate is None:
        return next_stage_on_approve

    decision = read_gate_decision(run_dir, gate)
    if decision is None:
        if not (run_dir / f"{gate}.request.md").exists():
            context = f"Review `{output_marker.relative_to(run_dir)}` under `{run_dir}`."
            options = (
                "decision: approved\n"
                "# or\n"
                "decision: rewrite\n"
                "feedback: |\n"
                "  <what to fix>\n"
            )
            if gate == "gateB" and state["mode"] == "evolve":
                options += (
                    "# or (evolve only; breaking change acknowledged)\n"
                    "decision: approved_with_breaking\n"
                    "breaking_notes: |\n"
                    "  <which public APIs break, migration strategy>\n"
                )
            write_gate_request(run_dir, gate, title, context, options)
        print(f"[run.py] paused at {gate}. Write {gate}.decision.md and rerun with --resume.", file=sys.stderr)
        sys.exit(0)

    if decision.get("decision") in ("approved", "approved_with_breaking"):
        state["gate_decisions"][gate] = decision["decision"]
        save_state(run_dir, state)
        return next_stage_on_approve
    elif decision.get("decision") == "rewrite":
        # Delete stage output; rerun stage with feedback next time.
        feedback = decision.get("feedback", "")
        (run_dir / f"{stage}").mkdir(exist_ok=True)
        (run_dir / f"{stage}" / "feedback.md").write_text(f"# Gate {gate} rewrite feedback\n\n{feedback}\n")
        if output_marker.exists():
            output_marker.unlink()
        # clear old decision to block until the stage reruns and a new gate cycle opens
        (run_dir / f"{gate}.decision.md").unlink()
        (run_dir / f"{gate}.request.md").unlink(missing_ok=True)
        state["gate_decisions"][gate] = None
        save_state(run_dir, state)
        return stage  # re-run this stage
    else:
        print(f"error: unknown decision in {gate}.decision.md: {decision}", file=sys.stderr)
        sys.exit(1)


def effective_thresholds(state: dict[str, Any], cfg: dict[str, Any]) -> dict[str, float | int]:
    """Resolve thresholds: config.yaml defaults + mode.json.overrides.

    Override keys match config keys exactly (line_coverage, branch_coverage,
    max_major_issues_new, max_major_issues_evolve); a null override means
    "use the default". Returning all four lets the orchestrator both feed
    gates.py (line/branch) and hand s5 a resolved policy snapshot
    (max_major) instead of making the LLM re-derive overrides.
    """
    base = cfg.get("thresholds", {}) or {}
    ov = state.get("overrides") or {}
    def pick(key: str, fallback: float | int) -> float | int:
        v = ov.get(key)
        if v is None:
            v = base.get(key, fallback)
        return v
    return {
        "line_coverage": float(pick("line_coverage", 0.90)),
        "branch_coverage": float(pick("branch_coverage", 0.80)),
        "max_major_issues_new": int(pick("max_major_issues_new", 0)),
        "max_major_issues_evolve": int(pick("max_major_issues_evolve", 2)),
    }


def write_effective_thresholds(run_dir: Path, state: dict[str, Any], cfg: dict[str, Any]) -> None:
    """Dump the fully-resolved policy snapshot to {run_dir}/effective_thresholds.json.

    s5 reads this file as authoritative (0-2): thresholds live in config.yaml,
    overrides in state.json, the reader LLM shouldn't have to join them. Also
    records the mode + the resolved `max_major_issues` relevant to this run
    so the reviewer can compare against its own issue counts directly.
    """
    th = effective_thresholds(state, cfg)
    mode = state["mode"]
    resolved = {
        "mode": mode,
        "line_coverage": th["line_coverage"],
        "branch_coverage": th["branch_coverage"],
        "max_major_issues_new": th["max_major_issues_new"],
        "max_major_issues_evolve": th["max_major_issues_evolve"],
        "max_major_issues_applicable": (
            th["max_major_issues_new"] if mode == "new" else th["max_major_issues_evolve"]
        ),
    }
    (run_dir / "effective_thresholds.json").write_text(json.dumps(resolved, indent=2))


def run_gates(run_dir: Path, state: dict[str, Any], cfg: dict[str, Any]) -> int:
    """Execute mechanical hard gates directly (0-2 clean separation).

    Writes authoritative results to {run_dir}/gates/*.json. The s5 headless
    reads these files as inputs — LLM never claims mechanical facts it can't
    own. Returns gates.py's exit code (0=all pass, 1=some failed, 2=error).
    """
    if state["mode"] == "new":
        source_dir = run_dir / "workspace"
    else:
        source_dir = Path(state["target_repo_path"])
    th = effective_thresholds(state, cfg)
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "gates.py"),
        "--run-dir", str(run_dir),
        "--source-dir", str(source_dir),
        "--lib-name", state.get("lib_name") or "",
        "--line-threshold", str(th["line_coverage"]),
        "--branch-threshold", str(th["branch_coverage"]),
    ]
    print(f"[run.py] running mechanical gates in {source_dir} "
          f"(line≥{th['line_coverage']}, branch≥{th['branch_coverage']})", file=sys.stderr)
    r = subprocess.run(cmd, check=False)
    return r.returncode


def stage_s5_review(run_dir: Path, state: dict[str, Any], cfg: dict[str, Any]) -> None:
    # 0-2: orchestrator runs mechanical gates before *every* s5 invocation.
    # Re-running on loopbacks matters — `clear_stage_outputs` does not wipe
    # `gates/`, so a cached summary.json would otherwise describe the
    # previous s4 attempt. We pay for an extra uv run on resume-after-crash
    # and accept that cost in exchange for correctness on MINOR/MAJOR loops.
    # Resolved policy (config + overrides) goes to effective_thresholds.json
    # so s5 doesn't have to re-derive max_major / coverage thresholds.
    write_effective_thresholds(run_dir, state, cfg)
    rc = run_gates(run_dir, state, cfg)
    if rc == 2:
        print("[run.py] gates.py errored (rc=2). Fix toolchain and resume.", file=sys.stderr)
        sys.exit(2)
    # rc 0 (pass) or 1 (some failed) both mean gates ran; s5 will read results.

    verdict_path = run_dir / "s5" / "verdict.yaml"
    if not verdict_path.exists():
        rc = call_headless("s5_review", run_dir)
        if rc != 0:
            print("[run.py] s5 review failed", file=sys.stderr)
            sys.exit(rc)
        state["counters"]["total_stages"] += 1
    if not verdict_path.exists():
        print("error: s5 did not produce verdict.yaml", file=sys.stderr)
        sys.exit(1)


def load_verdict(run_dir: Path) -> dict[str, Any]:
    return yaml.safe_load((run_dir / "s5" / "verdict.yaml").read_text())


def issues_key(verdict: dict[str, Any]) -> list[str]:
    """Stable keys for comparing issue sets across verdicts.

    `file:severity` alone conflates distinct issues that happen to live in the
    same file at the same severity (common for "two major issues in core.py"),
    which under-counts them in `compute_update_candidates` and under-detects
    stagnation overlap. Including the first few words of the description
    discriminates without making the key brittle to minor wording edits.
    """
    def _key(issue: dict[str, Any]) -> str:
        file = issue.get("file", "?")
        sev = issue.get("severity", "?")
        desc = (issue.get("description", "") or "").strip()
        desc_head = " ".join(desc.split()[:5])
        return f"{file}:{sev}:{desc_head}" if desc_head else f"{file}:{sev}"
    return sorted(_key(i) for i in verdict.get("issues", []))


def stagnation_triggered(history: list[dict[str, Any]], cfg: dict[str, Any]) -> bool:
    window = cfg["stagnation"]["window"]
    ratio = cfg["stagnation"]["min_overlap_ratio"]
    if len(history) < window:
        return False
    recent = history[-window:]
    sets = [set(h["issues_key"]) for h in recent]
    union = set().union(*sets)
    if not union:
        return False
    inter = set.intersection(*sets)
    return (len(inter) / len(union)) >= ratio


def stage_s6_decide(run_dir: Path, state: dict[str, Any], cfg: dict[str, Any]) -> str:
    verdict = load_verdict(run_dir)
    state["verdict_history"].append({
        "stage_run_idx": state["counters"]["total_stages"],
        "verdict": verdict["verdict"],
        "issues_key": issues_key(verdict),
    })

    # 0-2 guard: mechanical gates are authoritative. If python found failures
    # but the LLM wrote PASS, the LLM either didn't read the gate results or
    # hallucinated. Either way, the run is untrustworthy — escalate.
    gates_summary_path = run_dir / "gates" / "summary.json"
    if gates_summary_path.exists():
        gates_summary = json.loads(gates_summary_path.read_text())
        if not gates_summary.get("all_passed", True) and verdict["verdict"] == "PASS":
            return escalate(run_dir, state, "llm_pass_despite_failing_gates", verdict)

    # stagnation
    if stagnation_triggered(state["verdict_history"], cfg):
        return escalate(run_dir, state, "stagnation", verdict)

    # caps
    counters = state["counters"]
    caps = cfg["caps"]
    if counters["total_stages"] >= caps["total_stages"]:
        return escalate(run_dir, state, "cap_total_stages", verdict)

    v = verdict["verdict"]
    if v == "PASS":
        (run_dir / "s6").mkdir(exist_ok=True)
        (run_dir / "s6" / "decision.json").write_text(json.dumps({"action": "advance", "target": "s7"}, indent=2))
        save_state(run_dir, state)
        return "s7"
    if v == "CRITICAL":
        return escalate(run_dir, state, "critical_verdict", verdict)
    if v == "MINOR":
        if counters["minor_loop"] >= caps["minor_loop"]:
            return escalate(run_dir, state, "cap_minor_loop", verdict)
        counters["minor_loop"] += 1
        preserve_loop_feedback(run_dir, "s4_implement")
        # Clear s4/s5 outputs *and* gates — stage_s5_review re-runs gates
        # unconditionally, but leaving a stale summary.json around invites
        # confusion if a reviewer inspects run_dir mid-loop.
        clear_stage_outputs(run_dir, ["s4", "s5", "gates"])
        (run_dir / "s6").mkdir(exist_ok=True)
        (run_dir / "s6" / "decision.json").write_text(json.dumps({"action": "loop", "target": "s4"}, indent=2))
        save_state(run_dir, state)
        return "s4"
    if v == "MAJOR":
        if counters["major_loop"] >= caps["major_loop"]:
            return escalate(run_dir, state, "cap_major_loop", verdict)
        counters["major_loop"] += 1
        counters["minor_loop"] = 0
        preserve_loop_feedback(run_dir, "s2_design")
        clear_stage_outputs(run_dir, ["s2", "s3", "s4", "s5", "gates"])
        # gateB must be reopened
        for f in ("gateB.request.md", "gateB.decision.md"):
            (run_dir / f).unlink(missing_ok=True)
        state["gate_decisions"]["gateB"] = None
        (run_dir / "s6").mkdir(exist_ok=True)
        (run_dir / "s6" / "decision.json").write_text(json.dumps({"action": "loop", "target": "s2"}, indent=2))
        save_state(run_dir, state)
        return "s2"
    print(f"error: unknown verdict value: {v}", file=sys.stderr)
    sys.exit(1)


def clear_stage_outputs(run_dir: Path, stages: list[str]) -> None:
    for s in stages:
        d = run_dir / s
        if d.exists():
            shutil.rmtree(d)


def preserve_loop_feedback(run_dir: Path, loop_stage: str) -> None:
    """Write s5/review.md + verdict.yaml to {run_dir}/{loop_stage}/feedback.md
    before the upcoming clear_stage_outputs wipes s5.

    Harness-builder SKILL §0-4: the looped-back stage must receive "why the
    last attempt failed" as input. Without this, MINOR/MAJOR loops re-run
    blind because s5 is cleared alongside s4.

    `loop_stage` is the headless stage name (e.g. "s4_implement",
    "s2_design") — matches the gate-rewrite convention in
    stage_headless_with_gate so each stage has one feedback.md path.
    """
    review = run_dir / "s5" / "review.md"
    verdict = run_dir / "s5" / "verdict.yaml"
    if not review.exists() and not verdict.exists():
        return
    target_dir = run_dir / loop_stage
    target_dir.mkdir(exist_ok=True)
    parts = [f"# Feedback from prior s5 review (loop into {loop_stage})\n"]
    if verdict.exists():
        parts.append("## verdict.yaml\n\n```yaml\n" + verdict.read_text() + "\n```\n")
    if review.exists():
        parts.append("## review.md\n\n" + review.read_text() + "\n")
    (target_dir / "feedback.md").write_text("\n".join(parts))


def escalate(run_dir: Path, state: dict[str, Any], trigger: str, verdict: dict[str, Any]) -> str:
    path = run_dir / "escalation.md"
    last_two = state["verdict_history"][-2:]
    pattern_block = format_cross_run_pattern_block(current_run_id=state["run_id"], limit=10)
    gates_summary_path = run_dir / "gates" / "summary.json"
    gates_block = (
        gates_summary_path.read_text() if gates_summary_path.exists() else "(not available — gates not yet run this cycle)"
    )
    verdict_yaml = (run_dir / "s5" / "verdict.yaml").read_text() if (run_dir / "s5" / "verdict.yaml").exists() else "(not found)"
    # Flush-left triple-quoting avoids the textwrap.dedent trap (see write_gate_request).
    body = (
        f"# Escalation\n"
        f"\n"
        f"## Trigger\n"
        f"{trigger}\n"
        f"\n"
        f"## Current state\n"
        f"- current_stage: {state.get('current_stage')}\n"
        f"- counters: {json.dumps(state['counters'])}\n"
        f"- mode: {state['mode']}\n"
        f"\n"
        f"## Mechanical gates (python-authoritative, 0-2)\n"
        f"```json\n"
        f"{gates_block.rstrip()}\n"
        f"```\n"
        f"\n"
        f"## LLM verdict (judgment, 0-2)\n"
        f"```yaml\n"
        f"{verdict_yaml.rstrip()}\n"
        f"```\n"
        f"\n"
        f"## Recent verdict history (up to last 2)\n"
        f"```json\n"
        f"{json.dumps(last_two, indent=2)}\n"
        f"```\n"
        f"\n"
        f"## Cross-run context (prior runs, 0-5 evidence)\n"
        f"{pattern_block.rstrip()}\n"
        f"\n"
        f"## Expected user decision\n"
        f"\n"
        f"Write `escalation.decision.md` next to this file:\n"
        f"\n"
        f"```\n"
        f"action: abort\n"
        f"# or\n"
        f"action: resume_from_plan\n"
        f"feedback: |\n"
        f"  <what was wrong>\n"
        f"# or\n"
        f"action: resume_from_design\n"
        f"feedback: |\n"
        f"  <...>\n"
        f"# or\n"
        f"action: force_continue\n"
        f"reset_counters: [minor_loop, major_loop]\n"
        f"```\n"
    )
    path.write_text(body)
    # Remember the trigger so the final index entry (written on PASS/abort) can
    # record it. Previously we appended an "escalated" row here and another
    # "<final_verdict>" row later, double-counting the same run in cross-run
    # stats. Now we record exactly one entry per run at its terminal state.
    state["last_escalation_trigger"] = trigger
    save_state(run_dir, state)
    print(f"[run.py] escalation written to {path}. Resolve and rerun with --resume.", file=sys.stderr)
    sys.exit(2)


def handle_escalation_decision(run_dir: Path, state: dict[str, Any]) -> str | None:
    dec_path = run_dir / "escalation.decision.md"
    if not dec_path.exists():
        return None
    dec = read_gate_decision(run_dir, "escalation")
    if not dec:
        return None
    action = dec.get("action", "").strip()
    if action == "abort":
        # Terminal state: record one-and-only-one index entry for this run.
        append_index_entry(
            state,
            final_status="aborted",
            escalation_trigger=state.get("last_escalation_trigger"),
        )
        print("[run.py] run aborted by user.", file=sys.stderr)
        sys.exit(0)
    if action == "resume_from_plan":
        # Also clear stage-feedback directories (s0_survey, s1_plan, s2_design,
        # s4_implement) — these hold rewrite/loop feedback written by prior
        # stage runs. Carrying them into a fresh plan-resume would feed stale
        # "here's what went wrong last time" context into stages that are now
        # answering a different question.
        clear_stage_outputs(run_dir, [
            "s1", "s2", "s3", "s4", "s5", "s6",
            "s0_survey", "s1_plan", "s2_design", "s4_implement",
        ])
        for f in ("gateA.request.md", "gateA.decision.md", "gateB.request.md", "gateB.decision.md"):
            (run_dir / f).unlink(missing_ok=True)
        state["gate_decisions"]["gateA"] = None
        state["gate_decisions"]["gateB"] = None
        state["counters"] = {"minor_loop": 0, "major_loop": 0, "total_stages": state["counters"]["total_stages"]}
        # Fresh plan means the prior review cycle is no longer representative;
        # keeping its issues in verdict_history would let stagnation detection
        # trip on pre-escalation evidence that no longer reflects the new plan.
        state["verdict_history"] = []
        next_stage = "s1"
    elif action == "resume_from_design":
        clear_stage_outputs(run_dir, [
            "s2", "s3", "s4", "s5", "s6",
            "s2_design", "s4_implement",
        ])
        for f in ("gateB.request.md", "gateB.decision.md"):
            (run_dir / f).unlink(missing_ok=True)
        state["gate_decisions"]["gateB"] = None
        state["counters"]["minor_loop"] = 0
        state["counters"]["major_loop"] = 0
        next_stage = "s2"
    elif action == "force_continue":
        for c in (dec.get("reset_counters", "") or "").replace("[", "").replace("]", "").split(","):
            c = c.strip()
            if c in state["counters"]:
                state["counters"][c] = 0
        # force_continue routes us back to s6, which re-reads the same verdict
        # and would append it to verdict_history a second time (inflating the
        # stagnation window). Pop the last entry so the re-append nets zero.
        # The user is responsible for resetting the counter that triggered the
        # escalation — otherwise s6 will re-escalate on the same cap.
        if state.get("verdict_history"):
            state["verdict_history"].pop()
        next_stage = state.get("current_stage") or "s5"
    else:
        print(f"error: unknown escalation action: {action}", file=sys.stderr)
        sys.exit(1)
    # consume the escalation
    dec_path.unlink()
    (run_dir / "escalation.md").unlink(missing_ok=True)
    save_state(run_dir, state)
    return next_stage


# ---------- main loop ----------

STAGE_OUTPUT_MARKERS: dict[str, str] = {
    "s0": "s0/survey.md",
    "s1": "s1/plan.md",
    "s2": "s2/design.md",
    "s3": "s3/test-manifest.md",
    "s4": "s4/impl-notes.md",
    # s7 writes multiple artifacts (README/docs/CHANGELOG in new mode,
    # docs-diff.patch in evolve mode); docs-done.marker is the single
    # completion signal both modes emit and is what main()'s s7 branch checks.
    "s7": "s7/docs-done.marker",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--resume", action="store_true", help="(no-op flag; accepted for clarity)")
    args = ap.parse_args()

    run_dir = HARNESS_ROOT / "outputs" / args.run_id
    if not run_dir.is_dir():
        print(f"error: run directory not found: {run_dir}", file=sys.stderr)
        return 1

    state = load_state(run_dir)
    cfg = load_config()

    # Resolve escalation first if one is pending.
    if (run_dir / "escalation.md").exists():
        nxt = handle_escalation_decision(run_dir, state)
        if nxt is None:
            print("[run.py] escalation still pending. Resolve escalation.decision.md.", file=sys.stderr)
            return 0
        state["current_stage"] = nxt
        save_state(run_dir, state)

    if not stage_preflight(run_dir, state):
        return 1

    # Pipeline loop.
    while True:
        stage = state["current_stage"]

        if stage == "di":
            # Should not happen after preflight succeeds; safeguard.
            print("error: still at di after preflight. Check interview/mode.json.", file=sys.stderr)
            return 1

        if stage == "s0":
            if state["mode"] != "evolve":
                state["current_stage"] = "s1"
                save_state(run_dir, state)
                continue
            next_stage = stage_headless_with_gate(
                run_dir, state, "s0_survey", "gate0", "s1",
                run_dir / STAGE_OUTPUT_MARKERS["s0"],
                "Codebase survey approval (evolve mode)",
            )
            # stage_headless_with_gate may return the stage name ("s0_survey") on rewrite;
            # remap to pipeline stage id.
            state["current_stage"] = "s0" if next_stage == "s0_survey" else next_stage
            save_state(run_dir, state)
            continue

        if stage == "s1":
            next_stage = stage_headless_with_gate(
                run_dir, state, "s1_plan", "gateA", "s2",
                run_dir / STAGE_OUTPUT_MARKERS["s1"],
                "Plan approval",
            )
            state["current_stage"] = "s1" if next_stage == "s1_plan" else next_stage
            save_state(run_dir, state)
            continue

        if stage == "s2":
            next_stage = stage_headless_with_gate(
                run_dir, state, "s2_design", "gateB", "s3",
                run_dir / STAGE_OUTPUT_MARKERS["s2"],
                "Design approval" + (" (confirm breaking changes if any)" if state["mode"] == "evolve" else ""),
            )
            state["current_stage"] = "s2" if next_stage == "s2_design" else next_stage
            save_state(run_dir, state)
            continue

        if stage == "s3":
            _ = stage_headless_with_gate(
                run_dir, state, "s3_tests", None, "s4",
                run_dir / STAGE_OUTPUT_MARKERS["s3"],
                "",
            )
            state["current_stage"] = "s4"
            save_state(run_dir, state)
            continue

        if stage == "s4":
            _ = stage_headless_with_gate(
                run_dir, state, "s4_implement", None, "s5",
                run_dir / STAGE_OUTPUT_MARKERS["s4"],
                "",
            )
            state["current_stage"] = "s5"
            save_state(run_dir, state)
            continue

        if stage == "s5":
            stage_s5_review(run_dir, state, cfg)
            save_state(run_dir, state)
            state["current_stage"] = "s6"
            save_state(run_dir, state)
            continue

        if stage == "s6":
            nxt = stage_s6_decide(run_dir, state, cfg)
            state["current_stage"] = nxt
            save_state(run_dir, state)
            continue

        if stage == "s7":
            _ = stage_headless_with_gate(
                run_dir, state, "s7_docs", None, "s8",
                run_dir / "s7" / "docs-done.marker",
                "",
            )
            state["current_stage"] = "s8"
            save_state(run_dir, state)
            continue

        if stage == "s8":
            write_delivery(run_dir, state, cfg)
            state["current_stage"] = "done"
            save_state(run_dir, state)
            print(f"[run.py] delivery.md written. Run complete: {run_dir}/delivery.md")
            return 0

        if stage == "done":
            print(f"[run.py] run already complete: {run_dir}/delivery.md")
            return 0

        print(f"error: unknown stage: {stage}", file=sys.stderr)
        return 1


def compute_update_candidates(state: dict[str, Any]) -> list[tuple[str, int]]:
    """Return (file:severity, count) pairs that reviewer flagged across ≥2 loops.

    These are *candidates* for promotion into docs/tacit-knowledge.md or a stage
    prompt — repeated failure patterns that the harness itself couldn't learn
    from (by design; SKILL §0-5 forbids self-modification). A human reviews
    this list and decides what to promote.
    """
    from collections import Counter
    history = state.get("verdict_history", [])
    if len(history) <= 1:
        return []
    counts: Counter[str] = Counter()
    for entry in history:
        for key in entry.get("issues_key", []):
            counts[key] += 1
    return [(k, v) for k, v in counts.most_common() if v >= 2]


def write_delivery(run_dir: Path, state: dict[str, Any], cfg: dict[str, Any]) -> None:
    verdict_path = run_dir / "s5" / "verdict.yaml"
    verdict = yaml.safe_load(verdict_path.read_text()) if verdict_path.exists() else {}
    mode = state["mode"]
    lib_name = state.get("lib_name") or "<unknown>"

    next_actions_new = (
        f"- Review `outputs/{state['run_id']}/workspace/`.\n"
        f"- Move the workspace to your desired location (git init or drop into an existing mono-repo).\n"
        f"- Set up remote, tag, and publish with `uv publish` when ready.\n"
    )
    next_actions_evolve = (
        f"- Review the branch `{state.get('branch_name')}` in `{state.get('target_repo_path')}`.\n"
        f"- Inspect `outputs/{state['run_id']}/s4/changes.patch` for the exact diff.\n"
        f"- Open a PR or merge into your integration branch per your team's process.\n"
    )

    gates_summary = (run_dir / "gates" / "summary.json").read_text() if (run_dir / "gates" / "summary.json").exists() else "(missing)"
    verdict_yaml_text = verdict_path.read_text() if verdict_path.exists() else "(missing)"
    next_actions = next_actions_new if mode == "new" else next_actions_evolve

    # Flush-left triple-quoting avoids the textwrap.dedent trap (see write_gate_request).
    body = (
        f"# DELIVERY — {lib_name} ({mode})\n"
        f"\n"
        f"Run: `{state['run_id']}`\n"
        f"Completed: {state.get('created_at')}\n"
        f"Mode: **{mode}**\n"
        f"\n"
        f"## Gate decisions\n"
        f"```json\n"
        f"{json.dumps(state.get('gate_decisions', {}), indent=2)}\n"
        f"```\n"
        f"\n"
        f"## Loop counters (final)\n"
        f"```json\n"
        f"{json.dumps(state.get('counters', {}), indent=2)}\n"
        f"```\n"
        f"\n"
        f"## Mechanical gates (python-authoritative, 0-2)\n"
        f"```json\n"
        f"{gates_summary.rstrip()}\n"
        f"```\n"
        f"\n"
        f"## LLM verdict (judgment, 0-2)\n"
        f"```yaml\n"
        f"{verdict_yaml_text.rstrip()}\n"
        f"```\n"
        f"\n"
        f"## Next actions\n"
        f"{next_actions.rstrip()}\n"
        f"\n"
        f"## Known limits\n"
        f"- See `s5/review.md` for remaining minor notes.\n"
        f"- Thresholds applied: {json.dumps(cfg.get('thresholds', {}))}.\n"
    )

    # §0-5: delivery.md의 "암묵지 업데이트 후보" 섹션. 반복 지적된 패턴만
    # 제안으로 싣는다. tacit-knowledge.md / 프롬프트 수정은 사람이 결정.
    candidates = compute_update_candidates(state)
    if candidates:
        lines = [f"- `{key}` — reviewer flagged {n}× across loops" for key, n in candidates]
        candidates_md = "\n".join(lines)
    else:
        candidates_md = "_no issue repeated across loops — nothing to promote._"
    body += (
        f"\n"
        f"## 암묵지 업데이트 후보 (SKILL §0-5)\n"
        f"\n"
        f"리뷰에서 반복된 패턴. 사람이 검토해 필요하면 `docs/tacit-knowledge.md` 또는\n"
        f"해당 stage 프롬프트에 영구 규약으로 반영할 후보다. **하네스는 자동 수정하지\n"
        f"않는다.**\n"
        f"\n"
        f"{candidates_md}\n"
    )

    (run_dir / "delivery.md").write_text(body)
    final_status = verdict.get("verdict", "UNKNOWN") if verdict else "UNKNOWN"
    # Single terminal index entry per run. `last_escalation_trigger` is set by
    # escalate() and persists across the escalation resolution; if this run
    # never escalated, it's simply None.
    append_index_entry(
        state,
        final_status=final_status,
        escalation_trigger=state.get("last_escalation_trigger"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
