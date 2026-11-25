# KINI A 파트 개발 현황 및 인수인계

기준일: 2026-11-25 (Asia/Seoul) / 담당: A 데이터 플랫폼·연동.
팀 저장소: https://github.com/Dajeong0315/KINI
시작 기준: main `5d80dc2`. 이 문서는 이번 브랜치의 코드·원격 DB 작업·문서와 남은 통합을 정리한다.
공유 브랜치: `codex/a-data-platform-specs`.
전달 경로: 팀 저장소에 대한 현재 계정의 push 권한이 없어, 팀 저장소의 기존 fork인
`brian180099/KINI`에 같은 이름의 새 브랜치를 올리고 `Dajeong0315/KINI:main` 대상 draft PR로 전달한다.
팀 origin과 기존 fork main은 변경하지 않는다. 새 브랜치의 시작 기준은 팀 main이다.
main 병합 전 상태이며 실제 서비스 완료 선언이 아니다.

## 1. 확정된 방향

- 실제 ThinQ 인증·토큰 발급·기기 연결 없이 ThinQ 스타일 데모를 만든다.
- 기기/인식 유입은 mock부터, 재고·식사 데이터 변경은 실제 로컬 API/DB로 동작시킨다.
- 개인 프로필·알레르기·원문·실재고·입출고·섭취·세션은 SQLite 로컬 전용이다.
- 사용자가 추가 확정한 대로 기존 KINI Supabase에 공개 영양성분·레시피 테이블을 생성했다.
- Supabase에는 공개/승인 카탈로그와 비식별 운영 지표만 저장한다.
- 첨부 그림의 가구별 입출고 이력 클라우드 업로드는 로컬 저장으로 조정했다.
- 추천·AI 정책 B, 화면 HTML/CSS/JS C 소유권을 유지한다.

## 2. 이번 브랜치의 코드 변경

| 영역 | 변경 | 현재 확인 |
|---|---|---|
| DB 생성 | private/demo를 분리하는 init script, seed 차단 표식 | 빈 DB 재실행 시 demo 미주입 |
| migration | 공개 카탈로그·계정/세션·재고 이벤트·감사/버전 추가 | 반복 실행·기존 데이터 보존 시험 |
| 로컬 인증 repository | scrypt 계정, 토큰 해시·만료·폐기·가구 역할 | 가구 격리·viewer 쓰기 차단 |
| 식사 연결 helper | 세션 가구 참여자 검사·release 고정 | B handler 연결은 아직 없음 |
| 인식 이벤트 | UUID별 수신, pending/확인/거절 | 재시도·내용 충돌 시험 |
| 재고 변경 | lot 입고·잔량 출고·즉시 잠금·감사 기록 | 동시 출고 중 잔량 초과 차단 |
| A API | 상태/이벤트/확인/거절/SQLite 재고 조회 5개 | 보호 경로의 로컬 토큰 검사 |
| 로컬 import | stage/validate/quarantine/publish/rollback | 영양 누락·기준량·권리 승인 검사 |
| 정규화 | COOKRCP01 저장 응답→레시피/단계/1인분 영양 | 실제 API 대량 호출은 없음 |
| 지표 adapter | 고정 필드 일별 합계 REST upsert | fake transport로 개인정보 전송 차단 |
| 환경/시작점 | .env.example 옵션·A blueprint 등록 | 기존 B handler/정책과 화면 미수정 |

## 3. 실제 Supabase 작업

- 조직: brian180099 / 프로젝트: KINI / 서울 ap-northeast-2.
- 프로젝트 ref: zyjrwychceuxxfdproev.
- 대시보드: https://supabase.com/dashboard/project/zyjrwychceuxxfdproev/editor
- 생성 당시 도구 견적 월 $0. 향후 사용량/요금은 별도 확인이 필요하다.
- migration: kini_operational_metrics_v1, kini_food_catalog_v1, kini_food_catalog_fk_indexes.
- 운영 지표 1개 + 공개 카탈로그 11개 = public 앱 테이블 12개.
- 출처 레지스트리 3행, 영양 코드 6행만 등록. 실제 식품/레시피/release 데이터는 0행이다.
- 모든 테이블 RLS=true, anon/authenticated SELECT=false, backend INSERT=true 확인.
- backend의 시험 INSERT/JOIN과 음수값/고아 FK/staging pointer 거절을 transaction 내 검증 후 rollback했다.
- FK covering index 누락 2건을 추가 migration으로 보완했다.
- 기존 metrics의 RLS no-policy INFO는 서버 전용 접근을 위한 의도적 설정이다.
- 실제 secret key를 조회·출력·저장하지 않았다. Flask 자동 REST 전송/카탈로그 sync는 아직 연결하지 않았다.

원격 DB 변경은 이미 해당 프로젝트에 적용돼 있다. Git 브랜치 병합을 기다리는 migration과
혼동해 같은 DDL을 다시 적용하지 않는다. SQL 파일은 원격 migration 내용의 검토/재현용 기록이다.

## 4. 로컬 산출물과 Git 제외

