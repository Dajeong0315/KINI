# B 식후 피드백 계약 v1 — 독립 API·SQL 연결

작성·검증일: **2026-11-27 한국 시간**. 초기 코드 기준: main `73e1fab`. 실제 연결 작업은 최신 main `eaea05e` 위에서 수행했다.

이번 산출물은 **독립 B 서비스·SQL 저장 어댑터·GET/PUT API·C mock**이다. SQLite와 임시 PostgreSQL에서 영속 저장·실제 loopback HTTP를 검증했다. `app.meal_feedback_app:create_app`에서는 라우트가 등록되지만, 기존 `app.main`은 수정하지 않았다. B 배포 런타임 연결은 기본 OFF flag로 통합했으며 운영 기능은 비활성이다. 운영 Supabase 저장, 클라우드 배포, C 화면·음성 입력 연결, 전체 기능 완료를 뜻하지 않는다. [실제 연결·검증·패치 인수인계](B_식후_피드백_실제연결_2026-11-27.md)를 따른다. 기존 B 배포는 이 작업을 기다릴 필요가 없다.

## 2026-11-27 사용자 확정 사항

- 식사 저장 성공 후 평가 여부를 선택할 수 있고, 바로 패스할 수 있다. 필수 입력이나 식사 저장 완료 조건으로 만들지 않는다.
- 지정 평가자를 두지 않는다. 해당 식사 참여자 누구나 냉장고 디스플레이 또는 음성으로 참여할 수 있다. 조리자 한 명에게만 난이도 응답 권한을 제한하지 않는다. 모르는 항목은 비워둘 수 있다.
- 맛 만족도·간·난이도 분리, 일부 항목만 응답, skip을 중립/불만으로 학습하지 않기, 우선 평가 사실 저장 후 가중치 검증 방향을 채택했다. 간은 싱거움/적당함/짬, 난이도는 쉬움/적당함/어려움이라는 앞선 추천안을 기준으로 한다. 선택지는 사용자 승인된 추천안에 따라 deploy/b/meal-feedback-options.v1.json으로 구체화했다. 아래 승인 선택지 매핑을 따른다.
- 화면·음성은 같은 참여자별 계약을 사용한다. 사람마다 평가를 구분하기 위한 member_id는 필요하지만, 사전에 특정 사람을 평가 담당자로 배정하는 절차는 없다. 공유 기기의 기존 가구 인증과 실제 식사 참여 관계 확인은 유지한다.

C 흐름: **저장 성공 → 평가하기/패스 선택 → 평가할 경우 응답하는 식사 참여자 확인 → 디스플레이 선택 또는 음성 입력 → 항목 확인·저장**. 초기 패스는 참여자를 고르게 강요하지 않고 화면을 닫으며 API를 호출할 필요가 없다. 참여자가 확인된 이후의 skip만 그 member의 skipped PUT으로 기록할 수 있다. 한 사람의 패스가 식사 전체나 다른 사람의 평가를 닫지 않는다. 재노출 간격·상한 수치는 미정이며 자동 재질문은 이번 범위에 없다.

음성은 입력 수단이며 화자 인증이 아니다. 이번 구현은 음성 인식·화자 식별을 추가하지 않는다. 누가 답하는지 불명확하면 참여자를 확인하고, 평가 코드·참여자가 확정되기 전 저장하지 않는다. 타인의 발화를 임의로 특정 참여자 평가에 귀속하지 않는다. 기존 평가가 있을 때 단순 패스/닫기는 기존 값을 보존하며 PUT을 보내지 않는다. 기존 skipped PUT의 철회 동작은 명시적으로 철회할 때만 사용한다.

## 최신 코드와 재사용 범위

