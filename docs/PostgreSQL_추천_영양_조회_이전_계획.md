# 추천 후보·영양 데이터 조회 PostgreSQL 이전 계획

작성일: 2026-11-26 (KST). **이번 문서는 계획이며 신규 migration·추천 코드 구현은 하지 않았다.**

## 출발점

- main `fffdca4`: Vision 연결부와 PostgreSQL 재고 중복·동시성 검증 완료.
- 코드 커밋 `9d3d526`: 계정·가족·프로필 PostgreSQL 이전 완료, 작성 시점에는 main 미반영.
- 가족 이전 검증: PostgreSQL 74 passed(신규 가족/migration 10개 포함), 전체 SQLite·Chrome 회귀 290 passed/74 skipped, JavaScript 35 passed. 74개 skip은 PostgreSQL 묶음에서 별도 실행해 통과했다.
- 실제 Supabase 적용은 2026-11-27 재웅님께 전달할 별도 작업이다. 다음 개발은 로컬 PostgreSQL·합성 fixture로 진행할 수 있어 이를 기다릴 필요가 없다.

## 한 단위씩 끝내는 순서

| 순서 | 단위 | 종료 지점 |
| --- | --- | --- |
| N1 — 다음 작업 | 승인 카탈로그·레시피·재료 영양 **조회** | 실제 PostgreSQL에서 후보 원본과 영양·출처를 읽고 누락/미승인/캐시 장애를 안전하게 처리 |
| N2 — 후속 | 추천 규칙의 전체 조회·계산 경로 | 가구·참여자·재고·영양·식사 이력·개인화 조건으로 `recommend(..., persist=False)` 실행 및 기존 안전 규칙 회귀 |
| N3 — 후속 | 추천 결과 저장·HTTP 연결 | 추천 요청·후보·노출 이력 저장, 재조회·release 변경 시 stale 처리, 기존 HTTP 계약 유지 |

N1 종료를 “추천 API 전체 PostgreSQL 이전 완료”로 표현하지 않는다.
식사 이력·개인화 의존성을 빈 결과나 데모로 대체해 추천이 완성된 것처럼 만들지 않는다.
조리·섭취·리포트 이전과 실제 모델 호출·배포는 위 세 단위 밖이다.

## 확인한 기존 흐름

`recommendation_engine.recommend` → 레시피/재료/개인 기록 조회 →
`data_quality.inspect_ingredient` → `RecommendationNutritionProvider.get` →
`PublicCatalog.read` → 승인된 공개 카탈로그 또는 명시적인 기존 영양 출처.

| 현재 코드 | 재사용 및 이전 사항 |
| --- | --- |
| `recommendation_provider.py` | 단일 영양 공급 계약 유지. reviewed 연결 + active release + published + approved 모두 확인 |
| `public_catalog.py` | 공개 레코드 조회·검증·캐시 재사용. 캐시 실패 시 PostgreSQL 원본 직접 조회 |
| `catalog_repository.py` | stage/validate/publish/rollback 재사용 검토. 전면 운영 도구 이전은 N1에 묶지 않음 |
| `recommendation_repository.py` | 레시피·구성 재료 조회를 필요한 만큼 저장소 함수로 정리. SQLite 동작 유지 |
| `data_quality.py` | 공식 매핑·알레르기·라벨·재고 품질 검사는 유지. 전체 규칙 연결은 N2 |
| `recommendation_engine.py`, `personalization.py` | 식사 이력·개인화·날짜 계산·스냅샷까지 의존하므로 연결 문자열만 바꿔 완료 처리할 수 없음 |

## N1의 구현 범위

1. 기존 001·002를 보존하고 다음 버전 migration을 추가한다. 예정 이름은
   `003_recommendation_read.sql`이며 아직 생성하지 않았다.
2. 기존 코드가 읽는 레시피·구성 재료·재료 영양 컬럼과 공개 카탈로그 조회 테이블을 준비한다.
3. 레시피의 총 인분·구성 재료 g와 영양의 기준량/단위를 구분해 조회한다.
4. 공개 food/recipe, nutrients/steps, 출처·release·active pointer·검토된 연결을 PostgreSQL에 연결한다.
5. 기존 공개 캐시와 provider 계약을 유지하고 SQLite 회귀를 통과한다.

### 테이블 준비 목록

- 신규/확장: `recipes`, `recipe_ingredients`, `ingredient_nutrition`, 조회에 필요한 기존 `ingredients` 컬럼.
- 공개 카탈로그: `catalog_data_sources`, `data_releases`, `source_records`, `catalog_foods`,
  `catalog_food_nutrients`, `catalog_recipes`, `catalog_recipe_steps`, `catalog_recipe_nutrients`,
  `catalog_ingredient_links`, `catalog_recipe_links`.
- 001에 이미 있는 `active_data_releases`와 `meal_data_context`는 중복 생성하지 않고 필요한 제약을 검토한다.
- 기존 `official_food_items`, `official_nutrient_values`, `ingredient_official_food_mappings`,
  `dataset_releases`, `data_release_pointers`는 별도 공식 import 체계다. 공개 catalog release와 숫자 ID로 합치지 않는다.
  해당 체계의 전체 선정·안전 검사 연결은 N2로 구분한다. N1에서 경유하는 쿼리가 있다면 필요한 읽기 구조만 포함하고 범위를 문서화한다.

