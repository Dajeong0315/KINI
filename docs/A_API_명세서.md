# A 데이터 플랫폼 API 명세서

- 버전: v1 / 작성일: 2026-11-25 / 담당: A
- 기준 코드: `app/services/data_status_api.py`, `inventory_events.py`, `local_identity.py`
- 범위: 로컬 데이터 상태·인식 이벤트·재고 API 및 A 내부 연동 인터페이스
- 추천·조리·섭취·AI handler는 B 담당이며 이 문서에서 신규 구현으로 취급하지 않는다.
- 공통 문서 진입점: [API 계약서](API_계약서.md)

## 1. 공통 규칙

| 항목 | 계약 |
|---|---|
| 서버 | 로컬 Flask, 기본 개발 주소 `http://127.0.0.1:5000` |
| 본문 | 요청/응답 JSON, 요청 `Content-Type: application/json` |
| 인증 | 상태 조회 외 `Authorization: Bearer <LOCAL_SESSION_TOKEN>` |
| 토큰 발급 | 현재 `local_identity.login()` 내부 함수. HTTP 로그인 endpoint는 미구현 |
| 권한 | 가구는 서버가 세션에서 결정. viewer는 조회만, owner/editor는 변경 가능 |
| 식별자 | event_id·inventory_item_id·ingredient_id는 로컬 DB 정수 ID. 외부 식품 ID와 혼용 금지 |
| 재전송 | client_event_id UUID를 같은 논리 이벤트의 모든 재시도에 유지 |
| 시각 | last_applied_at은 SQLite UTC 문자열. 화면에서 Asia/Seoul로 변환 |
| 수량 | 확인 수량은 JSON number의 g 단위. 단위 추정·g/mL 자동 혼용 없음 |
| 추가 필드 | 이벤트 수신/확인 본문은 정의되지 않은 필드 거절 |

예시 토큰은 실제 비밀값이 아닌 자리표시자다. 토큰·가족 데이터는 Supabase·OpenAI·로그에 넣지 않는다.
JSON의 ID·시각·재고 수치는 합성 예시이며 DB의 실제 행 존재를 뜻하지 않는다.
Bearer는 현재 구현 계약이다. 향후 쿠키 인증으로 변경하면 C와 CSRF 처리까지 합의해 계약 버전을 올린다.

## 2. 구현된 HTTP API

| ID | Method | 경로 | 인증 | 성공 |
|---|---|---|---|---|
| A-API-01 | GET | `/api/data/status` | 없음 | 200 |
| A-API-02 | POST | `/api/data/inventory/events` | 로컬 세션·쓰기 | 200 |
| A-API-03 | POST | `/api/data/inventory/events/{event_id}/confirm` | 로컬 세션·쓰기 | 200 |
| A-API-04 | POST | `/api/data/inventory/events/{event_id}/reject` | 로컬 세션·쓰기 | 200 |
| A-API-05 | GET | `/api/data/inventory` | 로컬 세션·조회 | 200 |

### A-API-01 데이터 상태

요청 body와 query parameter 없음. 서버가 읽는 것은 **로컬** release pointer다.

```json
{
  "dataset_mode": "private",
  "releases": [],
  "catalog_projection": "not_connected_to_recommendation_engine",
  "supabase": {
    "purpose": "public_food_catalog_and_daily_metrics",
    "automatic_export_enabled": false,
    "catalog_sync_enabled": false
  },
  "authentication": {
    "repository_available": true,
    "http_enforced": false,
    "inventory_events_http_enforced": true
  }
}
```

| 필드 | 의미 |
|---|---|
| dataset_mode | private 또는 demo. 인증·암호화 완료 여부가 아님 |
| releases[] | 각 행은 source_code, release_id(UUID 문자열), cache_version(양의 정수) |
| catalog_projection | 현재 외부 카탈로그→추천 엔진 반영이 연결되지 않았음을 표시 |
| automatic_export_enabled | 자동 운영 지표 전송 비활성 |
| catalog_sync_enabled | Supabase→로컬 자동 카탈로그 동기화 비활성 |
| http_enforced | 기존 API 전체에는 아직 인증이 적용되지 않음 |
| inventory_events_http_enforced | 이 문서의 보호된 A API에는 인증 적용 |

