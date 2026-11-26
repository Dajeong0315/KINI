# 추천 PostgreSQL 이전 N1·N2·N3 완료 기록

작업·검증일: 2026-11-26 (KST).
후속 조리 세션 단위는 `PostgreSQL_조리세션_이전_검증.md`를 확인한다.
해당 기능 브랜치에서는 식사 참여자 세션·조리 생성/조회/재료 변경이 추가로 열리며,
아래 501 설명은 N3 완료 시점의 지원 경계로 보존한다.
브랜치: `codex/postgres-recommendation-read`.
가족·프로필 코드 `9d3d526`을 보존하고, 원격 main `d94d797`의 팀 기록 추가도 통합했다.

## 완료한 세 단위

| 단위 | 구현·종료 기준 |
| --- | --- |
| N1 — 조회 | `003_recommendation_read.sql`, 레시피·구성 재료·영양 조회 함수, 승인 공개 카탈로그와 단위·출처 검증 |
| N2 — 규칙 | `004_recommendation_dependencies.sql`, 식사·영양 이력·알레르기·공식 매핑·개인화 의존성 및 PostgreSQL 날짜·NULL 비교, `recommend(..., persist=False)` |
| N3 — 저장·HTTP | 추천 요청·후보·노출/선택 이력 원자 저장, 실제 HTTP 생성·조회·피드백·가구 권한, 오류/개인정보 차단/stale 처리 |

N1 커밋 `047a64a`, N2 커밋 `fc1d998`. 이후 최종 통합에서 보관 지침의 최신 행 선택,
비유한 인분·재고 수량 차단, 추천 저장 중 release 변경 검사와 동시 요청 보호를 보완했다.
기존 001·002는 변경하지 않았다. 003·004는 아직 실제 Supabase에 적용하지 않았다.

## 저장 구조와 재사용

- N1 공개 테이블은 기존 catalog_* 계약의 구조다. payload_json은 Python json.loads와 호환되는 TEXT로 두고 PostgreSQL JSON 검사를 적용했다. release는 기존 UUID 문자열 계약을 유지한다.
- ingredient_nutrition의 기준량은 g, recipe_ingredients의 중량은 레시피 전체량, nutrition_per_serving은 1인분이다. 조회 시험과 SQLite 비교로 이를 확인했다.
- catalog_context는 DB 행의 키가 아닌 명시적 컬럼 값을 사용한다. 승인 철회·release 변경을 정상 감지한다.
- provider는 NULL·음수·NaN·Infinity·Boolean·잘못된 기준량을 거부한다. 허용된 demo/verified_product/official fallback의 출처 표시는 유지한다.
- 공개 캐시는 공개 데이터만 저장한다. 캐시 장애 시 PostgreSQL 원본 조회, 승인 철회 시 캐시 hit라도 차단, release 교체·rollback 시 별도 cache version을 사용한다.
- N2에는 추천 계산에 필요한 meal_records/meal_record_items/nutrition_intakes와 개인화 테이블을 준비했다. 합성 이력을 실제로 적재해 남은 영양 목표와 최근 식사 계산을 검증했다. 빈 값을 반환하는 임시 대체 경로는 만들지 않았다.
- 공식 import의 숫자 release와 공개 catalog의 UUID release는 별도 체계다. 공식 매핑은 현재 published release·active 식품·명시적 승인 매핑에 한해 사용한다.
- 최근 식사 반복 점수는 해당 가구의 기록으로 제한했다. 다른 가구 이력이 점수에 반영되지 않는다.
- PostgreSQL 최신 보관 지침은 명시적 identity id, SQLite는 기존 rowid 순서를 사용한다. 스냅샷은 공개 컬럼의 결정적 정렬을 사용한다.
- 추천 계산 중 개인화 집계는 기존대로 DB에 쓰기를 수행한다. 같은 가구의 집계를 transaction advisory lock으로 직렬화해 동시 추천의 inferred identity 충돌을 방지한다.

## HTTP 계약과 C 연동 사항

SQLite의 요청·응답과 화면은 유지했다. **PostgreSQL의 개인 추천에는 Bearer 가구 세션이 필수**다.

| 경로 | PostgreSQL 권한 |
| --- | --- |
| `POST /api/recommendations` | 해당 가구의 owner/editor. 자연어 parser 호출 전 세션 검사, 구조화 후 가구 검사 |
| `GET /api/recommendations/{id}` | 저장된 요청과 같은 가구의 유효 세션. viewer 조회 허용 |
| `POST /api/recommendations/{id}/feedback` | 해당 가구 owner/editor. 기존 후보·참여자 확인 |

토큰 누락/무효 401, 가구 불일치·viewer 쓰기 403, 존재하지 않는 추천 404.
응답은 no-store다. API 응답 필드와 201 저장 응답은 유지한다.

**C에 전달할 변경:** 현재 `app/static/api_client.js`의 request 함수는 authenticated=true일 때만
Authorization을 붙인다. PostgreSQL로 화면을 연결할 때 위 세 추천 호출에도 해당 옵션을 켜야 한다.
토큰은 기존 메모리 세션을 재사용하며 localStorage/URL/콘솔에 저장하지 않는다.
이번 작업은 C 소유 화면·스타일·UI 테스트를 수정하지 않았다.
따라서 실제 화면의 PostgreSQL 전 구간 연결을 완료했다고 주장하지 않는다.

