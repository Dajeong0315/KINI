# C 화면 연동용 서버 API 핸드오프

기준 계약은 C와 합의한 `API_계약서_v1_초안.md`이다. 이 문서는 현재 서버 구현의 HTTP 동작과 화면 연동 예시를 설명한다. 두 문서가 다르면 기준 계약을 바탕으로 차이를 확인하고 문서 또는 구현을 수정한다.

## 공통 원칙

- JSON 요청은 `Content-Type: application/json`으로 보낸다. 입력 형식 검증 실패는 `400`과 `{"error":"요청 값의 형식을 확인해 주세요."}`, 업무 규칙 위반은 `400`과 `{"error":"요청을 처리할 수 없습니다. 입력과 제한 조건을 확인해 주세요."}`를 반환한다. 화면은 오류 문구가 아닌 HTTP 상태와 추천 응답의 `status`를 기준으로 처리한다.
- 추천 API는 **처리 성공 여부와 무관하게** 유효한 요청이면 `201`이다. `no_safe_recommendation`, `privacy_blocked`, `api_error`는 HTTP 실패가 아니라 본문의 종단 상태다.
- 기본 AI는 네트워크를 쓰지 않는 결정적 테스트 더블이다. `KINI_USE_OPENAI=1`을 명시하지 않는 한 OpenAI 요청/비용은 발생하지 않는다.
- `data_quality: "demo_data"`, `nutrition_confidence: "데모 추정치"`, `data_provenance_summary`의 `demo/local` 표시는 데모 데이터다. 공식 import가 승인 매핑된 경우에만 후보 `nutrition_confidence`가 `official imported (explicit mapping)`이 될 수 있다. 현재 데모 가구/재료/레시피는 실제 사용자·재고 데이터가 아니다.

## 화면 흐름에서 현재 사용 가능

### 0. 로컬 가구 로그인 (A 통합 서버)

처음 사용하는 계정은 [계정·가족·구성원 등록 API](A_가족_등록_API.md)의 계정 등록 → 계정 로그인 → 가족 생성 → 가구 로그인 → 구성원 등록 순서를 따른다. 계정 토큰은 가족 생성·소속 가족 목록 전용이며 A 재고/B 완료에는 가구 토큰을 사용한다. 등록 화면은 별도 작업이다.

계정이 연결된 가구 또는 private DB의 `GET /api/members?household_id=...`에는 가구 Bearer 토큰이 필요하다. C 클라이언트는 로그인 토큰·만료를 메모리에 설정한 뒤 인증된 구성원 조회를 수행하며, 조회 실패 시 세션을 폐기한다. 신규 가족은 등록 API로 준비한 뒤 기존 모바일 로그인 화면에 해당 가구 ID를 입력해 접근할 수 있다. 이 연결이 신규 가입·가족 생성 화면을 제공한다는 뜻은 아니다.

`POST /api/auth/login`에 `login_name`, `password`, `household_id`를 보내고 `200` 응답의 `access_token`을 받는다. A 재고 조회·출고 확정과 B 조리 완료에 동일한 `Authorization: Bearer <access_token>`을 붙인다. 토큰 수명은 3600초이며 `expires_at`은 Unix epoch 초다. 서버가 계정의 가구 소속과 역할을 확인한다. 잘못된 비밀번호·없는 계정·미소속 가구는 공통 `401`, 요청 형식 오류는 `400`이다. 상세 예시와 오류·권한 계약은 [A 로컬 로그인 HTTP 계약](A_로컬_로그인_API.md)을 따른다.

### 1. 식사 참여자 세션 생성

`POST /api/meal-sessions`

```json
{"household_id":1,"participant_ids":[3],"meal_type":"dinner"}
```

성공 `201`:

```json
{"session_id":1,"household_id":1,"meal_type":"dinner","participant_ids":[3]}
```

추천 전에 호출해도 되고, 추천 성공 뒤 조리 단계 전에 호출해도 된다. 조리 세션에는 추천의 `participant_ids`와 정확히 같은 참여자 세션이 필요하다.

### 2. 추천 요청과 결과 상태

`POST /api/recommendations`

```json
{
  "household_id": 1,
  "participant_ids": [3],
  "meal_type": "dinner",
  "cook_time_limit_minutes": 30,
  "purchase_allowed": false,
  "desired_menu": null,
  "excluded_ingredients": [],
  "excluded_recipe_ids": []
}
```

