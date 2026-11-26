# A 로컬 로그인 HTTP 계약 (2026-11-26)

`POST /api/auth/login`은 기존 `local_identity.login()`과 `authorize()`를 사용하는 어댑터다. #16의 A 코드와 main의 #18 B 변경이 함께 적용된 서버 기준이다. 회원가입, 외부 로그인, 전체 API 인증, C 화면, B 조리·차감 로직은 이번 변경에 포함하지 않는다.

## 요청과 응답

`Content-Type: application/json`

```json
{"login_name":"synthetic-owner","password":"<사용자가 입력한 비밀번호>","household_id":1}
```

세 필드는 필수이고 추가 필드는 거절한다. `login_name`은 공백 제거 후 대소문자를 구별하지 않으며 최대 256자다. `password`는 공백을 제거하지 않고 1~4096자 문자열로 받는다. `household_id`는 양의 SQLite 정수(최대 9223372036854775807)이며 문자열·불리언은 받지 않는다. 가구 ID는 선택값이며 권한의 근거가 아니다. 서버가 활성 계정, 비밀번호, 해당 가구 소속을 확인한다. 역할은 DB의 `owner`, `editor`, `viewer`에서 결정한다.

성공 `200` (토큰과 시간은 형식 예시):

```json
{
  "access_token":"<발급된 불투명 토큰>",
  "token_type":"Bearer",
  "expires_in":3600,
  "expires_at":1795653600,
  "household_id":1,
  "role":"owner"
}
```

`expires_in`은 발급 기준 수명(초), `expires_at`은 UTC Unix epoch 초다. 수명은 서버에서 3600초로 정하며 클라이언트가 지정할 수 없다. 로그인 응답은 `Cache-Control: no-store`, `Pragma: no-cache`를 반환한다. DB에는 원문 토큰 대신 SHA-256 해시만 저장한다. 비밀번호·토큰을 콘솔, 요청/응답 로그, 분석 이벤트에 기록하지 않는다. 아래 예시에도 실제 자격 증명은 사용하지 않았다.

## C의 호출 순서

1. 로그인 응답의 `access_token`을 받아 이후 A 조회·출고 확정과 B 완료 요청에 동일하게 `Authorization: Bearer <access_token>` 헤더를 붙인다.
2. `GET /api/data/inventory` → `200`. 토큰에 묶인 가구 재고만 반환한다. 별도 가구 ID로 조회 대상을 바꾸지 않는다.
3. 기존 추천 → 식사 세션 → 조리 세션 생성 API를 사용한다. 이 API들의 전체 인증 변경은 이번 범위 밖이다. 추천·식사 세션의 `household_id`는 로그인 응답과 일치하도록 전달한다.
4. 이번 조리에 사용한 A 출고가 이미 `applied`이면 그 ID를 재사용한다. 출고가 없으면 `/api/data/inventory/events`에서 `source: "manual"`, `action: "stock_out"`, UUID `client_event_id`, `detected_label`로 등록하고, `/api/data/inventory/events/{id}/confirm`에 `ingredient_id`, `inventory_item_id`, `quantity_g`를 보내 확정한다. 모든 호출에 같은 Bearer 헤더를 쓴다.
5. B 완료에 확정 출고 ID를 연결한다.

```http
POST /api/cooking-sessions/7/complete
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "confirmed":true,
  "eaten_at":"2026-11-26T12:00:00",
  "items":[{"member_id":3,"servings":1}],
  "inventory_event_ids":[41,42,43]
}
```

성공 `201`의 예시(영양·재료 수치는 해당 조리 세션에 따라 달라진다):

```json
{
  "record_id":8,
  "nutrition_per_serving":{"energy_kcal":222.5,"protein_g":13.03,"carbohydrate_g":32.38,"fat_g":5.33,"sodium_mg":11.75},
  "ingredients":[{"ingredient_id":1,"quantity_g":300},{"ingredient_id":4,"quantity_g":400},{"ingredient_id":5,"quantity_g":200}]
}
```

