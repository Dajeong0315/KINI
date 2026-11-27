# 서버 DB 연결 준비

2026-11-27 KST. 확인 기준: GitHub `main` `73e1fab2ebf739351b6aec656f9f5d74cd121245`.
서버 주소를 받기 전 준비한 파일이다. 실제 Supabase migration, 데이터 이전, 서버 생성·배포는 실행하지 않았다.

## 직접 확인한 현재 상태

- GitHub: `Dajeong0315/KINI`, main과 `feature/fridge-vision-yolo` 브랜치 확인.
- main에는 psycopg 연결, 명시적 migration 실행기, 001~008 및 가족·재고·조리·섭취 PostgreSQL 코드가 있다.
- Supabase `zyjrwychceuxxfdproev`: 서울, ACTIVE_HEALTHY, PostgreSQL 17.12.
- 읽기 전용 측정 당시 DB 166,819,507 bytes(약 159 MiB). 플랜·egress·청구상 잔여 용량은 이 수치로 판단하지 않는다.
- Storage objects 0개. 공개 카탈로그의 통계상 식품 약 342,351개·레시피 약 1,152개. 통계값이며 정확한 전수 집계는 아니다.
- public에는 공개 카탈로그가 있고 가족·재고·식사 운영 테이블은 없다. public 카탈로그 테이블의 RLS 활성화를 확인했다.
- 실제 로컬 개인 SQLite 파일은 저장소에 없다. GitHub에서 스키마·코드는 확인 가능하지만 미커밋 로컬 데이터는 확인할 수 없다.

## 저장 위치와 기존 카탈로그

서버는 기존 Supabase에 접속한다. Supabase 전체를 EC2 안에 복제하는 구성은 아니다.
SQLite는 개발·백업용이며 운영 쓰기 원본은 PostgreSQL 한 곳으로 전환한다.

현재 001~008을 기존 Supabase public에 그대로 적용하면 안 된다.
`data_releases`, `source_records`, `catalog_foods`, `active_data_releases`, `data_sources` 등 이름이 겹치고,
기존 cloud 카탈로그는 UUID·JSONB·압축 영양 컬럼을 사용하지만 앱 migration은 TEXT·별도 영양 테이블을 사용한다.
이름이 같다는 이유로 호환되는 것이 아니다.

`KINI_DB_SCHEMA=kini_private`로 앱 영역을 분리한다. 연결 search_path는 지정 schema와 pg_catalog뿐이며
public로 자동 fallback하지 않는다. schema는 관리자가 명시적으로 생성해야 한다.
기존 공개 카탈로그는 그대로 보존한다. **이 분리만으로 public 카탈로그가 추천 API에 연결되지는 않는다.**
기존 추천 provider에 맞는 읽기 adapter·승인된 재료/레시피 mapping은 후속 연결 작업이다.
현재 private migration의 비어 있는 카탈로그를 기존 34만 식품 데이터와 혼동하지 않는다.

## 서버 적용 순서

1. Linux 서버에 저장소를 `/opt/kini`로 배치하고 전용 OS 계정 `kini`를 준비한다.
   Python 3.13 가상 환경에서 `pip install -r deploy/requirements.txt`로 설치한다.
   음성 의존성 전체 설치·모델 파일과 실제 서버 RAM은 대상 Linux에서 별도 검증한다.
   현재 requirements의 범위 지정은 전체 의존성 lockfile이 아니다.
2. DB 관리자가 migration 소유 역할과 앱 로그인 역할을 분리하고, 비노출 `kini_private` schema를 migration 역할 소유로 만든다.
   앱 역할은 NOSUPERUSER·NOBYPASSRLS, schema USAGE만 받고 CREATE는 받지 않는다.
   앱 역할에 필요한 테이블 DML·sequence USAGE와 schema_migrations SELECT를 허용한다.
   schema owner·table owner가 앱 역할이 되지 않게 한다. 가구 격리는 기존 서버 인증·객체 검사로 검증한다.
   개인 schema를 Data API exposed schemas에 넣지 않고 PUBLIC/anon/authenticated 접근을 허용하지 않는다.
   RLS를 적용한다면 앱 역할의 실제 접근 모델에 맞는 정책을 추가하고 검증한다.
   기존 자체 계정 토큰을 Supabase Auth JWT로 가정하지 않는다.
3. `deploy/server.env.example`을 `/etc/kini/server.env`로 복사해 관리자만 읽게 설정한다.
   DB 비밀번호는 채팅이나 Git에 넣지 않고 이 파일/비밀 관리 경로에서 입력한다.
   Dashboard Connect에서 direct 또는 session pooler 연결을 가져오고 `sslmode=verify-full`과
   `sslrootcert=/etc/kini/supabase-ca.crt`를 지정한다. CA도 서버에 배치한다.
   migration 역할 연결은 앱 환경 파일과 분리한 관리자 환경으로만 설정한다.
4. migration 역할의 환경에서 KINI_DB_BACKEND=postgres, KINI_DB_SCHEMA=kini_private,
   KINI_DATABASE_URL을 설정하고 `python scripts/run_migrations.py`를 명시적으로 실행한다.
   실행기는 비어 있지 않고 KINI 이력이 없는 schema를 거부한다. 서비스 실행 중 자동 migration은 없다.
   기존 KINI 이력이 있는 schema의 임의 수동 변경까지 자동 검증하는 도구는 아니다.
