# python-lib-dev — Task Spec

## 목적

하나의 파이썬 라이브러리(PyPI 공개 수준 품질)에 대해 **신규 개발** 또는 **유지보수(evolve)** 를 기획→설계→테스트→구현→리뷰→문서화 파이프라인으로 처리한다.

한 번의 실행(run) = 하나의 라이브러리(new 모드) 또는 하나의 변경 요구(evolve 모드).

---

## 모드

- `new`: 빈 workspace에서 라이브러리를 처음부터 생성한다.
- `evolve`: 기존 라이브러리(`target_repo_path`)에 변경 요구 1건을 적용한다. 대규모 리팩터링은 여러 evolve run으로 쪼갠다.

---

## 고정 도구 스택 (매번 묻지 않음)

| 항목 | 값 |
|---|---|
| 패키지 매니저 | `uv` (모든 실행은 `uv run`) |
| 빌드 백엔드 | `hatchling` |
| 테스트 | `pytest` (필요시 `hypothesis` 추가 허용) |
| 린트/포맷 | `ruff` (통합 — `black` 불사용) |
| 타입 체커 | `mypy --strict` |
| Python 최소 | 기본 **3.10** (interview에서 override 가능) |
| Docstring | Google 스타일 |
| 레이아웃 | `src/<pkg>/` + 최상위 `tests/` (단일 패키지) |
| CI | GitHub Actions, Python 3.10/3.11/3.12/3.13 매트릭스 |
| 커밋 | Conventional Commits |

---

## 하드 게이트 (협상 불가, 스킵 금지)

Orchestrator(`scripts/gates.py`)가 s5 직전에 직접 실행하고 `{run_dir}/gates/*.json` 에 기록한다. s5 LLM은 이 파일들을 **읽기만** 한다 (0-2 clean separation).

- 모든 테스트 통과 (`uv run pytest`) → `gates/tests.json.passed == true`
- `uv run mypy --strict` 통과 → `gates/mypy.json.passed == true`
- `uv run ruff check` + `uv run ruff format --check` 통과 → `gates/ruff_check.json.passed && gates/ruff_format.json.passed`
- 리뷰 `verdict.yaml.issues` 중 `severity == "blocker"` 0건 (LLM 판단)

---

## 조정 가능 임계치 (`config.yaml`; interview에서만 override)

- line coverage ≥ 0.90
- branch coverage ≥ 0.80
- major 이슈 수: `new` 모드 0 / `evolve` 모드 ≤ 2 (evolve는 명시적 수용 시)

사용자가 interview에서 완화 요청을 명시하지 않으면 기본값 적용. 기본값 묻지 않음.

---

## 사용자 게이트

| # | 게이트 | 모드 | 유형 |
|---|---|---|---|
| 0 | survey 승인 | evolve 전용 | 블로킹 |
| A | plan 승인 | 공통 | 블로킹 |
| B | design 승인 (+ evolve: breaking change 명시 승인) | 공통 | 블로킹 |
| — | CRITICAL 판정 에스컬레이션 | 공통 | 조건부 |
| — | cap 도달 에스컬레이션 | 공통 | 조건부 |
| — | 정체(stagnation) 감지 에스컬레이션 | 공통 | 조건부 |
| Z | delivery.md 인도 리뷰 | 공통 | 종료 상태 |

블로킹 게이트: `new` 2개 (A, B) / `evolve` 3개 (0, A, B).

---

## 작업 공간

- **new**: `outputs/<run-id>/workspace/` 에서 `uv init --lib <pkg>` 후 모든 작업 수행. 최종 목적지는 하네스가 결정하지 않는다. 사용자가 `delivery.md` 보고 수동 이동.
- **evolve**: `target_repo_path`에서 직접 작업하되 시작 시점에 **`git checkout -b <branch_name>`** 으로 브랜치 격리. `branch_name` 은 interview에서 확정되며 기본값은 `harness/<run-id>`, 사용자가 override 가능(`feat/...` 등). `main`/`master`에 머지하지 않음. `outputs/<run-id>/` 에는 작업 메타(plan, design, review, verdict, changes.patch, delivery.md)만.

Preflight가 두 모드 공통으로 `uv`/`git` 설치, evolve 모드는 `target_repo_path`의 git repo 여부와 dirty tree를 검증한다.

---

## 루프백 규칙

| verdict | 액션 | 자동/수동 |
|---|---|---|
| `PASS` | 문서화(s7)로 진행 | 자동 |
| `MINOR` | 구현(s4)으로 루프백 | 자동 (cap 내) |
| `MAJOR` | 설계(s2)로 루프백 | 자동 (cap 내) |
| `CRITICAL` | 사용자 에스컬레이션; 기획부터 재검토 | **수동 게이트** |

기획(s1)으로의 자동 루프백은 없다.

## 루프 cap (`config.yaml`)

| 루프 | cap |
|---|---|
| 리뷰→구현 (MINOR) | 3 |
| 리뷰→설계 (MAJOR) | 2 |
| 전체 stage 누적 | 15 |

구현(s4) 내부 재시도(테스트 실패·import 오류 등 기술적 재시도)는 **하네스가 카운트하지 않는다**. s4 헤드리스가 스스로 제한(약 5회 권장)하고, 더 못 풀겠으면 `impl-notes.md` 에 "Blocked — needs review"로 기록해 s5 로 넘긴다.

어느 하나라도 도달 시 `outputs/<run-id>/escalation.md` 생성 후 정지.

## 정체 감지

연속된 `verdict.yaml` 3개에서 `(file, severity, description 앞부분)` 조합의 **Jaccard 교집합/합집합 비율이 50% 이상이면** 에스컬레이션. 같은 곳에서 같은 수준 문제가 반복되면 에이전트 혼자서는 못 푸는 것으로 본다. window=3인 이유: MINOR 루프 cap(=3) 안에서 한 번 겹쳤다고 즉시 튀지 않게 하려는 것.

---

## 문서화 (s7)

- **new**: `workspace/README.md`, `workspace/docs/`, `workspace/CHANGELOG.md` 신규 생성.
- **evolve**: `target_repo_path`의 기존 문서를 **덮어쓰지 않고**, 추가/수정(diff)만 허용. breaking change 있으면 `MIGRATION.md` 신규 생성.

---

## DELIVERY (종료 상태)

`outputs/<run-id>/delivery.md` 생성 후 하네스 정지. 내용:
- 산출물 요약
- 게이트 통과 기록, 루프 반복 횟수
- 최종 coverage / mypy / ruff 결과
- 알려진 한계
- 사용자가 할 다음 작업 (new: 목적지로 이동 / evolve: PR 또는 merge)
