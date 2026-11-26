# PostgreSQL 조리 완료·확정 출고 연결·구성원 섭취 저장

작업·검증일: 2026-11-26 (KST).
기준 main: `c8792b6` — PR #35 조리 세션 생성·조회·재료 변경 병합 완료.
작업 브랜치: `codex/postgres-cooking-completion`.

## 완료 범위

기존 `POST /api/cooking-sessions/{id}/complete`를 실제 PostgreSQL에 연결한다.
가구 로그인 → 추천 → 식사·조리 세션 → A 출고 생성·확정 → B 완료·구성원별 섭취 저장을 실제 루프백 HTTP로 시험한다.
화면·원본 데이터 이전·Supabase/AWS 배포·식사 이력/리포트 조회 API 이전은 이번 범위가 아니다.

## 재고 원칙

**재고 변경은 A 출고 확정에서만 일어난다. B 완료는 이미 확정된 출고를 연결하고 섭취량을 기록한다.**

1. 인식 또는 수동 이벤트 접수만으로 재고가 줄지 않는다.
2. A `stock_out` 확정이 재고·변경 이력·재고 버전을 한 번 변경한다.
3. B는 같은 가구의 applied stock_out과 재료별 합산 g를 검증한다.
4. B는 재고·A 이벤트·재고 버전을 수정하거나 A 출고를 자동 복원하지 않는다.
5. B 실패 시 B의 연결·식사·섭취·선호·완료 상태만 rollback한다. A에서 이미 빠진 재고는 그대로다.
6. B 재시도는 기존 확정 event id를 재사용한다. 실패했다고 A 출고를 새로 만들지 않는다.

합성 1인 사례: **1,000g → A 100g 출고 후 900g → B 완료 후 900g**.
0.5인분 섭취는 영양 기록에만 0.5배 반영하고, 조리에 사용한 100g 재고를 다시 계산하거나 차감하지 않는다.
2인 사례는 전체 조리량 200g 출고로 800g이 된 뒤, [1.5인분, 0인분] 기록 후에도 800g이다.

## 구현

- `006_cooking_completion.sql`: 출고 event PK의 단일 조리 연결 테이블, 조리 record 연결·구성원·영양소 중복 방지 인덱스.
- PostgreSQL 완료는 가구·쓰기 권한 확인 후 조리 세션을 FOR UPDATE로 잠근다.
- 여러 출고 event를 id 순으로 잠가 공유 이벤트 경쟁과 역순 입력에서 교착·중복 연결을 방지한다.
- INSERT ON CONFLICT와 PK로 단일 claim을 보장하고 이미 연결된 출고는 409로 거절한다.
- 같은 조리의 중복 완료는 기존 계약대로 400이며 추가 식사·섭취·출고 연결을 만들지 않는다.
- 구성원은 이번 식사 참여자여야 하며 중복될 수 없다. 사용자 확인 섭취 인분으로 영양을 계산한다.
- 0인분 저장은 허용하며 영양은 0, 해당 섭취에 대한 선호 증거는 생성하지 않는다.
- 섭취 합계가 전체 조리 인분을 넘는 요청은 400으로 거절한다. 1.5+0처럼 합계가 조리 인분 이내인 기록과 부분/미섭취는 허용한다.
- 현재 알레르기·식이·나트륨 검사와 구성원별 실제 인분에 따른 나트륨 한도 검사를 유지한다.
- 출고 claim·meal_records·meal_record_items·nutrition_intakes·선호 집계·completed 전환은 같은 B 트랜잭션이다.
- PostgreSQL은 runtime DDL을 실행하지 않는다. SQLite의 링크 테이블도 기존 초기화 schema로 옮겨 완료 함수에서 CREATE를 제거했다. SQLite 초기화 자체의 기존 DDL 동작은 별개다.
- A 재고 응답 source는 PostgreSQL에서 `postgresql`, SQLite에서는 기존 `local_sqlite`다.

기존 001~005 migration은 변경하지 않았다. 006의 유일 인덱스와 충돌하는 기존 중복 데이터가 있으면
임의 삭제하지 않고 적용 전 검토해야 한다. 실제 Supabase의 기존 schema·역할·RLS 검증은 별도다.

## HTTP 계약

```text
POST /api/cooking-sessions/{id}/complete
Authorization: Bearer <기존 가구 세션>
{
  "confirmed": true,
  "eaten_at": "2026-11-26T12:00:00+09:00",
  "items": [{"member_id": <참여자 ID>, "servings": 0.5}],
  "inventory_event_ids": [<A에서 확정된 출고 ID>]
}
```

