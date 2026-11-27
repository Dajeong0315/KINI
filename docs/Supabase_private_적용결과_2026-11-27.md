# Supabase private 스키마 적용 결과

작업·검증일: 2026-11-27 KST. 사용자가 kini_private 생성, migration 001~009,
전용 앱 역할 및 public 읽기 권한 설정을 명시적으로 승인한 뒤 적용했다.
SQLite 데이터 이전·서버 backend 전환·서비스 재시작·커밋·푸시는 수행하지 않았다.

## 적용본과 실행

- 프로젝트 `zyjrwychceuxxfdproev`의 기존 Dashboard SQL Editor, 관리자 postgres 세션.
  적용 전 CREATEROLE·DB CREATE와 대상 schema/역할 부재를 확인했다.
- `scripts/prepare_private_deployment.py`가 현재 migration SQL을 읽어
  `deploy/kini_private_setup.sql`을 생성한다. SQL에는 비밀번호·개인 데이터가 없다.
- 최초 적용본 SHA256 `a186e121e0587e34fe73be8d5a8b068f2306b4b8695c466a65bc7965a78697b3`.
  BEGIN~COMMIT 원자적 실행으로 신규 schema·역할·9개 migration·권한·정책을 적용했다.
  결과 `kini_private_setup_applied / migrations 9`를 확인했다.
- PostgreSQL 17에서 관리자 creator 멤버십만으로 앱 역할 SET이 허용되지 않아,
  후속으로 `GRANT kini_app TO postgres WITH SET TRUE`와 `WITH INHERIT FALSE`를 적용했다.
  관리자는 검증을 위해 역할 전환할 수 있으며 앱 역할은 관리자/migration 권한을 얻지 않는다.
- 최종 재생성 적용본 SHA256 `98eaaa1ed80421dacad75551595c2ef2c3bb677af66f140e2459ea85a44cd652`.
  위 두 GRANT를 포함한다. 실제 DB에는 최초 묶음+후속 두 GRANT로 같은 구성이 적용됐다.
  이 생성기는 기존 schema/역할이 있으면 중단하므로 적용 완료 DB에 재실행하지 않는다.

## 권한 구조

- `kini_migrator`: NOLOGIN·NOSUPERUSER·NOBYPASSRLS. private schema와 테이블 소유.
- `kini_app`: LOGIN·NOINHERIT·NOSUPERUSER·NOBYPASSRLS·NOCREATEDB·NOCREATEROLE,
  연결 제한 10, search_path kini_private/pg_catalog, statement_timeout 15초, lock_timeout 5초.
- 앱은 private SELECT 및 필요한 계정·가족·재고·식사·추천·조리·개인화 테이블의 DML만 허용한다.
  migration 이력·공개 참조 데이터·매핑 관리 테이블의 쓰기 권한은 부여하지 않았다.
- private 전체 테이블에 RLS와 kini_app 전용 backend 정책을 만들었다.
  이는 서버 역할의 접근 경계이며 가구별 RLS 격리 정책이 아니다.
  가구 격리는 기존 서버 인증·객체 권한 검사로 계속 검증해야 한다.
- anon/authenticated/service_role 및 PUBLIC의 private schema·테이블·sequence 접근을 회수했다.
  Data API 노출 스키마 설정을 변경하지 않았다.
- public 7개 테이블(data_sources/data_releases/active_data_releases/catalog_foods/
  catalog_recipes/catalog_recipe_steps/catalog_recipe_nutrients)에 SELECT grant 및
  kini_app만 대상으로 하는 SELECT RLS 정책을 추가했다.
  기존 service_role 정책을 삭제·교체하거나 public 행을 삽입·수정·삭제하지 않았다.

## 실제 제한 역할 검증

관리자 세션에서 BEGIN READ ONLY → SET LOCAL ROLE kini_app → private search_path로 전환해
조회한 결과다. 별도 외부 DB 로그인 성공과는 구분한다.

| 검사 | 결과 |
|---|---|
| current_user/current_schema | kini_app / kini_private |
| SUPERUSER / BYPASSRLS | false / false |
| migration 역할 멤버십 | false |
| schema CREATE 권한 | false |
| runtime 소유 private 테이블 | 0개 |
| RLS 비활성 private 테이블 | 0개 |
| migration 이력 | 001_step1_postgresql ~ 009_public_catalog_links, 9개 정확히 확인 |
| 가구 / 구성원 / 재고 | 각각 0행 |
| public 식품 / 레시피 | 347,421행 / 1,152행. 이전 측정 건수와 일치 |
| public 쓰기 가능한 테이블 | INSERT/UPDATE/DELETE/TRUNCATE 권한 기준 0개 |
| private 매핑 UPDATE 권한 | false |
| anon / authenticated private schema USAGE | false / false |
| 신규 public SELECT 정책 | 7개 |

운영 DB에 파괴적 테스트나 합성 가구 삽입을 실행하지 않았다.
INSERT·로그인·가구 격리·AI·전체 추천 정상 동작까지 통과한 것으로 해석하지 않는다.

## 격리 검증

실제 적용 전 Docker의 빈 PostgreSQL 16 DB에서 동일 생성본을 검증했다.
private 소유권, 실제 kini_app 로그인 때 search_path, RLS, 필요한 private INSERT,
public 읽기/쓰기 권한 없음, anon/auth 차단, 기존 public 데이터/정책 보존,
재실행 시 덮어쓰기 거부를 확인했다.
최종 역할 전환 보완을 포함한 재검증 **1 passed / 1.59초**.
테스트 컨테이너는 검증 후 제거한다. 기존 컨테이너는 변경하지 않는다.

## 아직 하지 않은 것

앱 비밀번호를 발급·입력하지 않았다. 역할은 존재하지만 외부 인증용 자격은 미설정이다.
비밀번호는 향후 보호된 경로에서 설정하며 채팅·Git·로그에 넣지 않는다.
서버의 TLS CA·DB URL 설정, c6be1e0 B 런타임 코드와 새 어댑터 통합 및 PostgreSQL 전환,
실제 앱 역할 로그인 후 check_server_db/읽기 CLI와 추천 검증은 남아 있다.

로컬 app/data/kini.sqlite3는 그대로 보존했다. 이전을 선택하면 원본을 백업하고
선정한 데이터를 PostgreSQL 구조로 변환·복사·건수/FK 검증한 다음 서버 연결을 전환한다.
원본 파일을 PostgreSQL 파일로 바꾸거나 삭제하는 작업이 아니며 자동 양방향 동기화도 아니다.
사용자는 이번 메시지에서 이전 의미를 질문했으므로 데이터 이전 승인으로 처리하지 않았다.
