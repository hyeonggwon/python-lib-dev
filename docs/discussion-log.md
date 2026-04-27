# 파이썬 라이브러리 개발 하네스 — 논의점 답변 종합

하네스 빌더 세션에서 확정한 결정 사항들. 논의점 1~5에 대한 답변을 하나의 참조 문서로 정리.

---

## 논의점 1 — 개발 대상의 성격 (스코프 고정)

### 주 도메인

순수 라이브러리(배포용 패키지). PyPI 공개 수준의 품질 기준.
CLI / 웹 서버 / ML 파이프라인은 이 하네스의 대상이 **아님** — 필요하면 별도 하네스로.

### 규약 고정값 (하네스 전체 고정, 매 실행 질문 없음)

- **패키지 매니저**: `uv`. 모든 실행은 `uv run` 경유. 빌드 `uv build`, 게시 `uv publish`. 빌드 백엔드는 uv 기본 `hatchling`.
- **테스트 프레임워크**: `pytest` 고정. property-based가 필요해지면 `hypothesis` 추가 허용 (기본값은 pytest만).
- **린터/포매터**: `ruff` 단일 (포맷 + 린트 통합). `black` 안 씀.
- **타입 체커**: `mypy --strict`. 공개 API는 타입이 정확해야 함.
- **Python 최소 버전**: 3.10.
- **Docstring 스타일**: Google.
- **커버리지 하드 기준**: line 90% / branch 80%.
- **CI**: GitHub Actions, `uv`로 Python 3.10 / 3.11 / 3.12 / 3.13 매트릭스.
- **커밋/PR 규약**: Conventional Commits.

### 레포 구조 (하네스가 생성하는 각 라이브러리의 레이아웃)

- **한 번의 하네스 실행 = 라이브러리 하나 생성**. 여러 라이브러리는 하네스를 여러 번 실행, 각 실행은 `outputs/<run-id>/`로 격리.
- 각 라이브러리 내부는 **단일 패키지 + `src/` 레이아웃** 강제. `src/<pkg_name>/` 아래 모듈, 최상위 `tests/`.
- 개별 라이브러리 내부 모노레포/workspaces (한 라이브러리가 여러 subpackage를 publish)는 이 하네스 범위 밖.

### Interview-guide 매 실행 변수 (필수)

- 라이브러리 이름 / PyPI 슬러그
- 한 줄 목적 설명
- 주요 사용 시나리오 2~3개
- (선택) 기존 유사 라이브러리 + 차별점

---

## 논의점 2 — 신규 개발 vs 유지보수: 옵션 B 채택

**단일 하네스, deep-interview에서 `mode` 변수로 분기.**

근거: 설계 이후(테스트/구현/리뷰/문서화)는 두 모드에서 구조가 동일하고 내용만 다름. 차이는 "기획/설계" 전반부에 집중. C안(자동 판정)은 오류 시 전체가 꼬이므로 기각. A안(분리)은 공통 skill 중복 유지보수 부담.

### 모드 변수

- `mode: new | evolve` — deep-interview에서 사용자에게 묻고 확정.
- `evolve`일 때만 필수 추가 변수: `target_repo_path` (기존 라이브러리의 로컬 경로).

### 작업 단위 정의 (evolve 모드)

- 한 번의 evolve run = "하나의 변경 요구" (기능 추가 1건, 버그 수정 1건, 또는 밀접 연관 변경 묶음).
- 대규모 리팩터링은 new로 다시 만들거나 여러 evolve run으로 쪼갠다. 하나의 run에 섞지 않음.

### 단계별 두 모드 차이 (stages.md에 명시)

