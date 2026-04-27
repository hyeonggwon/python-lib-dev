# python-lib-dev — Stages

## Orchestration 모드 — A (선형/단순)

근거 (harness-builder 단계 4 체크리스트):

1. **조건부 분기**: `s6` 의 verdict 분기(PASS/MINOR/MAJOR/CRITICAL/cap) 하나뿐 — 단일 분기점은 모드 B 신호 아님.
2. **병렬 단계**: 없음.
3. **동적 단계 수**: 새/evolve 모드에 따라 `s0` 유무 차이 외에는 정적.
4. **루프백**: `s6 → s4` (MINOR), `s6 → s2` (MAJOR) 가 있으나, 0-4 의 verdict 루프백은 단일 stage 내부 재시도이므로 모드 B 신호 아님.

따라서 단일 python script (`scripts/run.py`)가 전체 파이프라인을 조율한다.

전체 파이프라인은 `pre` → `di` → (`s0`) → `s1` → `s2` → `s3` → `s4` → `s5` → `s6` → (루프백 or 진행) → `s7` → `s8` 순서.
`s0`는 evolve 모드 전용. `s6`의 판정에 따라 `s2`/`s4`로 자동 루프백하거나 에스컬레이션 게이트를 연다.

---

## 단계 정의

| # | 단계 | 모드 | 실행자 | 입력 | 산출물 | 게이트 |
|---|---|---|---|---|---|---|
| **pre** | preflight + init_run | 공통 | main session | 환경 | `outputs/<run-id>/`, `state.json` | — |
| **di** | deep-interview | 공통 | **main session** | 사용자 대화 | `interview/spec.md`, `interview/mode.json` | — |
| **s0** | 코드베이스 조사 | evolve | headless (read-only) | `interview/spec.md`, `target_repo_path` | `s0/survey.md` | **게이트 0** |
| **s1** | 기획 | 공통 | headless | spec + (evolve: survey.md) | `s1/plan.md` | **게이트 A** |
| **s2** | 설계 | 공통 | headless | spec + plan + (evolve: survey) | `s2/design.md`, `s2/api_stubs.py` | **게이트 B** |
| **s3** | 테스트 작성 | 공통 | headless | design + api_stubs + (evolve: 기존 tests/) | new: `workspace/tests/`<br>evolve: `s3/tests-new/` + `s3/test-manifest.md` | — |
| **s4** | 구현 | 공통 | headless | design + api_stubs + tests | new: `workspace/src/<pkg>/`<br>evolve: 실제 수정 + `s4/changes.patch`, `s4/impl-notes.md` | 내부 재시도 cap 5 |
| **gates** | 기계 검증 | 공통 | **orchestrator (`gates.py`)** | workspace/ 또는 target_repo | `gates/summary.json` + 각 게이트 *.json | — |
| **s5** | 리뷰 | 공통 | **독립 headless** | plan + design + api_stubs + tests + 코드 + `gates/*.json` | `s5/review.md`, `s5/verdict.yaml` | — |
| **s6** | 판정 & 루프 | 공통 | script 로직 (LLM 호출 없음) | `s5/verdict.yaml`, `state.json`, `config.yaml` | `s6/decision.json` | CRITICAL/cap/정체 시 **에스컬레이션 게이트** |
| **s7** | 문서화 | 공통 | headless | 전체 산출물 | new: `workspace/README.md`, `workspace/docs/`, `workspace/CHANGELOG.md`<br>evolve: README/CHANGELOG 갱신 + (breaking 시) `MIGRATION.md` | — |
| **s8** | 인도 리뷰 | 공통 | script (템플릿 채우기) | state + 모든 산출물 | `delivery.md` | **종료 상태** |

**evolve 모드**: s3의 `tests-new/`는 s4에서 `target_repo_path/tests/`에 통합. 기존 테스트는 유지.

---

## `state.json` 스키마

```json
{
  "run_id": "2026-04-23T15-22-04",
  "harness": "python-lib-dev",
  "created_at": "2026-04-23T15:22:04",
  "mode": "new",
  "current_stage": "s2",
  "target_repo_path": null,
  "lib_name": "my_lib",
  "pypi_slug": "my-lib",
  "python_min": "3.10",
  "overrides": {
    "line_coverage": null,
    "branch_coverage": null,
    "max_major_issues_new": null,
    "max_major_issues_evolve": null
  },
  "counters": {
    "minor_loop": 0,
    "major_loop": 0,
    "total_stages": 0
  },
  "verdict_history": [
    {
      "stage_run_idx": 3,
      "verdict": "MINOR",
      "issues_key": ["src/foo.py:blocker", "tests/test_bar.py:major"]
    }
  ],
  "gate_decisions": {
    "gate0": null,
    "gateA": null,
    "gateB": null
  },
  "preflight_done": false,
  "branch_name": null,
  "escalation_triggers": []
}
```

