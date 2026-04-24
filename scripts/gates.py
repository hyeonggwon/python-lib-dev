#!/usr/bin/env python3
"""Mechanical hard-gate execution (harness-builder SKILL.md §0-2).

The orchestrator runs these gates directly rather than trusting the s5 review
LLM to self-report. Results land in `{run_dir}/gates/<name>.json` and become
authoritative inputs for s5 (the LLM reads the files rather than running the
commands itself).

Exit codes:
  0 = all gates passed
  1 = one or more gates failed (still wrote results)
  2 = gates.py itself errored out (missing toolchain, bad args, etc.)

Invoked from run.py:stage_s5_review before the s5 headless call.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


OUTPUT_TAIL = 2000  # bytes of command output captured per gate


def run_cmd(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run a subprocess capturing both streams. `env`, if given, is merged onto os.environ."""
    merged_env: dict[str, str] | None = None
    if env is not None:
        merged_env = {**os.environ, **env}
    r = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, check=False, env=merged_env,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def check_pytest_cov(source_dir: Path) -> str | None:
    """Return an error message if pytest-cov is not importable, else None.

    Without this preflight, `pytest --cov=...` fails with "unrecognized
    arguments" and the coverage gate reports it as a plain test failure —
    confusing for s5 and the human reviewing escalation.md.
    """
    rc, out = run_cmd(["uv", "run", "python", "-c", "import pytest_cov"], source_dir)
    if rc != 0:
        return (
            "pytest-cov is not importable in the target environment. "
            "Add it to the library's dev dependencies (e.g. `uv add --dev pytest-cov`).\n"
            f"uv output tail:\n{out[-500:]}"
        )
    return None


