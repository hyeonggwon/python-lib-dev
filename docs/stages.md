# python-lib-dev — Stages

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
| **s5** | 리뷰 | 공통 | **독립 headless** | plan + design + api_stubs + tests + 코드 + 테스트 실행 결과 | `s5/review.md`, `s5/verdict.yaml`, `s5/test-run.log` | — |
| **s6** | 판정 & 루프 | 공통 | script 로직 (LLM 호출 없음) | `s5/verdict.yaml`, `state.json`, `config.yaml` | `s6/decision.json` | CRITICAL/cap/정체 시 **에스컬레이션 게이트** |
| **s7** | 문서화 | 공통 | headless | 전체 산출물 | new: `workspace/README.md`, `workspace/docs/`, `workspace/CHANGELOG.md`<br>evolve: README/CHANGELOG 갱신 + (breaking 시) `MIGRATION.md` | — |
| **s8** | 인도 리뷰 | 공통 | script (템플릿 채우기) | state + 모든 산출물 | `DELIVERY.md` | **종료 상태** |

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
    "max_major_new": null,
    "max_major_evolve": null
  },
  "counters": {
    "impl_retry": 0,
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
  "branch_name": null
}
```

- `gate_decisions[gateX]`: `null` = pending, `"approved"` = 통과, `"rewrite"` = 재작업(해당 단계 재실행), `"approved_with_breaking"` = 게이트 B evolve 전용.
- `verdict_history`는 s5 종료 시마다 append. 정체 감지에 사용.
- `branch_name`은 evolve 모드에서 interview가 확정한 브랜치명을 저장. 기본값은 `harness/<run-id>` 이며 사용자가 override 가능. new 모드는 `null`.

---

## `s5/verdict.yaml` 스키마 (고정, 자유 서술 금지)

```yaml
verdict: PASS | MINOR | MAJOR | CRITICAL
rationale: "한 문장 이유"
hard_gates:
  tests_pass: true
  mypy_strict: true
  ruff: true
  blockers: 0
thresholds:
  line_coverage: 0.92
  branch_coverage: 0.81
issues:
  - severity: blocker   # blocker | major | minor
    stage_to_loop: implement   # implement | design | null
    file: "src/my_lib/core.py"
    description: "공개 API `compute()`가 음수 입력 처리 안 됨"
loop_target: implement  # implement | design | null (PASS/CRITICAL은 null)
```

- `verdict == PASS`: s7로 진행.
- `verdict == MINOR`: `loop_target == "implement"`, s4로 루프백.
- `verdict == MAJOR`: `loop_target == "design"`, s2로 루프백.
- `verdict == CRITICAL`: `loop_target == null`, 에스컬레이션 게이트로.

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
- 정체 감지 (연속 verdict_history 2개의 `issues_key` 교집합 비율 ≥ 0.5)

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
  s5/
    review.md
    verdict.yaml
    test-run.log
  s6/
    decision.json
  s7/                     # new는 workspace/ 직접; evolve는 README diff만 기록
    docs-diff.patch       # evolve만
  escalation.md           # 필요 시
  escalation.decision.md  # 사용자 작성
  DELIVERY.md

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
6. DELIVERY.md 생성 후 종료.
7. 중간 어디서든 에스컬레이션 발생 시 `escalation.md` 생성 후 정지, 메인 세션 사용자 결정 후 `--resume`.
