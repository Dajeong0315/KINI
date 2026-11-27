# 2단계 완료: 서버 PostgreSQL·public 카탈로그 추천 연결

작업·검증일: 2026-11-27 KST. 사용자의 서버 연결·2단계 마무리 명시 승인 범위에서 수행했다.
원본 작업 공간의 변경과 로컬 SQLite는 보존했다. 커밋·푸시는 수행하지 않았다.

## 실제 배포와 저장소

- API: https://kini-b-20261127.tailacd652.ts.net:8443 (기존 tailnet 전용).
- 활성 release: `/opt/kini-b/releases/step2-20261127-327a28242086`.
  `/opt/kini-b/current`를 해당 배포본으로 전환하고 systemd kini-b를 재시작했다.
- 기반 main `eaea05e`와 기존 실제 B 배포 `c6be1e0`의 변경을 작업 worktree에서 통합했다.
  미병합 B 실행기·피드백 flag OFF 코드도 보존했다. main 코드만 덮어쓰지 않았다.
- Git 커밋을 만들지 않았으므로 위 release ID를 커밋 SHA라고 부르지 않는다.
  소스 fingerprint `327a28242086`으로 시작하는 manifest와 152개 파일 SHA256을
  서버 배치 후 및 서비스 전환 후 두 번 대조했고 모두 일치했다.
  archive SHA256 `7a0b5d10e85ffacc9bbc846c1e4eee3299a9ce5ff5535c375c191981494c6bf8`.
- 서버 환경은 PostgreSQL/kini_private 및 public 직접 읽기 어댑터를 사용한다.
  계정 kini_app, migration 001~009, NOSUPERUSER/NOBYPASSRLS, 스키마/테이블 비소유,
  public SELECT 전용 권한을 실제 앱 로그인에서 확인했다.
- session pooler IPv4 5432를 사용한다. 공식 Dashboard의 Supabase CA로
  `sslmode=verify-full`·서버 호스트 검증을 통과했다. 시스템 CA만 사용한 초기 검증은
  custom CA 때문에 실패했으며 검증 강도를 낮추지 않고 공식 CA를 설치했다.
  인증서 파일 SHA256 `700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7`.
- DB 자격은 보호된 서버 EnvironmentFile(root:kini-b, 0640)에만 설정했다.
  사용자 직접 등록 후 앱 역할 인증에 성공했다. 임시 서버 password staging 파일은 제거했다.
  비밀번호·토큰·연결 문자열·개인키를 출력하거나 배포 묶음/Git에 넣지 않았다.

## 최소 시연 데이터와 실제 출처

시연 자료는 새 합성 가구 1개·참여자 1명·재고 3개·앱 작성 메뉴 1개다.
운영 개인 데이터나 로컬 SQLite를 복사하지 않았다. UI/API의 demo_data·확인 필요 표시를 유지한다.

`[시연] 닭가슴살 당근밥 · 원재료 기준`은 설계상 4인분, 쌀 300g·닭가슴살 400g·당근 200g이다.
25분은 시연 메뉴의 설정값이며 실제 조리 시간 측정 결과가 아니다.
식약처 레시피를 임의로 4인분이라고 바꾼 메뉴가 아니라 앱에서 작성한 시연 메뉴다.
열·조리수율·영양 손실은 미측정이며 원재료 기준 영양 합계만 계산한다.

모든 연결은 source mfds_nutrition, active published release
`1d211108-0849-479d-9205-ac5060529431`, approved 출처와 100g 기준을 확인했다.

| 재료 상태 | 실제 food ID | 메뉴 1인분 원재료 g |
|---|---|---:|
| 멥쌀, 백미, 생것 | R101-008000301-0003 | 75 |
| 닭고기, 가슴, 생것 | R109-008000401-0000 | 100 |
| 당근, 뿌리, 생것 | R106-041007601-0000 | 50 |

추천 응답과 저장 재조회 양쪽에서 nutrition_source=catalog_published,
nutrition_release_id·nutrition_food_id가 각 재료에 표시됐다.

| 원재료 기준 1인분 지표 | 실제 API 결과 |
|---|---:|
| 에너지 | 397 kcal |
| 단백질 | 28.14 g |
| 탄수화물 | 63.08 g |
| 지방 | 1.85 g |
| 나트륨 | 59 mg |

기존 API의 반올림 규칙을 유지했다. 별도 reference recipe 3010의 NULL servings는
계속 미확정이며 임의 환산하거나 추천 메뉴로 등록하지 않았다.

최종 실제 건수: public 식품 347,421행 / 레시피 1,152행, private catalog_foods **0행**,
private 명시적 연결 3행. public 원본 행이나 기존 release를 변경하지 않았다.
`deploy/step2_demo_references.sql`은 보호된 private 참조 행만 만들며 현재 release·ID·이름·g 기준이
달라지거나 private 참조 테이블이 이미 채워져 있으면 덮어쓰지 않고 거부한다.

## AI 상태를 구분한 배포

