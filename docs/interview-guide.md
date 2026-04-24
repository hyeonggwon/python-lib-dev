# python-lib-dev — Interview Guide

Deep-interview는 **메인 세션**에서 수행한다. 이 가이드의 변수만 묻는다. `task-spec.md`와 `tacit-knowledge.md`에 이미 고정된 규약은 묻지 않는다.

---

## 필수 변수

### 1. mode — `new` | `evolve`

새 라이브러리를 만들 것인지, 기존 라이브러리에 변경을 가할 것인지. 사용자가 망설이면:
- 빈 디렉토리에서 시작 → `new`
- 특정 레포가 이미 있음 → `evolve`

### 2. (evolve 전용) target_repo_path

기존 라이브러리의 **로컬 절대 경로**. preflight가 검증:
- git repo인가
- dirty tree 아닌가 (커밋되지 않은 변경이 없는가)
- 현재 브랜치가 무엇인가 (참고용)

### 3. 라이브러리 이름 / PyPI 슬러그

- 파이썬 임포트명 (`my_lib`, 소문자 + 언더스코어)
- PyPI 슬러그 (`my-lib`, 소문자 + 하이픈). 생략 시 임포트명을 하이픈으로 변환.
- evolve 모드에선 기존 값을 그대로 사용 (interview에서 재확인만).

### 4. 한 줄 목적 설명

이 라이브러리(또는 이번 변경)가 **무엇을 해결하는가**를 한 문장으로. 모호하면 구체 예시 요청.

### 5. 주요 사용 시나리오 2~3개

누가, 언제, 어떻게 쓰는지. 각 시나리오는 입력-처리-출력이 명확해야 한다.

### 6. (evolve 전용) 이번 변경 요구

무엇을 추가/수정/제거하려는가. 한 문단 수준. 여러 건이 섞여 있으면 **하나의 변경 묶음**으로 좁힐지 물어본다. 대규모 리팩터링이면 여러 evolve run으로 쪼개라고 안내.

### 7. (evolve 전용) 작업 브랜치명

기본값: `harness/<run-id>` (예: `harness/2026-04-23T15-22-04`). 

**반드시 물어본다.** 사용자가 기본값으로 OK 하면 그대로 확정, 커스텀 이름을 주면 그 값으로 확정.

안내 문구 예시:
> "evolve 모드는 기존 레포에서 작업 브랜치를 만들어 진행합니다. 기본 이름은 `harness/<run-id>` 이고, 이대로 가도 되고 원하시면 다른 이름(`feat/async-parser`, `fix/parser-edge` 등)으로 지정할 수 있어요. 어떻게 할까요?"

- 프리픽스 `harness/` 는 자동 정리/추적 목적으로 쓰이는 기본 관례. 기본값 사용을 권장하되 팀 컨벤션이 있으면 맞춰도 됨.
- git 브랜치명 규약 준수(공백/콜론/`..`/`~` 등 금지). 이상하면 interview에서 되묻거나 preflight가 실패시킴.

---

## 선택 변수

### 8. 유사 라이브러리 + 차별점

있다면. 설계(s2) 단계에서 API 설계 참고용.

### 9. Python 최소 버전 override

기본 **3.10**. 더 높은 버전(예: 3.11+)이 필요한 사유가 있으면 명시.

### 10. 임계치 override

기본값:
- line coverage ≥ 0.90
- branch coverage ≥ 0.80
- major 이슈: new 0개 / evolve ≤ 2개

사용자가 먼저 "이번 run은 완화"를 말할 때만 물어본다. 기본적으로 묻지 말 것.

---

## 진행 규칙

- 사용자가 답을 망설이면 **후보 2~3개 제시 후 고르게** 한다. "뭐든 괜찮다"는 답을 그대로 받지 않는다.
- 이해되지 않는 답변은 즉시 되묻는다.
- 답변 중 상호 모순이 발견되면 **두 답변 모두를 보여주고 어느 쪽인지** 물어본다.
- 마지막에 "지금까지의 요약을 보시고, 추가/수정이 없으면 `OK`로 답해주세요" 형식으로 명시적 확정을 받는다.

---

## 산출물

### `outputs/<run-id>/interview/spec.md`

자연어 요약. 다음 섹션을 포함:
- 모드와 target (있다면)
- 라이브러리 이름 / 슬러그
- 한 줄 목적
- 사용 시나리오
- (evolve) 변경 요구 요약
- (있으면) 유사 라이브러리와 차별점
- Python 최소 버전
- (있으면) override된 임계치

### `outputs/<run-id>/interview/mode.json`

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

- `null` = 기본값 사용
- evolve 모드에서 `target_repo_path`는 절대 경로
- `branch_name`: evolve 시 interview에서 확정된 이름을 저장. 기본값을 선택한 경우도 실제 문자열(`"harness/<run-id>"`)로 저장한다. new 모드에선 `null`.

---

## Interview 종료 후

메인 세션은 다음을 실행한다:

```bash
python {{HARNESS_ROOT}}/scripts/run.py --run-id <run-id>
```

`run.py`가 이후 모든 단계를 순차 실행한다. 게이트에서 중단되면 해당 게이트 파일을 메인 세션이 사용자와 함께 작성한 뒤 `--resume`으로 재개한다.