- `gate_decisions[gateX]`: `null` = pending, `"approved"` = 통과, `"rewrite"` = 재작업(해당 단계 재실행), `"approved_with_breaking"` = 게이트 B evolve 전용.
- `verdict_history`는 s5 종료 시마다 append. 정체 감지에 사용.
- `branch_name`은 evolve 모드에서 interview가 확정한 브랜치명을 저장. 사용자가 커스텀 이름(`feat/...` 등) 을 주면 그 문자열을 저장. **기본값(`harness/<run-id>`) 으로 OK 하면 `null`** — `run.py` preflight 가 `f"harness/{run_id}"` 로 채운다. new 모드는 `null`.
- `escalation_triggers`는 escalate() 마다 append 되는 리스트. 한 run 에서 여러 종류의 에스컬레이션을 거쳤다면 모두 보존되어 0-5 cross-run 분석에 반영된다. terminal entry(`outputs/.index.jsonl`) 에는 리스트와 함께 legacy 단수 필드(`escalation_trigger` = 마지막 항목) 도 호환을 위해 같이 기록된다.

---

## `s5/verdict.yaml` 스키마 (고정, 자유 서술 금지)

s5 LLM이 **판단 필드만** 작성한다. 기계 게이트 결과(tests/mypy/ruff/coverage pass 여부 + 커버리지 %)는 orchestrator가 `{run_dir}/gates/*.json` 에 authoritative로 기록하며 verdict에 포함하지 않는다 (0-2 clean separation).

```yaml
verdict: PASS | MINOR | MAJOR | CRITICAL
rationale: "한 문장 이유"
issues:
  - severity: blocker   # blocker | major | minor
    stage_to_loop: implement   # implement | design | null
    file: "src/my_lib/core.py"
    description: "공개 API `compute()`가 음수 입력 처리 안 됨"
loop_target: implement  # implement | design | null (PASS/CRITICAL은 null)
```

- `verdict == PASS`: s7로 진행. 단, `gates/summary.json.all_passed == false` 이면 자동으로 `llm_pass_despite_failing_gates` 에스컬레이션 (LLM이 게이트 결과를 무시한 신호).
- `verdict == MINOR`: `loop_target == "implement"`, s4로 루프백.
- `verdict == MAJOR`: `loop_target == "design"`, s2로 루프백.
- `verdict == CRITICAL`: `loop_target == null`, 에스컬레이션 게이트로.

## `gates/*.json` (orchestrator가 작성)

`{run_dir}/gates/` 아래 파일들은 `scripts/gates.py` 가 s5 직전에 생성한다. LLM은 읽기만 한다.

- `summary.json` — `{all_passed, gates: {tests, mypy, ruff_check, ruff_format, coverage}, line_coverage, branch_coverage, line_threshold, branch_threshold}`
- `tests.json`, `mypy.json`, `ruff_check.json`, `ruff_format.json`, `coverage.json` — 각 게이트의 `{passed, rc, output_tail}`. `coverage.json` 에는 `line_coverage`, `branch_coverage`, `line_threshold`, `branch_threshold`, `thresholds_met` 추가. **커버리지 임계치 비교는 orchestrator가 한다** — `coverage.passed` 는 `tests_pass && thresholds_met` 로 이미 반영되어 있으므로 s5는 다시 비교하지 않는다.

## `effective_thresholds.json` (orchestrator가 작성)

`{run_dir}/effective_thresholds.json` 은 `run.py:stage_s5_review` 가 s5 직전에 config.yaml defaults + mode.json.overrides 를 머지해 쓰는 **해석된 정책 스냅샷**. s5 LLM이 override를 재계산하지 않도록 하기 위한 파일.

- 필드: `mode`, `line_coverage`, `branch_coverage`, `max_major_issues_new`, `max_major_issues_evolve`, `max_major_issues_applicable`.
- `max_major_issues_applicable` 은 현재 run의 mode 에 맞는 값(new면 `_new`, evolve면 `_evolve`).
- 오버라이드 키는 `config.yaml` 키와 **이름이 동일**해야 한다 (`line_coverage`, `branch_coverage`, `max_major_issues_new`, `max_major_issues_evolve`). 이름 불일치는 무음 실패를 유발하므로 `init_run.py` / interview skill / docs 간 동기화 필수.

---

## 게이트 파일 규약

각 블로킹 게이트는 script가 **요청 파일**을 생성한 뒤 정지하고, 메인 세션이 사용자와 함께 **decision 파일**을 작성한 뒤 `--resume` 한다.

### 요청 파일: `outputs/<run-id>/<gate>.request.md`

