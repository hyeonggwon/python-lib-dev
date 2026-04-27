# CLAUDE.md — python-lib-dev 하네스 유지보수 가이드

이 파일은 **이 하네스 레포(`/home/obigo/harnesses/python-lib-dev/`)를 수정할 때만** 유효하다.
하네스를 **사용**(라이브러리 생성/수정)할 때의 작업 공간은 `outputs/<run-id>/workspace/` 또는 `target_repo_path`이며, 그쪽 규약은 이 파일이 아니라 `skills/python-library-conventions/SKILL.md`가 관장한다.

---

## 1. 이 레포는 라이브러리가 아니다

- 여기는 **하네스 인프라**다. 산출물은 `outputs/<run-id>/` 또는 `target_repo_path`에 만들어진다.
- `uv`, `pytest`, `mypy --strict`, `ruff`, `src/` 레이아웃, Google docstring, `hatchling` — 이 모두는 **생성 대상 라이브러리**에 적용되는 규약이다. 이 레포 자체에 적용하지 말 것.
- 이 레포의 파이썬 코드(`scripts/*.py`)는 stdlib + `PyYAML`만 쓰는 얇은 오케스트레이션 계층이다. 의존성 추가 전에 반드시 정당화하라.
- 루트에 `pyproject.toml`, `src/`, `tests/`를 만들지 말 것. 그건 이 레포를 "또 하나의 라이브러리"로 잘못 취급하는 신호다.
- **harness-builder SKILL.md 단계 0 의 다섯 축** (컨텍스트 파일·결정론적 게이트·도구 경계·in-run 피드백 루프·자기교정 경계) 은 이 하네스에 모두 구현되어 있다. 만약 미래에 어떤 축을 의도적으로 생략·완화하게 된다면 **그 사유를 이 `CLAUDE.md` 에 명시 기록**해야 한다 (SKILL 원칙). 현재는 생략 축 없음.

## 2. Placeholder 정책 (최우선 규약, 실수하면 배포가 깨짐)

두 종류의 placeholder가 있고 **둘 다 install-time 치환하지 않는다**. (Amendment A4)

### 2-1. `{{HARNESS_ROOT}}` — skill/문서용, **영구 유지**

대상 파일: `skills/*/SKILL.md`, `docs/*.md`.

- 정본에 `{{HARNESS_ROOT}}`을 그대로 둔다. 메인 세션이 skill 로드 직후 `realpath` 기반 bash 한 줄로 runtime resolve한다 (`orchestrate-python-lib/SKILL.md` §0 참조).
- 어떤 커밋에서도 `{{HARNESS_ROOT}}`이 `/home/obigo/...` 같은 절대 경로로 치환되어 들어가면 **버그**다. PR/커밋 검토 시 확인할 것.
- `install.sh`은 심볼릭 링크만 만든다. 파일 내용을 손대지 않는다.

### 2-2. `{HARNESS_ROOT}`, `{run_dir}`, `{run_id}`, `{target_repo_path}`, `{lib_name}` — 프롬프트용, **매 호출 시 치환**

대상 파일: `scripts/prompts/*.md`.

- `run.py`의 `call_headless()`가 headless 호출 직전에 `str.replace`로 치환해 `{run_dir}/.prompts/<stage>.md`로 쓴다.
- 따라서 정본 프롬프트에도 이 토큰은 그대로 둔다.
- 치환은 `str.format`이 아니라 `str.replace`이므로 프롬프트에 중괄호 예시 코드를 넣어도 안전하다. 새 토큰을 추가할 거면 `run.py:135` 부근 치환 블록에도 추가할 것.

## 3. Skill 정본과 symlink

- 정본: `skills/<name>/SKILL.md`. 편집은 여기서만 한다.
- `~/.claude/skills/<name>`은 `install.sh`이 만드는 **심볼릭 링크**. 절대 직접 파일을 만들지 말 것. (Amendment A1)
- 하네스 디렉토리를 `mv`로 옮긴 뒤에는 `./install.sh`만 다시 돌리면 symlink가 새 경로로 갱신된다. 복잡한 재설치 절차 없음.
- `uninstall.sh`은 symlink만 제거하고 소스는 건드리지 않는다.

## 4. 파일별 책임 (어디를 고쳐야 하는가)

