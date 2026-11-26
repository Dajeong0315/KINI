# PostgreSQL 재고 확정 동시성 보완

## 범위

`inventory_events.confirm()`의 PostgreSQL 경로에 이벤트 행 `FOR UPDATE` 잠금과 출고 재고 행 `FOR UPDATE` 잠금을 적용했다. 출고 UPDATE에도 가구·재료·잔량 조건을 넣어 부족 재고가 차감되지 않게 했다. `ingest()`는 `(household_id, client_event_id)` 유일 제약과 `ON CONFLICT DO NOTHING`으로 같은 내용의 동시 접수를 idempotent하게 처리하고, 다른 내용은 기존 `EventConflict` 계약으로 거부한다. SQLite의 `BEGIN IMMEDIATE` 경로와 API 응답은 유지한다.

변경된 연결 adapter는 PostgreSQL 식별자와 UPDATE `rowcount`만 제공한다. 스키마의 이벤트 유일 제약과 변경 이력의 `event_id` primary key를 그대로 사용한다.

## 재현 환경과 명령

무료 로컬 Docker PostgreSQL 16 Alpine 일회성 컨테이너(`kini-pg-concurrency`, host port `55432`), 합성 household/ingredient/item만 사용했다. Supabase/AWS, 외부 AI, 실제 migration 대상 DB는 사용하지 않았다.

```powershell
docker run -d --name kini-pg-concurrency -e POSTGRES_PASSWORD=test -e POSTGRES_DB=kini_test -p 55432:5432 postgres:16-alpine
$env:KINI_DB_BACKEND='postgres'
$env:KINI_DATABASE_URL='postgresql://postgres:test@127.0.0.1:55432/kini_test'
python scripts/run_migrations.py
python -m pytest -q tests/test_postgres_inventory_concurrency.py tests/test_postgres_integration.py
```

동시성 시험은 각 worker가 `open_db()`로 독립 PostgreSQL 연결을 열고 `threading.Barrier`에서 동시에 출발했다. 실행 결과는 5 passed이며, 서버는 `pg_isready`에서 `/var/run/postgresql:5432 - accepting connections`로 확인했다. 시험은 다음을 각각 1회 실행했다: 같은 이벤트 동시 확정, 서로 다른 이벤트의 700g+700g 출고(1건 반영·300g 잔량), 범위 내 400g+300g 동시 출고(2건 반영·300g 잔량), 같은 `client_event_id` 동시 접수 및 다른 내용 충돌.

실패 경로는 부족 재고 예외 후 재고·이벤트 pending 상태·변경 이력·버전이 함께 보존되는 기존 테스트와 PostgreSQL 권한/rollback 경계 테스트로 확인했다. viewer 쓰기 차단 및 다른 household 접근 차단도 기존 PostgreSQL 통합 시험에서 확인했다.

SQLite 회귀:

```powershell
Remove-Item Env:KINI_DB_BACKEND,Env:KINI_DATABASE_URL -ErrorAction SilentlyContinue
python -m pytest -q tests/test_inventory_events.py
```

결과: `11 passed`.

## 남은 한계

이번 단위는 재고 확정 A 경로만 다룬다. 조리 완료 B에서 재고를 재차감하지 않는 정책, 추천·조리·리포트 전체 PostgreSQL 이전, 운영 role/RLS 배포 구성은 범위 밖이다. 동시성 시험은 합성 데이터와 단일 로컬 PostgreSQL 컨테이너에서 수행했으며 운영 네트워크·pooler 장애 특성은 측정하지 않았다.
