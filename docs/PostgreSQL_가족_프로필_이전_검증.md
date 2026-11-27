# 계정·가족·프로필 API PostgreSQL 이전

검증일: 2026-11-26 (KST). 기준 main: `fffdca4`.

## 완료 범위

기존 SQLite 가족 API를 PostgreSQL에서도 같은 요청·응답으로 사용한다.
검증은 Docker PostgreSQL 16.15와 합성 데이터, 실제 루프백 HTTP로 수행했다.
Supabase 프로젝트나 실제 가족 DB에 접속하지 않았다.

| 경로 | 기존 계약 유지 |
| --- | --- |
| `POST /api/auth/register` | 계정 등록 201, 대소문자·공백 정규화 후 중복 409 |
| `POST /api/auth/account-login` | account 범위 Bearer 세션, 1시간 수명, 로그인 실패 401 |
| `GET/POST /api/households` | account 세션으로 본인 가구 목록 조회·가구 생성 |
| `POST /api/auth/login` | 기존 household 범위 세션 발급 |
| `GET /api/households/{id}` | 인증된 가구·역할·구성원 프로필 조회 |
| `GET/POST /api/households/{id}/members` | 구성원 목록·등록, 알레르기·기피 식재료 저장 |
| `GET /api/members?household_id=...` | 기존 클라이언트 조회 경로, PostgreSQL에서는 항상 인증 필요 |
| `GET /api/households/demo` | PostgreSQL은 개인 DB이므로 404 |

가구 경로의 헤더 누락은 401, 잘못된/만료된 가구 세션·다른 가구·viewer 쓰기는
기존 계약대로 403이다. account 경로의 무효 세션은 401이다. 세션 범위를 섞어 사용할 수 없다.
화면·스타일·UI 테스트와 API 필드는 변경하지 않았다.

## 저장·트랜잭션

- `002_family_profiles.sql`: account-only 세션과 취향·알레르기·식단 제약·영양 목표 테이블을 추가한다.
- 프로필은 기존 `member_id`, account 세션은 기존 UUID `account_id`로 연결한다.
- 이름·나이·알레르기·기피 식재료의 기존 등록 입력만 사용한다. 식단 제약·영양 목표는 기존 조회 응답을 위해 테이블을 제공하며 새 편집 API는 만들지 않는다.
- 새 프로필 테이블에는 FK와 구성원 삭제 시 CASCADE, 중복 방지 키를 둔다.
- PostgreSQL 쓰기에서는 세션·계정·가구 역할 행을 공유 잠금으로 읽고, 그 트랜잭션이 끝날 때까지 권한 변경과의 경쟁을 막는다. SQLite의 BEGIN IMMEDIATE는 유지한다.
- 가구와 owner 관계, 구성원과 알레르기·취향은 각각 한 트랜잭션으로 저장한다.
- 비밀번호는 기존 hash, 세션은 기존 token hash로만 저장한다. 가족 응답은 no-store를 유지한다.

## Migration

`001_step1_postgresql.sql`은 수정하지 않았다. 실행기는 SQL 파일명 순서와
`schema_migrations`를 확인하여 미적용 파일만 한 트랜잭션에서 적용한다.
동시 실행기는 PostgreSQL transaction advisory lock으로 직렬화한다.
실패 시 이번 적용과 version 기록을 rollback한다. 요청 처리 중 DDL·demo seed는 실행하지 않는다.

빈 DB의 001→002 적용, 001에 계정·가족·구성원·가구 세션이 있는 상태의 002 적용,
재실행 시 skip 및 기존 기록 보존을 별도 임시 schema에서 검증했다.
이 작업은 SQLite 파일을 복사하거나 실사용 데이터를 이전하는 작업이 아니다.

## 재현

프로젝트 Python과 Docker Desktop Linux engine, 기존 `postgres:16-alpine` 이미지가 필요하다.
프로젝트 루트에서 PowerShell로 실행한다. 연결 문자열·비밀번호를 채팅이나 Git에 넣지 않는다.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:KINI_USE_OPENAI='0'
$env:PYTHONPATH=(Get-Location).Path
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_vision_postgres.ps1 -Python <프로젝트-Python-절대경로>
```

스크립트는 임시 비밀번호와 임의 루프백 포트로 tmpfs 기반 DB를 만들고,
migration 적용 후 PostgreSQL 시험을 실행하며 해당 컨테이너를 정리한다.
가족 시험용 임시 schema도 시험 종료 시 제거한다.

SQLite 가족·인증 회귀는 별도로 실행한다.

```powershell
$env:KINI_DB_BACKEND='sqlite'
python -m pytest tests/test_family_api.py tests/test_local_auth_api.py -q
```

## 직접 실행 결과

| 시험 | 결과 | 조건 |
| --- | ---: | --- |
| PostgreSQL 전체 묶음 | 74 passed / 40.39초 | 기존 재고 경쟁 60회 + HTTP 3개 + 저장 경계 1개 + 가족/migration 10개 |
| 가족·인증 SQLite 회귀 | 59 passed / 46.11초 | 임시 SQLite, 기존 시험 유지 |
| Python 전체·Chrome 회귀 | 290 passed, 74 skipped / 190.92초 | SQLite·테스트 대역, Chrome E2E 13개 포함. 제외 74개는 위 PostgreSQL 묶음에서 독립 실행해 통과 |
| JavaScript UI | 35 passed | 기존 Node 시험, 화면 변경 없음 |

가족 PostgreSQL 10개는 실제 HTTP 등록·중복 등록 경쟁·가구/프로필 조회,
account/household 세션 구분·다른 가구·viewer 거절, account 세션 만료/폐기/비활성화,
가구 관계 또는 취향 INSERT에 DB 오류를 주입한 전체 rollback,
빈 DB/001 DB의 migration·재실행·프로필 FK 삭제를 검증한다.
영양 목표·식단 제약 조회는 합성 값을 DB에 넣어 확인했다.
실제 개인정보·외부 AI·Supabase·AWS는 사용하지 않았다. 실행 시간은 검증 소요 시간이며 성능 benchmark가 아니다.

## 남은 경계

- 추천 후보·영양 카탈로그·조리·섭취·리포트 전체 PostgreSQL 이전은 다음 단위다.
- 초대·계정 복구·프로필 수정/삭제 화면은 이번 범위 밖이다.
- DB 시험의 가구 격리는 서버 권한 검사 결과다. 운영 DB 최소 권한 역할·Supabase anon/authenticated·RLS 검증을 대신하지 않는다.
- 신규 테이블의 PUBLIC 권한은 REVOKE하지만, Supabase의 기존 명시적 role grant는 계정주가 실제 적용 단계에서 확인해야 한다.
- Supabase 적용·실데이터 이전·AWS 배포·실제 카메라/모델 실행은 수행하지 않았다.