| 바꾸고 싶은 것 | 고칠 위치 |
|---|---|
| 파이프라인 단계 내부 행동 (한 stage가 뭘 만드는지) | `scripts/prompts/<stage>.md` |
| 단계 간 라우팅, 게이트 로직, 루프/에스컬레이션 | `scripts/run.py` |
| 캡(cap), 임계치, 정체 감지 파라미터 | `scripts/config.yaml` |
| Interview 변수·대화 흐름 | `skills/deep-interview-python-lib/SKILL.md` + `docs/interview-guide.md` (두 곳 동기화) |
| 생성 라이브러리의 고정 규약 | `skills/python-library-conventions/SKILL.md` + `docs/tacit-knowledge.md` (두 곳 동기화) |
| 오케스트레이션 진입 흐름, 사용자 대응 | `skills/orchestrate-python-lib/SKILL.md` |
| 전체 단계 정의표 | `docs/stages.md` |
| 설치/제거 동작 | `install.sh` / `uninstall.sh` |
| Preflight 검사 항목 | `scripts/preflight.py` (독립 파일, Amendment A3) |
| `state.json` 초기 스키마 | `scripts/init_run.py` |
| 기계 게이트 실행 (pytest/mypy/ruff/coverage) | `scripts/gates.py` (Amendment A7, 0-2 clean separation) |
| Stage별 헤드리스 도구 권한 | `scripts/run.py` 상단 `STAGE_TOOLS` 맵 (0-3) |
| Run 간 증거 축적 (`.index.jsonl`, 에스컬레이션 과거 맥락) | `scripts/run.py` `append_index_entry` / `format_cross_run_pattern_block` (0-5) |
| Drift 검사 (프롬프트 ↔ STAGE_TOOLS / 치환 맵 / feedback 경로) | `scripts/validate_harness.py` |
| Pre-commit 강제 (위 검사를 커밋 전에 실행) | `.githooks/pre-commit` (트래킹됨; `install.sh` 가 `core.hooksPath` 자동 설정) |

**두 곳 동기화** 항목: skill과 docs가 중복 기재되는 이유는 skill이 사용자 지시용, docs가 참조 저장소용이기 때문. 한쪽만 고치면 drift가 난다.

## 5. `outputs/`는 커밋하지 않는다

- `outputs/`는 `.gitignore`에 들어 있다. run 산출물은 모두 여기 격리된다.
- 커밋 전에 `git status`로 확인. `outputs/` 하위 파일이 추적 대상에 나타나면 뭔가 잘못된 것.
- 테스트/디버깅 중 만든 run은 지워도 무방하다 (`rm -rf outputs/<run-id>/`). 상태는 각 run 안에 자기완결적으로 있다.

## 6. `install.sh` / `uninstall.sh` 계약

- **`install.sh`**: 심볼릭 링크 생성/갱신만. 이미 링크가 올바른 위치를 가리키면 no-op. 깨진 링크나 이동 감지 시 교체. 파일 수정 금지.
- **`uninstall.sh`**: symlink 제거. 소스 트리 그대로 둠. placeholder 복원 같은 건 하지 않는다 (정본에 placeholder가 원래 남아 있으므로 복원할 게 없다).
- 두 스크립트 모두 `set -euo pipefail` 유지. `install.sh`에 placeholder 치환 로직을 **다시 추가하지 말 것** (Plan Y는 기각됨, A4 참조).

## 7. Headless 호출 구조 (수정 시 주의점)

- `run.py:call_headless()`는 `claude -p <wrapper>`로 비대화형 호출한다.
- wrapper에는 **짧은 지시 + 실제 프롬프트 파일 경로**만 담고, 본문은 `{run_dir}/.prompts/<stage>.md`에서 읽게 한다. wrapper를 장황하게 만들지 말 것 — 명세의 단일 진실은 `scripts/prompts/<stage>.md`에 있어야 한다.
- 각 stage는 완료 라인을 stdout 마지막 줄에 찍는다 (`<STAGE>_DONE: <path>`). 프롬프트 수정 시 이 규약 유지.
- stage 실패(rc ≠ 0)는 `run.py`가 즉시 종료한다. 재시도 로직은 stage 안(구현 retry cap) 또는 verdict 루프백(MINOR/MAJOR)에만 존재.

## 8. 게이트 / 에스컬레이션 파일 규약

- 요청: `{run_dir}/<gate>.request.md` (하네스 생성).
- 결정: `{run_dir}/<gate>.decision.md` (사용자가 메인 세션과 함께 작성).
- 파서는 `run.py:read_gate_decision()` — 아주 가벼운 `key: value` / `key: |` + 들여쓰기 블록 파서. 새 필드를 추가하면 이 포맷을 깨뜨리지 않도록 주의.
- 게이트 목록: `gate0`(evolve 전용, survey) · `gateA`(plan) · `gateB`(design, evolve는 breaking 승인 포함) · `escalation`(CRITICAL / cap 도달 / 정체).

## 9. 피해야 할 변경

- `install.sh`에 sed 치환 부활 — A4가 명시적으로 기각한 방식.
- `~/.claude/skills/` 아래에 하네스 파일을 직접 생성 — symlink 위반 (A1).
- `scripts/init_run.py`에 preflight 로직 섞기 — A3가 분리한 이유가 있음.
- `{run_dir}/outputs/...`처럼 outputs 안에 또 outputs를 만드는 경로 계산 — `HARNESS_ROOT/outputs/<run-id>/`이 정답.
- 생성 라이브러리용 규약을 이 레포 루트에 끌어다 놓기 (pyproject.toml 등).

## 10. 참고 포인터

- 작업 명세 & 고정 스코프: `docs/task-spec.md`
- 암묵지 (생성 라이브러리 대상): `docs/tacit-knowledge.md`
- 단계 정의표: `docs/stages.md`
- Interview 변수: `docs/interview-guide.md`
- 설계 결정 이력: `docs/discussion-log.md`
- 캡/임계치/정체 감지 설정: `scripts/config.yaml`