- 원본 작업 폴더의 미커밋 `AI_DB_전체_파이프라인_설계_v1.md`와 `C_원준님_전달사항_2026-11-27.md`는 읽기만 했다. 두 문서의 과거 SQLite 설명을 최신 구현으로 단정하지 않았다.
- 최신 `recommendation_api.py`에는 `POST /api/recommendations/<request_id>/feedback`가 있다. 추천 후보의 selected/rejected/cooked/skipped/rated와 단일 별점이며, 저장 식사·참여자별 맛/간/난이도와 수정 멱등성 계약은 없다. 이 경로를 식후 평가 저장용으로 재사용하지 않는다.
- `cooking.complete`는 A 확정 출고를 연결하고 `meal_records`, `meal_record_items`, `nutrition_intakes`를 저장한다. 이미 식사 기반 선호 증거도 생성한다. 피드백 서비스는 이를 조회할 뿐 완료·섭취량·재고를 변경하거나 섭취 증거를 다시 만들지 않는다.
- 최신 코드에는 PostgreSQL 경로·인증·가구 권한 검사와 지원 라우트 제한이 존재한다. 이전 문서만 보고 PostgreSQL 미구현이라고 판정하지 않았다. 전용 피드백의 PostgreSQL 저장은 임시 합성 환경에서 검증했으며 운영 PostgreSQL에는 미연결이다.
- 기존 `local_identity.authorize`의 만료/철회/활성 계정/가구 membership/쓰기 역할 검사를 A 어댑터에서 재사용한다. 신규 인증 체계나 가구 권한 저장소를 만들지 않는다.
- `personalization.add_evidence`는 신호와 strength/confidence를 요구하고, `aggregate`는 누적 행을 집계한다. 미정 평가를 임의의 prefer/avoid/neutral로 변환하거나 기본 strength=1로 넣지 않는다.

## API 제안

기존 `/api/meal-records/<record_id>/adjustments` 계열에 맞춘 경로다. 아래 계약은 독립 피드백 앱에서 구현·검증했다. 배포 런타임에는 미연결이며 C가 운영 주소에서 호출할 수 있는 상태로 안내하지 않는다.

| 항목 | 계약 |
|---|---|
| 조회 | `GET /api/meal-records/{record_id}/participants/{member_id}/feedback` |
| 생성·전체 교체·건너뛰기 | 같은 경로 `PUT` |
| 인증 | 두 메서드 모두 `Authorization: Bearer <가구 세션 토큰>`, 데모 예외 없음 |
| 입력 형식 | PUT은 JSON object. 알 수 없는 필드 거절. 경로 ID는 양의 정수 |
| 객체 기준 | 커밋된 `meal_records.id`와 그 식사의 저장된 참여자. 추천 ID·조리 세션 ID는 대상 ID가 아님 |
| 응답 | GET/PUT 모두 200. 최초 PUT도 알려진 단일 자원을 교체하는 PUT 관례로 200 제안. 기존 POST 201 경로는 변경하지 않음 |
| 캐시 | 연결 시 성공·실패 응답 모두 `Cache-Control: no-store` |

요청자는 가구 ID, 레시피 ID, 지정 평가자 계정 ID, 학습 가중치, 안전 우회 옵션을 전달하지 않는다. household는 인증에서, recipe는 식사에서 결정한다. member는 **응답하는 식사 참여자의 평가 슬롯**이며 공유 기기에 로그인한 계정과 같다는 뜻이 아니다. 지정 평가자나 조리자 전용 검사 없이 기존 가구 인증과 식사 참여 관계를 확인한다. 다른 사람 대신 입력하는 대리 평가 정책은 별도로 미정이며, 누구나 참여 가능하다는 결정을 타인의 평가를 임의로 대신 작성해도 된다는 뜻으로 해석하지 않는다.

```json
{
  "status": "submitted",
  "expected_revision": 0,
  "ratings": {
    "taste": "taste_satisfied",
    "seasoning": null,
    "difficulty": null
  }
}
```

`taste`는 맛 만족도, `seasoning`은 간, `difficulty`는 난이도다. 간·난이도는 위 확정 방향을 따른다. 승인된 선택지와 API 코드는 아래 표 및 deploy/b/meal-feedback-options.v1.json으로 관리한다. 서비스 생성 시 `FeedbackOptions`로 각 항목의 승인 코드를 주입한다. 설정 파일을 명시적으로 주입하며 `mock_*`를 운영에 주입하지 않는다. 코드는 1~100자 공백 없는 문자열, 숫자 점수는 받지 않는다. 이는 코드 형식 제약이며 평가 단계 수를 정한 것이 아니다.

일부 항목만 답할 수 있다. submitted는 최소 한 항목이 필요하며, 생략/null인 항목은 미평가다. PUT은 부분 PATCH가 아니므로 수정할 때 유지할 항목도 함께 보내야 한다.