필수: `household_id`, 비어 있지 않은 `participant_ids`, `meal_type`. 나머지는 생략 가능하다. `excluded_recipe_ids`는 다음 후보 보기 용도다. 자유 텍스트 `desired_menu` 또는 `{"text":"..."}` 경로는 로컬 해석/개인정보 차단 경로가 있으므로 6개 화면의 기본 연동에는 사용하지 말고 구조화 요청을 사용한다.

유효 요청은 `201`. 공통 필드에는 `request_id`, `status`, `recommendations`, `excluded`, `data_quality`, `warnings`, `requires_confirmation`이 있다.

| `status` | 화면 처리 | 핵심 필드 |
| --- | --- | --- |
| `recommendations_available` | 후보 목록과 조리 시작 버튼 표시 | `recommendations`, `ai_explanation`, `explanation` |
| `no_safe_recommendation` | 빈 상태/조건 수정 유도 | `recommendations: []`, `excluded` |
| `privacy_blocked` | 개인정보 제거 후 구조화 조건으로 재시도 | `recommendations: []`, `message` |
| `api_error` | 재시도 UI 표시, 조리 시작 금지 | `recommendations: []`, `message`, `ai_error_code` |
| `stale_unvalidated` | 재추천 유도, 이전 후보 사용 금지 | 조회 API에서만 가능 |

후보 1개 예시(필드는 더 추가될 수 있음):

```json
{
  "recipe_id": 2,
  "name": "두부 버섯밥",
  "cook_time_minutes": 20,
  "nutrition_per_serving": {"energy_kcal": 222.5, "protein_g": 13.03, "carbohydrate_g": 32.38, "fat_g": 5.33, "sodium_mg": 11.75},
  "nutrition_confidence": "데모 추정치",
  "inventory_usage": [],
  "purchase_missing_g": 0,
  "data_quality": "demo_data",
  "warnings": ["데모 데이터 또는 확인 필요 경고가 올 수 있습니다."],
  "requires_confirmation": true
}
```

`GET /api/recommendations/{request_id}`는 `200`, 없으면 `404 {"error":"추천 요청을 찾을 수 없습니다."}`이다. 재조회 시 데이터 릴리스가 바뀌었거나 이전 검증 결과이면 `stale_unvalidated`가 될 수 있으므로 그 결과로 조리 세션을 만들지 않는다.

### 3. 조리 세션과 재료 변경

`POST /api/cooking-sessions` (`201`):

```json
{"meal_session_id":1,"recommendation_request_id":12,"recipe_id":2}
```

추천이 `recommendations_available`이고 `ai_explanation`이 있으며, 해당 추천에서 실제로 표시된 후보만 시작할 수 있다. 응답에는 `id`, `status: "active"`, `ingredients`(각 `{ingredient_id, quantity_g}`), `nutrition_per_serving`, `nutrition_confidence`, `warnings`, `requires_confirmation`, `data_quality`가 포함된다.

`POST /api/cooking-sessions/{id}/ingredients` (`200`):

```json
{"ingredient":"버섯","quantity_g":0}
```

수량은 레시피 전체 기준 g이다. `0`은 기존 재료 제거, 양수는 추가/변경이다. `ingredient`는 현재 서버 등록 이름이어야 하며, 단위 필드는 보내지 않는다. 변경 후 동일한 조리 세션 상태와 재계산된 `nutrition_per_serving`을 반환한다. 알레르기/식이제한/소비기한/라벨/영양 정보/나트륨 제한을 통과하지 못하면 `400`이고 저장되지 않는다.

`GET /api/cooking-sessions/{id}`는 `200`으로 최신 상태를 반환한다. 존재하지 않는 조리 세션은 현재 `400`으로 축약된다(아래 계약 보완 항목).

### 4. 섭취 기록 완료

`POST /api/cooking-sessions/{id}/complete` (`201`):

```json
{
  "confirmed": true,
  "eaten_at": "2026-11-25T12:00:00",
  "items": [{"member_id":3,"servings":1}]
}
```

응답은 `{"record_id": 1, "nutrition_per_serving": {...}, "ingredients": [...]}`이다. `items`는 조리 세션 참여자를 중복 없이 선택해야 하며, 먹지 않은 참여자는 `servings: 0`으로 기록하거나 생략할 수 있다. 0인분은 선호 학습에 반영하지 않는다. A 재고 API가 적용되기 전의 단독 서버에서는 확정된 레시피 전체 재료량을 로컬 재고에서 소비기한이 빠른 순으로 한 번 차감한다. 차감된 행의 단위는 g으로 정리되며, 재고 부족이나 검증 실패 시 차감과 식사 기록은 함께 취소된다. 완료된 세션은 다시 변경하거나 완료할 수 없고 `400`이다. 외부 ThinQ 재고에는 반영되지 않는다.