성공 201의 기존 `record_id`, `nutrition_per_serving`, `ingredients`를 유지한다.
위 예시는 형식 설명이며 실제 토큰·개인 데이터가 아니다.

| 응답 | 의미 |
| --- | --- |
| 401 | Bearer 헤더 없음 |
| 403 | 기존 완료/A 계약과 같이 무효·만료 세션 또는 가구/쓰기 권한 없음 |
| 409 + inventory_confirmation_required | 출고 누락·미확정·거절·잘못된 action/가구/재료·중량 부족·ID 중복·다른 조리에 재사용 |
| 400 | 잘못된 섭취/시간/참여자/건강 제한, 이미 완료한 조리, 인분 합계 초과 |
| 500 | 예기치 않은 DB 오류. B 전체 rollback, 기존 A 출고는 유지 |

현재 C의 완료 호출은 이미 Bearer와 inventory_event_ids를 보내므로 같은 코드를 중복 구현하지 않는다.
이전 문서의 추천·식사 세션·조리 생성/조회/변경에 대한 토큰 전달 연동은 별도로 확인해야 한다.
완료 응답을 잃었다면 조리 GET의 status/meal_record_id를 확인한다. 중복 요청을 새 식사로 기록하지 않는다.
재고를 화면에서 추가 차감하지 않는다. `POST /api/meal-records`와 리포트 조회 등 미이전 PostgreSQL 경로는 계속 501이다.

## 시험

`tests/test_cooking_completion_postgres.py`는 합성 가구·fake AI·실제 PostgreSQL·실제 HTTP를 사용한다.

- 대기/확정/확정 재시도/완료/중복 완료 각각의 재고 snapshot과 version을 비교.
- 0·0.5·1인분, 다인 [1.5,0]/[1,1]의 구성원별 다섯 영양소 저장값 확인.
- 타 가구/viewer/헤더 누락/무효 토큰 차단.
- pending/rejected/stock_in/foreign/short/wrong ingredient/duplicate/missing event 거절.
- 복수 lot 출고 합산과 명시적 A 반납 후 B 재고 불변 확인.
- 같은 조리 동시 완료, 다른 조리의 공유 출고 경쟁, 완료 대 재료 편집, 복수 출고 역순 경쟁.
- 실제 섭취 인분 기준 건강 제한 및 유효하지 않은 섭취 입력 거절.
- 식사/구성원/영양소/선호/최종 상태 저장에 DB 오류를 주입해 B 원복·A 보존·같은 출고로 재시도 확인.
- B 실행 SQL에 DDL이나 inventory 쓰기가 없음을 확인.
- 005 DB의 기존 조리·재고·식사·섭취를 보존한 006 적용·재실행 확인.

## 재현

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:KINI_USE_OPENAI='0'
$env:PYTHONPATH=(Get-Location).Path
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_vision_postgres.ps1 -Python <프로젝트-Python-절대경로>
```

전체 묶음에 이번 완료 시험을 추가했다. 단독 실행은 `-TestPaths tests/test_cooking_completion_postgres.py`를 붙인다.
스크립트는 임시 비밀번호·루프백 임의 포트·tmpfs PostgreSQL을 만들고 자신의 컨테이너만 정리한다.
일반 회귀는 `KINI_DB_BACKEND=sqlite`에서 `python -m pytest -q --tb=short`, Node는 `node --test tests/ui/*.test.cjs`다.

최종 직접 실행 수치는 아래에 기록한다. 실제 Supabase/AWS·외부 AI·카메라·원본 가족 데이터는 사용하지 않았다.

## 최종 직접 실행 결과

| 검증 | 결과 |
| --- | --- |
| 실제 PostgreSQL 16.15 전체 | **183 passed / 208.71초** |
| 이번 조리 완료·섭취 | 위 묶음 중 **37개**: 실제 HTTP 36개 + 005→006 업그레이드·재실행·보존 1개 |
| 기존 재고·가족·추천·조리안 | 위 묶음 중 **146개** |
| SQLite 전체·Chrome 회귀 | **290 passed, 183 skipped / 236.66초**, Chrome E2E 13개 포함 |
| JavaScript UI | **35 passed** |
| 기존 SQLite 완료·조리 회귀 별도 | **11 passed / 11.59초** |
| git diff --check | 통과 |

일반 시험의 183개 skip은 PostgreSQL 환경 전용이며 실제 PostgreSQL 묶음에서 모두 독립 실행했다.
수치는 합성 데이터·일회성 DB·fake AI 기준이다. 실행 시간은 이 PC의 시험 소요 시간으로 운영 성능 benchmark가 아니다.
각 실행 후 테스트 컨테이너와 임시 schema를 정리했다.
