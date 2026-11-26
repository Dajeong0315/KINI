# PostgreSQL 재고 확정 동시성 보완

## 범위

`confirm()`과 `reject()`는 가구에 속한 동일 이벤트 행을 `FOR UPDATE`로 잠근다. 먼저 완료한 요청의 상태를 두 번째 요청이 읽으므로 확정 뒤 거절하거나 거절 뒤 확정할 수 없다. 출고는 그다음 재고 행을 잠그고 잔량을 읽는다. 출고 UPDATE는 가구·재료·잔량 조건도 검사한다. `ingest()`는 기존 `(household_id, client_event_id)` 유일 제약과 `ON CONFLICT DO NOTHING`으로 중복 접수를 처리한다. SQLite는 `BEGIN IMMEDIATE`를 사용한다.

변경된 연결 adapter는 PostgreSQL 식별자와 UPDATE `rowcount`만 제공한다. 스키마의 이벤트 유일 제약과 변경 이력의 `event_id` primary key를 그대로 사용한다.

## 재현 환경과 명령

저장소 루트에서 Docker와 프로젝트 Python 환경으로 실행한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_vision_postgres.ps1
```

스크립트는 임의의 루프백 포트와 메모리 파일시스템으로 일회성 DB를 띄우고, `scripts/run_migrations.py`를 명시적으로 실행한 다음 생성한 컨테이너를 정리한다. 합성 데이터만 적재한다. PostgreSQL 16.15에서 64개가 통과했다. HTTP 시험도 DB 연결에서 `SELECT version()`과 migration 적용 여부를 검사한다.

실제 PostgreSQL 서비스 수준 경쟁 시험은 요청마다 독립 DB 연결을 열고 두 worker를 barrier에서 동시에 출발시킨다. 각 시나리오를 새 합성 상태에서 10회 실행했으며 실패 재시도는 없다.

| 시나리오 | 통과 | 검사 |
| --- | ---: | --- |
| 같은 이벤트와 같은 확정 내용 | 10/10 | 두 응답 일치, 반영 1회 |
| 같은 이벤트와 다른 확정 내용 | 10/10 | 성공 1건, 충돌 1건, 이력·버전 1건 |
| 확정과 거절 | 10/10 | 확정 승리 시 차감 1회, 거절 승리 시 재고 불변 |
| 1,000g에서 700g씩 출고 | 10/10 | 1건만 반영, 잔량 300g, 실패 이벤트 대기 유지 |
| 1,000g에서 400g과 300g 출고 | 10/10 | 처리 순서와 관계없이 잔량 300g, 이력의 전후 수량 연결, 버전 2 |
| 동일 UUID 동시 접수 | 10/10 | 대기 1건, 재고 불변, 다른 내용 재접수 충돌 |

기존 PostgreSQL 저장·권한 시험 1개와 실제 루프백 HTTP 시험 3개도 통과했다. HTTP 롤백 시험은 버전 UPDATE 직후 PostgreSQL 트리거로 오류를 발생시켜 재고·이벤트·변경 이력·버전이 모두 원복되는지 확인한다.

SQLite와 Vision 클라이언트 회귀는 별도 실행한다.

```powershell
$env:KINI_DB_BACKEND = 'sqlite'
python -m pytest -q tests/test_inventory_events.py tests/test_kini_inventory_client.py tests/test_step1_postgres_boundary.py
```

43개가 통과했다. 재고 이벤트 11개, Vision 클라이언트 30개, PostgreSQL 설정 경계 2개를 포함한다.

## 남은 한계

이번 단위는 재고 확정 A 경로만 다룬다. 조리 완료 B에서 재고를 재차감하지 않는 정책, 추천·조리·리포트 전체 PostgreSQL 이전, 운영 role/RLS 배포 구성은 범위 밖이다. 동시성 시험은 합성 데이터와 단일 로컬 PostgreSQL 컨테이너에서 수행했으며 운영 네트워크·pooler 장애 특성은 측정하지 않았다.
