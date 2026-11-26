# 재웅님 전달: 계정·가족 PostgreSQL 적용 준비

작성일: 2026-11-26 (KST). 전달 예정일: **2026-11-27**.
이 문서를 작성하며 메시지를 발송하거나 Supabase에 접속·적용하지 않았다.

## 복사해서 전달할 내용

```text
재웅님, KINI 계정·가족·프로필의 PostgreSQL 이전 코드를 로컬에서 검증했습니다.
내일 기존 Supabase 프로젝트에 적용 가능한지 확인 부탁드립니다.

코드는 codex/postgres-family-profile 브랜치에 있습니다.
가족 API 코드 커밋은 9d3d526이며, 적용할 최종 PR/커밋을 먼저 확인해 주세요.
main에 아직 병합되지 않았다면 사전 확인까지만 진행하고 적용 기준을 확정해 주세요.

우선 확인할 것
1. 기존 프로젝트의 사용량·PostgreSQL 버전·기존 schema/table 구조
2. 기존 테이블과 001/002 migration의 이름·컬럼·타입 충돌 여부
3. 기존 데이터에 영향 없는 검증 DB 또는 별도 비노출 schema 사용 가능 여부
4. migration 역할과 앱 실행 역할, 접속 방식·TLS, anon/authenticated 접근 제한

대상 파일
- db/migrations/001_step1_postgresql.sql
- db/migrations/002_family_profiles.sql
- scripts/run_migrations.py
- docs/PostgreSQL_가족_프로필_이전_검증.md
- docs/Supabase_용량_확인_읽기전용.sql (기존 용량 확인용)

기존 public schema에 바로 실행하지 말고 검증 환경에서 최초 적용·재실행과
계정 등록→로그인→가족 생성→구성원/알레르기·기피 재료 등록·조회부터 확인해 주세요.
다른 가구 접근 차단과 오류 시 rollback도 확인 부탁드립니다.

publishable key와 프로젝트 URL만으로는 서버 DB 연결이 되지 않습니다.
서버 전용 DB 역할·연결 설정은 승인된 환경 안에서 설정해 주세요.
비밀번호·DB 연결 문자열·secret/service_role key는 채팅이나 Git에 보내지 않으셔도 됩니다.

이번 대상은 기초 재고 구조와 계정·가족·프로필입니다.
전체 추천·조리·리포트와 실사용 데이터 이전, AWS 배포는 아직 대상이 아닙니다.
적용한 커밋과 migration 버전, 검증 결과, 충돌/미적용 항목만 공유 부탁드립니다.
유료 전환이나 플랜 변경은 하지 말아 주세요.
```

## 적용자가 확인할 경계

- 001은 CREATE TABLE IF NOT EXISTS를 사용하므로 기존 테이블과의 구조 일치를 보장하지 않는다.
  이름이 같다는 이유로 적용 가능하다고 판단하지 않는다. 다른 팀 데이터·공개 카탈로그를 덮어쓰지 않는다.
- 002는 version 기록을 확인하는 실행기를 통해 한 번만 적용한다.
  일부 SQL만 수동 적용해 테이블과 schema_migrations가 어긋난 경우 먼저 상태를 확인한다.
- 별도 schema를 쓴다면 앱·migration의 search_path와 기존 공개 카탈로그 참조를 함께 확인한다.
  기본 public에 연결한 로컬 시험이 Supabase의 기존 구조와 같다고 가정하지 않는다.
- 앱 실행 역할은 DDL 권한 없이 필요한 테이블/sequence만 접근한다. migration 역할은 분리한다.
- PUBLIC REVOKE만으로 Supabase anon/authenticated의 명시적 grant까지 없어지는 것은 아니다.
  schema/table/sequence 권한과 노출 schema, RLS·서버 역할 정책을 실제 프로젝트에서 확인한다.
- 현재 인증은 앱의 자체 계정·세션이다. account_id를 Supabase Auth uid와 같다고 가정하거나,
  앱 Bearer 토큰을 Supabase JWT로 간주한 RLS 정책을 임의로 만들지 않는다.
