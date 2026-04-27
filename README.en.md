# python-lib-dev

[한국어](README.md) | **English**

<div align="center">
<img width="480" height="360" alt="python-lib-dev-logo" src="https://github.com/user-attachments/assets/ed2a2885-38ea-443d-93e4-c15b3e062b1f" />
</div>
</br>

A Claude Code harness that builds or updates a single Python library through a planning → design → testing → implementation → review → documentation pipeline.

## Initialize

```bash
uv pip install pyyaml    # or pip install pyyaml, one-time only
./install.sh             # creates symlinks under ~/.claude/skills/, one-time only
```

To uninstall, run `./uninstall.sh` (removes symlinks only).

## Quick Start

In a Claude Code session</br>
-> Request something like "Build a Python library" / "Add feature X to this library", or trigger the `orchestrate-python-lib` skill</br>
-> The main session drives interview → run initialization → pipeline execution end-to-end.</br>
-> If it stops at a blocking gate, fill out `<gate>.decision.md` together and resume.</br>

## How It Works

1. **Interview** — The main session uses `deep-interview-python-lib` to talk with the user and finalize `spec.md` + `mode.json`.
2. **Init** — `scripts/init_run.py` creates `outputs/<run-id>/`.
3. **Orchestrate** — `scripts/run.py --run-id <id>` calls each stage (s0..s8) headlessly.
4. **Gates** — When a blocking gate (evolve's survey, plan, design) is hit, it writes `<gate>.request.md` and stops. After writing the decision with the user, resume with the same command.
5. **Loop** — Based on the s5 review verdict, MINOR → loop back to s4, MAJOR → loop back to s2 automatically. On CRITICAL / cap exceeded / stall, it writes `escalation.md` and stops.
6. **Deliver** — Once all stages pass, it generates `delivery.md` and exits.

Stage definitions, state/verdict schemas, and gate formats are in `docs/stages.md`.

## Architecture

```
python-lib-dev/
  install.sh / uninstall.sh   # symlink install/uninstall + core.hooksPath setup
  scripts/
    init_run.py               # initializes run directory + state.json
    preflight.py              # validates uv / git / claude / target_repo_path
    run.py                    # main orchestrator (gates, loops, escalation)
    gates.py                  # mechanical gates (pytest / mypy / ruff / coverage)
    validate_harness.py       # drift checks: prompts ↔ STAGE_TOOLS / placeholders / feedback paths
    config.yaml               # caps, thresholds, stall detection params
    prompts/                  # source prompts for s0..s7
  skills/                     # install.sh symlinks these into ~/.claude/skills/
    orchestrate-python-lib/
    deep-interview-python-lib/
    python-library-conventions/
  docs/                       # task-spec, stages, interview-guide, tacit-knowledge, discussion-log
  .githooks/pre-commit        # runs validate_harness.py before each commit
  outputs/                    # run artifacts (.gitignore)
```

- This repo itself is not a library. It's a thin orchestration layer using only stdlib + PyYAML.

## Generated Files

Per run under `outputs/<run-id>/`:

```
state.json
interview/{spec.md, mode.json}
s0/survey.md                  # evolve only
s1/plan.md
s2/{design.md, api_stubs.py}
s3/...                        # new writes directly to workspace/tests/, evolve to tests-new/
s4/{impl-notes.md, changes.patch}   # changes.patch is evolve only
gates/{summary.json, tests.json, mypy.json, ruff_check.json, ruff_format.json, coverage.json}
s5/{review.md, verdict.yaml}
s6/decision.json
gate0.request.md / gate0.decision.md    # evolve only
gateA.request.md / gateA.decision.md
gateB.request.md / gateB.decision.md
escalation.md / escalation.decision.md  # if needed
delivery.md

workspace/                    # new mode artifacts (the actual library body)
  pyproject.toml, src/<pkg>/, tests/, README.md, docs/, CHANGELOG.md
```

## Development

When modifying this repo, **read `CLAUDE.md` first.** Summary:

- `skills/<name>/SKILL.md` is the source of truth; paths under `~/.claude/skills/` are symlinks. Do not edit them directly.
- Do not create `pyproject.toml` / `src/` / `tests/` at the root — this repo is not a library.
- Do not commit `outputs/`.

See `CLAUDE.md` §5 for the mapping of what-to-edit-where.

## Requirements

- `claude` CLI (Claude Code)
- `uv`
- `git`
- `Python 3.10+`
- `PyYAML` (used by `run.py` to parse `config.yaml`. Install via `uv pip install pyyaml` or `pip install pyyaml`)

Fixed stack for generated libraries: `uv` · `hatchling` · `pytest` · `ruff` · `mypy --strict` · `src/` layout · Google docstrings · Conventional Commits · GitHub Actions on 3.10/3.11/3.12/3.13.