**A 재고 API가 적용된 서버**에서는 같은 가구의 로컬 로그인 `Authorization: Bearer <token>`과 `inventory_event_ids`를 완료 요청에 함께 보낸다. 이 값에는 이번 조리에 사용한, A에서 반영 완료(`applied`)된 `stock_out` 이벤트 ID를 넣는다. 해당 출고들이 조리 재료별 사용량을 모두 포함하면 B는 출고를 조리 세션에 연결하고 조리·섭취만 기록한다. 같은 출고 ID를 다른 조리에서 다시 사용할 수 없다.

카메라 이벤트가 없으면 `ingredients`의 재료 ID와 이번에 쓴 g을 A의 기존 수동 출고 흐름에 전달한다. `POST /api/data/inventory/events`에 `source: "manual"`, `action: "stock_out"`으로 등록하고, 해당 이벤트의 `/confirm`에 실제 재고 행 ID와 중량을 보내 확정한 다음 그 ID로 조리를 완료한다. 냉장고에 남은 양을 돌려놓으면 A에서 별도 입고로 확인한다. 이미 확정된 출고가 있는 재료에 수동 출고를 다시 만들지 않는다.

세션 토큰 누락은 `401`, 권한 불일치는 `403`이다. 출고 확인이 필요하면 `409`와 `code: "inventory_confirmation_required"`, 대상 `ingredients`를 반환한다. 확인 대기·사용량 부족·이미 다른 조리에 연결된 출고는 이 응답으로 안내한다.

하위 호환 용도로 `POST /api/meal-records`도 남아 있다. 이것은 독립 섭취 기록이며 조리 세션의 재료 변경 영양을 반영하지 않는다. 6개 시나리오의 추천→조리→섭취 흐름에서는 `/complete`만 사용한다.

## 수정 또는 계약 합의 필요

- 조리 세션 없음, 완료된 조리 세션, 안전 제한 위반은 일반 `400`으로 처리한다. A 재고 API 적용 후 출고 확인이 필요한 `409`는 위 코드로 구분한다.
- `POST /api/recommendations`의 종단 상태도 `201`이다. HTTP `5xx`로 AI 오류를 판정하면 안 되며 반드시 `status`를 확인해야 한다.
- 후보의 수치형 영양 필드는 데모 추정치일 수 있다. 건강 판단·의료 안내 표현으로 고정 표시하지 말고 `data_quality`/`requires_confirmation` 경고를 함께 표시한다.
- 음성 추천은 로컬 STT/Ollama 해석에 의존하며, `POST /api/voice/recommendations`의 multipart 계약은 6개 핵심 화면의 기본 연동 범위에서 제외한다.

## 아직 미구현 또는 A와 조율할 항목

- A 재고 API가 적용된 서버의 조리 완료는 로컬 세션과 가구 쓰기 권한을 확인한다. 전체 API 인증, 사용자별 가구 선택, 삭제/내보내기는 별도 작업이다. `/api/households/demo`와 기본 가구 ID 1은 데모 전용이다.
- 실제 재고/사용자/공식 영양·알레르기 데이터의 공급 상태는 A 담당 범위다. C는 `data_quality`, `warnings`, `requires_confirmation`, `data_provenance_summary`를 표시하고 데모 여부를 숨기지 않는다.
- 조리 재료 응답은 ID와 g만 제공한다. 재료 표시명·검색·재고 입력 화면은 A 데이터 API인 `GET /api/ingredients/search?q=...`, `POST /api/inventory/normalize`, `POST /api/inventory/items`와 데이터 확정 정책을 별도 조율해야 한다.
- 조리 질문의 RAG 출처는 데모 문구이며 검증된 외부 근거가 아니다. 안전/의료 판단 UI로 사용하지 않는다.

## 이번 검증 범위

`tests/test_b_api_handoff.py`는 외부 AI 없이 테스트 더블로 추천 성공, 추천 불가, 개인정보 차단, AI 오류와 추천→조리 세션→재료 변경→섭취 완료를 검증한다. 재료 변경 후 영양 재계산, 실패 시 롤백, 재고 차감과 중복 완료 방지 및 0인분도 확인한다.
