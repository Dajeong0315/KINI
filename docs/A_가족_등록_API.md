# 계정 등록 → 가족 생성 → 구성원 등록 API

2026-11-26. 서버 API·임시 SQLite 테스트·명세만 추가한다. C 화면은 변경하지 않는다.

## 구조와 호출 순서

로그인 계정(`local_accounts`)과 가족 구성원 프로필(`members`)은 별개다. 하나의 가족(`households`) 안에 구성원 네 명을 등록해도 로그인 계정 네 개가 생기지 않는다. 계정과 가족은 `account_households`로 연결한다. 가족 생성자는 서버에서 자동으로 `owner`가 된다. 클라이언트가 계정 ID·가구 소속·역할을 지정할 수 없다.

1. `POST /api/auth/register`: 최초 로그인 계정 생성.
2. `POST /api/auth/account-login`: 가구 선택 전 계정 토큰 발급.
3. 계정 토큰으로 `POST /api/households`: 가족 생성. 돌아온 `id`가 `household_id`다.
4. 기존 `POST /api/auth/login`: 계정 자격 증명과 위 `household_id`로 **가구 토큰** 발급.
5. 가구 토큰으로 `POST /api/households/{household_id}/members`: 가족 안에 구성원 등록. 네 명이면 네 번 호출한다.

기존 사용자도 2번부터 재진입하여 `GET /api/households`로 소속 가족을 조회한 뒤 4번을 호출할 수 있다. 계정 등록 응답에는 토큰을 반환하지 않으며 이어서 계정 로그인을 수행한다.

| 토큰 | 발급 경로 | 허용되는 새 API | 재고·구성원 접근 |
| --- | --- | --- | --- |
| 계정 토큰 | `/api/auth/account-login` | 가족 생성·소속 가족 목록 | 불가 |
| 가구 토큰 | 기존 `/api/auth/login` | 해당 가족·구성원 조회 및 권한에 따른 구성원 등록 | 기존 A 재고/B 완료에도 사용 |

둘 다 `Authorization: Bearer <access_token>`을 쓰지만 서로 대체할 수 없다. 두 토큰은 별도 테이블의 해시로 보관하며 수명은 3600초다. `expires_at`은 UTC Unix epoch 초다. 예시 토큰·ID·시각은 형식 설명용이며 실제 비밀번호·토큰은 기록하지 않는다.

## 1. 최초 계정 등록

`POST /api/auth/register`, `Content-Type: application/json`. 인증 불필요.

```json
{"login_name":"family-owner","password":"<12자 이상의 입력 비밀번호>"}
```

성공 `201`:

```json
{"account_id":"<서버가 생성한 UUID>","login_name":"family-owner"}
```

`login_name`: 공백 제거 후 1~256자 문자열. 저장·중복 검사는 `casefold()` 기준이므로 대소문자를 구별하지 않는다. `password`: 공백을 제거하지 않는 12~4096자 문자열. 기존 Werkzeug 비밀번호 해시를 재사용한다. 같은 로그인 이름은 `409`다.

빈 DB에서 이 등록 경로는 데모 가족·구성원을 자동 생성하지 않고 private DB로 초기화한다. 기존 DB의 가족·계정·재고는 보존한다. 기존 데모 DB를 실제 운영에 사용하려면 새 `KINI_DB_PATH` 및 `KINI_SEED_DEMO=0`으로 별도 시작한다. DB 초기화·기존 데이터 삭제 작업은 이 API가 수행하지 않는다.

## 2. 계정 로그인과 가족 목록

`POST /api/auth/account-login`, `Content-Type: application/json`.

```json
{"login_name":"family-owner","password":"<입력 비밀번호>"}
```

성공 `200`:

```json
{
  "access_token":"<계정 토큰>",
  "token_type":"Bearer",
  "token_scope":"account",
  "expires_in":3600,
  "expires_at":1795653600
}
```

