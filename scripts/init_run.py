#!/usr/bin/env python3
"""Initialize a python-lib-dev run.

Creates outputs/<run-id>/ under this harness and writes an initial state.json.
Invoked by the orchestrating skill as the first action after preflight passes.

Usage:
    python init_run.py [--run-id <id>]

Prints the absolute run directory path to stdout for the caller to capture.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parent.parent


def make_run_id() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def main() -> int:
    ap = argparse.ArgumentParser(description="Initialize a python-lib-dev run.")
    ap.add_argument("--run-id", default=None, help="Run ID (default: timestamp)")
    args = ap.parse_args()

    run_id = args.run_id or make_run_id()
    run_dir = HARNESS_ROOT / "outputs" / run_id
    if run_dir.exists():
        print(f"error: run already exists: {run_dir}", file=sys.stderr)
        return 1

    (run_dir / "interview").mkdir(parents=True)
    # Schema follows harness-builder § 단계 4 모드 A 정식 스키마:
    # `status`/`current_stage`/`awaiting_input_schema`/`user_input`/`stage_outputs`
    # are baseline; `counters`/`overrides` are domain-specific per the spec.
    # Domain extensions: mode/target_repo_path/lib_name/etc., verdict_history (0-4),
    # gate_decisions (per-gate state), preflight_done, branch_name, last_escalation_trigger.
    state = {
        "run_id": run_id,
        "harness": "python-lib-dev",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "status": "running",
        "current_stage": "di",
        "awaiting_input_schema": None,
        "user_input": {},
        "stage_outputs": {},
        "mode": None,
        "target_repo_path": None,
        "lib_name": None,
        "pypi_slug": None,
        "python_min": None,
        "overrides": {
            "line_coverage": None,
            "branch_coverage": None,
            "max_major_issues_new": None,
            "max_major_issues_evolve": None,
        },
        "counters": {"minor_loop": 0, "major_loop": 0, "total_stages": 0},
        "verdict_history": [],
        # Pre-populate every gate slot the orchestrator may touch so the schema
        # is closed at init time. gate0 is evolve-only but the key still exists
        # in new mode (stays None). This matches how preflight_done /
        # branch_name / last_escalation_trigger are declared up-front rather
        # than auto-created on first write.
        "gate_decisions": {"gate0": None, "gateA": None, "gateB": None},
        "preflight_done": False,
        "branch_name": None,
        "last_escalation_trigger": None,
    }
    (run_dir / "state.json").write_text(json.dumps(state, indent=2))
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