건너뛰기:

```json
{"status": "skipped", "expected_revision": 0}
```

skipped는 모든 ratings가 생략/null이어야 한다. 빈 평가를 neutral/avoid로 저장하지 않는다. 이미 평가한 뒤 skipped로 바꾸는 것은 이 제안에서는 **철회**이며 현재 평가 증거를 제거한다. 단순 화면 닫기로 기존 평가를 철회하면 안 된다. 기존 평가를 유지하며 닫는 화면은 PUT을 보내지 않는다. 철회 버튼 문구·지원 여부는 기획 확인 대상이다. 최초 skip 기록은 질문 빈도 정책을 자동으로 결정하지 않는다.

응답 예시(시각은 형식 설명용):

```json
{
  "record_id": 201,
  "member_id": 1,
  "recipe_id": 1,
  "status": "submitted",
  "ratings": {"taste": "<승인 코드>", "seasoning": null, "difficulty": null},
  "revision": 1,
  "updated_at": "2026-11-27T09:00:00+00:00",
  "personalization_status": "pending_mapping"
}
```

- 저장 대상은 있지만 아직 응답하지 않았으면 GET 200, `status=not_submitted`, `revision=0`, 모든 ratings/updated_at=null, `personalization_status=no_evidence`.
- skipped도 `personalization_status=no_evidence`. submitted는 현재 `pending_mapping`이며 **추천 점수에 반영됐다는 뜻이 아니다**.
- updated_at은 서버 UTC ISO 8601. 작업·검증일은 한국 날짜로 문서화한다. 오류 응답에는 토큰·DB 오류·평가 원문을 노출하지 않는다.

## 중복·수정·저장 실패

현재 자원 키는 `(record_id, member_id)`다. 최초 요청 expected_revision=0, 실제 변경마다 revision을 1 증가시킨다.

1. 인증·대상 권한을 항상 다시 확인한다.
2. 현재 status와 세 항목 값이 동일하면 기존 응답을 반환한다. 원래 expected_revision을 가진 네트워크 재시도도 no-op이며 시각·revision·증거가 증가하지 않는다.
3. 값이 다르면 expected_revision이 현재 revision과 같아야 한다. 오래된 수정은 409. C는 재조회 후 사용자에게 수정 확인을 받는다. 최신 revision으로 무조건 자동 재전송하지 않는다.
4. A 저장은 atomic compare-and-swap을 수행한다. 신규 행끼리의 경합도 UNIQUE 키로 방지하고 충돌을 409로 변환한다. 순차 테스트 통과만으로 실제 DB 동시성 보장을 주장하지 않는다.
5. 저장과 **커밋까지 성공한 뒤** 성공 반환. 실패하면 입력값을 유지하고 재시도한다. 커밋 결과를 클라이언트가 못 받은 경우 동일 요청 재시도로 현재 상태를 확인한다.

명시 평가의 현재 사실은 `Feedback.evidence()`의 항목별 값이다. 안정적 출처 키는 `(meal_feedback, record_id, member_id, dimension)`. 새 증거를 무조건 append하지 않고 같은 키를 교체한다. skip/항목 삭제는 해당 키를 제거한다. 현재 구현은 이 사실을 별도 선호 테이블로 복제하지 않고 **피드백 현재 snapshot에서 산출**한다.

후속 점수 연결 시 raw 평가 코드→기존 feature/signal/strength/confidence 매핑을 먼저 합의한다. 기존 섭취 기반 `source_type=meal_record`와 분리하고, 식사 전체의 모든 재료를 일괄 선호/비선호로 추정하지 않는다. projection을 별도 저장하면 교체·삭제·집계 재계산을 동일 트랜잭션에 포함해야 한다. `add_evidence`를 재시도마다 부르는 연결은 금지한다. 알레르기·영양·명시 제한·출력 안전 검사에는 변경 권한을 주지 않는다.

