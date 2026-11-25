# A 사전 적재 및 무료 DB 용량 설계

2026-11-25. 사용자 확정: 무료 Supabase 사용, 식약처 정식 키 설정 후 레시피/영양 전체 수집,
서비스 실행 전에 DB 구성. 변화하는 냉장고 데이터의 적재 공간을 남긴다.

## 1. 사전 구축과 서비스 실행 분리

사전 구축: 식약처 API 전체 페이지 수집 → 로컬 gzip snapshot → 응답 필드 정규화/검증 →
용량 제한 SQL 배치 준비 → Supabase staging 적재 → 행 수/권리 검증 → 명시적 publish.
서비스 실행: 구축한 공개 DB의 정보와 로컬 가족·재고 조건을 결합해 B가 검색/추천한다.
추천 요청에서 식약처나 만개의레시피를 호출하지 않는다.
기존 화면의 외부 API 직접 조회 경로 전환은 B/C 통합 때 별도로 필요하다.

현재 정식 두 API 키는 미설정이며 실제 전체 수집/적재는 시작하지 않았다.
공식 sample 응답 접근 가능 여부만 확인했으며 이를 전체 DB로 적재하지 않았다.
만개의레시피는 이용 가능한 API/제휴/파일 제공 권한 확보 전까지 비워 둔다.

## 2. 500MB 논리 예산

1MB=1,000,000 bytes. 제공량과 물리 disk/WAL 사용량은 구분한다.
현재 화면의 Free를 기준으로 작성했으며 유료 전환·유료 프로젝트 생성은 하지 않는다.

| 구역 | 예산 | 저장/상태 |
|---|---:|---|
| 시스템·스키마 기본 여유 | 30MB | PostgreSQL/system 관련 기본 여유 |
| 식품·영양 카탈로그 | 70MB | 식품 typed rows·영양값·인덱스 포함 |
| 레시피 | 100MB | 레시피·재료 원문·단계·영양·인덱스 포함 |
| 공통 출처·버전·검색 | 30MB | compact provenance와 검색 여유. 벡터 생성은 없음 |
| 냉장고 변화 데이터 예약 | 150MB | 공개 카탈로그 적재가 사용하지 못하게 남김. 원본 개인 이력은 로컬 |
| 운영 집계 | 20MB | 개인/가구 ID 없는 일별 지표 |
| 교체 적재·복구 여유 | 50MB | 갱신/롤백 시 필요한 작업 여유, 자동으로 채우지 않음 |
| 안전 여유 | 50MB | 예기치 않은 증가 대비 |
| 합계 | 500MB | 실제 디스크를 미리 할당하는 방식이 아닌 논리 예산 |

공개 카탈로그는 위 70+100+30=**200MB 공통 pool**로 제한한다.
70/100/30은 계획상 분배이며 테이블별 독립 hard quota는 아니다.
전체 DB 목표 300MB, 경고 350MB, 신규 카탈로그 쓰기 중지 400MB다.
공개 pool이 먼저 소진돼도 중단한다. 자동 요금제 변경이나 무단 기존 이력 삭제를 하지 않는다.
냉장고 예약 150MB의 cloud event schema/권한은 아직 구현하지 않았으며 실제 사용 중인 공간이 아니다.
향후 해당 영역에 넣을 데이터는 비식별 운영/변화 집계로 먼저 검토한다.
개인 가구 재고·입출고 시각·식사 이력은 기존 결정대로 SQLite에 두며 500MB cloud quota와 별개다.

## 3. 실제 적용한 용량 검사

- config/catalog_storage_policy.json에 예산을 기록한다.
- 원격 catalog_storage_policy 및 catalog_capacity_status()/assert_catalog_capacity()를 생성했다.
- 카탈로그 BEFORE INSERT/UPDATE statement trigger에서 전체 DB/pool 크기를 확인한다.
- 준비 SQL은 100행/256KB 이하 payload로 나누고, transaction마다 예상 payload를 별도로 검사한다.
- 예상 증가량은 payload 4배 + 8MB 여유라는 보수적 추정이다. 건수당 실사용량 보장이 아니다.
- 실제 pg_database_size와 pg_total_relation_size에는 테이블·인덱스·TOAST가 반영된다. WAL/오브젝트 스토리지는 별도다.
- 수동 관리자가 한 번에 거대한 INSERT를 하는 경로까지 사후 용량을 완전히 보장하는 것은 아니다.
  운영 적재는 반드시 bounded batch를 사용하고 매 배치 실제 증가량을 다시 확인한다.
- source_records.payload_json은 2KB 이하 compact provenance만 허용한다. API 원본 전체는 로컬 gzip이다.
- 이미지/영상 binary·Base64·원본 음성·전체 페이지 JSON을 cloud DB에 저장하지 않는다.
- active/previous release 2개 유지가 목표지만 자동 삭제/retention job은 아직 없다.
  공간 부족 시 기존 데이터를 지우지 않고 적재를 중단해 별도 보관·정리 후 재개한다.

## 4. 변화 이력 저장 모델

현재 SQLite에는 inventory_events(인식/확인 상태), inventory_items(현재 lot 잔량),
inventory_event_changes(변경 g·변경 전후 값·확인자·시각), inventory_sync_state(버전)가 있다.
동일 이벤트 반복 수신/확인은 중복 행·중복 차감을 만들지 않는다.
모든 프레임이나 매초 전체 냉장고 스냅샷을 누적하지 않고 실제 확인된 변화만 저장한다.

