---
name: deep-interview-python-lib
description: python-lib-dev 하네스 실행 전, 메인 세션이 사용자와 대화해 작업을 이해하고 interview 산출물(spec.md, mode.json)을 생성하는 단계. orchestrate-python-lib 가 이 skill을 호출한다. 단독 호출도 가능하나 보통은 orchestrator가 관리.
---

# deep-interview-python-lib

python-lib-dev 하네스의 **첫 단계**. 메인 세션이 사용자와 대화해 이번 run의 변수를 확정하고 `interview/spec.md`, `interview/mode.json`을 기록한다.

## 0. HARNESS_ROOT 구하기

이 문서의 `{{HARNESS_ROOT}}` 는 placeholder. 메인 세션이 아직 값을 모른다면:

```bash
HARNESS_ROOT=$(dirname $(dirname $(dirname $(realpath ~/.claude/skills/deep-interview-python-lib/SKILL.md))))
```

`orchestrate-python-lib` 에서 이미 resolve 했다면 그 값을 그대로 사용.

## 0b. 이 skill을 호출하기 전 준비

- 아직 `RUN_DIR` 이 없다면, interview 종료 후 `{{HARNESS_ROOT}}/scripts/init_run.py`를 실행해 생성한다.
- 이미 생성된 run에 대해 interview를 다시 하고 싶다면, 기존 `interview/` 디렉토리를 지우고 이 skill을 다시 실행한다.

## 1. 먼저 읽을 것

다음 파일을 **반드시 먼저 읽는다**. 이미 고정된 규약을 사용자에게 다시 묻지 않기 위해.

1. `{{HARNESS_ROOT}}/docs/task-spec.md` — 하네스의 고정 스코프와 도구 스택
2. `{{HARNESS_ROOT}}/docs/tacit-knowledge.md` — 실행자가 말 안 해도 아는 것
3. `{{HARNESS_ROOT}}/docs/interview-guide.md` — 매 실행마다 물어야 할 변수 목록

## 2. 물어야 할 변수 (interview-guide 요약)

**필수:**

1. **mode**: `new` | `evolve`
2. (evolve 전용) **target_repo_path**: 기존 라이브러리의 로컬 절대 경로
3. **라이브러리 이름 / PyPI 슬러그**: `my_lib` / `my-lib`. evolve는 기존 값 재확인만.
4. **한 줄 목적 설명**
5. **주요 사용 시나리오 2~3개**
6. (evolve 전용) **이번 변경 요구**: 기능 추가 1건, 버그 수정 1건, 또는 밀접 연관 묶음. 대규모면 여러 run으로 쪼개도록 안내.
7. (evolve 전용) **작업 브랜치명**: 반드시 물어본다. 기본값 `harness/<run-id>` 를 제시하고 사용자가 OK 하면 그대로, 커스텀 이름을 주면 그 값을 확정.
   - mode.json 저장 규칙: **기본값 선택 시 `branch_name: null`** (preflight 가 실제 run-id 로 채움). 커스텀 이름이면 그 문자열을 저장.
   - `<run-id>` 는 placeholder 이므로 `"harness/<run-id>"` 를 literal 로 저장하지 말 것.

**선택 (사용자가 먼저 언급하면만 기록):**

7. 유사 라이브러리 + 차별점
8. Python 최소 버전 override (기본 3.10)
9. 임계치 override (coverage, major 이슈 허용)

## 3. 진행 규칙

- 사용자가 망설이면 후보 2~3개를 제시해 고르게 한다. "뭐든 괜찮다"는 답을 그대로 받지 않는다.
- 답변이 이해 안 되면 즉시 되묻는다.
- 답변 간 모순이 있으면 두 답변을 모두 보여주고 어느 쪽인지 물어본다.
- 고정 규약(uv, pytest, mypy --strict, ruff, src 레이아웃 등)은 **묻지 않는다**. 사용자가 "uv 말고 poetry 쓰자" 같은 걸 요구하면 이 하네스 스코프를 벗어난다고 설명하고 별도 하네스가 필요함을 안내.
- 마지막에 "지금까지 요약을 보시고, 추가/수정이 없으면 OK로 답해주세요"로 명시적 확정을 받는다.

## 4. 산출물 작성

### `$RUN_DIR/interview/spec.md`

자연어 요약. 다음을 포함:
- 모드와 target_repo_path (evolve)
- 라이브러리 이름 / 슬러그
- 한 줄 목적
- 사용 시나리오 2~3개
- (evolve) 변경 요구 요약
- (evolve) 작업 브랜치명
- (선택 변수) 사용자가 답한 것만
- Python 최소 버전

### `$RUN_DIR/interview/mode.json`

```json
{
  "mode": "new",
  "target_repo_path": null,
  "lib_name": "my_lib",
  "pypi_slug": "my-lib",
  "python_min": "3.10",
  "branch_name": null,
  "overrides": {
    "line_coverage": null,
    "branch_coverage": null,
    "max_major_issues_new": null,
    "max_major_issues_evolve": null
  }
}
```

- `branch_name`: evolve 모드에서 사용자가 커스텀 이름을 줬을 때만 그 문자열. **기본값(`harness/<run-id>`) 선택 시 `null`** — preflight 가 채워준다. new 모드도 `null`.

- `null` = 기본값 사용
- `target_repo_path` 는 evolve 모드에서만 절대 경로 문자열

## 5. 완료 후

메인 세션은 `orchestrate-python-lib` 의 다음 단계로 돌아간다:

```bash
python {{HARNESS_ROOT}}/scripts/run.py --run-id <run-id>
```

run-id는 `init_run.py` 실행 시 stdout으로 출력된 경로의 마지막 부분(= 디렉토리 basename).
