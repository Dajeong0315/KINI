# API 계약 v1 초안 — AI 추천 안전 체인

> `docs/API_계약서.md`가 현재 없으므로, 기존 Flask handler와 `tests/fixtures/api_contract/recommendation_ai_v1.json`를 바탕으로 역할 B가 제안하는 초안이다. 팀 합의 전에는 canonical 계약으로 간주하지 않는다.

## `POST /api/recommendations`

기존 구조화 요청을 유지한다. `household_id`와 `participant_ids`는 로컬 규칙 엔진 전용이며 OpenAI로 전송하지 않는다. `desired_menu` 같은 자유 텍스트가 있으면 외부 AI 체인은 `privacy_blocked`로 종료한다.

`excluded_ingredients`도 외부 전송용 원문 필드가 아니다. 로컬 `ingredients`의 활성 canonical 항목과 정확히 일치할 때만 로컬 규칙 엔진이 내부 코드로 정규화한다. 외부 payload에는 해당 내부 ID 대신 요청 범위의 `constraint_1` 같은 임시 코드만 보낸다. 일치하지 않는 값은 외부 payload에서 제거하며, 민감정보 패턴이면 `privacy_blocked`로 종료한다.

```json
{
  "household_id": 1,
  "participant_ids": [1, 3],
  "meal_type": "dinner",
  "cook_time_limit_minutes": 30,
  "purchase_allowed": false,
  "excluded_ingredients": ["shrimp"]
}
```

모든 응답은 기존의 `data_quality`, `warnings`, `requires_confirmation`을 유지한다. 상태는 다음 중 하나다.

| status | recommendations | 의미 |
| --- | --- | --- |
| `recommendations_available` | 검증 후보만 포함 | 규칙 엔진 통과 후 AI 설명이 검증됨 |
| `no_safe_recommendation` | 빈 배열 | 로컬 규칙 엔진이 안전 후보를 찾지 못함 |
| `privacy_blocked` | 빈 배열 | 자유 텍스트 또는 식별·상세 건강 정보가 안전하게 최소화되지 않음 |
| `api_error` | 빈 배열 | timeout, API 오류, malformed AI response |
| `stale_unvalidated` | 기존 저장 결과 | published reference 변경 뒤 재검증 필요 |

`api_error`의 `message`는 반드시 다음 의미를 유지한다: `AI 연결 오류로 추천을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.`

`ai_explanation`은 `recommendations_available`일 때만 추가되며, API 응답에서는 로컬 recipe ID와 고정된 로컬 설명만 포함한다. OpenAI에는 recipe ID가 아닌 `candidate_1` 같은 요청 범위 임시 코드만 전송하고, 응답 코드는 로컬에서 다시 매핑한다. 모델의 요약·후보별 자유 문장은 사용자에게 표시하지 않는다. 메뉴, 영양값, 알레르기·안전 판정은 이 필드로 변경할 수 없다. 후보 코드가 하나라도 누락·중복·추가되면 `api_error`로 종료한다.

OpenAI payload의 후보 사실에는 임시 후보 코드, 유효 범위의 조리 시간, 수치형 영양 요약, 명시 허용된 코드만 포함한다. 후보명·경고 원문·제외 사유 원문·로컬 DB ID는 포함하지 않는다.

외부 전송 예시(가상 데이터):

```json
{
  "schema_version": "ai-recommendation-v1",
  "participant_count": 2,
  "meal_type": "dinner",
  "cook_time_limit_minutes": 20,
  "purchase_allowed": false,
  "constraint_codes": ["constraint_1"],
  "validated_candidates": [{
    "candidate_code": "candidate_1",
    "cook_time_minutes": 18,
    "nutrition_per_serving": {"energy_kcal": 310, "protein_g": 18, "sodium_mg": 420},
    "recommendation_reason_codes": ["PERSONALIZED"]
  }],
  "excluded_reason_codes": ["allergen_conflict"]
}
```

## 역할 C 전달 사항

`app/templates/index.html`에는 추천 뷰 바깥에 고정 `reason-card`가 남아 있다. `privacy_blocked`, `api_error`, `no_safe_recommendation`일 때도 정적 메뉴·영양·개인 이름이 표시될 수 있으므로, 역할 C는 해당 카드를 추천 결과 상태에 종속시키거나 제거해야 한다. 역할 B는 C 소유 HTML/CSS/JS를 수정하지 않는다.

## `POST /api/voice/recommendations`

기존 multipart contract를 유지한다. 음성 원본과 전사 원문은 로컬 STT/파싱에만 사용하고 OpenAI payload에는 넣지 않는다. AI 체인 실패 시 위와 동일한 `api_error` 응답이며 추천 후보를 포함하지 않는다.

## 저장과 재조회

추천 및 음성 추천은 AI 체인 최종 상태를 확정한 뒤 결과와 표시된 후보를 함께 저장한다. `api_error`, `privacy_blocked`, `no_safe_recommendation`에는 후보 및 후보 표시 기록을 저장하지 않는다. `GET /api/recommendations/<request_id>`는 저장된 최종 상태를 반환한다. 이전 버전에서 AI 체인 검증 기록 없이 저장된 추천은 `stale_unvalidated`로 처리하고 후보를 표시하지 않는다. 해당 이전 결과로 후보 피드백이나 조리 세션을 시작할 수 없으며 다시 추천을 요청해야 한다.