B는 출고 연결 및 조리·섭취만 기록하고 추가 재고 차감을 하지 않는다. 같은 출고를 다른 조리 세션에서 다시 쓰면 `409`다.

## 오류 상태

| 대상 | 상태 | 응답/의미 |
| --- | --- | --- |
| 로그인 | `400` | `{"error":"로그인 요청 형식을 확인해 주세요."}` — 누락·타입·범위·추가 필드·잘못된 JSON |
| 로그인 | `401` | `{"error":"로그인 정보를 확인해 주세요."}` — 없는 계정·잘못된 비밀번호·비활성 계정·가구 미소속에 동일 응답. 세션 미발급 |
| A 재고/B 완료 | `401` | Bearer 헤더 누락 |
| A 재고/B 완료 | `403` | 유효하지 않거나 만료·철회된 세션, 가구 권한 불일치. viewer는 조회 가능, 출고/완료 불가 |
| A 이벤트 확인 | `404` | 해당 가구에 없는 이벤트(다른 가구 이벤트 포함) |
| B 완료 | `409` | `code: "inventory_confirmation_required"` — 출고 미확정·부족·중복 사용 등 |
| B 완료 | `400` | 이미 완료된 세션, 입력/안전 검증 실패 등 기존 B 계약 |

가구 선택을 바꾸려면 해당 가구로 다시 로그인한다. 로그인 성공 이후에도 기존 권한 검사는 계정 활성, 현재 소속·역할, 만료·철회 상태를 매 요청에서 확인한다. 세션 갱신/로그아웃 HTTP 경로는 이번에 추가하지 않았다.

## 검증

임시 SQLite와 합성 계정·데모 재고만 사용하며 외부 AI는 비활성화한다. 테스트 출력에 자격 증명을 남기지 않도록 `--tb=short`를 사용하고 요청·응답 원문을 출력하지 않는다.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/test_local_auth_api.py tests/test_b_api_handoff.py tests/test_inventory_events.py tests/test_data_platform.py -q --tb=short
```

HTTP 통합 테스트는 내부 함수에서 토큰을 직접 발급하지 않는다. 로그인 응답 → 재고 `200` → A 확정 출고/100g 반납 → B 완료 `201` → 섭취·영양 기록 생성과 재고 스냅샷 동일성을 검증한다. 다른 가구 완료 `403`, 다른 가구 이벤트 확인 `404`, 출고 재사용 `409`, viewer 쓰기 거절, 동일 로그인 실패, 만료·철회·소속 삭제·비활성 계정 거절을 포함한다.

2026-11-26 검증 결과 (Windows, Python 3.13, Flask 테스트 클라이언트):

- 관련 4개 테스트 파일: **57 passed / 82.84초**.
- 전체 `python -m pytest -q --tb=short`: **191 passed, 1 failed, 7 skipped / 173.83초**. 기존 C 계약 테스트가 로그인·출고 확정 없이 완료를 호출해 `401`이었다.
- 해당 `tests/ui/test_local_contract.py`를 HTTP 로그인·출고 확정 계약으로 갱신한 뒤 재검증: **1 passed / 2.01초**. 수정 후 추가 재고 차감이 없음을 함께 확인했다. 나머지 테스트 코드는 이 재검증 사이 변경하지 않았다.
- 총 192개 테스트의 통과를 확인했다. Playwright 미설치로 브라우저 테스트 7개는 제외했으며 실제 C 화면 E2E는 검증하지 않았다. `git diff --check` 통과.

작업 기반은 A #16의 `85daa9f`와 확인 시점 최신 main `6f9edb1`의 통합이다. B 서비스·C 화면은 수정하지 않았다. #16이 아직 미병합이므로 이 로그인 커밋만 A 코드 없는 main에 적용할 수는 없다.
