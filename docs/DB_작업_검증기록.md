# A 데이터 플랫폼 작업 검증 기록

2026-11-25 / 팀 저장소 Dajeong0315/KINI / 기준 5d80dc2.
작업 브랜치 codex/data-platform-db. commit/push는 하지 않았다.

- 실제 생성: app/data/kini.sqlite3 (private, 가구 0개), app/data/kini-demo.sqlite3 (별도 시연 데이터).
- 로컬 기본 DB: 테이블 50개, PRAGMA integrity_check=ok, foreign_key_check 위반 0개.
- DB 두 파일은 git check-ignore로 제외 확인.
- Supabase: brian180099 조직에 KINI / 서울 / zyjrwychceuxxfdproev 생성.
- 신규 프로젝트 비용 도구 견적 $0/월. 기존 프로젝트는 변경하지 않았다.
- 원격 migration kini_operational_metrics_v1 적용.
- kini_daily_metrics: RLS=true, anon SELECT=false, authenticated SELECT=false, service_role INSERT=true.
- service_role로 트랜잭션 내 시험 INSERT 후 ROLLBACK 성공. 영구 지표 행 0개 확인.
- Supabase security advisor: INFO 1개(rls_enabled_no_policy). 서버 전용이라 의도적으로 일반 사용자 정책을 두지 않았다.
  근거: https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy
- 실제 Supabase 키를 조회하거나 파일에 저장하지 않았다. Flask REST 업로드는 fake transport로 경계 테스트만 수행.
- 전체 pytest: 90 passed, 1 failed. 기존 test_chatollama_receives_non_thinking_context_parameters가
  xxhash DLL 로딩에 대한 Windows 애플리케이션 제어 정책 차단으로 실패.
- pytest 플러그인 자동 로딩도 같은 DLL에서 실패하여 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1로 실행했다.
- .venv는 DB/API 테스트에 필요한 Python 패키지만 설치했다. STT/TTS 실제 모델·장치 실행은 검증하지 않았다.
- git diff --check 통과.

신규 테스트는 빈 DB 재실행 시 시연 seed 방지, migration 반복 적용, 토큰 해시·만료·폐기,
가구 간 접근과 viewer 쓰기 차단, 참여자 조인, source 이용 조건 검사,
영양 누락값 보존·잘못된 기준량 격리, stage 원자성, publish/rollback 버전,
식사 release 고정, 조리 단계·1인분 영양 보존, 개인정보 필드의 외부 전송 차단, 상태 API를 검증한다.

실제 API 수집·카탈로그 추천 엔진 투영·HTTP 인증·암호화·브라우저 E2E는 수행하지 않았다.
현재는 실제 개인정보 없이 가상 데이터로 개발한다.

## 사용자 추가 확정 및 워크플로우 반영

2026-11-25, 사용자가 '기존 KINI에 영양성분·레시피 DB 테이블 추가'를 확정했다.
앞선 운영 지표 전용 Supabase 설계를 확장했다. 개인정보 로컬 전용 원칙은 유지했다.

- 원격 migration kini_food_catalog_v1: 공개 카탈로그 테이블 11개 추가, public 앱 테이블 총 12개.
- 출처 레지스트리 3행, 영양 코드 6행. 실제 식품/레시피/release는 0행.
- 모든 앱 테이블 RLS=true. anon/authenticated SELECT=false, backend INSERT=true 확인.
- backend 역할로 food/recipe/영양/단계/재료 INSERT와 JOIN을 트랜잭션 내 시험하고 ROLLBACK.
- 음수 영양값, 고아 영양 행, staging release를 active pointer로 설정하는 시도는 제약으로 차단됨.
- 성능 advisor의 FK covering index 누락 2개는 kini_food_catalog_fk_indexes migration으로 보완하고 실제 index 확인.
- 신규 카탈로그 security advisor 문제 없음. 기존 metrics의 no-policy INFO는 이전과 동일.
- 로컬 migration v2: inventory_events, inventory_sync_state, inventory_event_changes 추가.
- 두 로컬 DB 모두 테이블 53개, integrity_check=ok, FK 위반 0개.
- 인식 결과는 자동 확정하지 않고 사용자 확인 후 재고·버전·변경 전후 수량·확인 계정을 원자적으로 저장.
- 가구 소유권, 이벤트 중복/내용 충돌, 거절, 잔량 초과, 동일 요청 재시도, 인증 API를 테스트.
- 별도 SQLite 연결 두 개의 동시 700g 출고 시험에서 1000g 재고에 한 번만 반영되고 300g 잔량 유지.
- 최종 전체 pytest: **101 passed, 1 failed**, 85.04초. 실패는 이전과 같은 xxhash DLL 로딩 차단.
- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 사용. YOLO 학습/실카메라/OpenAI/Supabase REST 자동 동기화는 실행하지 않음.
- 상세 기준은 docs/워크플로우_반영_설계.md와 docs/API_계약서.md에 반영.