이 응답은 ThinQ 연결 상태·실시간 Supabase ping·재고 최신성 보증이 아니다.
DB 오류는 현재 별도 JSON 래핑 없는 500일 수 있다. C는 일반 상태 조회 실패를 표시한다.

### A-API-02 인식 이벤트 수신

```json
{
  "client_event_id": "550e8400-e29b-41d4-a716-446655440000",
  "source": "mock",
  "action": "stock_in",
  "detected_label": "두부",
  "confidence": 0.99
}
```

| 필드 | 필수 | 형식·제약 |
|---|---|---|
| client_event_id | 예 | UUID. 프레임마다 새 ID를 만드는 방식은 금지하고 출입 이벤트 단위로 유지 |
| source | 아니오 | mock/yolo/manual. 기본 mock. 모델 실행 여부를 서버가 증명하는 값은 아님 |
| action | 예 | stock_in/stock_out. 검출→추적→방향 판정은 인식 모듈 책임 |
| detected_label | 예 | 문자열 길이 1~100. 로컬 전용이며 표준 재료 ID로 자동 확정하지 않음 |
| confidence | 아니오 | null 또는 유한한 0~1 숫자. 기본 null. 자동 반영 기준으로 사용하지 않음 |

household_id, quantity_g, 이미지·영상·파일 경로 등 추가 필드는 400으로 거절한다.

```json
{
  "event_id": 1,
  "client_event_id": "550e8400-e29b-41d4-a716-446655440000",
  "source": "mock",
  "is_demo": true,
  "action": "stock_in",
  "detected_label": "두부",
  "confidence": 0.99,
  "status": "pending_confirmation",
  "requires_confirmation": true,
  "inventory_item_id": null
}
```

status는 pending_confirmation/applied/rejected. is_demo는 source=mock 여부다.
신규 수신은 pending이며 재고를 변경하지 않는다. 동일 UUID·동일 정규화 본문은 저장된 이벤트의
**현재 상태**를 반환한다. 이미 applied라면 재수신 응답도 applied일 수 있다. 다른 본문은 409.

### A-API-03 재고 반영 확인

path event_id는 A-API-02가 반환한 ID다. 입고와 출고 모두 표준 ingredient_id를 사용한다.

입고 예:

```json
{"ingredient_id":4,"quantity_g":200,"expiry_date":"2027-01-01"}
```

출고 예:

```json
{"ingredient_id":4,"quantity_g":100,"inventory_item_id":12}
```

| 필드 | 필수 | 형식·제약 |
|---|---|---|
| ingredient_id | 예 | 양의 JSON 정수, 문자열·bool 불가. 활성 재료 ID여야 함 |
| quantity_g | 예 | 0 초과~1,000,000 이하의 유한한 JSON number. 문자열·bool 불가 |
| inventory_item_id | 출고만 예 | 양의 JSON 정수. 같은 가구·재료의 재고여야 함. 입고는 생략 또는 null |
| expiry_date | 아니오 | YYYY-MM-DD 또는 null. 출고에는 생략/null만 허용 |

성공은 A-API-02의 이벤트 응답 형식으로 status=applied, requires_confirmation=false,
inventory_item_id가 채워진다. 입고는 매번 새 lot를 만들며 기존 lot에 자동 합산하지 않는다.
출고 잔량 초과는 400이며 이벤트·수량·버전·감사 기록이 모두 롤백된다.
같은 확인 본문 재시도는 중복 반영하지 않고 200, 다른 본문은 409.