장기 운영 목표는 '현재 상태 + 변경 이벤트 + 필요 시 주기 요약'이다.
스냅샷 주기·원본 이력 보존 일수·요약/압축·외부 백업은 정책 합의 후 구현한다.
현재 자동 삭제·요약은 없다. 150MB로 보관 가능한 일수/건수는 실제 이벤트당 크기 측정 후 산출한다.
인식 원본 이미지와 개인 이력을 cloud 공개 카탈로그와 섞지 않는다.

## 5. API 응답 → DB 컬럼 매핑

### COOKRCP01

| 응답 필드 | cloud 저장 |
|---|---|
| RCP_SEQ | source_records.external_id, catalog_recipes.external_id |
| RCP_NM | catalog_recipes.name |
| RCP_WAY2 / RCP_PAT2 | method / category |
| RCP_PARTS_DTLS | ingredients_text + 미분해 원문 재료 행(mapping_status=unreviewed) |
| MANUAL01~20 | catalog_recipe_steps.step_no/instruction |
| INFO_ENG/PRO/CAR/FAT/NA | catalog_recipe_nutrients의 영양 코드/amount |
| ATT_FILE_NO_MAIN | image_url 문자열. 이미지 파일은 내려받지 않음 |
| 원본 row 정규화 hash | source_records.payload_sha256 |

servings·cook_time은 원본에서 근거를 확보하지 못하면 null이다.
재료 원문에서 중량/알레르겐을 추정하지 않는다. 개별 재료 분해·표준 food mapping은 후속 검토다.
레시피 영양은 ingredient당 100g 정보로 복사하지 않는다.

### FoodNtrCpntDbInfo03 — 실제 응답 fixture 확보 전 초기 mapping

| 응답 필드 | cloud 저장 |
|---|---|
| FOOD_CD | catalog_foods.external_id |
| FOOD_NM_KR / FOOD_CAT1_NM | name / category |
| SERVING_SIZE 또는 NUTRI_SERVING_SIZE + 명시 단위 | basis_amount / basis_unit |
| AMT_NUM1/3/4/6/7/13 | energy_kcal/protein_g/fat_g/carbohydrate_g/sugar_g/sodium_mg |

실제 계정 응답으로 필드/단위 의미를 검증한 뒤 확정한다. 단위 없는 '100'을 100g으로 가정하지 않는다.
누락은 NULL/영양 행 없음, 음수·NaN·알 수 없는 기준량은 격리한다.
현재 준비 도구는 한 행이라도 mapping 실패하면 전체 publish를 중단하고 로컬 격리 보고서를 남긴다.

## 6. 실행 절차

팀 저장소의 .env에 정식 값 설정:

```dotenv
FOODSAFETY_KOREA_API_KEY=<식품안전나라 COOKRCP01 승인 키>
DATA_GO_KR_SERVICE_KEY=<공공데이터포털 식품영양성분 승인 키>
```

값은 채팅·로그·Git에 넣지 않는다. 키 파일은 로컬에만 존재하며 현재 빈 항목으로 준비했다.

```powershell
python scripts/collect_mfds.py --source all
python scripts/prepare_mfds.py --source mfds_recipe --input output/catalog-build/mfds/mfds_recipe --revision YYYY-MM-DD
python scripts/prepare_mfds.py --source mfds_nutrition --input output/catalog-build/mfds/mfds_nutrition --revision YYYY-MM-DD
```

collector는 100건씩 전체 페이지를 순회하고 로컬 페이지 checkpoint에서 재개한다.
인증/호출 한도 오류를 성공으로 저장하지 않으며 totalCount 변경·반복 페이지·불완전 수집은 배포하지 않는다.
권한 키가 없으면 네트워크 호출 전에 exit 2다. 키가 들어 있는 요청 URL/HTTP 오류 원문은 출력하지 않는다.

prepare는 번호순 SQL 배치·파일 SHA256 manifest·validate.sql·publish.sql을 생성한다.
연결된 관리 도구로 번호순 배치를 실행하고 검증/출처 권리 승인 후에만 publish한다.
전체 수집과 전체 cloud 적재는 다르다. 200MB pool을 넘으면 모든 데이터를 억지로 넣지 않고 멈춘다.
어떤 범위를 우선 적재할지는 실제 데이터 크기/분류 분석 후 사용자와 합의한다.
현재 자동 SQL 실행 runner·원격→로컬 downloader·B 추천 조회 통합은 아직 없다.

## 7. 근거와 확인값

- Free 제공량: https://supabase.com/pricing (2026-11-25 확인)
- DB/물리 disk/WAL 구분: https://supabase.com/docs/guides/platform/database-size
- 식약처 recipe schema: https://www.foodsafetykorea.go.kr/api/newDatasetDetail.do?svc_no=COOKRCP01
- 영양 API: https://www.data.go.kr/data/15127578/openapi.do
- 확인 DB 사용량: 약 12.1MB. 실제 식품/레시피 적재 전 값이다.
- 신규 quota/collector/planner tests: tests/test_mfds_batch.py.
- 원격 oversized batch와 초과 예산 거절을 transaction 내 시험 후 rollback했다.