| HTTP | code | 조건·C 동작 |
|---|---|---|
| 400 | `invalid_request` | 형식·옵션·unknown 필드 오류. 입력 보존·수정 안내 |
| 401 | `authentication_required` | 누락/만료/철회 토큰·비활성 계정. 다시 로그인 |
| 403 | `forbidden` | viewer 쓰기 등 가구 쓰기 권한 없음 |
| 404 | `feedback_target_not_found` | 없는 식사/참여자, 다른 가구, 저장된 item 또는 세션 참여 관계 불일치. 타 가구 존재 여부는 구분하지 않음 |
| 409 | `revision_conflict` | 오래된 다른 값 수정 또는 DB CAS 경합. 재조회·수정 확인 |
| 503 | `feedback_options_unconfigured` | 선택지 미주입. 평가 저장 차단, 조회/건너뛰기는 가능 |
| 503 | `feedback_storage_unavailable` | 어댑터·조회·쓰기·commit 실패. 성공 화면 전환 금지 |

오류 body는 기존 `error` 문자열에 기계 처리용 `code`를 추가하는 제안이다. `FeedbackError.public_body()`는 code/error를 제공한다. Pydantic ValidationError/ValueError는 HTTP adapter에서 400으로, 기존 AuthenticationError/AuthorizationError는 401/403으로 매핑한다. 기존 공용 `errors`는 이 계약의 409/503을 처리하지 않으므로 그대로 붙이지 않는다.

## A 저장 연결 계약

`app/services/meal_feedback.py`의 `FeedbackTransaction` Protocol은 `meal_feedback_store.py`의 SQL 어댑터로 구현했다. 기존 A connection과 authorize를 재사용하고 요청 중 DDL·seed·fake fallback을 하지 않는다. `tests/test_meal_feedback.py`의 FakeStore는 도메인 테스트 전용이며 실제 SQL/HTTP 검증은 `test_meal_feedback_sql.py`에 분리했다.

| 인터페이스 | A 구현 요구 |
|---|---|
| `transactions()` | DB 연결/transaction 시작, 정상 종료 commit, 모든 실패 rollback, 연결 close. context 종료 예외도 전달 |
| `authorize(token, write=...)` | 같은 transaction DB의 기존 `local_identity.authorize` 호출 후 Identity(household_id,role) 반환. 기존 인증/권한 예외 보존 |
| `target(household_id, record_id, member_id)` | 아래 관계를 확인해 MealTarget 반환, 없거나 범위 밖이면 None. 읽기에서 새 식사 생성 금지 |
| `get(target)` | 현재 Feedback 또는 None 반환. 같은 target 데이터만 반환 |
| `save(feedback, expected_revision=...)` | 현재 snapshot 원자적 CAS 교체. absent=0. UNIQUE(record_id,member_id), 실제 수정 revision 증가, conflict는 FeedbackError 409. DB 오류는 원인 예외로 전달 |

대상 조회는 기존 테이블을 다음처럼 연결한다(테스트에서도 이 관계를 확인):

```sql
SELECT r.id AS record_id, i.member_id, s.household_id, r.recipe_id
FROM meal_records r
JOIN meal_sessions s ON s.id = r.session_id
JOIN meal_record_items i ON i.record_id = r.id
JOIN meal_session_participants p ON p.session_id = s.id AND p.member_id = i.member_id
JOIN members m ON m.id = i.member_id AND m.household_id = s.household_id
WHERE s.household_id = :authorized_household
  AND r.id = :record_id AND i.member_id = :member_id;
```

성공한 조리 완료에서 받은 `record_id`를 C가 사용한다. 조리 세션 active 상태만으로는 대상이 되지 않는다. 다른 유효한 저장 경로의 meal_record도 허용하므로 cooking_sessions.completed 연결을 필수 조건으로 추가하지 않았다.

저장 항목 제안: record/member FK, status, taste/seasoning/difficulty nullable code, revision, updated_at. household/recipe는 원본 식사에서 검증·유도한다. 정규화·FK 삭제 정책·감사 이력 보존·실제 migration은 A와 협의한다. 프로토콜의 evidence()는 파생 값이므로 별도 테이블은 필수가 아니다.

