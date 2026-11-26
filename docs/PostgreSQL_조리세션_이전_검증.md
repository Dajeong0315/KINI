# 조리 세션 PostgreSQL 이전

작업·검증일: 2026-11-26 (KST).
기준 main: `6b1b5b6` — 가족 PR #33과 추천 N1~N3 PR #34 병합 완료.
작업 브랜치: `codex/postgres-cooking-session`.

## 이번 단위

실제 HTTP로 **가구 로그인 → 추천 생성 → 식사 참여자 세션 → 조리안 생성 → 조회 → 재료 변경**을
로컬 PostgreSQL에 연결한다. 조리 완료·섭취 저장·리포트는 이번 범위가 아니다.

| 경로 | 동작·권한 |
| --- | --- |
| `POST /api/meal-sessions` | 같은 가구의 참여자 세션 생성, owner/editor |
| `POST /api/cooking-sessions` | 같은 가구의 유효 추천·식사 세션과 표시된 후보로 조리안 생성, owner/editor |
| `GET /api/cooking-sessions/{id}` | 같은 가구 세션 조회, viewer도 허용 |
| `POST /api/cooking-sessions/{id}/ingredients` | 실제 재료·중량 변경 및 영양 재검증, owner/editor |

PostgreSQL에서는 위 네 경로 모두 기존 가구 Bearer 세션을 사용한다.
토큰 누락/무효 401, 타 가구·viewer 쓰기 403, 잘못된 조리안·재료·입력 400이다.
응답 필드와 SQLite API의 기존 동작은 유지한다. PostgreSQL 응답은 no-store다.

## 변경 내용

- `005_cooking_sessions.sql`: 조리안·확정된 총 조리 인분·재료 JSON·상태·관계 FK와 가구 조회 인덱스.
  기존 001~004는 변경하지 않았다. version 실행기로만 적용하며 요청 중 DDL을 실행하지 않는다.
- PostgreSQL INSERT RETURNING에 cooking_sessions를 추가했다. API의 기존 숫자 id를 유지한다.
- 새 식사 세션은 참여자가 해당 가구 소속인지 검사하고 현재 source/release context도 함께 저장한다.
- 추천·식사 가구와 참여자 집합, 표시 후보 여부, AI 검증 상태, 공개/공식 reference release가 바뀌지 않았는지 확인한다.
- 레시피 전체 중량을 실제 참여 인원 수로 조정한다. 1인분 영양과 전체 조리 중량은 구분한다.
- 매 변경에서 알레르기·식이 제한·나트륨·라벨/영양·소비기한·최신 보관 지침을 다시 검사한다.
- PostgreSQL의 최신 보관 지침은 명시적 id, SQLite는 기존 rowid를 사용한다.
- 수량/인분 누락과 비유한 값을 거부한다. 조리안의 마지막 재료까지 제거할 수 없다.
- PostgreSQL 변경은 조리 세션 행을 FOR UPDATE로 잠가 서로 다른 재료의 동시 편집이 덮어쓰이지 않게 한다.
- 변경·선호 evidence·집계·요약은 같은 트랜잭션이다. 중간 DB 오류 시 전체 rollback한다.

## 재고·완료 경계

조리안 생성·조회·편집은 **재고 예약·차감·출고 확정·섭취 기록을 하지 않는다.**
재고 충분 여부의 최종 출고 검사는 기존 A 확정 흐름이 담당한다. 조리안 편집 성공을
재료 출고 또는 재고 확보가 끝났다는 뜻으로 해석하지 않는다.

PostgreSQL의 `/api/cooking-sessions/{id}/complete`, `/api/meal-records`, 조리 질문·리포트 등
미이전 경로는 계속 501이다. completion 구현이나 runtime cooking_inventory_event_links DDL은 이번에 실행하지 않는다.
시험에서 completed 상태를 직접 설정한 경우는 편집 거부 검증이며 실제 조리 완료 API를 실행한 것이 아니다.

## 실제 PostgreSQL HTTP 시험

합성 가구 2개, owner/viewer/다른 가구 계정, 합성 공개 영양·레시피와 fake AI를 사용한다.
Flask test_client가 아닌 루프백 HTTP 서버와 requests, PostgreSQL 16.15를 사용했다.

