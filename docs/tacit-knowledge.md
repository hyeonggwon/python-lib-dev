# python-lib-dev — Tacit Knowledge

실행자(메인 세션 및 headless)가 "말 안 해도 아는 것". 매 실행마다 interview에서 다시 묻지 않는다.

---

## 품질 관점

- 이 하네스의 모든 산출물은 **PyPI 공개 수준** 이라고 가정한다. 내부용 스크립트가 아니다.
- 공개 API는 다음을 만족한다:
  - 타입이 정확하고 `mypy --strict` 통과
  - Google 스타일 docstring
  - 예외 계약(어떤 상황에서 어떤 예외를 던지는지)이 문서화됨
- 테스트는 pytest 기본 기능만으로 작성한다. 불필요한 magic(커스텀 플러그인, fixture 남용 등) 피함.
- 파이썬 스타일: PEP8 + ruff 기본 규칙. ruff가 잡으면 고친다.

---

## 도구 사용 (반복 등장)

- 모든 파이썬 실행은 `uv run <...>` 형태. 글로벌 `python` 직접 호출 금지.
- 의존성 추가는 `uv add <pkg>`. 가능한 한 표준 라이브러리로 해결 시도.
- 테스트: `uv run pytest [-q]`
- 타입: `uv run mypy --strict src tests`
- 린트: `uv run ruff check .` / 포맷: `uv run ruff format .`
- 커버리지: `uv run pytest --cov=<pkg> --cov-branch --cov-report=term-missing`

---

## 모드별 행동

### new 모드

- 시작 시 `outputs/<run-id>/workspace/` 로 이동해 `uv init --lib <pkg_name>` 실행. `src/<pkg>/` 레이아웃 자동 생성.
- `pyproject.toml`에 `requires-python = ">=3.10"` (interview 값), `tool.ruff`, `tool.mypy`, `tool.pytest.ini_options` 설정.
- 최종 배포 목적지는 결정하지 않는다. `DELIVERY.md`에 사용자가 이동할 책임 명시.

### evolve 모드

- 시작 시 `cd <target_repo_path> && git checkout -b <branch_name>`. `branch_name` 은 `mode.json.branch_name` (interview에서 확정, 기본값 `harness/<run-id>`). dirty tree면 preflight에서 이미 중단됨.
- `main`/`master`에 머지하지 않는다. PR 또는 머지는 사용자 몫.
- 기존 공개 API는 **breaking change 승인** 없이 바꾸지 않는다.
- 기존 테스트는 **모두** 통과해야 한다 (회귀 방지).
- 기존 README/CHANGELOG는 **덮어쓰지 않는다**. 추가/수정(diff)만.

---

## 리뷰(s5) 격리 원칙

- s5 headless는 **구현자(s4)의 reasoning을 보지 않는다**. 입력은 오직:
  - `plan.md`, `design.md`, `api_stubs.py`
  - `tests/` 또는 `s3/tests-new/`
  - 실제 코드 (new: `workspace/src/`, evolve: git diff 기반 변경 파일)
  - 테스트 실행 결과(`s5/test-run.log`)
- 리뷰어가 구현자 노트(`s4/impl-notes.md`)를 참조하는 것은 허용하나, 그 내용을 **근거로 삼지 않는다** — 리뷰는 산출물 자체로만 판정.

## Verdict 형식

- `s5/verdict.yaml` 은 고정 YAML 스키마. 자유 서술 금지.
- 스키마는 `stages.md`에 정의. 산문 설명이 필요하면 `rationale` 필드에 한 문장, 상세는 `s5/review.md`에.

---

## 루프백

- `MINOR` → 구현 루프 (cap 3)
- `MAJOR` → 설계 루프 (cap 2)
- `CRITICAL` → 사용자 게이트 (자동 루프백 금지)
- 기획(s1)으로의 자동 루프백은 **없다**.
- 구현 내부 재시도 cap 5 (예: 테스트 실행 실패, import 오류 등 기술적 재시도).
- 전체 stage 누적 cap 15.
- 정체 감지: 연속 2개 verdict의 `issues[].file + severity` 조합이 50% 이상 겹치면 에스컬레이션.

---

## 하네스가 하지 않는 것

- `uv publish` / PyPI 배포 자동 실행 금지. 사용자가 `DELIVERY.md` 읽고 수동으로.
- evolve 모드에서 `main`/`master` 자동 머지 금지.
- 하드 게이트 스킵 금지. 통과 못 하면 에스컬레이션.
- Sub-agent를 `.claude/agents/`에 추가로 생성하지 않는다 (초기 단계 결정).
- 하네스 범위를 벗어난 작업(예: CLI, 웹 서버, ML 파이프라인)은 이 하네스로 처리하지 않는다.