전용 어댑터는 SQLite BEGIN IMMEDIATE, PostgreSQL 대상별 transaction advisory lock + CAS + 유일키로 신규/수정 경합을 처리한다. PostgreSQL 쓰기는 기존 authorize의 권한 행 잠금과 대상 관계 행 FOR SHARE도 사용한다. 실제 두 DB에서 동시 최초 제출·중복·수정 충돌을 검증했다. serialization/deadlock/연결 오류를 성공으로 삼키지 않는다. 운영 적용 시 A가 전용 schema 후보와 권한·기존 삭제 정책의 호환성을 검토한다. 기존 A repository·공통 migration은 변경하지 않았다.

## 공유 파일 연결 작업 목록 — 이번 커밋에서 미적용

공유 파일 수정 이유는 **HTTP 등록과 실제 저장 DI** 두 가지뿐이다. 기존 추천·AI·배포 경로 변경은 필요 없다.

1. A: 구현된 전용 SQL 어댑터와 schema 후보를 검토해 공식 운영 migration·제한된 앱 역할 권한에 반영. 운영 DB에는 아직 적용하지 않았다. 기존 migration 이력은 임의로 추가하지 않았다.
2. B: `meal_feedback_api.py` Blueprint와 `meal_feedback_app.py` 독립 앱 연결 완료. Bearer·JSON object·오류 매핑·no-store·16 KiB 제한을 구현했고 잘못된 JSON은 거절한다.
3. 서비스 구성은 `MealFeedbackService(real_store.transaction, options=approved_options)`. 독립 앱은 `KINI_MEAL_FEEDBACK_OPTIONS_FILE`의 승인 코드 JSON도 읽는다. 미설정이면 제출은 503, 조회/skip은 허용. DB 누락·오류는 503이고 fake로 대체하지 않는다.
4. 배포 담당이 `docs/patches/meal_feedback_b_runtime.patch`를 검토·적용. Blueprint 등록뿐 아니라 proxy GET/PUT 허용 목록·PUT 메서드·readiness 대상 테이블 연결을 포함한다. 다른 세션 파일은 직접 수정하지 않았다. app.main을 쓰는 환경의 등록 예시는 연결 인수인계 문서를 참고한다.
5. C: 먼저 평가하기/패스 선택을 제공하고, 평가할 때 응답하는 참여자를 확인. 지정 평가자 배정 없이 디스플레이·음성을 같은 계약으로 연결. 저장 성공 응답 record_id와 해당 기록의 참여자 ID로 조회. 응답 revision을 보관해 전체 ratings를 PUT. loading/성공/실패/중복/409 재조회/건너뛰기/가구 전환 상태 구현. 이전 가구·식사의 입력과 revision을 재사용하지 않기. 초기 패스·기존 평가 화면 닫기는 PUT 없이 종료.
6. 독립 앱의 실제 SQL/HTTP 검증은 완료. 운영 런타임에 패치를 적용한 뒤에는 제한된 운영 역할·TLS·실제 주소·가구 전환·화면/음성 경로를 다시 검증해야 한다. 이번 검증을 운영 배포 검증으로 대신하지 않는다.

이 순서는 인수인계 작업 목록이며 공유 파일에 적용한 변경이 아니다. requirements·공통 환경 설정·기존 API handler·DB repository·privacy gateway·AI chain·인프라는 이번 작업에서 수정하지 않는다.

## C 화면용 mock

파일: `tests/fixtures/api_contract/meal_feedback_v1.json`.

- `contract_status=implemented_in_isolated_app_not_deployed`로 운영 API와 구분한다. `mock_*` 옵션과 record/member/recipe는 합성 데이터다.
- `ui_flow`에 선택 참여·초기 패스 시 API 미호출·지정 평가자 없음·디스플레이/음성 입력 방향을 담았다. 이는 C 구현 지침이며 음성 API·화자 인증 지원을 뜻하지 않는다.
- normal→duplicate→edit→skip 순서로 재생 가능. 조회 미작성 상태, 잘못된 옵션, 인증/권한/대상 오류, 충돌, 선택지 설정 누락, 저장 실패 예시 포함.
- updated_at은 형식을 보여주는 합성 시각이다. 실제 응답에는 서버 시각이 포함된다. 오류 문자열은 mock 설명용 영어이며 C는 code/status를 기준으로 동작하고 서비스 문자열과 동일하다고 가정하지 않는다.
- 배포 런타임 미등록 상태에서 이 mock을 운영 서버가 지원하는 성공 응답으로 안내하지 않는다. 특히 pending_mapping을 ‘추천 학습 완료’로 표시하지 않는다.

