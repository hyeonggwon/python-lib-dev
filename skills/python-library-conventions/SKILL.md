---
name: python-library-conventions
description: python-lib-dev 하네스가 생성/유지보수하는 모든 파이썬 라이브러리에 적용되는 고정 규약. 메인 세션과 headless 모두 이 규약을 "말 안 해도 아는 것"으로 전제한다. uv, pytest, mypy --strict, ruff, src 레이아웃, Google docstring, Conventional Commits. PyPI 공개 수준 품질.
---

# python-library-conventions

python-lib-dev 하네스 범위 안의 모든 산출물이 따르는 공통 규약. 이 skill은 독립 호출 용도가 아니라 orchestrator / headless가 참조하는 중앙 저장소 역할.

## 0. HARNESS_ROOT 구하기

이 문서의 `{{HARNESS_ROOT}}` 는 placeholder. 필요 시:

```bash
HARNESS_ROOT=$(dirname $(dirname $(dirname $(realpath ~/.claude/skills/python-library-conventions/SKILL.md))))
```

호출 측이 이미 resolve 했다면 그 값을 공유.

## 1. 스코프

- **대상**: PyPI 공개 수준 품질의 순수 파이썬 라이브러리.
- **대상 아님**: CLI 도구, 웹 서버(FastAPI/Django), ML 파이프라인, 내부 스크립트.

## 2. 도구 스택 (협상 불가)

| 항목 | 값 |
|---|---|
| 패키지 매니저 | `uv` |
| 빌드 백엔드 | `hatchling` |
| 테스트 | `pytest` (+ 필요시 `hypothesis`) |
| 린트/포맷 | `ruff` 단일 |
| 타입 | `mypy --strict` |
| Python 최소 | 기본 3.10 (interview override 가능) |
| Docstring | Google 스타일 |
| 레이아웃 | `src/<pkg>/` + 최상위 `tests/` |
| CI | GitHub Actions, 3.10/3.11/3.12/3.13 매트릭스 |
| 커밋 | Conventional Commits |

## 3. 실행 명령 (전부 `uv run` 경유)

```bash
uv run pytest -q
uv run pytest --cov=<pkg> --cov-branch --cov-report=term-missing
uv run mypy --strict src tests
uv run ruff check .
uv run ruff format .
uv run ruff format --check .
```

글로벌 `python`, `pip install` 금지. 의존성은 `uv add <pkg>`.

## 4. 하드 게이트 (s5에서 강제)

- 모든 테스트 통과
- `mypy --strict` 통과
- `ruff check` + `ruff format --check` 통과
- 리뷰 blockers 0개

## 5. 조정 가능 임계치 (interview override 가능)

- line coverage ≥ 0.90
- branch coverage ≥ 0.80
- major 이슈 수: new 0 / evolve ≤ 2

## 6. 코드 품질 기준

- **공개 API**: 타입 정확, `mypy --strict` 통과, Google docstring (Args/Returns/Raises 명시), 예외 계약 문서화.
- **테스트**: 행동 기반 테스트 우선. 모킹은 경계(네트워크/파일시스템/시계/난수)에서만. 자잘한 값 변이는 parametrize. 세션 스코프 fixture는 비용 이유 있을 때만.
- **의존성**: 표준 라이브러리로 먼저 시도. 추가 의존성은 `impl-notes.md`에 이유 기록.

## 7. evolve 모드 추가 규칙

- 시작 시 `{target_repo_path}` 에서 `git checkout -b <branch_name>` (인터뷰에서 확정된 `mode.json.branch_name`, 기본값 `harness/<run-id>`). 이 작업은 `run.py` 의 preflight 가 수행하므로 헤드리스가 직접 만들지 않음.
- `main`/`master` 자동 머지 금지.
- 기존 공개 API는 **breaking change 승인(게이트 B)** 없이 바꾸지 않음.
- 기존 테스트는 **모두** 통과해야 함.
- 기존 README/CHANGELOG 덮어쓰기 금지. 추가/수정만. breaking 시 `MIGRATION.md`.

## 8. 하네스가 하지 않는 것

- 자동 `uv publish`, PyPI 배포
- `main`/`master` 자동 머지
- 하드 게이트 스킵
- 스코프 밖 작업(CLI/웹/ML) 수용

## 9. 참고

- 전체 작업 명세: `{{HARNESS_ROOT}}/docs/task-spec.md`
- 암묵지 상세: `{{HARNESS_ROOT}}/docs/tacit-knowledge.md`
- 단계 정의: `{{HARNESS_ROOT}}/docs/stages.md`
