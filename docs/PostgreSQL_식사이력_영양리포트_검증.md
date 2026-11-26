# PostgreSQL 식사 이력·영양 리포트 조회

작업·검증일: 2026-11-26 (KST). 기준 main: `73f6847` (PR #36 병합).
작업 브랜치: `codex/postgres-meal-reports`.

## 이번 단위

기존 화면 리포트는 탭 메모리의 조리 완료 결과를 표시하며 서버 이력 조회 API가 없었다.
이번에는 PostgreSQL에 저장된 식사·구성원별 실제 섭취 스냅샷을 읽는 API 세 개를 추가했다.
화면·스타일·UI 테스트는 변경하지 않았다. C 담당자가 아래 GET 계약을 연결해야 재접속 후 이력이 화면에도 나온다.
기존 직접 쓰기 `POST /api/meal-records`는 PostgreSQL에서 계속 미지원(501)이며,
기록 생성은 A 확정 출고 → B 조리 완료 흐름을 사용한다.

| 요청 | 응답 |
| --- | --- |
| `GET /api/meal-records` | 날짜·구성원별 식사 이력, 실제 섭취량·영양 합계, 페이지 정보 |
| `GET /api/meal-records/{record_id}` | 같은 가구의 식사 상세와 구성원별 실제 섭취량 |
| `GET /api/reports/nutrition` | 기간 전체·날짜별·구성원별 다섯 영양소 합계 |

모든 요청은 `Authorization: Bearer <가구 세션>`을 사용한다.
가구는 서버가 세션에서 확인하며 선택적 `household_id`가 있으면 세션과 일치해야 한다.
owner/editor/viewer 모두 읽을 수 있다. 응답은 `Cache-Control: no-store`, `Pragma: no-cache`다.
헤더 누락·무효/만료 세션은 401, 다른 가구/구성원 요청은 403,
없는 식사 또는 타 가구 식사 상세는 동일한 404, 잘못된 필터는 400이다.
기존 A/조리 완료 API의 403 세션 계약을 변경한 것은 아니다.

## 날짜·페이지 계약

- 이력·리포트 기본값은 `period=week`: 한국 시간 오늘을 포함한 최근 7일이다. 달력상의 월~일 주간이 아니다.
- `period=today`: 한국 시간 오늘.
- `period=custom&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`: 양 끝 날짜 포함, 최대 366일.
  두 날짜만 보내도 custom으로 처리한다. today/week와 날짜를 섞으면 400이다.
- 선택적 `member_id`: 해당 가구 구성원만 허용. 이력의 items와 합계도 선택한 구성원으로 한정한다.
- 이력만 `limit` (기본 20, 1~100), `offset` (기본 0, 0~1,000,000)을 받는다.
- 같은 시간은 record_id 역순, 다른 시간은 실제 섭취 시각 역순이다.
  `total`, `has_more`를 함께 반환한다. 페이지를 읽는 사이 새 기록이 생기면 offset 페이지는 달라질 수 있다.
- 중복 쿼리 키·알 수 없는 필드·역전 기간·잘못된 날짜는 400이다.
- UTC/다른 offset은 Asia/Seoul로 변환한다. 기존 offset 없는 시각은 KST로 해석한다.
  해석할 수 없는 시각은 기간 조회에서 제외하고 `excluded_invalid_timestamp_count`로 알린다.
  이 수는 해당 가구/구성원 전체 이력 중 잘못된 시각의 개수로, 어느 날짜에 속하는지는 추정하지 않는다.
  상세에는 남겨 두되 `meal_date`/`eaten_at_kst`는 null, 품질은 incomplete다.

## 영양과 품질

`nutrition_intakes`의 저장값을 합산한다. 현재 레시피·공개 카탈로그를 다시 읽어 영양을 계산하거나
조리 인분을 또 곱하지 않는다. 1.5인분·0인분 같은 실제 섭취가 그대로 반영된다.

`nutrition_totals`와 각 item의 `nutrition`은 energy_kcal(kcal), protein_g(g), carbohydrate_g(g),
fat_g(g), sodium_mg(mg) 다섯 항목이다. 응답의 `units`도 단위를 명시한다.
현재 저장되지 않는 식이섬유·의료적 평가·개인 권장량 충족률은 만들지 않는다.

- 완전한 기록: `data_quality=recorded`.
  이는 저장 항목의 완전성을 뜻하며, 공식 영양 검증·의료적 정확성이나 실제 섭취 측정을 보증하지 않는다.
- 기록 없는 기간/날짜/구성원: `empty`, 합계는 0.
- 영양소 누락·무효 값: 해당 영양 합계는 null, `incomplete`.
  부분적으로 확인된 합계는 `known_nutrition_totals`로 분리한다. 이 값만으로 총 섭취량을 확정해서는 안 된다.
- 0인분은 정상 기록이며 0 영양값이다. 미입력을 0으로 취급하지 않는다.
- `meal_record_count`는 기록 수, `consumed_meal_count`는 양수 섭취가 있는 식사 수다.
  `intake_item_count`, `zero_serving_item_count`, `total_servings`, `incomplete_item_count`를 별도로 제공한다.
- 식사 이름은 연결된 추천 응답의 당시 이름을 우선한다(`recipe_name_source=recommendation_snapshot`).
  구형 기록에 스냅샷이 없으면 현재 이름임을 `current_catalog`로 표시한다. 이름도 없으면 unavailable이다.
- 원본 recommendation result/profile/inventory JSON은 응답하지 않는다.
- 가구 재고·A 이벤트·버전·식사·섭취 기록을 쓰거나 수정하지 않는다.
  PostgreSQL은 READ ONLY 트랜잭션, SQLite는 기존 초기화 이후 query_only를 사용한다.
  SQLite의 기존 open_db 초기화 DDL까지 제거한 것은 아니다.

## DB와 적용

`007_meal_reporting_indexes.sql`은 가구-세션, 세션-기록, 구성원-기록 조회 인덱스만 추가한다.
001~006을 수정하거나 기존 기록/영양을 보정하지 않는다. 명시적 migration 실행기에 포함된다.
서버 요청에서 PostgreSQL DDL·seed를 실행하지 않는다.

현재는 기존 TEXT 날짜의 여러 offset/구형 형식을 정확히 처리하기 위해 **해당 가구의 기록 메타데이터를 읽고
Python에서 KST 기간 필터링**한다. 영양 행은 선택된 기록 ID를 200개씩 묶어 읽는다.
기간 366일·페이지 100 제한이 가구 전체 메타데이터 스캔량까지 제한하는 것은 아니다.
데모 규모의 구현이며 대규모 운영 전 시각 컬럼 정규화와 SQL 기간/커서 조회가 필요하다.
성능·부하 수치는 이번에 측정하지 않았다. 개인정보 캐시나 외부 AI 호출을 추가하지 않았다.

## 검증과 재현

`tests/test_meal_reporting_postgres.py`: 합성 가구·fake AI, 실제 루프백 HTTP와 PostgreSQL 16.15.

- 추천 → 조리 → A 확정 → B 완료 → 이력/상세/리포트 읽기.
- 다인 실제 섭취·0인분·기간/개인 합계와 현재 카탈로그 변경 후 스냅샷 불변.
- KST 자정 직전·직후, UTC offset, 구형 naive 시각, 최근 7일 빈 날짜와 페이지 순서.
- 인증·viewer 읽기·가구/구성원 경계·타 가구 상세 비노출·입력 필터 거절.
- 누락 영양 null 처리, 손상된 타 가구 item 비노출, 잘못된 시각 표시.
- 반복 조회 후 재고/이벤트/식사/섭취 불변 및 READ ONLY가 실수로 삽입한 쓰기를 거절.
- 006 DB의 기존 이력·섭취·재고를 보존하는 007 적용/재실행.

SQLite 호환과 legacy 기록 시험은 `tests/test_meal_reporting_sqlite.py`다.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:KINI_USE_OPENAI='0'
$env:PYTHONPATH=(Get-Location).Path
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_vision_postgres.ps1 -Python <프로젝트-Python-절대경로>
```

단독 실행은 `-TestPaths tests/test_meal_reporting_postgres.py`를 덧붙인다.
검증 결과:

- PostgreSQL 16.15 기존 회귀 183개 + 신규 조회/업그레이드 26개 통과 (고유 시험 합계 209개).
  전체 묶음 재실행에서는 208 passed, 신규 업그레이드 시험의 인덱스 확인 SQL 바인딩 오류 1건이었다.
  해당 시험만 수정한 뒤 신규 모듈 전체를 다시 실행해 **26 passed / 39.53초**를 확인했다.
  이 마지막 실행을 전체 209개의 단일 실행 통과로 표현하지 않는다.
- 일반 전체: **294 passed, 209 skipped / 255.73초**. 기존 Chrome E2E 13개 포함.
  skip은 별도 실행한 PostgreSQL 전용 시험이다. SQLite 신규 호환 시험 4개도 포함한다.
- JavaScript: **35 passed**. C 파일은 변경하지 않았다.
- `git diff --check` 통과. 자신의 테스트 컨테이너와 업그레이드용 schema 정리 완료.

실제 Supabase migration·데이터 이전·운영 역할/RLS·AWS 배포·실제 AI·카메라·C 화면 연동은 이번 검증 범위 밖이다.
