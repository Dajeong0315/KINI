# B 텍스트 클라우드 런타임

작업일 2026-11-27 KST. A의 PR #39 (`eaea05e`) 위에 독립 B 실행기를 추가한다.
`app/main.py`, 공용 requirements/환경 파일, A 저장·migration, C 화면, 신규 식후 피드백을 변경하지 않는다.
실제 배포 결과는 별도 검증 기록에 쓴다. 이 안내 자체가 배포 완료 증거는 아니다.

## 실행 구성

- 전용 `/opt/kini-b/releases/<commit>`와 `current` 심볼릭 링크, 서비스 `kini-b`.
- Python 3.12, 텍스트 의존성만 `requirements.lock.txt`로 설치. 음성·영상 모델 및 자연어 parser는 열지 않는다.
- Gunicorn 1 worker / gthread 4, API `127.0.0.1:5101`.
- 요청별 spawn 프로세스에서 기존 Flask blueprint·가구 Bearer 인증·추천 규칙·기존 AI enrichment·저장을 실행한다.
- 단일 admission gate, 대기열 없이 busy 503. 기본 전체 요청 예산 15초, 최대 18초.
- Ollama loopback 전용, 검사 connect 1초/read 10초, 외부 OpenAI 5초. 전체 wall deadline 도달 시
  요청 프로세스를 종료/kill하여 이후 외부 전송·DB 후속 작업을 막는다. 응답 504 `api_error`, 후보 없음.
  종료 정리에 최대 약 0.4초가 추가될 수 있다. 늦게 완료된 쓰기는 재시도 전 GET으로 상태를 확인한다.
- 필수 검사 누락/예외/비정상 반환은 `api_error`; 민감 입력·검사 거부는 `privacy_blocked`.
  두 경우 모두 후보 없음. wire schema 검증과 request scoped code·출력 후보 검증을 유지한다.
- 모델 입력/출력·예외·HTTP URL/헤더/본문은 기록하지 않는다. Gunicorn access/error와 서비스 stdout/stderr를
  저장하지 않는다. 따라서 앱 로그 회전 대상도 없다. 운영 진단은 상태·카운트만 수동 수집한다.
  Ollama override 역시 본문 로그를 저장하지 않는다. 별도 시스템 journal은 OS 정책을 따른다.

## 모드와 저장소

| 설정 | 내부 검사 | 외부 설명 | 용도 |
|---|---|---|---|
| `synthetic-fake` | 명시적 fake | fake | 오프라인 합성 시험 |
| `synthetic-ollama` | 실제 내부 Ollama 필수 | 명시적 fake | 첫 CPU 서버 검증 |
| `real` | 실제 내부 Ollama 필수 | 실제 OpenAI | 승인 프로젝트·잔액·키/모델 확인 후 최소 합성 호출 |

`KINI_B_STORAGE=synthetic-sqlite`는 신규 fixture와 `synthetic-only` marker를 요구한다.
실제 DB 복사·운영 원본·가짜 저장 성공 대체로 사용하지 않는다. SQLite 초기화의 기존 DDL은 이 격리 환경에만 존재한다.
`postgres`는 A의 실제 적용·TLS·제한 역할·schema·migration 점검을 통과해야 시작한다.
공개 카탈로그 adapter와 운영 데이터 이전이 완료됐다는 보장은 아니다.
`KINI_B_EXTERNAL_APPROVED=1`은 계정 소유자가 실제 조직/프로젝트·잔액을 확인한 뒤에만 설정한다.
실제 모드에서 자동 fake 전환은 없다. health는 모드를 항상 표시한다.

## 설치·시작

승인한 Ubuntu 24.04 서버에 Python 3.12 venv와 curl이 필요하다. 검토한 소스만 전송하고
`.env`, `.git`, 원본 DB, 키, 음성·이미지를 배포 묶음에 넣지 않는다.