로컬 `app/data/kini.sqlite3`(빈 private)와 `app/data/kini-demo.sqlite3`(시연)을 생성했다.
두 DB는 테이블 53개, integrity_check=ok, FK 위반 0개를 확인했다.
실제 DB 파일은 Git 제외이며 다른 팀원이 clone하면 자동으로 전달되지 않는다.
`.venv`, `.env`, 비밀키·음성·모델도 이 브랜치에 포함하지 않는다.

```powershell
python scripts/init_database.py
python scripts/init_database.py --path app/data/kini-demo.sqlite3 --demo
```

기존 파일에는 init script를 다시 실행하지 않는다. 앱 repository 초기화가 additive migration을 수행한다.
데모 앱은 환경 설정에서 KINI_DB_PATH를 별도 demo 파일로 지정한다. 실제 개인정보는 아직 입력하지 않는다.

## 5. 검증 결과

- 마지막 전체 실행: **101 passed, 1 failed / 85.04초**.
- 기존 실패: test_chatollama_receives_non_thinking_context_parameters.
- 원인: Windows 애플리케이션 제어 정책에서 xxhash DLL 로딩이 차단되어 langchain_ollama import 실패.
- 테스트 코드를 skip하거나 보안 정책을 우회하지 않았다.
- 같은 DLL이 pytest 플러그인 자동 로딩도 막아 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1로 실행했다.
- Python 가상환경의 pytest는 저장소 요구 범위인 8.4.2다.
- 두 SQLite 무결성·FK, Supabase RLS·권한·시험 transaction, git diff --check 확인.
- A 명세서·기능·아키텍처 문서와 C 연결용 mock JSON fixture를 추가했다.
- 문서 6개 상대 링크·JSON 예시 14개·fixture 3개 형식 점검. 상태/이벤트 fixture를 실제 임시 DB API 응답과 대조했다.
- STT/TTS 실제 모델·YOLO 학습·카메라·ThinQ·OpenAI·휴대폰/Tailscale E2E는 검증하지 않았다.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q
```

상세 이력: [DB 작업 검증 기록](DB_작업_검증기록.md).
위 결과는 팀 저장소에서 실행한 결과다. 과거 개인 저장소의 '26개 통과' 결과와 혼용하지 않는다.

## 6. 구현과 계획의 차이

| 남은 작업 | 담당/협업 | 완료 기준 |
|---|---|---|
| 로그인 HTTP·전체 객체 권한·보존/암호화 | A+B+C | 실제 개인정보 입력 전 전 경로 검증 |
| JSON 재고와 SQLite 기준 통일 | A+B+C | C가 SQLite API 사용, 모든 쓰기 버전 정책 합의 |
| 대기 이벤트 목록/상세·재인식 UX | A+C | 새로고침 후 대기 이벤트 복원 |
| 가상 기기·온도/문·지연 상태 | A+C | 실제 연결 없이 명확한 시연 표시 |
| 식약처 실제 batch 수집 | A | 승인 키·응답 fixture·호출 제한·재시도·출처 검토 |
| 만개의레시피 연동 | A | 공개 API/제휴/파일 권한과 schema 확정 |
| Supabase writer/downloader | A | release UUID·권리/검증 보고서 변환·원자적 pointer·실패 복구 |
| 카탈로그→추천 투영 | A+B | 검토 매핑·단위·알레르기 정책을 통과한 재료만 사용 |
| Redis provider/TTL | A | fake cache부터 hit/miss/만료·무효화 시험 |
| Ollama 보안 게이트/OpenAI | B | allowlist·외부 전송 차단·api_error 추천 중단 |
| YOLO 클래스·95% 기준·추적 | 인식 담당 합의 | 검증셋/지표와 이벤트 생성 규칙 확정 |
| 모바일 흐름·E2E | C, A/B 지원 | 참여자→추천→조리→섭취·오류 시나리오 통과 |

## 7. 팀원이 먼저 읽을 문서

1. [A API 명세서](A_API_명세서.md): 실제 경로·JSON·권한·오류·재시도와 미구현 계약.
2. [A 기능 명세서](A_기능_명세서.md): 기능 ID·구현 상태·정상/예외·완료 기준.
3. [A 아키텍처](A_아키텍처.md): 구성도·ERD·시퀀스·개인정보 경계·원격/로컬 흐름.
4. [공통 API 계약서](API_계약서.md): 담당별 계약 진입점.
5. [워크플로우 반영 설계](워크플로우_반영_설계.md): 첨부 그림과의 차이·추가 합의.
6. [DB 상세 설계](DB_아키텍처_및_연동_설계.md): 기존 SQLite 분석·조인·운영 명령.

## 8. 통합 주의

이번 코드의 인증 helper가 기존 B API까지 자동 보호하지 않는다.
data_status의 http_enforced=false와 inventory_events_http_enforced=true를 함께 읽는다.
인식 확인 완료는 알레르기/신선도 검증 완료가 아니다.
Supabase 표가 생성된 것과 실제 식품 데이터가 적재·추천에 연결된 것은 구분한다.
현재 AI 경로는 main의 로컬 Ollama이며 OpenAI 중심 목표 아키텍처와 차이가 있다.
브랜치에는 A 기반 구현과 문서를 올리고 main 병합은 별도 리뷰로 진행한다.