기존 B 실행기는 postgres 저장을 실제 OpenAI 모드와 묶고 있어 DB 연결에도 비용 승인을 요구했다.
이를 분리해 명시적인 rules-only 모드를 추가했다.
현재 추천은 실제 public 영양 조회+기존 안전 규칙이고 AI 설명은 **disabled**다.
fake 설명을 정상 AI 결과로 표시하거나 AI 예외 때 rules-only로 자동 전환하지 않는다.
health/ready는 storage=postgres, ai_mode=rules-only, external_ai=disabled를 표시한다.
Ollama 서비스는 보존했지만 이 모드의 추천·readiness에서는 호출하지 않는다.
실제 AI 성능·오류 검증은 4단계이며 이번 완료 주장에 포함하지 않는다.

현재 규칙 버전과 명시적 rules-only/disabled 표식을 가진 저장 결과만 AI 설명 없이
재조회·조리 생성에 사용할 수 있게 했으며, 기존 AI 출력·catalog 변경 검사는 유지했다.

## 검증

1. 서버의 실제 kini_app 별도 인증 로그인, TLS verify-full, current_schema=kini_private,
   migration 9개 및 check_server_db 통과.
2. 실제 public 읽기 CLI: 당근 10g의 기여 3.3kcal·단백질 0.08g·탄수화물 0.77g·
   지방 0.02g·나트륨 2.5mg. NULL servings 미확정 유지.
3. 기존 서비스를 보존한 채 loopback 5102의 임시 canary에서 로그인→실제 카탈로그 추천→
   저장 GET 재조회 통과. 그 뒤 5101의 기존 서비스로 전환하고 동일 흐름 통과.
4. Windows PC에서 유효한 Tailscale HTTPS를 통해 같은 흐름 통과.
   실제 후보 1개와 위 영양값·3개 source ID·재조회 결과를 확인했다.
5. canary POST+저장 GET 구간 합계 15.452초, 본 서비스 loopback 16.004초,
   PC HTTPS 16.032초. 각 경로 표본 1회이며 로그인/readiness는 이 측정에서 제외했다.
   이 숫자는 단일 추천 요청 지연이나 p95가 아니다. 모델 추론·휴대폰 성능 수치도 아니다.
   개별 요청은 기존 서버 15초 wall/client 20초 예산 안에서 성공했고 예산은 변경하지 않았다.
6. 서비스 journal 최근 15분에서 DB 비밀번호와 합성 로그인 비밀번호의 일치 항목 없음.
   로그 원문을 출력하지 않았다. 모든 외부/플랫폼 로그의 무기록 보증은 아니다.

격리 자동 검증:

- 이전 통합 관련 suite: 85 passed / 105.58초.
- 저장 재조회·조리 연결 수정 후 관련 B/AI/privacy/catalog/cooking suite:
  **78 passed / 122.74초** (Linux Python 3.12.16, 일회용 Docker 검증 이미지).
- 새 빈 PostgreSQL 16 컨테이너에서 신규 schema/역할/RLS→최소 참조→합성 가구/재고→
  제한 역할 로그인→실제 API 경로 추천·저장 재조회: **1 passed / 2.95초**.
  초기 실패로 드러난 앱 metadata 쓰기 시도와 rules-only 저장 재조회 조건을 수정한 최종 결과다.
- 운영 DB에서 TRUNCATE/DROP·오류 주입·AI 장애·가구 간 실패 테스트를 실행하지 않았다.
  실패 경계는 별도 합성 테스트에서 검증했다. 운영에서는 정상 시연 흐름만 실행했다.
- 이번 일회용 PostgreSQL·canary 서비스는 종료했으며 기존 컨테이너는 변경하지 않았다.

## 복구와 남은 범위

이전 release `/opt/kini-b/releases/c6be1e0` 및 이전 합성 SQLite는 그대로 남아 있다.
서버 `/etc/kini-b/step2-rollback.json`에는 이전 release/환경 backup 경로가 있으며 비밀값은 없다.
원래 환경 backup은 `/etc/kini-b/runtime.env.before-step2-20261127`(root 전용)이다.
수동 복구 시 서비스 중지→이전 환경 복원→current 링크를 c6be1e0으로 복원→시작→health 확인 순서다.
이는 이전 synthetic-sqlite/synthetic-ollama 배포로 돌아가는 명시적 복구이며 자동 fake 대체가 아니다.
복구로 PostgreSQL 행을 삭제하거나 public release를 변경하지 않는다.
배포 중 실패 시 이 복구를 실행하는 절차를 준비했으며 실제 실패 롤백/재시작 리허설은 8단계다.

2단계 완료 기준인 실제 카탈로그 연결 추천·영양·출처와 저장 재조회는 충족했다.
로컬 SQLite는 별도 보존했고 백업·변환·건수/FK 검증 계획은 기존 문서에 있다. 실제 데이터 이전은 하지 않았다.
휴대폰 화면 저장·재조회와 재고 정규화 가구 인증 보완은 3단계이며 이번 API 검증과 구분한다.
