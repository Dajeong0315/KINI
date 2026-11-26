# PostgreSQL 1단계 검증·권한 경계

## 이번 단계

1단계는 계정·가구·구성원·기초 재고·식사 세션에 필요한 PostgreSQL 저장 구조와 서버 인증/가구 권한 경계를 준비한다. migration은 `scripts/run_migrations.py`로 명시적으로 실행하며, 요청 처리 중 DDL과 demo seed를 실행하지 않는다.

재고 인식 이벤트의 접수·확정·멱등성 및 A 출고/B 조리 완료 연동은 기존 `inventory_events` 경로를 기준으로 검증한다. 재고 추가 API도 인증된 가구의 쓰기 권한을 요구한다.

## 후속 단계 경계

추천·조리·리포트 전체 테이블의 PostgreSQL 이전, 실제 Supabase migration 적용, 데이터 이전, RLS 운영값 확정은 후속 단계다. 이 단계에서 실제 Supabase나 AWS에는 접근하지 않는다.

## 접속·권한 설계

- 백엔드: Supabase transaction/session pooler의 PostgreSQL 접속 문자열을 서버 환경변수 `KINI_DATABASE_URL`로만 주입한다.
- 개인정보/가구 데이터: 서버 전용 schema/table로 두고, 백엔드 역할만 읽기·쓰기한다.
- `anon`/`authenticated`: 앱이 Supabase REST를 직접 호출하지 않으므로 개인정보 테이블 직접 접근을 허용하지 않는다. 계정주가 Supabase에서 해당 역할의 table/schema 권한을 `REVOKE`하고 필요 시 서버 전용 role에만 `GRANT`한다.
- RLS: 서버가 가구 권한을 검증하는 애플리케이션 경계가 1차 방어선이다. Supabase 적용 시 개인정보 테이블에는 RLS를 추가하고, `authenticated` 직접 접근을 열 경우에는 JWT의 가구 claim과 membership을 함께 검사한다. 현재 로컬 migration은 RLS 운영값을 대신 확정하지 않는다.
- 로그: 토큰·비밀번호·원본 음성·개인정보를 기록하지 않는다.

## 재현 명령

```powershell
$env:KINI_DB_BACKEND='postgres'
$env:KINI_DATABASE_URL='postgresql://...'
python scripts/run_migrations.py
python -m pytest tests/test_step1_postgres_boundary.py -q
```

실제 PostgreSQL 검증은 로컬 PostgreSQL 실행 환경이 준비된 경우에만 수행한다. Supabase에는 이 명령을 실행하지 않는다.