## 진아님과 논의할 내용과 추천안

사용자의 위 확정 사항을 반영한 논의 현황이다. 남은 수치·문구·학습 가중치는 임의로 채우지 않는다.

| 결정할 질문 | 추천안·이유 | 현재 구현 영향 |
|---|---|---|
| 맛은 무엇을 묻고 몇 단계로 나눌까? | 메뉴에 대한 만족도로 질문. 간·난이도와 섞은 단일 별점은 피하기 | 만족/보통/불만족을 안정 코드로 매핑 완료; 가중치 없음 |
| 간은 짠맛만인가, 양념 전반인가? | 싱거움/적당함/짬 방향 추천안 채택. 맛 만족도와 다른 신호 | seasoning 별도 저장. 싱거움/적당함/짬 코드 매핑 완료. 영양/나트륨 허용값 변경은 없음 |
| 난이도는 누가 답하나? | 지정 평가자 없이 식사 참여자 누구나 참여. 난이도는 쉬움/적당함/어려움 방향, 모르면 생략 | 사용자 확정. 조리자 전용 권한을 추가하지 않음. API 코드 매핑 완료 |
| 가족 관리자가 다른 참여자 대신 평가해도 되나? | 대리 평가라면 화면에서 평가 대상을 명확히 표시하고 본인 평가와 혼동 방지 | 현재 가구 쓰기 권한 재사용 제안. 자기 평가 제한·대리 여부 감사 필드는 미정 |
| 0인분 저장된 참여자도 평가 대상인가? | 맛·간은 실제 섭취자에게, 난이도는 조리자에게 묻는 방향 검토 | 현재는 저장 item+참여 관계 기준이며 0인분을 추가 차단하지 않음. 확정 전 운영 노출 보류 |
| 언제, 얼마나 자주 질문할까? | 섭취 저장 성공 후 평가 여부를 선택하고 바로 패스 가능 | 선택 참여 확정. 자동 질문·재노출·알림 없음. 재노출 간격/상한 미정 |
| 한 항목만 답해도 되나? | 모르는 항목은 비워두고 가능한 항목만 답하기 | 추천안 채택. omitted/null은 평가 없음 |
| 수정/철회는 언제까지, skip은 어떤 뜻인가? | 최초 skip과 기존 평가 철회를 UI에서 구분. 단순 닫기는 기존 값 유지 | 현재 skipped PUT은 기존 평가 철회. 수정 기한 제한은 추가하지 않음, 기획 합의 필요 |
| 평가를 추천에 어떻게 반영할까? | 우선 raw 사실을 검증하고 맛/간/난이도 각각의 대상 feature·방향·상충 처리·가중치를 따로 결정. 수치 근거 없이 정하지 않기 | pending_mapping. 기존 점수·안전 규칙 변경 없음 |
| 성공 안내 문구는? | ‘평가 저장됨’과 ‘추천에 반영됨’을 구분 | 실제 연결 전 mock 표시, 연결 후에도 pending_mapping이면 학습 완료 문구 금지 |

남은 후속 산출물: 대리 입력과 0인분 대상의 세부 처리, 철회 표시 문구, 재노출 조건, 개인화 매핑·가중치 승인 여부. 선택 참여·초기 패스·지정 평가자 없음·디스플레이/음성 방향은 위 사용자 결정으로 확정됐다. C는 이를 화면 상태로 구체화하고 B는 승인 코드 파일을 주입한다. 선택지 재승인을 배포 차단 조건으로 두지 않는다. 학습 수치 합의가 늦어도 저장 계약·권한·중복 방지 검증은 독립 진행할 수 있다.

## 검증 범위와 한계

초기 도메인 테스트는 합성 식사, 임시 SQLite의 기존 identity/식사 관계, **테스트 전용 메모리 feedback 저장소**를 사용했다. 후속 실제 연결 검증은 전용 SQL 테스트에서 임시 SQLite·PostgreSQL 16과 Flask/실제 loopback HTTP를 사용했다. 운영 Supabase·기기·LLM 검증은 아니다. 기존 추천 엔진 안전 회귀는 로컬 규칙·데모 카탈로그 조건이다. 평가가 existing preference_evidence/알레르기 profile/추천 결과를 변경하지 않는지도 확인한다.