| 단계 | new | evolve |
|---|---|---|
| 단계 0: 코드베이스 조사 | 없음 | **있음** — 기존 구조·API·테스트 매핑, 변경 영향 범위 식별 |
| 기획 | 범위·비목표·경쟁 분석 | 변경 요구 정리 + 현 상태 대비 delta |
| 설계 | 공개 API 신규 | 기존 위에 얹는 설계 + 최소 침습 원칙 + breaking change 판정 |
| 게이트 A | 기획 승인 | 기획 + 영향 범위 승인 |
| 게이트 B | API 승인 | API 승인 + **breaking change 명시적 승인** |
| 테스트 작성 | 신규 TDD | 신규 TDD + **기존 테스트 전부 통과 보장** |
| 구현 | 신규 작성 | 기존 수정 + 신규 추가 |
| 리뷰 | 공통 체크 | 공통 + **회귀 테스트 + API 호환성 재검증** |
| 문서화 | README·examples | README 갱신 + **CHANGELOG + (breaking 시) 마이그레이션 가이드** |

### Headless 프롬프트 처리

- Script는 단일 유지. 프롬프트 상단에 `{{MODE}}` 주입하고 "mode에 따라 이렇게 다르게 행동하라" 명시.
- 단계 0만 evolve 전용 별도 script (`stage0_investigate.py`). new 모드에서는 스킵.

### 산출물 경로

- **new**: `outputs/<run-id>/workspace/src/<pkg>/` 에 새로 생성.
- **evolve**: 실제 코드 변경은 `target_repo_path`에 직접 적용. `outputs/<run-id>/`엔 작업 메타(plan·design·review·verdict·changes.patch)만.

---

## 논의점 3 — 각 단계의 산출물

빌더 초안 대부분 동의. 네 질문에 대한 답 + 초안 보완 2건.

### Q1. 리뷰어는 누구여야 하나?

**독립 headless**로 시작. 핵심 제약: 리뷰어가 구현자의 context / reasoning을 **보지 않음**. 리뷰어 입력은 오직:

- `plan.md`
- `design.md`
- `api-stubs.py`
- `tests/`
- 실제 코드
- 테스트 실행 결과

Sub-agent는 초기엔 만들지 말 것. 실제 돌려보며 리뷰어가 특정 차원을 반복적으로 놓친다는 관찰이 있을 때만 `api-critic`, `test-skeptic`, `doc-reviewer` 등을 리뷰어 headless 내부에서 병렬 호출하는 구조로 확장.

### Q2. 완성도 판정 기준

**하드 게이트 (고정, 협상 불가)**:

- 모든 테스트 통과
- `mypy --strict` 통과
- `ruff check` + `ruff format --check` 통과
- 리뷰 리포트 `blocker` 심각도 이슈 0개

**설정 가능 (기본값 고정, interview override 허용)**:

- line coverage ≥ 90%
- branch coverage ≥ 80%
- 리뷰 `major` 이슈: new 모드 0개 / evolve 모드 ≤ 2개 (명시적 수용 시)

Interview는 기본적으로 이것들을 묻지 않음. 사용자가 "이번 런에선 완화"를 먼저 말할 때만 override.

### Q3. 루프백 범위

**설계까지는 자동, 기획 루프백은 반드시 사용자 게이트.** 리뷰 판정은 `verdict.md`에 4단계로 고정:

| 판정 | 액션 | 자동/수동 |
|---|---|---|
| `PASS` | 문서화로 진행 | 자동 |
| `MINOR` | 구현으로 루프백 | 자동 (cap 내) |
| `MAJOR` | 설계로 루프백 | 자동 (cap 내) |
| `CRITICAL` | 사용자 에스컬레이션, 기획부터 재검토 | **수동 게이트** |

근거: "요구사항 자체가 틀렸다"는 판단은 자율로 돌릴 영역이 아님. 기획 루프백 자동화 시 에이전트가 자기 실수를 감추려 요구사항을 재해석하는 실패 모드 가능.

### Q4. 최대 반복 횟수

| 루프 | 캡 |
|---|---|
| 구현 내부 재시도 | 5회 |
| 리뷰 → 구현 (MINOR) | 3회 |
| 리뷰 → 설계 (MAJOR) | 2회 |
| 전체 stage 실행 누적 | 15회 |