`tests/test_cooking_postgres_http.py`의 검증 항목:

- 1인·다인 레시피 전체량 조정, 생성·재조회, 재료 추가/제거·영양 재계산.
- 401·403, viewer 조회/쓰기 구분, 타 가구의 추천/식사/조리 id 사용 차단.
- 공개·공식 release 변경, 참여자 불일치, 미표시 후보, 새 알레르기, 중량/인분 무효에서 생성 거부.
- unknown/음수/과다/NULL/NaN/Infinity/추가 필드/전체 재료 제거에서 기존 조리안 보존.
- 변경 재료의 알레르기·나트륨·소비기한·최신 보관 지침 재검증.
- 재료 UPDATE 뒤 evidence INSERT 오류를 주입해 조리안·선호·요약이 모두 원복됨을 확인.
- 두 실제 HTTP 요청이 다른 재료를 동시에 수정해도 두 변경이 모두 남음.
- 생성·수정·실패 전후 재고·이벤트·변경 이력·재고 버전·식사 기록·영양 섭취 데이터가 동일함을 확인.
- 004까지 적용된 별도 임시 schema에 기존 추천·식사 참여자·재고를 저장한 후 005 적용·재실행·보존 확인.

## 재현

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:KINI_USE_OPENAI='0'
$env:PYTHONPATH=(Get-Location).Path
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_vision_postgres.ps1 -Python <프로젝트-Python-절대경로>
```

기본 묶음에 조리 시험을 추가했다. 단독 시험은 `-TestPaths tests/test_cooking_postgres_http.py`를 붙인다.
스크립트는 임시 비밀번호·루프백 임의 포트·tmpfs DB를 사용하고 해당 컨테이너를 정리한다.
시험용 schema도 정리하며 기존 컨테이너·실제 DB·개인 데이터는 사용하지 않는다.

일반 회귀는 `KINI_DB_BACKEND=sqlite`에서 `python -m pytest -q --tb=short`,
JavaScript는 `node --test tests/ui/*.test.cjs`로 실행한다. 최종 수치는 아래에 기록한다.

## 최종 직접 실행 결과

| 시험 | 결과·조건 |
| --- | --- |
| PostgreSQL 전체 | **146 passed / 123.54초**, 실제 PostgreSQL 16.15·루프백 HTTP·합성 데이터 |
| 이번 조리 세션 | 위 묶음 중 **26개**: 실제 HTTP 25개 + 004→005 업그레이드/재실행/보존 1개 |
| 기존 PostgreSQL | 위 묶음 중 재고·가족·추천 N1~N3 **120개** |
| 일반 전체 회귀 | **290 passed, 146 skipped / 240.35초**, SQLite·AI 테스트 대역. Chrome E2E 13개 포함 |
| JavaScript UI | **35 passed** |
| diff 검사 | `git diff --check` 통과 |

일반 시험의 146개 skip은 PostgreSQL 환경 전용 시험이며 위 실제 PostgreSQL 묶음에서 모두 실행했다.
실행 시간은 이 PC의 검증 소요 시간으로 운영 성능·지연 benchmark가 아니다.
실제 Supabase·외부 AI·사용자 가족 데이터를 사용하지 않았다. 임시 컨테이너·schema 정리를 확인했다.

## C 담당자 및 Supabase 적용

- C 파일은 수정하지 않았다. 위 네 호출도 기존 클라이언트 `request(..., authenticated=true)`로 토큰을 전달해야 PostgreSQL에서 연결된다.
- 화면에 표시하는 ingredients.quantity_g는 이번 조리의 전체 g, nutrition_per_serving은 1인분이다.
- 실패 응답에서 기존 조리안을 유지하고, 401은 재로그인·403은 권한 안내로 처리한다.
- 이번 단위는 서버의 실제 HTTP 검증이다. 모바일 화면의 PostgreSQL 전 구간 연결 완료를 뜻하지 않는다.
- 실제 Supabase/AWS 적용·원본 데이터 이전·OpenAI/Ollama 호출·카메라 실행은 수행하지 않았다.
- 해당 브랜치의 migration 실행기는 001~005를 적용한다. 재웅님은 승인된 적용 SHA와 실제 프로젝트 schema/역할/RLS를 확인한 뒤 범위를 정한다.
