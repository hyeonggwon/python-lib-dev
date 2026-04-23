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


# ---------- gates ----------

def gate_pending(run_dir: Path, gate: str) -> bool:
    request = run_dir / f"{gate}.request.md"
    decision = run_dir / f"{gate}.decision.md"
    return request.exists() and not decision.exists()


def write_gate_request(run_dir: Path, gate: str, title: str, context: str, options: str) -> None:
    body = textwrap.dedent(f"""\
        # Gate: {title}

        ## Context

        {context}

        ## Expected decision

        Write `{gate}.decision.md` next to this file with one of:

        {options}
    """)
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
                buf: list[str] = []
                i += 1
                while i < len(lines) and (lines[i].startswith("  ") or lines[i].startswith("\t") or not lines[i].strip()):
                    if lines[i].strip():
                        buf.append(lines[i].lstrip())
                    i += 1
                result[key] = "\n".join(buf).strip()
                continue
            else:
                result[key] = rest
        i += 1
    return result


# ---------- headless invocation ----------

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

    # Non-interactive; assumes the user runs claude with appropriate permissions.
    # Using --print to keep output capturable; the actual work is file-based.
    cmd = ["claude", "-p", wrapper]
    print(f"[run.py] invoking headless for stage={stage}", file=sys.stderr)
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
        subprocess.run(
            ["git", "-C", state["target_repo_path"], "checkout", "-b", branch],
            check=True,
        )
        state["branch_name"] = branch
    state["preflight_done"] = True
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
            options = textwrap.dedent("""\
                    decision: approved
                    # or
                    decision: rewrite
                    feedback: |
                      <what to fix>
            """)
            if gate == "gateB" and state["mode"] == "evolve":
                options += textwrap.dedent("""\
                    # or (evolve only; breaking change acknowledged)
                    decision: approved_with_breaking
                    breaking_notes: |
                      <which public APIs break, migration strategy>
                """)
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


def stage_s5_review(run_dir: Path, state: dict[str, Any]) -> None:
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
    return sorted(f"{i.get('file', '?')}:{i.get('severity', '?')}" for i in verdict.get("issues", []))


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
        clear_stage_outputs(run_dir, ["s4", "s5"])
        (run_dir / "s6").mkdir(exist_ok=True)
        (run_dir / "s6" / "decision.json").write_text(json.dumps({"action": "loop", "target": "s4"}, indent=2))
        save_state(run_dir, state)
        return "s4"
    if v == "MAJOR":
        if counters["major_loop"] >= caps["major_loop"]:
            return escalate(run_dir, state, "cap_major_loop", verdict)
        counters["major_loop"] += 1
        counters["minor_loop"] = 0
        clear_stage_outputs(run_dir, ["s2", "s3", "s4", "s5"])
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


def escalate(run_dir: Path, state: dict[str, Any], trigger: str, verdict: dict[str, Any]) -> str:
    path = run_dir / "escalation.md"
    last_two = state["verdict_history"][-2:]
    body = textwrap.dedent(f"""\
        # Escalation

        ## Trigger
        {trigger}

        ## Current state
        - current_stage: {state.get('current_stage')}
        - counters: {json.dumps(state['counters'])}
        - mode: {state['mode']}

        ## Latest verdict
        ```yaml
        {(run_dir / 's5' / 'verdict.yaml').read_text() if (run_dir / 's5' / 'verdict.yaml').exists() else '(not found)'}
        ```

        ## Recent verdict history (up to last 2)
        ```json
        {json.dumps(last_two, indent=2)}
        ```

        ## Expected user decision

        Write `escalation.decision.md` next to this file:

            action: abort
            # or
            action: resume_from_plan
            feedback: |
              <what was wrong>
            # or
            action: resume_from_design
            feedback: |
              <...>
            # or
            action: force_continue
            reset_counters: [minor_loop, major_loop]
    """)
    path.write_text(body)
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
        print("[run.py] run aborted by user.", file=sys.stderr)
        sys.exit(0)
    if action == "resume_from_plan":
        clear_stage_outputs(run_dir, ["s1", "s2", "s3", "s4", "s5", "s6"])
        for f in ("gateA.request.md", "gateA.decision.md", "gateB.request.md", "gateB.decision.md"):
            (run_dir / f).unlink(missing_ok=True)
        state["gate_decisions"]["gateA"] = None
        state["gate_decisions"]["gateB"] = None
        state["counters"] = {"impl_retry": 0, "minor_loop": 0, "major_loop": 0, "total_stages": state["counters"]["total_stages"]}
        next_stage = "s1"
    elif action == "resume_from_design":
        clear_stage_outputs(run_dir, ["s2", "s3", "s4", "s5", "s6"])
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
    "s7": "s7/docs-diff.patch",  # evolve; new: workspace/README.md exists too
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
            stage_s5_review(run_dir, state)
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
            print(f"[run.py] DELIVERY.md written. Run complete: {run_dir}/DELIVERY.md")
            return 0

        if stage == "done":
            print(f"[run.py] run already complete: {run_dir}/DELIVERY.md")
            return 0

        print(f"error: unknown stage: {stage}", file=sys.stderr)
        return 1


def write_delivery(run_dir: Path, state: dict[str, Any], cfg: dict[str, Any]) -> None:
    verdict_path = run_dir / "s5" / "verdict.yaml"
    verdict = yaml.safe_load(verdict_path.read_text()) if verdict_path.exists() else {}
    mode = state["mode"]
    lib_name = state.get("lib_name") or "<unknown>"

    next_actions_new = textwrap.dedent(f"""\
        - Review `outputs/{state['run_id']}/workspace/`.
        - Move the workspace to your desired location (git init or drop into an existing mono-repo).
        - Set up remote, tag, and publish with `uv publish` when ready.
    """)
    next_actions_evolve = textwrap.dedent(f"""\
        - Review the branch `{state.get('branch_name')}` in `{state.get('target_repo_path')}`.
        - Inspect `outputs/{state['run_id']}/s4/changes.patch` for the exact diff.
        - Open a PR or merge into your integration branch per your team's process.
    """)

    body = textwrap.dedent(f"""\
        # DELIVERY — {lib_name} ({mode})

        Run: `{state['run_id']}`
        Completed: {state.get('created_at')}
        Mode: **{mode}**

        ## Gate decisions
        ```json
        {json.dumps(state.get('gate_decisions', {}), indent=2)}
        ```

        ## Loop counters (final)
        ```json
        {json.dumps(state.get('counters', {}), indent=2)}
        ```

        ## Final verdict
        ```yaml
        {verdict_path.read_text() if verdict_path.exists() else '(missing)'}
        ```

        ## Next actions
        {next_actions_new if mode == 'new' else next_actions_evolve}

        ## Known limits
        - See `s5/review.md` for remaining minor notes.
        - Thresholds applied: {json.dumps(cfg.get('thresholds', {}))}.
    """)
    (run_dir / "DELIVERY.md").write_text(body)


if __name__ == "__main__":
    raise SystemExit(main())
