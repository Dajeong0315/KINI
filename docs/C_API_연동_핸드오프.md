# C 화면 연동용 서버 API 핸드오프

기준: 현재 `feature/ai-privacy-openai-chain` 구현. 이 문서는 화면 구현용 실제 동작 계약이며, `API_계약서_v1_초안.md`의 AI 외부 전송 설계와 충돌할 때에는 이 문서의 HTTP/API 동작을 우선한다.

## 공통 원칙

- JSON 요청은 `Content-Type: application/json`으로 보낸다. 입력 형식 검증 실패는 `400`과 `{"error":"요청 값의 형식을 확인해 주세요."}`, 업무 규칙 위반은 `400`과 `{"error":"요청을 처리할 수 없습니다. 입력과 제한 조건을 확인해 주세요."}`를 반환한다. 화면은 오류 문구가 아닌 HTTP 상태와 추천 응답의 `status`를 기준으로 처리한다.
- 추천 API는 **처리 성공 여부와 무관하게** 유효한 요청이면 `201`이다. `no_safe_recommendation`, `privacy_blocked`, `api_error`는 HTTP 실패가 아니라 본문의 종단 상태다.
- 기본 AI는 네트워크를 쓰지 않는 결정적 테스트 더블이다. `KINI_USE_OPENAI=1`을 명시하지 않는 한 OpenAI 요청/비용은 발생하지 않는다.
- `data_quality: "demo_data"`, `nutrition_confidence: "데모 추정치"`, `data_provenance_summary`의 `demo/local` 표시는 데모 데이터다. 공식 import가 승인 매핑된 경우에만 후보 `nutrition_confidence`가 `official imported (explicit mapping)`이 될 수 있다. 현재 데모 가구/재료/레시피는 실제 사용자·재고 데이터가 아니다.

## 화면 흐름에서 현재 사용 가능

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

응답은 `{"record_id": 1, "nutrition_per_serving": {...}, "ingredients": [...]}`이다. `items`는 조리 세션 참여자를 중복 없이 선택해야 하며, 먹지 않은 참여자는 `servings: 0`으로 기록하거나 생략할 수 있다. 0인분은 선호 학습에 반영하지 않는다. 완료 시 확정된 레시피 전체 재료량을 확인된 로컬 재고에서 소비기한이 빠른 순으로 한 번 차감한다. 차감된 행의 수량 단위는 g으로 정리된다. 재고가 부족하거나 검증에 실패하면 차감과 식사 기록 모두 저장되지 않는다. 완료된 세션은 다시 변경하거나 완료할 수 없고 `400`이다. 외부 ThinQ 재고에는 반영되지 않는다.

하위 호환 용도로 `POST /api/meal-records`도 남아 있다. 이것은 독립 섭취 기록이며 조리 세션의 재료 변경 영양을 반영하지 않는다. 6개 시나리오의 추천→조리→섭취 흐름에서는 `/complete`만 사용한다.

## 수정 또는 계약 합의 필요

- 조리 세션 없음, 완료된 조리 세션, 안전 제한 위반이 같은 `400`/일반 오류 문구다. C는 현재 HTTP `400`을 공통 오류로 표시해야 한다. `404`/`409` 및 기계 판독 오류 코드를 도입하려면 화면과 합의 후 버전 필드를 추가해야 한다.
- `POST /api/recommendations`의 종단 상태도 `201`이다. HTTP `5xx`로 AI 오류를 판정하면 안 되며 반드시 `status`를 확인해야 한다.
- 후보의 수치형 영양 필드는 데모 추정치일 수 있다. 건강 판단·의료 안내 표현으로 고정 표시하지 말고 `data_quality`/`requires_confirmation` 경고를 함께 표시한다.
- 음성 추천은 로컬 STT/Ollama 해석에 의존하며, `POST /api/voice/recommendations`의 multipart 계약은 6개 핵심 화면의 기본 연동 범위에서 제외한다.

## 아직 미구현 또는 A와 조율할 항목

- 인증, 사용자별 가구 선택, 권한, 삭제/내보내기 API는 미구현이다. 현재 `/api/households/demo`와 기본 가구 ID 1은 데모 전용이다.
- 실제 재고/사용자/공식 영양·알레르기 데이터의 공급 상태는 A 담당 범위다. C는 `data_quality`, `warnings`, `requires_confirmation`, `data_provenance_summary`를 표시하고 데모 여부를 숨기지 않는다.
- 조리 재료 응답은 ID와 g만 제공한다. 재료 표시명·검색·재고 입력 화면은 A 데이터 API인 `GET /api/ingredients/search?q=...`, `POST /api/inventory/normalize`, `POST /api/inventory/items`와 데이터 확정 정책을 별도 조율해야 한다.
- 조리 질문의 RAG 출처는 데모 문구이며 검증된 외부 근거가 아니다. 안전/의료 판단 UI로 사용하지 않는다.

## 이번 검증 범위

`tests/test_b_api_handoff.py`는 외부 AI 없이 테스트 더블로 추천 성공, 추천 불가, 개인정보 차단, AI 오류와 추천→조리 세션→재료 변경→섭취 완료를 검증한다. 재료 변경 후 영양 재계산, 실패 시 롤백, 재고 차감과 중복 완료 방지 및 0인분도 확인한다.