어느 하나라도 도달 시 `outputs/<run-id>/escalation.md` 생성 후 정지. 메인 세션이 파일 읽고 사용자와 상의 → `--resume`.

캡은 `harnesses/python-lib-dev/config.yaml`로 외부화하여 추후 튜닝 가능.

### 빌더 초안 보완 #1 — 설계 단계에 `api-stubs.py` 추가

`design.md`(산문) + `api-stubs.py`(타입 시그니처만, 구현 `...`) 둘 다 생성.

- 테스트 작성자가 stubs를 import 소스로 사용.
- `mypy --strict`를 stubs + tests에 미리 돌려 타입 계약을 구현 이전에 검증.
- 구현 단계에서 stubs 시그니처와 실제 구현 일치 여부 교차검증.

### 빌더 초안 보완 #2 — `verdict.md`는 고정 YAML 스키마

```yaml
verdict: PASS | MINOR | MAJOR | CRITICAL
rationale: <한 문장 이유>
hard_gates:
  tests_pass: true/false
  mypy_strict: true/false
  ruff: true/false
  blockers: 0
thresholds:
  line_coverage: 0.92
  branch_coverage: 0.81
issues:
  - severity: blocker|major|minor
    stage_to_loop: implement|design
    description: <...>
    file: <path>
loop_target: implement | design | null
```

자유 서술 금지. Script가 YAML 파싱해 분기 결정. 자유 서술이면 script가 리뷰어 산문에서 결론을 추론해야 하는데 이게 실패 지점.

---

## 논의점 4 — 추가 사용자 게이트

**원칙**: 게이트는 사용자 주의력을 소비하는 자원. 각 게이트는 자기 존재를 **다운스트림 손실 예방**으로 정당화해야 함.

### 후보 1 — 코드베이스 조사 결과 게이트 (evolve 전용): **추가**

이유: `survey.md`가 기존 코드베이스를 잘못 읽으면 plan → design → impl 전부 그 위에 쌓임. 리뷰 단계에서야 "처음부터 틀렸다"가 드러나고, 되돌리는 비용이 survey 검토 5분보다 훨씬 큼.

- 이름: **게이트 0 · 코드베이스 조사 승인** (evolve 전용, new 모드엔 없음)
- 확인 포맷 가볍게: "내가 알기로 누락된 중요 파일/모듈/패턴 있나?"만 체크.

### 후보 2 — 루프 중간 사용자 개입 게이트: **추가하지 않음**

이미 루프별 cap이 있고 도달 시 `escalation.md`로 넘어옴. 그 cap 전에 "3회차쯤 물어보기"는 자동화를 다시 수동으로 되돌리는 것.

대신 **정체(stagnation) 감지**를 조기 에스컬레이션 트리거로 추가:

- 연속된 `verdict.md` 2개에서 `issues[].file` + `severity` 조합이 **50% 이상 겹치면** → 에스컬레이션 파일 생성 후 정지.
- 근거: 반복해도 같은 곳에서 같은 수준 문제 = 에이전트 혼자서는 못 푼다.

### 후보 3 — 문서화 후 최종 확인: **추가, 단 "인도(delivery) 리뷰" 형태**

"승인 안 하면 진행 불가" 블로킹이 아닌 **종료 상태로서의 인도 문서**.

- 마지막 단계가 `delivery.md` 생성 후 정지.
- 내용: 산출물 요약, 게이트 통과 기록, 커버리지 / 타입 체크 결과, 알려진 한계, 사용자가 할 다음 작업 (`uv publish`, git 태그, CHANGELOG 링크 등).
- 사용자가 읽고 직접 판단 (커밋 / 게시 / 재작업 / 폐기). 하네스는 더 이상 행동하지 않음.

### 확정된 전체 게이트 목록

