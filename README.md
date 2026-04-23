# python-lib-dev

**한국어** | [English](README.en.md)

<div align="center">
<img width="480" height="360" alt="python-lib-dev-logo" src="https://github.com/user-attachments/assets/ed2a2885-38ea-443d-93e4-c15b3e062b1f" />
</div>
</br>
파이썬 라이브러리 1개를 기획 → 설계 → 테스트 → 구현 → 리뷰 → 문서화 파이프라인으로 만들거나 고치는 Claude Code 하네스.

## Initiailize

```bash
uv pip install pyyaml    # 또는 pip install pyyaml, 최초 한 번만 실행
./install.sh             # ~/.claude/skills/ symlink 생성 스크립트, 최초 한 번만 실행
```

제거는 `./uninstall.sh` (symlink만 제거).

## Quick Start
Claude Code 세션 
-> "파이썬 라이브러리 만들어줘" / "이 라이브러리에 X 기능 추가" 같이 요청 or `orchestrate-python-lib` skill을 트리거
-> 메인 세션이 interview → run 초기화 → 파이프라인 실행을 끝까지 몰아간다.
-> 블로킹 게이트에서 멈추면 `<gate>.decision.md`를 함께 작성하고 재개.

## How It Works

1. **Interview** — 메인 세션이 `deep-interview-python-lib`로 사용자와 대화해 `spec.md` + `mode.json` 확정.
2. **Init** — `scripts/init_run.py`가 `outputs/<run-id>/` 생성.
3. **Orchestrate** — `scripts/run.py --run-id <id>`가 각 단계(s0..s8)를 headless로 호출.
4. **Gates** — 블로킹 게이트(evolve의 survey, plan, design) 도달 시 `<gate>.request.md` 남기고 정지. 사용자와 함께 decision 작성 후 같은 명령으로 재개.
5. **Loop** — s5 리뷰 verdict에 따라 MINOR → s4, MAJOR → s2로 자동 루프백. CRITICAL / cap 초과 / 정체 시 `escalation.md` 생성 후 정지.
6. **Deliver** — 모든 단계 통과 시 `DELIVERY.md` 생성 후 종료.

단계 정의·state/verdict 스키마·게이트 포맷은 `docs/stages.md`.

## Architecture

```
python-lib-dev/
  install.sh / uninstall.sh   # symlink 설치/제거
  scripts/
    init_run.py               # run 디렉토리 + state.json 초기화
    preflight.py              # uv / git / claude / target_repo_path 검증
    run.py                    # 메인 오케스트레이터
    config.yaml               # 캡, 임계치, 정체 감지 파라미터
    prompts/                  # s0..s7 프롬프트 정본
  skills/                     # install.sh이 ~/.claude/skills/로 symlink
    orchestrate-python-lib/
    deep-interview-python-lib/
    python-library-conventions/
  docs/                       # task-spec, stages, interview-guide, tacit-knowledge, discussion-log
  outputs/                    # run 산출물 (.gitignore)
```

- 레포 자체는 라이브러리가 아니다. stdlib + PyYAML만 쓰는 얇은 오케스트레이션 계층.

## 생성되는 파일

`outputs/<run-id>/` 한 run 기준:

```
state.json
interview/{spec.md, mode.json}
s0/survey.md                  # evolve 전용
s1/plan.md
s2/{design.md, api_stubs.py}
s3/...                        # new는 workspace/tests/에 직접, evolve는 tests-new/
s4/{impl-notes.md, changes.patch}   # changes.patch는 evolve만
s5/{review.md, verdict.yaml, test-run.log}
s6/decision.json
gate0.request.md / gate0.decision.md    # evolve 전용
gateA.request.md / gateA.decision.md
gateB.request.md / gateB.decision.md
escalation.md / escalation.decision.md  # 필요 시
DELIVERY.md

workspace/                    # new 모드 산출물 (실제 라이브러리 본체)
  pyproject.toml, src/<pkg>/, tests/, README.md, docs/, CHANGELOG.md
```

## Development

이 레포를 수정할 때는 **`CLAUDE.md`를 먼저 읽는다.** 요약하면:

- `skills/<name>/SKILL.md`가 정본, `~/.claude/skills/` 하위는 symlink. 직접 편집 금지.
- 루트에 `pyproject.toml` / `src/` / `tests/`를 만들지 말 것 — 이 레포는 라이브러리가 아님.
- `outputs/`는 커밋 금지.

무엇을 어디서 고치는지 매핑 표는 `CLAUDE.md` §5.

## Requirements

- `claude` CLI (Claude Code)
- `uv`
- `git`
- `Python 3.10+`
- `PyYAML` (`run.py`가 `config.yaml` 파싱에 사용. `uv pip install pyyaml` 또는 `pip install pyyaml`)

생성되는 라이브러리 쪽 고정 스택: `uv` · `hatchling` · `pytest` · `ruff` · `mypy --strict` · `src/` 레이아웃 · Google docstring · Conventional Commits · GitHub Actions 3.10/3.11/3.12/3.13.