```sh
sudo apt-get update
sudo apt-get install -y python3-venv curl
# /opt/kini-b/releases/<commit>에 소스 배치 후 해당 디렉터리에서:
sudo sh deploy/b/install.sh
sudo ln -s /opt/kini-b/releases/<commit> /opt/kini-b/current
# 새 합성 DB를 전용 계정으로 초기화. 로그인/비밀번호는 대화형 입력, 출력 없음.
sudo -u kini-b env KINI_DB_BACKEND=sqlite KINI_DB_PATH=/var/lib/kini-b/synthetic.sqlite3 \
  /opt/kini-b/venv/bin/python scripts/b_synthetic_store.py
# /etc/kini-b/runtime.env를 관리자 전용 경로에서 설정한 뒤:
sudo systemctl start kini-b
curl --fail http://127.0.0.1:5101/health/live
curl --fail http://127.0.0.1:5101/health/ready
```

Ollama는 공식 배포판을 설치하고 버전을 기록한다. 전용 서버에서만
`ollama-override.conf`를 `/etc/systemd/system/ollama.service.d/kini-b.conf`에 설치한다.
기존 공용 Ollama를 운영하는 서버에서는 소유자와 영향 범위를 먼저 맞춘다.
`systemctl daemon-reload`, `systemctl restart ollama` 후
`ollama pull qwen2.5:3b-instruct-q4_K_M`으로 후보 모델 한 개만 준비한다.
최초 모델 준비/로딩은 15초 요청 밖에서 관리자 작업으로 수행한다.
모델명·digest·Ollama 버전과 준비 요청 시간을 실제 서버 기록에 남긴다.
관리자 warmup은 `OLLAMA_MODEL=qwen2.5:3b-instruct-q4_K_M /opt/kini-b/venv/bin/python scripts/b_warm_model.py`로
실행한다. 최대 connect 2초/read 40초이며 모델 원문을 출력하지 않는다. 이는 개인정보 검사 통과를 대신하지 않는다.
실측 cold load 약 2.3초와 첫 추천 payload 검사에서 5초 초과를 확인해 검사 read 10초,
readiness 전체 wall 12초를 사용한다. 개별 상한의 합과 무관하게 추천 전체 wall 15초가 우선한다.
추천 API 전체 15초·C 20초는 유지한다. 5분 이상 idle 후 unload될 수 있으며 readiness는 cold load를 포함해 재검사한다.
`/health/ready`는 DB 대표 조회와 필수 검사 실제 추론을 수행한다. 모델이 아직 로딩 중이면 503/504가 정상이다.
`/health/live`의 200만으로 DB·모델 준비를 판단하지 않는다.

## Tailscale와 C 접속

기존 tailnet 플랜·장치 여유·HTTPS 및 ACL을 확인하고 서버를 승인된 tailnet에 등록한다.
등록 자격 증명을 문서·명령 이력·로그에 넣지 않는다.
기존 Serve 구성을 먼저 조회하여 충돌을 피한다.

```sh
sudo tailscale serve status
sudo tailscale serve --bg --https=8443 http://127.0.0.1:5101
# 출력된 https://<server>.<tailnet>.ts.net:8443 사용, Funnel 사용 금지.
```

tailnet 내부 HTTPS에서도 기존 로그인 `/api/auth/login`과 가구 Bearer token이 필요하다.
허용 경로: 구성원 GET, 추천 POST/GET, 식사 세션 POST, 조리 생성/조회/재료 변경/완료.
음성, 일반 채팅, 임의 재고 변경, 신규 피드백 경로는 이 실행기에서 제공하지 않는다.
조리 완료에는 A에서 확정한 출고 event가 필요하다. A 출고 경로는 별도 계약이며 무인증 대체가 없다.
C는 추천·조리 호출에도 Bearer를 붙이고 현재 20초 timeout을 유지한다. 90/120초안은 적용하지 않았다.
실제 서버에서 정상 응답이 이 시간 안에 가능한지 측정 전에는 화면 연결 완료로 표시하지 않는다.
허용 origin을 임의로 확장하지 않았다. 별도 origin 화면 연결의 CORS/프록시 계약은 C/A와 합의한다.