## 구현 전에 주의할 호환성

- `catalog_context()`의 `list(row)`는 SQLite Row에서는 값, 현재 PostgreSQL HybridRow에서는 키를 반환한다.
  명시적인 컬럼 값으로 같은 context를 만들고 release 변경 감지를 시험해야 한다.
- `source_records`의 `json_valid`, `INSERT OR IGNORE`, datetime 표현식은 SQLite 전용이다.
  TEXT/JSONB 선택과 Python `json.loads` 반환 형식을 일치시켜야 한다.
- UUID release와 기존 숫자 official release를 구분한다. 캐시 키·JSON 응답에는 정규화된 문자열을 사용한다.
- PostgreSQL numeric/Decimal·Boolean과 기존 Python 숫자 검사의 차이를 확인한다.
  NULL·음수·NaN·Infinity·Boolean을 유효 영양값으로 통과시키지 않는다. 0은 정상 값이다.
- 본문에 source_type=official이라고 쓰는 것만으로 공식 매핑 검증을 통과한 것으로 간주하지 않는다.
- 공개 레시피의 `ingredients_text`를 그대로 조리 가능한 표준 재료·중량으로 간주하지 않는다.
  검토된 연결과 실제 `recipe_ingredients`가 필요하다.

다음 N2/N3에 이미 확인된 의존성: SQLite `date(...,'-7 days')`, `rowid`, `member_id IS ?`,
`lastrowid`, JSON 직렬화·스냅샷 순서, `aggregate()`의 쓰기, meal_records/영양 섭취/개인화 테이블,
추천 요청·후보·피드백 저장. N1에서 이를 임의로 우회하지 않는다.

## N1 완료 기준

- 일회성 실제 PostgreSQL에 migration 최초 적용·기존 002 DB 업그레이드·재실행 통과.
- 작은 합성 fixture로 표준 레시피/재료/g와 승인된 현재 release의 공개 영양값을 조회.
- 미승인 출처·staging/quarantined release·검토되지 않은 연결이 추천 영양 공급자로 사용되지 않음.
- 누락·잘못된 영양 기준·중량 미확정·비유한 값에서 안전한 제외/확인 필요를 반환.
- 기존 정책이 허용하는 demo/verified_product/official fallback의 출처가 유지되고, 개인 DB에 자동 demo seed를 넣지 않음.
- 캐시 miss/hit/장애·승인 철회·release 교체/롤백에서 DB 원본과 일치하고 이전 release가 섞이지 않음.
- SQLite/PostgreSQL에서 같은 fixture의 ID·영양·인분·출처·단위가 일치.
- 개인 재고·가구·가족·건강·토큰이 공개 캐시에 들어가지 않음.
- 기존 추천/provider/cache 회귀 통과. 실제 외부 식품 API·AI·Supabase 호출 없음.

## 재사용할 시험과 실행 준비

기존: `tests/test_recommendation_provider.py`, `tests/test_public_cache.py`,
`tests/test_recommendation.py`, `tests/test_catalog_operations.py`.
이 중 recommend 전체를 호출하는 시험을 DB 환경변수만 바꾸어 PostgreSQL 시험이라고 부르지 않는다.
N1 경계를 직접 검증하는 PostgreSQL fixture/시험을 추가하고, 기존 SQLite 시험은 회귀로 유지한다.

Docker·psycopg·일회성 DB 실행은 `scripts/test_vision_postgres.ps1`의 방식을 재사용한다.
기존 개인 DB·실사용 계정은 사용하지 않는다. 라이브 카탈로그 적재·유료 가입은 하지 않는다.

## 다음 VS Code 세션용 작업 요청

아래를 다음 세션에 전달한다.

```text
KINI의 N1 “승인 카탈로그·레시피·영양 조회 PostgreSQL 이전”만 구현해 주세요.
작업 폴더: C:\Users\aicam\Desktop\Project_3-postgres-concurrency
준비 브랜치: codex/postgres-recommendation-read
먼저 AGENTS.md와 docs/PostgreSQL_추천_영양_조회_이전_계획.md를 읽고 실제 상태를 확인하세요.
현재 브랜치에는 가족·프로필 이전 코드 9d3d526과 준비 문서가 포함됩니다.
원격 main에 가족 PR이 반영됐는지 확인하고 의존 관계를 보존하세요.
main이 앞서면 안전하게 통합하되, 기존 변경을 reset/덮어쓰기하지 마세요.

계획의 N1만 구현하고 실제 로컬 PostgreSQL과 합성 공개 fixture로 검증하세요.
추천 점수·식사 이력·개인화·추천 결과 저장·조리 이전으로 범위를 늘리지 마세요.
미완성 의존성을 데모/빈 값으로 감춰 전체 추천 API가 완료됐다고 표현하지 마세요.
기존 API 계약·SQLite 경로·로컬 규칙 최종 판단·공개 캐시 개인정보 제외를 유지하세요.
C 화면/스타일/UI 테스트, Vision 모델, Supabase/AWS와 외부 API는 수정/호출하지 마세요.

구현 전 변경 범위를 짧게 보고하고, 완료 기준을 만족하면 코드·시험·문서를 로컬 커밋하세요.
원격 push·main 병합·브랜치 삭제는 하지 마세요.
실제 PostgreSQL/SQLite/mock을 구분한 결과·재현 명령·남은 범위를 보고하고 종료하세요.
```