실행 명령:

```text
python -m pytest tests/test_meal_feedback.py tests/test_personalization.py tests/test_recommendation.py tests/test_cooking.py -q
```

초기 구현 결과: **52 passed, 0 skipped, 44.83초**. 전용 식후 피드백 37개 + 기존 개인화·추천·조리 15개. Python 3.12.7 / pytest 8.4.2 / Pydantic 2.14.1 / Windows 10에서 1회 최종 실행한 테스트 경과 시간이며 API 지연 벤치마크가 아니다. 전용 검증에는 정상·다른 가구/없는 대상·입력 오류·중복·수정 충돌·skip·쓰기/commit 실패 rollback·세션 철회/만료/비활성 계정·안전 결과 유지·mock 계약 대조가 포함된다. CAS 경합은 테스트 대역에서 충돌을 주입했다.

검증 작성 중 만료 세션 fixture가 `expires_at > created_at` 기존 제약을 위반한 1건을 수정한 뒤 위 명령을 재실행했다. 실제 PostgreSQL 동시성, 피드백 영속 저장, HTTP 등록 검증은 미실행이다. 새 학습 가중치·모델 품질·운영 성능은 미측정이다.

사용자 확정 사항 반영 후 `python -m pytest tests/test_meal_feedback.py -q` 재실행: **38 passed, 0 skipped, 34.04초**. 공유 기기에서 지정 평가자 없이 참여자별 평가가 가능하고, 한 사람의 skip이 다른 사람의 제출·기존 평가에 영향을 주지 않는 검증을 추가했다. 서비스 처리 로직과 공통 경로는 변경하지 않았으며, 이 후속 실행에서 기존 회귀 15개는 재실행하지 않았다. 초기 패스 화면과 음성 입력의 실제 UI 검증은 C 연결 이후 대상이다.

실제 SQL/API 연결 후 최종 통합 검증: **71 passed, 0 skipped, 61.44초**. 전용 SQL/API 18개 + 위 도메인 38개 + 기존 회귀 15개. 임시 SQLite·실제 PostgreSQL 16·실제 loopback HTTP·합성 데이터 조건이다. 자세한 실행·권한·패치·미연결 범위는 [실제 연결 인수인계](B_식후_피드백_실제연결_2026-11-27.md)를 따른다. 운영 Supabase와 클라우드 배포 검증은 포함하지 않는다.


## 승인 선택지의 API 매핑 (2026-11-27 정정)

개발 채팅 「식후 피드백 계약·서비스 구현」(01a21a66-6be3-7bd1-8faa-c036448409bd)의 원래 추천안과
사용자의 “추천안대로 갈게” 승인을 확인했다. 본 배포 채팅에서 재승인 없이 설정 파일로 구체화하라는 지시도 받았다.
원문은 맛을 만족도로 묻도록 했으나 개별 단계 문구를 나열하지 않았다. 다음 만족/보통/불만족은 이번 설정의 구현 매핑이며 원문 인용이나 학습 가중치가 아니다.

| 항목 | 표시 문구 | 안정 API 코드 |
|---|---|---|
| 맛 만족도 | 만족 / 보통 / 불만족 | taste_satisfied / taste_neutral / taste_dissatisfied |
| 간 | 싱거움 / 적당함 / 짬 | seasoning_bland / seasoning_balanced / seasoning_salty |
| 난이도 | 쉬움 / 적당함 / 어려움 | difficulty_easy / difficulty_moderate / difficulty_hard |

파일: deploy/b/meal-feedback-options.v1.json. KINI_MEAL_FEEDBACK_OPTIONS_FILE의 절대 경로로 연결한다.
mock_*·test_* 파일 코드는 loader가 거절한다. 파일 미설정 시 submitted 503은 설정 누락 대응이며 기획 미승인 상태가 아니다.
저장은 pending_mapping이며 개인화 가중치 합의는 저장 활성화의 선행 조건이 아니다.
스키마·제한 앱 역할 준비와 선택 참여·패스·식사 참여자별 인증은 유지한다. C 화면·음성 입력 구현은 별도다.