**requires_confirmation=false는 재고 변경 승인만 의미한다.** 새 입고의 data_status는
needs_confirmation이며 라벨·알레르기·영양·신선도 안전 판정을 대신하지 않는다.
expiry_date의 날짜 형식은 검사하지만 과거 날짜를 입고에서 자동 거절하지 않는다.
보관/섭취 적합성은 B의 기존 정책과 별도로 확인해야 한다.

### A-API-04 이벤트 거절

body 없음. 현재 서버는 body를 사용하지 않는다.

```json
{"event_id":1,"status":"rejected","requires_confirmation":false}
```

pending→rejected, rejected 재요청은 200. applied 거절은 409.
이미 반영한 재고를 보정하려면 별도 stock_in/stock_out 이벤트를 생성한다.

### A-API-05 가구 재고 스냅샷

body/query 없음. 자신의 세션 가구만 조회한다.

```json
{
  "items": [{"id":12,"ingredient_id":4,"name":"두부","quantity_g":200,"expiry_date":"2027-01-01","data_status":"needs_confirmation"}],
  "inventory_version": 1,
  "last_applied_at": "2026-11-25 03:00:00",
  "pending_event_count": 0,
  "source": "local_sqlite",
  "cloud_inventory_export_enabled": false
}
```

expiry_date/last_applied_at은 null일 수 있다. 현재 목록은 페이지 나눔 없이 0g 재고도 포함한다.
inventory_version은 **A 확인 API로 적용한 변경만** 센다. 기존 재고 추가 handler나 수동 SQL의
수정까지 추적하는 전역 버전이 아니다. 목록·버전·대기 수의 조회를 하나의 read transaction으로
고정하는 기능도 아직 없으므로 동시 변경 중 일관된 특정 시점 스냅샷을 보장하지 않는다.
버전은 UI 갱신 힌트로 쓰고 추천의 안전성·최신성 보증으로 쓰지 않는다.
데모 구분은 데이터 상태 API의 dataset_mode 및 인식 이벤트의 is_demo와 함께 표시한다.

## 3. 공통 오류와 C 처리

| HTTP | 실제 error 값 | C 처리 |
|---|---|---|
| 400 | 재료·수량·이벤트 형식을 확인하세요. | 입력 수정, 재고 변경 성공 표시 금지 |
| 401 | 로컬 로그인 세션이 필요합니다. | 로그인/세션 확보 안내 |
| 403 | 세션 또는 가구 권한을 확인하세요. | 재인증 또는 권한 안내. 다른 가구 ID로 재시도 금지 |
| 404 | 이벤트를 찾을 수 없습니다. | 가구/화면 상태 재조회. 타 가구 이벤트 존재도 노출하지 않음 |
| 409 | 이벤트 ID 또는 반영 상태가 충돌합니다. | 성공으로 덮지 말고 상태 재확인 |
| 500 | JSON error 보장 없음 | 일반 실패 안내. 무제한 자동 재시도 금지 |

```json
{"error":"이벤트 ID 또는 반영 상태가 충돌합니다."}
```

현재 별도 기계용 error_code는 없다. 입력 형식 검증이 DB 세션 조회보다 먼저인 경로에서는
잘못된 토큰과 잘못된 본문을 함께 보내면 400이 먼저 반환될 수 있다.
네트워크 응답 유실 시 동일 UUID와 동일 확인 본문으로 재시도한다.
event_id별 상세 GET·대기 이벤트 목록 API는 아직 없으므로 이미 만든 ID를 C가 화면 상태에서 보존해야 한다.

## 4. A 내부 인터페이스 — HTTP endpoint가 아님