## 검증·중지·복구

```sh
/opt/kini-b/venv/bin/python scripts/b_runtime_smoke.py http://127.0.0.1:5101
# tailnet 장치에서도 같은 스크립트에 HTTPS URL 사용
sudo systemctl restart kini-b
sudo systemctl is-active kini-b
sudo systemctl stop kini-b
sudo tailscale serve --https=8443 off
# 필요할 때 모델도 중지 (전용 서비스일 때만):
sudo systemctl stop ollama
```

합성 장애 재현: 환경 파일에 `KINI_B_SYNTHETIC_EXTERNAL_FAULT=error` 또는 `timeout`을 설정하고
재시작한 뒤 smoke `--expect api_error`. 시험 후 변수를 제거하고 재시작/정상 확인한다.
이 설정은 real 모드에서 시작을 거부한다. 실제 OpenAI 장애를 검증했다고 표현하지 않는다.
필수 모델 장애는 전용 Ollama를 중지하거나 존재하지 않는 모델로 설정한 뒤 같은 실패 검증을 한다.
차단 시 외부 미호출은 계측된 단위/통합 테스트로 검증하며 cloud 검증 기록에 시험 방식도 구분한다.

복구는 서비스 중지 → 이전 검증 release로 current 링크 교체 → 시작 → readiness와 인증 smoke 순서다.
실제 DB schema rollback은 수행하지 않는다. timeout 후 쓰기 결과를 잃었을 수 있으므로
조리 완료를 새 요청으로 재생성하지 말고 기존 세션 GET을 확인한다.
EC2를 쓰지 않으면 콘솔에서 **중지**한다. 디스크는 중지 중에도 과금/크레딧 소모된다.
자동 중지 예약·유료 전환·발표 일정 변경은 이 구성에서 만들지 않는다.

## A 및 동시 VS Code 인수인계

식후 피드백 연결 코드는 후속 통합했지만 `KINI_B_MEAL_FEEDBACK_ENABLED=0`이 기본이다.
비활성 상태에서는 피드백 GET/PUT은 404이며 B readiness가 meal_feedback를 요구하지 않는다.
승인 선택지 `meal-feedback-options.v1.json`을 설치 도구가 `/etc/kini-b/`에 복사하며
`KINI_MEAL_FEEDBACK_OPTIONS_FILE`로 연결한다. mock_/test_ 코드는 설정 파일 loader가 거절한다.
선택지 재승인·개인화 가중치 합의는 저장 활성화 조건이 아니다.
실제 스키마·제한 앱 역할의 SELECT/INSERT/UPDATE가 준비된 후에만 flag를 1로 켜고
ready/인증 GET·PUT·가구 격리를 검증한다. 요청 시 스키마를 만들거나 fake 저장으로 대체하지 않는다.
필수 준비 전 cloud flag는 0으로 유지한다. 별도 독립 앱도 동일 승인 파일을 명시적으로 사용할 수 있다.

A PR #39는 코드·점검 도구 준비이며 실제 Supabase private schema/migration/제한 역할 적용은 별도다.
A 적용 담당이 관리 역할로 schema와 001~008을 적용하고, runtime 역할·검증 TLS CA를 서버의 비밀 경로에 설정한 뒤
`scripts/check_server_db.py` 통과와 합성 가구 2개의 격리 검증을 전달해야 한다.
기존 public 카탈로그 호환 adapter·승인 mapping·실데이터 변환·운영 전환도 A 담당 후속 항목이다.
B는 이 작업에서 A의 DDL·재고 로직이나 운영 데이터에 쓰지 않는다.
VS Code 식후 피드백 커밋 3개는 사용자 후속 지시로 배포 브랜치에 반영했다.
인증·PUT allowlist·bounded dispatcher 통합을 검증하되 A 운영 저장 기반 준비 전 기능을 활성화하지 않는다.

공식 근거: [EC2 무료 플랜 대상](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html),
[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve).