| # | 게이트 | 모드 | 유형 |
|---|---|---|---|
| 0 | 코드베이스 조사 승인 | evolve 전용 | 블로킹 |
| A | 기획 승인 | new / evolve | 블로킹 |
| B | API 승인 (+ evolve의 breaking change 승인) | new / evolve | 블로킹 |
| — | CRITICAL 판정 에스컬레이션 | new / evolve | 조건부 |
| — | 캡 도달 에스컬레이션 | new / evolve | 조건부 |
| — | 정체 감지 에스컬레이션 | new / evolve | 조건부 |
| Z | 인도 리뷰 (delivery.md) | new / evolve | 종료 상태 |

블로킹 게이트: new 2개 / evolve 3개. 조건부 에스컬레이션은 문제 발생 시에만 발동.

---

## 논의점 5 — 실행 환경

### 대상 디렉토리 (모드별 분리)

**new 모드 — 격리된 workspace에서 작업, 종료 후 사용자가 수동 이동**

- deep-interview에서 최종 목적지 안 물음.
- 매 실행마다 `outputs/<run-id>/workspace/` 안에서 `uv init`부터 전부 수행.
- 종료 시 `delivery.md`에 "산출물을 어디로 옮길지는 사용자 결정" 명시.
- 사용자가 수동으로 `cp -r` 또는 `git init` 후 push.

근거: 중간 abort 시 사이드 이펙트 없이 버릴 수 있음. 라이브러리 이름도 interview 시점엔 임시일 수 있어 최종 경로에 못 박지 않음.

**evolve 모드 — `target_repo_path` 직접 수정, 단 브랜치 격리 강제**

- deep-interview에서 `target_repo_path` 필수 입력.
- 시작 시점에 **`git checkout -b harness/<run-id>` 강제**.
- 완료 시 main에 머지하지 않음 — 사용자가 직접 PR 또는 머지.
- `outputs/<run-id>/`엔 작업 메타(plan, design, review, verdict, changes.patch, delivery.md)만.

**Preflight 검사 (두 모드 공통)**:

- `git` 설치 확인
- evolve: `target_repo_path`가 git repo인지, dirty tree 아닌지, 현재 브랜치 확인 후 스태시 또는 경고.

### 하네스 설치 위치

**`/home/obigo/Desktop/lib`은 하네스 위치로 부적절.** 이 경로는 개별 작업 공간 성격. 하네스는 여러 라이브러리에 재사용되는 설치물이라 프로젝트 디렉토리 내부에 두면 안 됨.

**권장 구조**:

```
~/harnesses/                          # 하네스 설치 루트
  scripts/init_run.py                 # 모든 하네스 공용
  python-lib-dev/
    docs/
    scripts/
    outputs/<run-id>/                 # workspace + 메타데이터

~/.claude/                            # user-level Claude Code 설정
  skills/
    orchestrate-python-lib/
    deep-interview-python-lib/
    python-library-conventions/
  agents/                             # 필요 시
```

**근거**:

1. Skills를 **user-level `~/.claude/skills/`**에 두면 어느 디렉토리에서 Claude Code를 켜도 `/orchestrate` 작동. "여러 라이브러리를 만든다"는 목적에 부합.
2. `~/harnesses/` 별도로 두면 나중에 다른 하네스(research-assistant, paper-summarizer 등)를 같은 규약으로 설치 가능. `init_run.py` 공용 재사용.
3. `/home/obigo/Desktop/lib`은 오히려 **evolve 모드의 `target_repo_path` 후보** 중 하나로 들어가는 게 맞음.

### 확정 전 확인 필요

1. `~/harnesses/` 생성 승인 (또는 대체 경로 제안).
2. `~/.claude/skills/`에 이미 존재하는 skill과 충돌 여부 사전 확인.
3. Preflight 로직(`git` 설치, repo 상태 체크)은 `init_run.py` 또는 orchestrating skill 진입부에 위치.

---

## 주요 결정 요약