5. 앱 역할 환경으로 `python scripts/check_server_db.py`를 실행한다. 환경 파일은 자동 로드하지 않으므로
   관리자 세션에서 환경으로 주입하거나 systemd의 EnvironmentFile로 전달한다.
   TLS 설정·접속·schema·버전·제한된 역할·대표 읽기를 검사한다. 실패 출력은 자격정보를 포함하지 않는다.
   이 검사는 전체 테이블 권한, 가구 격리, 카탈로그 mapping, AI 준비를 보증하지 않는다.
6. 합성 가구 2개로 로그인·가구 간 차단, 동시 출고·중복·부족·조리 완료 재시도를 확인한다.
   저장소의 PostgreSQL 통합 테스트에는 TRUNCATE가 있으므로 **실제 서비스 DB에서 실행하지 않는다.**
7. `output/audio` 디렉터리를 생성해 kini가 쓰게 하고 코드는 쓰지 못하게 한다.
   `deploy/kini.service`를 설치해 daemon-reload 후 시작한다.
   서비스는 127.0.0.1:5000에만 바인딩하며 시작 전 DB 점검을 수행한다.
   이 시작 전 점검은 지속적인 health/readiness endpoint를 대체하지 않는다.
   Ollama 설치·시작 및 모델 준비는 별도다. API 1 worker/2 threads는 모델 추론 동시성 제한 구현을 대신하지 않는다.
8. 서버 주소가 확정되면 Tailscale Serve/HTTPS 접근과 화면 API 연결을 설정한다.
   API·Ollama·DB 포트를 인터넷 전체에 공개하지 않는다. 중지는 `systemctl stop kini`로 한다.

## SQLite 데이터 준비

원본 DB에서 다음 명령으로 일관된 SQLite snapshot을 만든다. 실행 중 WAL 기록도 포함한다.
기존 목적지 덮어쓰기는 거부하며 integrity_check와 SHA256을 확인한다.

```text
python scripts/backup_sqlite.py --source <원본.sqlite3> --destination <보호된경로/이전용.sqlite3>
```

이 파일은 개인정보·건강정보·세션을 포함할 수 있다. Linux에서는 0600으로 만들고,
Windows에서는 대상 폴더 ACL을 제한한다. GitHub 업로드나 일반 공유 링크로 전달하지 않는다.
백업 도구는 PostgreSQL 변환기가 아니다. 실제 이전 전 원본 schema·행 수·참조 관계를 확인해
보존할 계정/가구/재고/식사 테이블을 매핑하고, UUID·boolean·timestamp·identity/sequence를 변환·검증해야 한다.
세션 토큰은 이전하지 않고 재로그인한다. 원본을 보존한 채 빈 목적지로 이전하고,
건수·FK·샘플·재고 일치 검증을 마친 뒤 쓰기를 전환한다. 실패 시 원본 운영으로 복귀한다.

## 검증 범위

검증 결과는 아래에 추가한다. Supabase는 메타데이터·집계만 읽었으며 DDL/DML은 실행하지 않았다.
실데이터 변환·기존 public 카탈로그 adapter·실제 서버 연결은 아직 완료하지 않았다.

2026-11-27 검증:

- Windows Python 3.13.14: 백업/WAL·덮어쓰기 금지·schema 입력 검증·TLS/seed 검사와 기존 저장 경계 검사 13개 통과.
- Windows의 psycopg binary DLL은 OS 응용 프로그램 제어로 로드되지 않아 실제 DB 검증은 별도 Linux 컨테이너에서 수행했다.
- Linux Python 3.13 + psycopg 3.3.9 + 로컬 일회용 PostgreSQL 17: kini_private에 001~008 최초 적용·재실행 통과.
- 기존 public 테이블을 둔 별도 schema 적용 시 DDL 거부 및 테이블 보존 확인.
- 가족·재고 동시성·조리완료·식사리포트 첫 통합 검사 130개 통과, 임시 schema 지정 테스트 4개 실패.
  실패 원인은 기존 PGOPTIONS 기반 테스트가 명시적 KINI_DB_SCHEMA 설정을 바꾸지 못한 것이었다.
  migration subprocess 테스트 5곳을 KINI_DB_SCHEMA 지정으로 갱신했다.
- 실패 4개 및 영향받는 조리 세션·재료 도구 검증을 포함한 재검증 71개 전부 통과.
- Linux에서 Flask 앱 import와 Gunicorn --check-config 통과. 연결 미설정 상태의 시작 전 검사 비정상 종료와 비밀정보 없는 상태 출력 확인.
- systemd 실제 시작, Supabase 앱 역할/TLS 실접속, 전체 음성 의존성·모델 설치, 실제 SQLite 데이터 이전은 미검증.
- 합성 검증용 컨테이너만 사용했으며 실제 프로젝트 데이터는 쓰지 않았다.

공식 연결 기준: https://supabase.com/docs/guides/database/connecting-to-postgres