PostgreSQL에서 이 recommendations blueprint 중 이전되지 않은 조리·섭취·음성·대화·개인화 편집 등은
501 not_implemented로 차단한다. 새로 준비된 개인 테이블을 레거시 무권한 경로로 노출하거나 부분 저장하지 않는다.
가족 API, A의 `/api/data/inventory/events` 및 확정·조회는 다른 blueprint로 계속 동작한다.
기존 `/api/inventory/items`와 `/api/inventory/normalize`도 PostgreSQL에서 아직 지원 대상으로 열지 않았다.
SQLite의 기존 경로는 그대로다. 공개 release/품질 조회는 지원 범위에 남긴다.

## 실패·stale·트랜잭션 검증

- AI/timeout/형식 오류는 api_error, 개인정보 검사 차단은 privacy_blocked. DB에는 요청 상태만 남기고 후보·노출 이력은 남기지 않는다.
- 알레르기·기한·부족 재고·영양/중량 누락은 로컬 규칙에서 제외하며 no_safe_recommendation으로 저장한다.
- 공개 또는 공식 reference release가 저장 전 달라지면 stale_unvalidated로 저장하고 후보를 노출하지 않는다. 저장 후 조회에서도 release 변경을 감지해 후보를 숨긴다.
- 후보 INSERT에 DB 오류를 주입하면 요청·후보·노출 이력과 그 요청에서 갱신한 개인화가 함께 rollback된다. 오류 제거 후 정상 요청을 검증했다.
- 두 실제 HTTP 추천 요청을 동시에 실행해 각각 별도의 추천 기록이 남고, 집계된 개인화 identity는 중복 생성되지 않음을 확인했다. 추천 생성 자체를 동일 요청 ID로 합치는 계약을 새로 만들지는 않았다.
- 외부 AI 전송 경계는 fake client로 포착해 가구/구성원 ID·프로필·알레르기 원문·토큰이 payload에 없는지 검사했다. 실제 AI API 호출은 하지 않았다.

## 재현

Docker Desktop Linux engine, 프로젝트 Python과 psycopg가 필요하다. 무료 일회성 로컬 DB와 합성 입력만 사용한다.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:KINI_USE_OPENAI='0'
$env:PYTHONPATH=(Get-Location).Path
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_vision_postgres.ps1 -Python <프로젝트-Python-절대경로>
```

기본 시험 묶음은 기존 재고/가족과 N1·N2·N3를 함께 실행한다. 임시 DB는 tmpfs와 루프백 임의 포트를 사용하고 정리한다.
`-TestPaths tests/test_recommendation_http_postgres.py`처럼 단위 하나만 선택할 수도 있다.

SQLite 전체 회귀와 Node 시험:

```powershell
$env:KINI_DB_BACKEND='sqlite'
python -m pytest -q --tb=short
node --test tests/ui/*.test.cjs
```

최종 직접 실행 수치는 아래 결과 절에 기록한다.

## 최종 직접 실행 결과

| 시험 | 결과 |
| --- | ---: |
| 실제 PostgreSQL 16.15 전체 묶음 | **120 passed / 73.24초** |
| N1 조회·영양·캐시·SQLite parity | 19개 |
| N2 규칙·공식 매핑·이력·개인화·비유한 수량·최신 보관 지침 | 15개 |
| N3 실제 루프백 HTTP·권한·오류·저장·동시성·rollback | 12개 |
| 기존 재고 경쟁·HTTP·가족·migration | 74개 |
| SQLite 전체·기존 Chrome E2E | **290 passed, 119 skipped / 212.63초** |
| JavaScript UI | **35 passed** |
| git diff --check | 통과 |

SQLite 전체 실행의 119개 skip은 PostgreSQL 환경 전용 시험이다. 해당 실행의 수집 이후
추가한 PostgreSQL 전용 “자연어 parser 이전 인증” 시험 1개까지 최종 PostgreSQL 120개 묶음에서
모두 실행해 통과했다. 그 추가는 PostgreSQL 경로에만 적용되며 일반 SQLite 동작은 바꾸지 않는다.
전체 290개에는 기존 Chrome E2E 13개가 포함되며, 이 화면 회귀는 SQLite·합성 데이터·AI 테스트 대역 조건이다.
실제 PostgreSQL HTTP 시험을 실제 모델/모바일 기기/운영 RLS 시험으로 해석하지 않는다.
각 실행 후 테스트 컨테이너는 정리했다.

## 범위 밖

실제 Supabase의 기존 데이터/역할/RLS/용량·TLS 검증, AWS 배포, 카메라/YOLO,
실제 OpenAI/Ollama 호출, 조리 완료·섭취 기록·리포트 쓰기의 PostgreSQL 이전은 하지 않았다.
새 표에 식사 이력이 존재한다는 사실과 식사 기록 API의 이전 완료는 다르다.
의료적 안전성 성능이나 실사용 가족 데이터 성능으로 주장하지 않는다.