| 항목 | 결정 |
|---|---|
| 대상 | 순수 파이썬 라이브러리 (PyPI 공개 수준) |
| 패키지 매니저 | uv |
| 테스트 / 타입 / 린트 | pytest / mypy --strict / ruff |
| Python 최소 버전 | 3.10 |
| 레이아웃 | src/ + 단일 패키지 |
| 모드 분기 | B안 (단일 하네스, deep-interview `mode` 변수) |
| 블로킹 게이트 | new 2개 (기획·API) / evolve 3개 (+조사) |
| 리뷰어 | 독립 headless, sub-agent 초기 미도입 |
| Verdict 형식 | 고정 YAML 스키마 |
| 루프 cap | 구현 5 / MINOR 3 / MAJOR 2 / 전체 15 |
| 기획 루프백 | 자동 금지, 사용자 게이트 |
| 정체 감지 | 연속 verdict의 issues 50% 이상 겹침 |
| 종료 상태 | delivery.md 인도 리뷰 |
| new 작업 공간 | `outputs/<run-id>/workspace/` 격리 |
| evolve 작업 공간 | `target_repo_path` + `harness/<run-id>` 브랜치 |
| 하네스 설치 위치 | `~/harnesses/`, skills는 `~/.claude/skills/` |

---

# Amendments (구축 중 결정 변경)

이 섹션은 **위 원본 답변 확정 이후 실제 구축 과정에서 재검토되어 바뀐 결정들**을 기록한다. 원본은 의사결정 스냅샷으로 보존하고, 현재 하네스가 실제로 따르는 규약은 이 Amendments를 우선한다.

각 항목은 `원본 → 실제 + 이유` 형식.

## A1. 하네스 설치 구조 (논의점 5 수정)

- **원본**: `~/harnesses/` 에 하네스 루트, skills는 **`~/.claude/skills/` 에 직접 파일 생성**.
- **실제**: Skill 정본은 `~/harnesses/python-lib-dev/skills/<name>/SKILL.md`. `~/.claude/skills/<name>` 은 이 정본을 가리키는 **symlink**. `install.sh` / `uninstall.sh` 가 symlink 생성/제거를 담당.
- **이유**: 하네스의 **자기완결성**. `~/.claude/skills/` 에 직접 파일을 두면 하네스 디렉토리 삭제 시 두 곳을 건드려야 하고, git 한 단위로 배포하기 어렵다. Symlink 방식이면 `git clone && ./install.sh` 한 줄로 타인 배포 가능하고, Claude Code는 `~/.claude/skills/` 규약대로 자동 로드.

## A2. `init_run.py` 위치 (논의점 5 수정)

- **원본**: `~/harnesses/scripts/init_run.py` 를 **모든 하네스가 공유**.
- **실제**: `~/harnesses/python-lib-dev/scripts/init_run.py` 로 이 하네스 **전용**.
- **이유**: A1과 동일한 자기완결성 원칙. `init_run.py` 는 40줄 안팎으로 작아 중복 비용이 거의 없고, 공용으로 두면 `python-lib-dev` 만 받아서 쓰려는 사용자가 별도 공용 파일을 추가로 챙겨야 한다. harness-builder 기본 규약과 달라졌다.

## A3. Preflight 위치 및 구현 (논의점 5 확인 사항 #3)

- **원본**: Preflight 로직은 "`init_run.py` 또는 orchestrating skill 진입부"에 둔다(확정 전 확인 필요).
- **실제**: `~/harnesses/python-lib-dev/scripts/preflight.py` 로 **독립 파일**. `run.py` 가 진입 시 subprocess 로 호출. `uv` / `git` / `claude` 설치 확인 + evolve 시 `target_repo_path` 의 git repo 여부 / dirty tree 검증.
- **이유**: 책임 분리. `init_run.py` 는 `outputs/<run-id>/` 와 `state.json` 초기화만 담당해야 단순성을 유지함. Skill 진입부에 파이썬 로직을 넣으면 메인 세션이 해석해야 하는 지시가 늘어난다.

