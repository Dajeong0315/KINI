# API 계약서 — A 데이터 상태 v1

2026-11-25. 이 문서는 공통 계약 진입점과 A 데이터 API 요약이다.
A의 상세 필드·권한·예외·현재 한계는 [A API 명세서](A_API_명세서.md)를 기준으로 한다.
B의 추천/조리 API와 C의 UI 계약은 담당자가 별도로 추가한다.

`GET /api/data/status` — 읽기 전용, 요청 body 없음, 로컬 DB만 조회한다.

성공 HTTP 200 예:

```json
{
  "dataset_mode": "private",
  "releases": [],
  "catalog_projection": "not_connected_to_recommendation_engine",
  "supabase": {"purpose": "public_food_catalog_and_daily_metrics", "automatic_export_enabled": false, "catalog_sync_enabled": false},
  "authentication": {"repository_available": true, "http_enforced": false, "inventory_events_http_enforced": true}
}
```

releases의 각 항목: source_code, release_id(UUID), cache_version(양의 정수).
private는 빈 개인용 DB 모드이며 인증 완료나 암호화 완료를 의미하지 않는다.
demo는 기존 데모 seed DB를 의미한다. 공식 catalog가 있다고 demo 가족이 실제 데이터가 되지 않는다.
Supabase 항목은 목적과 자동 업로드 활성 여부만 알리며 연결 성공을 주장하지 않는다.
기기 동기화 상태는 아직 구현하지 않아 이 응답에 포함하지 않는다.

금지 필드: 사용자/가구/기기 ID, 이름, 알레르기, 영양 목표, 대화, 경로, 토큰, 키.
warnings/requires_confirmation/data_quality는 추천 품질 필드라 이 상태 API에서 생성하지 않는다.
실패: DB 접근/스키마 초기화 실패 시 HTTP 500. C는 상태 확인 실패를 표시하고 기존 추천의 유효성을 추정하지 않는다.
이 endpoint 실패로 재고가 최신이라고 표시하면 안 된다.

인증 API는 아직 제공하지 않는다. local_identity.py는 HTTP endpoint가 아닌 repository 계약이다.
실제 개인정보를 다루는 기존 추천/조리 endpoint 인증 연결은 B와 협의 후 진행한다.

## A 재료 인식·재고 이벤트 API v1

다음 endpoint는 모두 `Authorization: Bearer <로컬 세션 토큰>`을 요구한다.
이 토큰은 Supabase/OpenAI/ThinQ 토큰이 아니다. HTTP 로그인 발급 endpoint는 아직 없으며
local_identity.login() repository에서 발급한 토큰을 사용한다. 브라우저 토큰 보관 방식은 로그인 통합 때 확정한다.
요청에 household_id를 받지 않는다. 세션에서 서버가 가구를 결정한다.
가구별 이력·재고는 로컬 전용이며 외부 클라우드에 전송하지 않는다.

### POST /api/data/inventory/events

```json
{"client_event_id":"550e8400-e29b-41d4-a716-446655440000","source":"mock","action":"stock_in","detected_label":"두부","confidence":0.99}
```

source: mock/yolo/manual. action: stock_in/stock_out. UUID는 출입 이벤트마다 하나를 생성하고
같은 이벤트 재시도 시 유지한다. 새 프레임마다 새 UUID를 만드는 방식은 중복 방지가 아니다.
YOLO가 단순 검출한 프레임을 입출고 이벤트로 바꾸는 tracking/방향 판정은 인식 모듈 담당이다.
quantity_g, household_id, 이미지/영상/파일 경로 등 정의되지 않은 필드는 거절한다.

성공 HTTP 200:

```json
{"event_id":1,"client_event_id":"550e8400-e29b-41d4-a716-446655440000","source":"mock","is_demo":true,"action":"stock_in","detected_label":"두부","confidence":0.99,"status":"pending_confirmation","requires_confirmation":true,"inventory_item_id":null}
```

이 응답은 재고 변경 성공이 아니라 확인 대기 성공이다. confidence가 높아도 무게·소비기한을 추정하지 않는다.
같은 UUID·내용 재전송은 같은 결과, 같은 UUID·다른 내용은 409.

### POST /api/data/inventory/events/{event_id}/confirm

입고 예:
```json
{"ingredient_id":4,"quantity_g":200,"expiry_date":"2027-01-01"}
```
출고 예:
```json
{"ingredient_id":4,"quantity_g":100,"inventory_item_id":12}
```

성공 HTTP 200, 위 이벤트 응답에서 status=applied, requires_confirmation=false, inventory_item_id가 채워진다.
이 requires_confirmation은 **이벤트 반영 확인**이며 재료의 알레르기·라벨·신선도 검증을 뜻하지 않는다.
새 입고 재고의 data_status는 needs_confirmation으로 남는다. 기존 B의 품질 검사를 우회하지 않는다.
입고는 lot별 새 재고를 만들며 item ID를 받지 않는다. 출고는 가구·재료·재고 ID와 잔량을 검사한다.
잔량 초과는 전체 롤백. 같은 확인 내용 재시도는 추가 반영 없음, 다른 확인 내용은 409.
재고 변경·이벤트 완료·inventory_version 증가는 하나의 BEGIN IMMEDIATE 트랜잭션이다.

### POST /api/data/inventory/events/{event_id}/reject

body 없음. pending 이벤트를 rejected로 바꾸며 재고는 변경하지 않는다.
이미 applied인 이벤트는 거절할 수 없고 별도 보정 이벤트를 사용한다.

### GET /api/data/inventory

```json
{"items":[],"inventory_version":0,"last_applied_at":null,"pending_event_count":0,"source":"local_sqlite","cloud_inventory_export_enabled":false}
```

items 각 항목은 id, ingredient_id, name, quantity_g, expiry_date, data_status다.
last_applied_at은 SQLite UTC 시각이며 C가 Asia/Seoul로 변환한다.
이 API는 기존 /api/inventory(JSON 파일)를 변경하지 않는다. C는 새 경로로 전환하고 B와 재고 기준을 통일한다.

공통 실패: 토큰 없음 401, 세션/가구/쓰기 권한 오류 403, 가구 소속 이벤트 없음 404,
잘못된 입력/잔량 부족 400, 멱등 ID·상태 충돌 409. C는 실패 시 재고를 성공으로 표시하지 않는다.