def gate_tests_and_coverage(
    source_dir: Path,
    lib_name: str,
    line_threshold: float,
    branch_threshold: float,
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run pytest once with coverage; derive the tests and coverage gates.

    - tests.passed    = pytest rc == 0
    - coverage.passed = tests passed AND both coverage thresholds met

    Threshold comparison is a mechanical check, so it lives here and not in
    the s5 LLM. `summary.all_passed` (which the s6 guard trusts) therefore
    reflects coverage threshold violations.
    """
    # Keep the raw cov json inside run_dir/gates/ so we never write to the
    # target_repo_path in evolve mode. Deleted after parsing — the parsed
    # fields land in coverage.json. COVERAGE_FILE env redirects the `.coverage`
    # sqlite sidecar into the same dir so the evolve-mode target working tree
    # stays clean (otherwise pytest-cov writes `.coverage` at cwd, which dirties
    # the branch between gate runs).
    cov_json = run_dir / "gates" / ".coverage-raw.json"
    cov_sqlite = run_dir / "gates" / ".coverage"
    cov_json.parent.mkdir(parents=True, exist_ok=True)

    rc, out = run_cmd(
        [
            "uv", "run", "pytest",
            f"--cov={lib_name}",
            "--cov-branch",
            f"--cov-report=json:{cov_json}",
            "--cov-report=term",
            "-q",
        ],
        source_dir,
        env={"COVERAGE_FILE": str(cov_sqlite)},
    )

    line_cov: float | None = None
    branch_cov: float | None = None
    if cov_json.exists():
        try:
            data = json.loads(cov_json.read_text())
            totals = data.get("totals", {})
            percent = totals.get("percent_covered")
            if percent is not None:
                line_cov = float(percent) / 100
            covered = totals.get("covered_branches")
            num = totals.get("num_branches")
            if covered is not None and num:
                branch_cov = float(covered) / float(num)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
        finally:
            cov_json.unlink(missing_ok=True)
    # Clean up the `.coverage` sqlite sidecar regardless of parse outcome.
    cov_sqlite.unlink(missing_ok=True)

    tests_result = {
        "gate": "tests",
        "passed": rc == 0,
        "rc": rc,
        "output_tail": out[-OUTPUT_TAIL:],
    }

    # Missing coverage data is treated as a fail — conservative, and the raw
    # output_tail will show why (missing plugin, crashed collector, etc.).
    thresholds_met = (
        line_cov is not None
        and line_cov >= line_threshold
        and branch_cov is not None
        and branch_cov >= branch_threshold
    )
    coverage_result = {
        "gate": "coverage",
        "passed": rc == 0 and thresholds_met,
        "rc": rc,
        "line_coverage": line_cov,
        "branch_coverage": branch_cov,
        "line_threshold": line_threshold,
        "branch_threshold": branch_threshold,
        "thresholds_met": thresholds_met,
        "output_tail": out[-OUTPUT_TAIL:],
    }
    return tests_result, coverage_result


def gate_mypy(source_dir: Path) -> dict[str, Any]:
    """Run mypy --strict against the src layout when present.

    `mypy --strict .` is actively harmful — it recurses into `.venv/`, vendored
    directories, and build artifacts, and fails on third-party code we don't
    own. We prefer a `src` (and `tests`) target when the standard layout is in
    place (both modes, typically), and otherwise pass no paths so mypy falls
    back to the repo's own `[tool.mypy]` configuration.
    """
    targets: list[str] = []
    if (source_dir / "src").is_dir():
        targets.append("src")
        if (source_dir / "tests").is_dir():
            targets.append("tests")
    cmd = ["uv", "run", "mypy", "--strict", *targets]
    rc, out = run_cmd(cmd, source_dir)
    return {
        "gate": "mypy",
        "passed": rc == 0,
        "rc": rc,
        "targets": targets or ["(defer to repo mypy config)"],
        "output_tail": out[-OUTPUT_TAIL:],
    }


def _ruff_targets(source_dir: Path) -> list[str]:
    """Mirror gate_mypy's target selection: prefer the standard src/tests layout.

    In evolve mode, `ruff check .` / `ruff format --check .` would scan the
    entire repo including files the harness never touched, so a pre-existing
    style drift unrelated to this run fails the gate. Narrowing to src/tests
    keeps the gate judgment aligned with what s4 was allowed to modify.
    Falls back to `.` when neither directory exists (e.g. flat layout).
    """
    targets: list[str] = []
    if (source_dir / "src").is_dir():
        targets.append("src")
    if (source_dir / "tests").is_dir():
        targets.append("tests")
    return targets or ["."]


def gate_ruff_check(source_dir: Path) -> dict[str, Any]:
    targets = _ruff_targets(source_dir)
    rc, out = run_cmd(["uv", "run", "ruff", "check", *targets], source_dir)
    return {"gate": "ruff_check", "passed": rc == 0, "rc": rc, "targets": targets, "output_tail": out[-OUTPUT_TAIL:]}


def gate_ruff_format(source_dir: Path) -> dict[str, Any]:
    targets = _ruff_targets(source_dir)
    rc, out = run_cmd(["uv", "run", "ruff", "format", "--check", *targets], source_dir)
    return {"gate": "ruff_format", "passed": rc == 0, "rc": rc, "targets": targets, "output_tail": out[-OUTPUT_TAIL:]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run mechanical hard gates and write results to run_dir/gates/.")
    ap.add_argument("--run-dir", required=True, help="Absolute path to outputs/<run-id>/")
    ap.add_argument("--source-dir", required=True, help="Where uv run executes (new: workspace/, evolve: target_repo_path)")
    ap.add_argument("--lib-name", required=True)
    ap.add_argument("--line-threshold", type=float, required=True,
                    help="Effective line-coverage threshold (config.yaml + mode.json.overrides).")
    ap.add_argument("--branch-threshold", type=float, required=True,
                    help="Effective branch-coverage threshold.")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    source_dir = Path(args.source_dir).resolve()
    if not source_dir.is_dir():
        print(f"error: source_dir not a directory: {source_dir}", file=sys.stderr)
        return 2

    gates_dir = run_dir / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)

    # Toolchain preflight (rc=2 → run.py stops cleanly with a clear message).
    cov_err = check_pytest_cov(source_dir)
    if cov_err:
        print(f"error: toolchain: {cov_err}", file=sys.stderr)
        return 2

    tests_result, coverage_result = gate_tests_and_coverage(
        source_dir=source_dir,
        lib_name=args.lib_name,
        line_threshold=args.line_threshold,
        branch_threshold=args.branch_threshold,
        run_dir=run_dir,
    )
    results: dict[str, dict[str, Any]] = {
        "tests": tests_result,
        "mypy": gate_mypy(source_dir),
        "ruff_check": gate_ruff_check(source_dir),
        "ruff_format": gate_ruff_format(source_dir),
        "coverage": coverage_result,
    }

    for name, result in results.items():
        (gates_dir / f"{name}.json").write_text(json.dumps(result, indent=2))

    all_passed = all(r["passed"] for r in results.values())
    summary = {
        "all_passed": all_passed,
        "gates": {name: r["passed"] for name, r in results.items()},
        "line_coverage": coverage_result["line_coverage"],
        "branch_coverage": coverage_result["branch_coverage"],
        "line_threshold": args.line_threshold,
        "branch_threshold": args.branch_threshold,
    }
    (gates_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    passed_count = sum(1 for r in results.values() if r["passed"])
    print(f"gates: {'PASS' if all_passed else 'FAIL'} ({passed_count}/{len(results)})")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