## A4. Placeholder 치환 방식 (원본엔 없던 결정)

- **원본**: 이 결정이 다뤄지지 않음.
- **구축 중 1차 결정 (Plan Y)**: `install.sh` 가 정본의 `{{HARNESS_ROOT}}` placeholder를 실제 절대 경로로 `sed` 치환.
- **실제 (Plan Z)**: 치환 없음. 정본은 `{{HARNESS_ROOT}}` 를 **영구 유지**. 메인 세션이 skill 로드 시 `realpath` 기반 bash 한 줄로 runtime resolve. 프롬프트 파일(`scripts/prompts/*.md`) 안의 `{HARNESS_ROOT}`, `{run_dir}`, `{run_id}`, `{target_repo_path}`, `{lib_name}` 도 `run.py` 가 매 headless 호출마다 `{run_dir}/.prompts/<stage>.md` 로 치환본을 생성.
- **이유**: Install-time 치환은 파일 수정 워크플로우와 근본적으로 충돌했다. SKILL.md 를 편집하면 치환본이 수정되고, git commit 시 절대 경로가 그대로 유출된다. 또한 디렉토리 이동 시 "이전 경로 → 새 경로" 재치환 로직이 install.sh 에 필요해진다. Runtime resolve 로 전환하면 파일 수정/git 관리/디렉토리 이동이 모두 자연스럽게 해결된다. `install.sh` 는 symlink 생성/갱신만 담당하며, 옮긴 후 `./install.sh` 한 번으로 symlink가 갱신된다.

## A5. evolve 브랜치명 (논의점 5, "주요 결정 요약" 수정)

- **원본**: 시작 시점에 **`git checkout -b harness/<run-id>` 강제** (고정).
- **실제**: `harness/<run-id>` 는 **기본값**. Interview 필수 질문 #7 `작업 브랜치명` 에서 사용자에게 반드시 물어보고, 기본값으로 OK 하면 그대로, 커스텀 이름을 주면 그 값으로 확정. `mode.json.branch_name` 에 확정된 문자열을 저장하고 `run.py` 의 preflight 에서 `git checkout -b <branch_name>` 수행.
- **이유**: 고정 `harness/<run-id>` 는 timestamp 기반이라 의미 전달이 0이고 팀 컨벤션(`feat/`, `fix/` 등)과 충돌 가능. 기본값 유지로 자동 추적/정리 이점을 살리면서 override 여지를 열어두는 것이 유연성 / 추적성의 균형점.

## A6. 주요 결정 요약 표 갱신

원본 표의 일부 항목이 위 Amendments 로 무효화됨. 갱신된 최신 값:

| 항목 | 최신 결정 |
|---|---|
| 하네스 설치 위치 | 정본 `~/harnesses/python-lib-dev/`, symlink `~/.claude/skills/<name>` |
| `init_run.py` 위치 | `~/harnesses/python-lib-dev/scripts/init_run.py` (하네스 전용) |
| Preflight 위치 | `~/harnesses/python-lib-dev/scripts/preflight.py` |
| Placeholder 전략 | 정본은 placeholder 유지, runtime resolve (install-time 치환 없음) |
| evolve 브랜치명 | interview 확정값 (기본 `harness/<run-id>`, 사용자 override 가능) |

나머지 항목(대상, 도구 스택, 레이아웃, 모드 분기, 블로킹 게이트, 리뷰어, 루프 cap, 기획 루프백, 정체 감지, 종료 상태, new/evolve 작업 공간)은 원본 결정이 그대로 구현되었다. verdict 스키마는 아래 A7 참조.

## A7. 기계 게이트 실행 주체 (0-2 clean separation)