아직 소속 가족이 없어도 로그인할 수 있다. 로그인 비밀번호 길이는 1~4096자다. 없는 계정·비밀번호 불일치·비활성 계정은 모두 `401 {"error":"로그인 정보를 확인해 주세요."}`이며 실패 시 토큰을 추가 발급하지 않는다.

`GET /api/households`, 계정 토큰 필요. 성공 `200`:

```json
{"households":[{"id":1,"name":"우리 가족","role":"owner"}]}
```

소속 가족이 없으면 `{"households":[]}`다. 현재 활성 계정의 소속 가족만 반환하며 재로그인 시 가구 선택에 사용한다.

## 3. 가족 프로필 생성

`POST /api/households`, 계정 토큰 필요.

```json
{"name":"우리 가족"}
```

성공 `201`:

```json
{"id":1,"name":"우리 가족","role":"owner","members":[]}
```

이름은 앞뒤 공백 제거 후 1~100자 문자열이다. 가족 행과 생성 계정의 owner 연결을 한 트랜잭션으로 저장한다. 이름이 같은 별도 가족도 만들 수 있으므로 ID로 구분한다. 이 POST는 재시도 시 새로운 가족을 생성한다.

## 4. 해당 가족 로그인

기존 `POST /api/auth/login` 계약을 유지한다.

```json
{"login_name":"family-owner","password":"<입력 비밀번호>","household_id":1}
```

성공 `200`의 `access_token`이 가구 토큰이다. 서버가 계정 활성·비밀번호·선택한 가족 소속을 확인하고, 기존 응답의 `household_id`, `role`, `expires_in`, `expires_at`, `token_type`을 반환한다. 상세 계약은 [A 로컬 로그인 API](A_로컬_로그인_API.md)를 따른다.

## 5. 구성원 등록과 조회

`POST /api/households/1/members`, **1번 가구 토큰** 필요. 현재 DB 역할이 owner/editor인 계정만 등록 가능하다.

```json
{"name":"구성원 1","age":40,"allergies":["새우"],"dislikes":["버섯"]}
```

성공 `201`:

```json
{
  "id":1,
  "household_id":1,
  "name":"구성원 1",
  "age":40,
  "allergies":["새우"],
  "preferences":[{"kind":"dislike","value":"버섯"}],
  "constraints":[],
  "targets":{}
}
```

- `name`: 필수, 앞뒤 공백 제거 후 1~100자 문자열.
- `age`: 필수, 0~130 정수. 문자열·실수·불리언은 거절한다.
- `allergies`, `dislikes`: 선택, 각각 기본 `[]`, 최대 32개. 항목은 공백 제거 후 1~100자 문자열이며 중복 항목은 한 번만 저장한다.
- 프로필·알레르기·기피 식품은 하나의 트랜잭션으로 저장한다. 나이 기반 영양 목표를 임의 생성하지 않는다.
- 이름이 같은 구성원도 가능하며 서버가 생성한 구성원 ID로 구분한다. POST 재시도는 새 프로필을 생성하므로 생성 성공 응답을 보관한다.

`GET /api/households/1/members` → `200 {"household_id":1,"members":[...]}`.

`GET /api/households/1` → `200 {"id":1,"name":"우리 가족","role":"owner","members":[...]}`.

두 GET 모두 해당 가구 토큰이 필요하며 viewer도 조회할 수 있다. 등록된 구성원 ID는 기존 식사 세션 API의 `participant_ids`로 사용할 수 있다.

URL의 가구 ID는 1~9223372036854775807 범위다. 이 범위를 벗어나면 `400`이며, 유효 범위의 다른 가구 ID는 소속 검사에서 `403`으로 거절한다.

## 오류와 권한