```markdown
# Gate: <gate name>

## Context
<단계 요약, 참조 파일 경로>

## Expected decision

decision 파일(`<gate>.decision.md`)에 다음 형식으로 작성:

    decision: approved
    # 또는
    decision: rewrite
    feedback: |
      <수정 요구사항>
    # 또는 (게이트 B evolve 전용)
    decision: approved_with_breaking
    breaking_notes: |
      <어떤 API가 깨지는지, 마이그레이션 전략>
```

### Decision 파일: `outputs/<run-id>/<gate>.decision.md`

단순 YAML 프론트매터 없음. 위 형식 그대로.

---

## 에스컬레이션 파일 규약

### `outputs/<run-id>/escalation.md`

조건:
- `verdict.yaml.verdict == CRITICAL`
- counters 중 하나가 cap 도달
- 정체 감지 (연속 verdict_history 3개의 `issues_key` Jaccard 교집합 비율 ≥ 0.5)

script는 이 파일 생성 후 종료 코드 2로 정지.

내용:
```markdown
# Escalation

## Trigger
<CRITICAL | cap_exceeded | stagnation>

## State snapshot
<current_stage, counters, last 2 verdicts>

## Expected user decision

`escalation.decision.md`에 작성:

    action: abort             # 런 폐기
    # 또는
    action: resume_from_plan  # 기획부터 재검토
    feedback: |
      <무엇이 잘못됐는지>
    # 또는
    action: resume_from_design
    feedback: |
      <...>
    # 또는
    action: force_continue    # cap 리셋 후 현재 단계 재시도
    reset_counters: [minor_loop, major_loop]
```

`action: resume_from_plan` 은 실질적 "기획 루프백"이며, 사용자 결정 없이 자동 발동되지 않는다.

### 트리거별 권장 액션

- `cap_minor_loop` / `cap_major_loop`: `force_continue` 로 해당 카운터 리셋. 같은 패턴이 또 안 풀리면 `resume_from_design` 으로 격상.
- `cap_total_stages`: PASS verdict 에서는 자동 면제(s7/s8 만 남았으면 그대로 종료까지 진행). 그 외 verdict 에서만 트리거된다. `force_continue` 로 `total_stages` 리셋 가능하나, 이 cap 까지 왔다는 건 진행이 막혔다는 뜻. 대개 `resume_from_plan` / `resume_from_design` 가 맞음.
- `stagnation`: **`force_continue` 비추천**. 정체는 verdict_history 의 다수 항목 overlap 으로 감지되므로 한 번 pop 한다고 풀리지 않고 다음 s6 에서 곧장 재escalate 된다. `resume_from_design` 또는 `resume_from_plan` 로 새로운 출발점에서 verdict_history 를 비우는 것이 정답.
- `critical_verdict` / `llm_pass_despite_failing_gates`: `force_continue` 부적절. `resume_from_design` 또는 `abort`.

---

## 디렉토리 레이아웃 (run 1회 기준)

```
outputs/<run-id>/
  state.json
  interview/
    spec.md
    mode.json
  s0/                     # evolve 전용
    survey.md
  gate0.request.md        # evolve 전용
  gate0.decision.md       # 사용자 작성
  s1/
    plan.md
  gateA.request.md
  gateA.decision.md
  s2/
    design.md
    api_stubs.py
  gateB.request.md
  gateB.decision.md
  s3/
    test-manifest.md
    tests-new/            # evolve만; new는 workspace/tests/에 직접
  s4/
    impl-notes.md
    changes.patch         # evolve만
  gates/                  # orchestrator가 s5 직전에 작성 (0-2)
    summary.json
    tests.json
    mypy.json
    ruff_check.json
    ruff_format.json
    coverage.json
  s5/
    review.md
    verdict.yaml
  s6/
    decision.json
  s7/                     # new는 workspace/ 직접; evolve는 README diff만 기록
    docs-diff.patch       # evolve만
  escalation.md           # 필요 시
  escalation.decision.md  # 사용자 작성
  delivery.md

# new 모드만
outputs/<run-id>/workspace/
  pyproject.toml
  src/<pkg>/
  tests/
  README.md
  docs/
  CHANGELOG.md
```

---

## 실행 흐름 요약

1. 메인 세션이 deep-interview → `interview/mode.json` 생성.
2. 메인 세션이 `python {{HARNESS_ROOT}}/scripts/run.py --run-id <id>` 실행.
3. `run.py`가 preflight → s0 (evolve만) → 게이트 0 요청 파일 생성 후 정지.
4. 메인 세션이 사용자와 decision 파일 작성 후 `run.py --resume <id>`.
5. 자동으로 s1 → 게이트 A → s2 → 게이트 B → s3 → s4 → s5 → s6 판정 → (루프 or 진행) → s7 → s8.
6. delivery.md 생성 후 종료.
7. 중간 어디서든 에스컬레이션 발생 시 `escalation.md` 생성 후 정지, 메인 세션 사용자 결정 후 `--resume`.