| 모듈/함수 | 입력·결과 | 구현 상태 |
|---|---|---|
| local_identity.create_account | 가구·계정명·비밀번호 → 계정 UUID | 구현. 로컬 관리/테스트 전용, HTTP 가입 없음 |
| local_identity.login/authorize/revoke | 로컬 토큰 발급·가구/역할 확인·폐기 | 구현. 기본 1시간/최대 24시간, 토큰 해시 저장 |
| create_meal_for_session | 토큰·참여자·식사 종류 → 식사 ID | 구현. B handler에는 미연결 |
| catalog_repository.stage/validate | source·정규화 rows·revision → release/검증 결과 | 로컬 구현 |
| catalog_repository.publish/rollback | 검증/권리 승인 후 pointer 변경, cache version 증가 | 로컬 구현. 원격 writer는 예정 |
| SupabaseMetricsRepository.write_daily_snapshot | 고정 4필드 일별 절대 합계 → REST upsert | adapter 구현. 실제 키 연결/자동 집계 미실행 |

지표 allowlist: metric_date, metric_code, sample_count, total_value.
metric_code: recommendation_latency/recommendation_success/api_error/cache_hit/cache_miss/import_success/import_failure.
sample_count는 1 이상 정수, total_value는 0 이상 유한값. 지연 total_value 단위는 ms 합계다.
단일 writer가 절대 합계를 보내야 하며, 증분 count를 같은 날짜에 덮어쓰면 안 된다.
추가 개인정보 필드는 네트워크 호출 전에 차단한다.

## 5. CLI 운영 명세

`scripts/init_database.py [--path PATH] [--demo]`: 새 DB만 생성. 기본은 빈 private DB.
기존 파일 덮어쓰기는 거절한다. DB 파일·백업은 Git에서 제외한다.

```powershell
python scripts/import_catalog.py --db app/data/kini.sqlite3 stage --source mfds_nutrition --revision 2026-11-25 --input public-foods.json
python scripts/import_catalog.py --db app/data/kini.sqlite3 validate --release RELEASE_UUID
python scripts/import_catalog.py --db app/data/kini.sqlite3 publish --release RELEASE_UUID
python scripts/import_catalog.py --db app/data/kini.sqlite3 rollback --source mfds_nutrition --release PREVIOUS_RELEASE_UUID
```

stage source: mfds_nutrition/mfds_recipe/10000recipe. format 기본 normalized,
mfds_recipe는 `--format cookrcp01`로 저장한 공식 응답 JSON 정규화 지원.
validate는 통과 시 exit 0, 격리 시 exit 1. 이용 조건 승인 UI/CLI는 없고 운영자가 근거 확인 후 별도 관리한다.
이 CLI는 SQLite용이며 Supabase 자동 upload/download 기능이 아니다.

## 6. 후속 계약 — 현재 호출 금지

반복 조회·cache provider의 내부 계약과 TTL/무효화/공개 데이터 allowlist는
[반복 패턴 및 캐시 설계](A_반복패턴_및_캐시_설계.md)에 추가했다.
캐시 전용 HTTP endpoint는 추가하지 않았고, 현재 응답이 cache hit를 제공한다고 가정하지 않는다.

가상 기기 목록/상태, 로그인 HTTP, 대기 이벤트 목록/상세, 카탈로그 검색·원격 동기화,
Redis 상태/TTL·무효화 API는 예정이다. 경로·schema는 아직 확정하지 않았으므로
C는 기존 설명의 `/api/devices` 등을 구현된 API로 가정하지 않는다.
B 상태 recommendations_available/no_safe_recommendation/privacy_blocked/api_error/stale_unvalidated는
추천 계약에서 유지하고 A 이벤트 상태 pending_confirmation/applied/rejected와 혼용하지 않는다.

## 7. 검증 근거

tests/test_inventory_events.py, tests/test_data_platform.py.
확인 항목: 인증, 가구 격리, 추가 필드 차단, 형식/수량 검사, 재시도, 거절, 원자성,
동시 잔량 초과 차단, release/누락값, 지표 개인정보 차단.
전체 실행 결과는 [개발 현황](A_개발현황.md)에 기록한다.
C의 화면 mock은 `tests/fixtures/api_contract/a_*.json`을 사용한다. 실제 API 연결 성공 증거로 취급하지 않는다.