| 경로/조건 | 상태 | 응답 |
| --- | --- | --- |
| 새 POST의 필수값 누락·타입/범위 오류·추가 필드·JSON 형식 오류 | `400` | `{"error":"요청 값의 형식을 확인해 주세요."}` |
| 계정 등록 이름 중복 | `409` | `{"error":"사용할 수 없는 로그인 이름입니다."}` |
| 계정 로그인 실패 | `401` | `{"error":"로그인 정보를 확인해 주세요."}` |
| 새 보호 API의 Bearer 누락/형식 오류 | `401` | `{"error":"로그인 세션이 필요합니다."}` |
| 가족 생성·목록의 계정 토큰 만료/철회/비활성/다른 종류 토큰 | `401` | `{"error":"세션 또는 가구 권한을 확인하세요."}` |
| 가족·구성원 API의 가구 토큰 만료/철회/비활성/소속 불일치, viewer 등록 시도 | `403` | `{"error":"세션 또는 가구 권한을 확인하세요."}` |

등록 API는 body의 `account_id`, `household_id`, `role` 등의 추가 필드를 거절한다. 구성원의 가구는 URL과 세션의 일치 여부를 확인한 뒤 서버에서 결정한다. 비밀번호·토큰·검증 오류의 입력 원문을 로그/오류 응답에 출력하지 않는다. 새 API 응답에는 `Cache-Control: no-store`, `Pragma: no-cache`를 설정한다.

## 스키마·기존 동작에 미치는 영향

- SQLite additive migration version 3: `local_account_sessions`와 account/expiry 인덱스 추가. 기존 `auth_sessions`와 계정·가족·구성원·재고를 유지한다. 재실행 가능하다.
- `local_identity.create_account()`는 가족 연결을 생략할 수 있게 확장한다. 기존 가구 ID를 넘기는 호출은 동일하게 owner로 연결한다. 계정/가구 로그인이 같은 비밀번호 검증 함수를 사용한다.
- 기존 `GET /api/members?household_id=...`도 계정에 연결된 가구 또는 private DB에서는 가구 토큰을 확인한다. 토큰 누락 `401`, 다른 가구/계정 토큰 `403`이다.
- 기존 `/api/households/demo`는 private DB 또는 계정에 연결된 1번 가구가 있으면 `404`다. 실제 가족 정보가 데모 응답으로 노출되지 않도록 한다. 계정에 연결되지 않은 기존 데모 환경은 유지한다.
- Supabase 스키마·권한·데이터, B 조리·차감 서비스, C 화면은 변경하지 않는다. 가족 초대·가입·구성원 로그인 연결, 수정·삭제, 계정 복구·HTTP 로그아웃, 전체 기존 API 인증 개편은 포함하지 않는다. 기존 추천·식사 세션 등 모든 API가 인증된 것으로 간주해서는 안 된다.

## 검증 방법

임시 SQLite, 합성 계정·프로필, 외부 AI 비활성화. 실제 사용자 계정·운영 DB·비밀번호는 사용하지 않는다.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/test_family_api.py tests/test_local_auth_api.py tests/test_data_platform.py -q --tb=short
python -m pytest -q --tb=short
git diff --check
```

새 테스트는 HTTP 계정 등록 → 계정 로그인 → 가족 생성 → 기존 가구 로그인 → 네 구성원 등록·조회 → 네 구성원 식사 세션 생성과 가구 재고 조회를 검증한다. 이름 중복, 입력 검증·추가 필드 차단, 토큰 종류 혼용, 타 가구 접근·공개 우회 조회, 역할 변경, 만료·철회·비활성 계정, 기존 DB 업그레이드 보존도 검증한다.

2026-11-26 실행 결과 (Windows / Python 3.13 / Flask 테스트 클라이언트):

- 가족·기존 로그인·플랫폼 관련 파일: **75 passed / 100.48초**.
- 전체 pytest: **226 passed, 7 skipped / 228.92초**. 브라우저 테스트 7개는 Playwright 미설치로 제외했다. 실제 C 화면 E2E는 검증하지 않았다.
- 최종 보강한 URL 가구 ID 범위와 네 구성원 흐름을 포함한 두 테스트 재검증: **2 passed / 3.64초**.
- `git diff --check` 통과. C 화면·정적 파일·템플릿과 B 조리 서비스 변경 없음.

작업 기반: 팀 main `56ce52d`. 전달 브랜치: `codex/family-onboarding-api`. 별도 브랜치로 푸시하며 main에는 직접 병합하지 않는다.