- 운영 DB 역할·RLS는 별도 검증 대상이다. 로컬 PostgreSQL의 가구 격리는 서버 검사 결과다.
- 원본 SQLite 개인 DB·건강정보·토큰을 가져오지 말고 합성 가구 2개로 먼저 검증한다.
- 기존 데이터가 있다면 적용 전 복구 방법을 확보한다. 실패 후 테이블을 DROP하거나 개인 DB를 덮어쓰지 않는다.

## 남길 확인 결과

| 항목 | 현재 상태 / 계정주 확인값 |
| --- | --- |
| 프로젝트 사용량·무료 한도 잔여 | 계정주 확인 대기 |
| PostgreSQL 버전·기존 schema 충돌 | 계정주 확인 대기 |
| 적용 기준 PR·SHA / migration 버전 | 적용 시 기록 |
| 검증 위치·서버 실행 역할·권한 검토 | 적용 시 기록, 비밀정보 제외 |
| 등록·조회·다른 가구 차단·rollback | 실제 Supabase 검증 대기 |
| 실제 적용 / 실데이터 이전 | 아직 수행하지 않음 |

추천 데이터 조회 개발은 이 확인을 기다리지 않고 로컬 PostgreSQL에서 진행할 수 있다.

## 2026-11-26 추가: 추천 단위 적용 범위 구분

가족 PR은 #33이며 코드 커밋은 9d3d526, 준비 문서 포함 head는 ca84006이다.
계정·가족만 검증하려면 이 승인된 기준을 고정한다.
이후 로컬 codex/postgres-recommendation-read에는 추천 N1·N2·N3용 003·004 migration이 추가됐다.
해당 브랜치의 run_migrations.py는 001~004 전체를 적용하므로 계정·가족 전용 실행으로 혼동하지 않는다.
추천 schema까지 실제 Supabase에 적용할지는 별도 기준 커밋·기존 schema 충돌·권한 검토 후 확정한다.
관련 완료 범위와 화면 토큰 전달 사항은 PostgreSQL_추천_N1_N3_검증.md를 확인한다.

## 같은 날 조리 단위 추가

가족 PR #33은 75fcf3e, 추천 PR #34는 6b1b5b6으로 main에 병합됐다.
따라서 해당 main의 실행기는 001~004를 적용한다.
별도 codex/postgres-cooking-session에는 005_cooking_sessions가 추가됐으며 아직 main 반영 대상과 구분해야 한다.
해당 브랜치를 적용 기준으로 선택하면 001~005가 적용되지만 조리 완료·섭취·리포트 API까지 지원하는 것은 아니다.
PostgreSQL_조리세션_이전_검증.md의 지원 경로와 C 토큰 전달 사항을 함께 확인한다.

## 006 완료 단위 추가

조리 세션 PR #35는 c8792b6으로 main에 병합되어 해당 main에는 001~005가 있다.
새 codex/postgres-cooking-completion 브랜치는 006으로 확정 출고 단일 연결·조리 완료·섭취 저장을 추가한다.
이 브랜치 실행기는 001~006을 적용한다. 적용 기준 SHA를 고정하고 기존 중복 데이터/인덱스 충돌·권한을 확인한다.
식사 이력/리포트 조회·직접 meal-records 쓰기·운영 RLS 검증까지 완료된 것으로 해석하지 않는다.

## 007 식사 이력·영양 리포트 조회 단위

PR #36은 `73f6847`로 main에 병합되어 해당 main은 001~006을 포함한다.
후속 `codex/postgres-meal-reports`는 기존 섭취 스냅샷을 읽는 이력/상세/리포트 GET API와
007 조회 인덱스를 추가한다. 기존 001~006이나 저장 기록을 변경하지 않는다.
해당 브랜치가 main에 반영된 뒤 적용 기준 SHA를 고정하고 001~007을 검토한다.
직접 `POST /api/meal-records`, 실제 Supabase 역할/RLS, C 화면 연결, 운영 부하 검증은 별개다.
자세한 계약·검증·데모 규모 조회 한계는 `PostgreSQL_식사이력_영양리포트_검증.md`를 확인한다.