- **원본**: s5 리뷰 headless가 `uv run pytest / mypy / ruff` 를 **직접 실행**하고 결과를 `verdict.yaml.hard_gates` 와 `verdict.yaml.thresholds` 필드에 기록.
- **실제**: orchestrator (`scripts/gates.py`) 가 s5 직전에 해당 명령을 **직접 실행**하고 결과를 `{run_dir}/gates/*.json` 에 authoritative로 기록. s5 headless 는 이 파일들을 **읽기만** 하고 판단 필드만 작성 (`verdict`, `rationale`, `issues`, `loop_target`). `hard_gates`/`thresholds` 필드는 verdict 스키마에서 삭제.
- **이유**: harness-builder SKILL.md §0-2. LLM이 기계 검증 가능한 사실(테스트 통과 여부 등)을 주장하게 두면 헛소리가 그대로 파이프라인을 통과할 수 있다. Python이 authoritative로 돌리고 LLM은 그 결과를 입력으로 받아 판단만 하는 구조가 "기계/판단 권한 분리"의 가장 깨끗한 형태. 추가 safeguard로 s6 decision에 cross-check guard 삽입: `gates.all_passed == false` 인데 LLM이 `verdict == PASS` 쓰면 자동 `llm_pass_despite_failing_gates` 에스컬레이션.
- **영향받는 파일**: 신규 `scripts/gates.py`, `scripts/run.py` (stage_s5_review, stage_s6_decide, STAGE_TOOLS의 s5_review에서 `Bash(uv run *)` 제거), `scripts/prompts/s5_review.md` 전면 재작성, `docs/stages.md` verdict 스키마 및 디렉토리 레이아웃 갱신, `docs/task-spec.md` 하드 게이트 표현 갱신, `docs/tacit-knowledge.md` s5 입력 목록 갱신.

## A8. Skill 위치 — user-level symlink → project-local

- **원본 (A4 / A6)**: 정본 `<harness>/skills/<name>/SKILL.md`, symlink `~/.claude/skills/<name>`. `install.sh` / `uninstall.sh` 가 symlink 생성/제거 담당. HARNESS_ROOT resolve 는 `dirname×3 + realpath ~/.claude/skills/.../SKILL.md` (symlink target 추적).
- **실제**: 정본 = 위치 = `<harness>/.claude/skills/<name>/SKILL.md`. **project-local skill** 로 이주. `install.sh` 의 symlink 로직 제거 → `core.hooksPath = .githooks` 설정만 남음. `uninstall.sh` 삭제 (되돌릴 게 없음). HARNESS_ROOT resolve 는 `git rev-parse --show-toplevel` 한 줄.
- **이유**: user-level symlink 방식은 `~/.claude/skills/` 에 하네스 skill 메타데이터를 항상 등록하므로 **모든 Claude Code 세션**에서 토큰을 차지함. 사용자가 다른 프로젝트에서 작업할 때도 `orchestrate-python-lib`/`deep-interview-python-lib`/`python-library-conventions` description 이 컨텍스트에 로드되어 누적 비용 발생. project-local 로 두면 CWD 가 이 harness 레포(또는 그 하위)일 때만 로드된다. 트레이드오프는 호출 측이 harness 디렉토리에서 Claude Code 를 띄워야 한다는 것 — 실행 결과는 `target_repo_path` / `outputs/<run-id>/workspace/` 어디로든 가므로 운용 제약은 사실상 없음.
- **A4 와의 관계**: A4 의 placeholder 정책은 그대로 유지 — `{{HARNESS_ROOT}}` 정본은 placeholder, runtime resolve. 단지 resolve 방식이 `realpath` 기반에서 `git rev-parse` 로 바뀌었을 뿐. install-time 치환 부활 금지 원칙은 동일.
- **영향받는 파일**: `skills/` → `.claude/skills/` (`git mv`), 3 개 SKILL.md §0 의 resolve 한 줄 교체, `install.sh` 슬림화, `uninstall.sh` 삭제, `CLAUDE.md` §3 / §6 / §9 갱신, `README.md` / `README.en.md` Initialize 및 Architecture 섹션 갱신.
