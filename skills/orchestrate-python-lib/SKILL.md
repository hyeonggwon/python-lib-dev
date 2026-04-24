---
name: orchestrate-python-lib
description: 파이썬 라이브러리의 신규 개발(new) 또는 유지보수(evolve) 하네스를 실행한다. 사용자가 "파이썬 라이브러리 만들어줘", "이 파이썬 라이브러리 고쳐줘", "기존 라이브러리에 기능 추가", "python-lib-dev 하네스 실행" 등을 요청할 때 트리거. 기획→설계→테스트→구현→리뷰→문서화 파이프라인으로 PyPI 공개 수준 품질의 산출물을 만든다.
---

# orchestrate-python-lib

파이썬 라이브러리 개발/유지보수용 하네스의 진입점. 이 skill이 로드되면 **메인 세션이 사용자와 함께** 아래 흐름을 끝까지 수행한다.

## 0. 먼저 — HARNESS_ROOT 구하기

이 skill 및 연관 문서에 등장하는 모든 `{{HARNESS_ROOT}}` 는 **placeholder**다. 파일 정본은 placeholder 형태로 유지되며 install 시점에 치환되지 않는다. 메인 세션이 이 skill을 로드한 직후 bash 한 줄로 resolve 하고, 이후 모든 `{{HARNESS_ROOT}}` 를 그 값으로 대입해 해석한다.

```bash
HARNESS_ROOT=$(dirname $(dirname $(dirname $(realpath ~/.claude/skills/orchestrate-python-lib/SKILL.md))))
echo "$HARNESS_ROOT"   # e.g. /home/user/harnesses/python-lib-dev
```

이 값은 세션 내내 유효하며, `deep-interview-python-lib`, `python-library-conventions` 를 호출할 때 그대로 전달/공유하면 된다 (각 skill도 자기 기준 동일 resolve를 지원하므로 독립 호출도 가능).

이하 이 문서의 모든 `{{HARNESS_ROOT}}` 는 이 값을 가리킨다.

## 1. 전체 흐름

```
메인 세션:
  1) deep-interview-python-lib skill 호출 → 사용자와 대화로 mode.json 확정
  2) {{HARNESS_ROOT}}/scripts/init_run.py 실행 → outputs/<run-id>/ 생성
  3) outputs/<run-id>/interview/{spec.md, mode.json} 기록
  4) {{HARNESS_ROOT}}/scripts/run.py --run-id <id> 실행
  5) run.py가 게이트에 도달해 정지하면 해당 게이트 파일을 사용자와 같이 작성 후 run.py 재호출
  6) 종료 상태(delivery.md) 까지 반복
```

## 2. 단계별 지침

### 2-1. Interview

`deep-interview-python-lib` skill을 직접 호출한다. 그 skill이 묻는 변수를 사용자로부터 받고 `spec.md` / `mode.json`을 확정한다.

### 2-2. Run 초기화

```bash
python {{HARNESS_ROOT}}/scripts/init_run.py
# 출력: {{HARNESS_ROOT}}/outputs/<run-id>   ← 이 경로를 RUN_DIR로 캡처
```

이 경로를 `RUN_DIR`로 두고, 사용자와 확정한 interview 산출물을 다음 위치에 저장한다:

- `$RUN_DIR/interview/spec.md`
- `$RUN_DIR/interview/mode.json`

JSON 스키마는 `{{HARNESS_ROOT}}/docs/interview-guide.md` 참조.

### 2-3. Orchestrator 실행

```bash
python {{HARNESS_ROOT}}/scripts/run.py --run-id <run-id>
```

`run.py` 는:
- preflight 검증 (uv, git, claude CLI, evolve 시 target_repo_path)
- 각 단계 headless 호출
- 게이트 도달 시 `<gate>.request.md` 생성 후 종료 코드 0으로 정지
- 에스컬레이션 발생 시 `escalation.md` 생성 후 종료 코드 2로 정지
- 모든 단계 통과 시 `delivery.md` 생성 후 종료 코드 0

### 2-4. 게이트 처리

`run.py` 가 정지하면 `$RUN_DIR` 안에 다음 중 하나가 있다:

- `gate0.request.md` (evolve 전용) — survey 승인
- `gateA.request.md` — plan 승인
- `gateB.request.md` — design 승인
- `escalation.md` — CRITICAL / cap 도달 / 정체

메인 세션이 할 일:

1. request 파일(또는 escalation)을 사용자에게 요약해 보여준다.
2. 요청된 산출물을 함께 검토한다 (plan.md, design.md, survey.md 등).
3. 사용자 판단을 받아 decision 파일을 작성:
   - `<gate>.decision.md` — 포맷은 request.md의 "Expected decision" 블록 그대로
   - `escalation.decision.md` — abort / resume_from_plan / resume_from_design / force_continue
4. 동일 명령으로 재개:

    ```bash
    python {{HARNESS_ROOT}}/scripts/run.py --run-id <run-id>
    ```

### 2-5. 종료

`delivery.md` 가 생성되면 작업 완료. 사용자에게:
- new 모드: `$RUN_DIR/workspace/` 를 원하는 위치로 옮길 것 안내
- evolve 모드: `target_repo_path` 의 작업 브랜치(interview에서 확정된 `branch_name`, 기본값은 `harness/<run-id>`)를 PR 또는 머지할 것 안내

## 3. 사용자 게이트에서의 진행 규칙

- 메인 세션은 게이트 decision을 사용자 확인 없이 작성하지 않는다.
- decision에 "rewrite" 피드백을 넣을 때는 해당 단계 산출물이 지워지고 재실행된다는 점을 사용자에게 먼저 고지한다.
- `approved_with_breaking` 은 evolve 모드 게이트 B에서만 유효하며, breaking 내용을 `breaking_notes` 필드에 구체적으로 적도록 안내한다.

## 4. 실패 시 대응

- preflight 실패: `uv` / `git` / `claude` 설치 또는 `target_repo_path` 상태를 사용자에게 안내. 해결 후 `run.py` 재호출.
- headless 호출 실패(rc ≠ 0): `run.py`가 즉시 종료. stderr 로그를 사용자에게 보여주고, 재시도 또는 escalation으로 전환할지 상의.
- 캐시된 불량 산출물: `$RUN_DIR/<stage>/` 디렉토리를 삭제하고 `run.py` 재호출하면 해당 단계부터 재실행.

## 5. 참고 문서

- 작업 명세: `{{HARNESS_ROOT}}/docs/task-spec.md`
- 고정 규약: `{{HARNESS_ROOT}}/docs/tacit-knowledge.md`
- 매 실행 interview 변수: `{{HARNESS_ROOT}}/docs/interview-guide.md`
- 단계 정의: `{{HARNESS_ROOT}}/docs/stages.md`
- 캡/임계치: `{{HARNESS_ROOT}}/scripts/config.yaml`
