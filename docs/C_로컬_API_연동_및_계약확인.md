# C 로컬 API 연결 결과 및 계약 확인 요청

최종 갱신: 2026-11-26. 기준: main `569267f`를 병합한 `feature/mobile-recommendation-flow`.

B가 공유한 기준 계약과 실제 Flask handler를 대조했다. PR #14 및 후속 #18을 포함한 최신 main을 현재 작업 브랜치에 병합했다. 로컬 재고 모드에서 조리 완료 시 전체 레시피 사용량 차감, 부족 시 롤백, 중복 완료 방지를 적용한다. A 재고 모듈이 활성화된 환경은 Bearer 세션 및 확정 출고 이벤트를 요구하며 401/403/409를 반환할 수 있다. 현재 체크아웃에는 A 모듈이 없어 해당 경로의 실환경 검증은 제외한다.

냉장고 홈은 시안의 음식 이미지·추천 시작·가족·재고·기록 카드 구성으로 변경했다. 음식 이미지는 소개용이며 재고·가족은 서버 응답으로 표시한다. 과거 기록 조회 API가 없으므로 끼니별 상태를 임의로 완료 처리하지 않고 이번 연결의 저장 확인 결과만 사용한다. 홈 질문창은 제거하고 재고 화면에서 필요 시 연다. 아래 항목 중 재고 미차감·PR 미반영 관련 설명은 2026-11-25 최초 점검의 이력이며 본 갱신과 현재 핸드오프가 이를 대체한다.

## 문서 우선순위 — 2026-11-25 확인

- **기준 계약:** C와 합의한 `API_계약서_v1_초안.md`. 파일명에 초안이 남아 있어도 전달받은 합의에 따라 기준으로 사용한다.
- **보조 문서:** `C_API_연동_핸드오프.md`는 현재 서버의 HTTP 동작과 연동 예시를 설명한다. 충돌하면 기준 계약을 먼저 확인하고 문서 또는 구현의 수정 대상을 판단한다.
- 이 문서와 `local_ui_v1.json`은 C 구현·관찰 결과이며 기준 계약을 대체하지 않는다. fixture는 기본 응답 구조를 검사하고, 재고 변경 동작은 최신 통합 테스트로 검사한다.
- Downloads로 전달된 핸드오프 사본에는 "핸드오프 우선"이라는 예전 문구가 남아 있다. 원격 B 브랜치의 `2208b26`에서는 "기준 계약 우선"으로 수정되었다. 첨부 사본의 이전 문구를 지침으로 사용하지 않는다.
- 기준 계약 파일 자체의 도입부에는 아직 "팀 합의 전에는 canonical 계약으로 간주하지 않는다"가 남아 있다. 합의 상태를 반영하는 도입부 정리가 필요하며, 기능별 미합의 항목은 별도로 유지한다.

## 연결한 기능

| 화면/시나리오 | 호출 및 표시 |
| --- | --- |
| 1 가족 선택 | `GET /api/households/demo`, `GET /api/members?household_id=1`; 서버 ID로 참여자 선택. 신규 가족 작성은 미연동 표시 |
| 기존 재고·영양·채팅 | `GET /api/inventory`, `GET /api/nutrition?food_name=...`, `POST /api/chat` 복구. 영양 검색은 제품 완전 일치를 보장하지 않음 |
| 2 추천 | `POST /api/recommendations`; 구조화 조건만 전송. 더 보기는 `excluded_recipe_ids`. 상태·warnings·data_quality·requires_confirmation 표시 |
| 추천 상세 | 후보의 `inventory_usage`와 서버 영양값 표시. 정적 메뉴·영양·개인 이름은 제거 |
| 3 조리 | 저장 추천 GET 재검증 → 식사 세션 POST → 조리 세션 POST. 재료 변경 POST 결과의 영양을 표시. 조리 질문 API 연결 |
| 4 섭취 기록 | `/api/cooking-sessions/:id/complete`; 확인 체크·개인별 인분 입력. 성공 후만 저장 완료 처리 |
| 5 재고 등록 | normalize → items POST. 실제 g만 입력. 미매칭 재료는 성공한 것처럼 임시 등록하지 않음 |
| 6 리포트 | 이번 탭의 서버 저장 성공 응답만 표시. 과거 이력을 생성하거나 총 기록이 0건이라고 단정하지 않음 |
| 데이터 상태 | `/api/data-releases`, `/api/data-quality/summary` 독립 조회. 일부 실패 시 가능한 응답과 실패 원인을 구분 |

브라우저는 `scenario_demo.js` 또는 synthetic provider를 로드하지 않는다. 과거 데모 테스트는 독립 fixture adapter(`tests/ui/demo_api_client.js`)로 보존했다. 서버 SQLite의 예시 데이터는 여전히 존재하며 `demo_data`로 명시한다.

## A와 확인할 데이터 계약

현재 `GET /api/data-releases`는 `nutrition_releases`, `allergen_reference_versions`, `current`를 반환한다. `current`의 `nutrition_release_id`, `allergen_reference_version_id`가 null이면 published 없음으로 표시한다. 목록의 `status`, `accepted_count`, `quarantined_count`, 출처명을 사용한다.

현재 `GET /api/data-quality/summary`는 `current`, `ingredients_by_source: [{source_type,count}]`, `official_food_items`, `active_mappings`, `note`를 반환한다. 누락/오류를 0이나 정상 상태로 대체하지 않는다.

현재 확인 예시는 `tests/fixtures/api_contract/local_ui_v1.json`, drift 검사는 `tests/ui/test_local_contract.py`에 있다. 추가 필드는 허용하고 현재 사용 필드의 삭제·타입 변경을 검사한다.

**A 확인 요청:**

1. JSON 재고(`/api/inventory`)와 SQLite 추천 재고의 원본·통합 조회 경로 확정. 현재 서로 자동 동기화되지 않는다. 화면도 두 출처를 구분한다.
2. 가구별 재고 목록/수정/삭제 및 ThinQ snapshot 시각·sync 상태 API. `source`, `snapshot_at`, `sync_status`, `stale` 등의 최종 필드명과 상태 enum, 실패 fixture 필요. 현재 release 정보로 ThinQ가 최신이라고 표시하지 않는다.
3. 데이터 상태 위 두 경로를 유지할지, `current` null과 published 교체 때의 의미, 오류/빈 목록 fixture 확정.
4. 조리 완료 시 재고 차감: PR #14와 #18 반영 완료. 기본 로컬 모드에서는 전체 재료량을 한 번 차감한다. A 모듈이 활성화되면 확정 출고 이벤트를 연결하고 추가 차감하지 않는다. 현재 로컬에서는 기본 모드를 검증했다.

## B와 확인할 추천·조리 계약

1. **분량 기준 차이:** 추천 `needed_g`는 참여자 수로 환산된다. 조리 세션 `ingredients[].quantity_g`는 기본 레시피 전체량이며, 조리 응답에 `recipe_servings`가 없다. 자동으로 참여 인원만큼 조리된다고 표시할 수 없다. `recipe_servings`, `prepared_servings`, `quantity_basis`, 재료명·ID를 명시하고 생성 요청의 조리 인분 지원 여부를 합의해야 한다.
2. 레시피 상세/조리 단계 조회가 없다. 조리 순서는 UI가 만들어 넣지 않는다. 상세 API 또는 추천/조리 응답에 재료 ID·이름·전체량·인분·단계를 제공해야 한다. 현재 이름 매핑은 추천 inventory_snapshot 범위이며 없으면 재료 ID로 표시한다.
3. 조리 완료 API `servings`는 이제 `>=0`이며 0인분을 허용한다. UI는 기존과 같이 미섭취자를 전송에서 제외하고, 모두 0이면 저장하지 않는다. 0인분 전송도 서버에서 지원하므로 호환된다.
4. `stale_unvalidated`는 서버가 후보를 포함해 반환하는 경우에도 UI에서 전부 숨긴다. 조리 시작 직전 GET 추천 재검증으로 차단한다. 단, 서버 create/change/complete의 published release 재검증과 원자적 보장은 B 확인이 필요하다.
5. 조리 목록/취소/재개와 저장 재시도용 idempotency 계약이 없다. 현재 새로고침 시 조리 상태를 자동 복원할 수 없으며 서버 세션은 삭제되지 않는다. 응답 유실 뒤 안전한 재시도·복구 API 필요.
6. 시간 제한은 현재 브라우저 20초. 비동기 처리/실제 모델 실행 시 서버 timeout 및 요청 진행 조회와 함께 조정 필요.
7. `ai_explanation`은 서버가 검증한 로컬 설명만 표시한다. 추천 없음·개인정보 차단·API 오류·stale에서 정적 카드와 기존 후보를 표시하지 않는다.

## 아직 API가 없어 제한한 기능

- 가족 생성·초대·프로필 수정·사용자별 가구 조회/인증. 현재 family 조회는 `/households/demo`만 제공.
- 레시피 상세·실제 조리 단계와 조리 인분 변경.
- 조리 취소/세션 목록·새로고침 후 복구.
- 과거 식사 이력/기간별 영양 리포트 조회. 이미 먹은 메뉴 직접 기록 POST는 있지만 전체 레시피 목록이 없어 해당 화면 선택 흐름은 보류.
- 재고 수정/삭제, 임시 미매칭 재료 등록, 카메라·YOLO 입고 인식.
- 보조식품 일정·체크·다음 목표·하루 마감.
- ThinQ 동기화 상태와 스냅샷 조회.
- 사진 기반 섭취 추정. 기존 음성 API는 존재하지만 녹음·STT 평가/입력 UI 연결은 이번 범위에서 보류했다. 음성 예시 버튼으로 실제 인식된 것처럼 처리하지 않는다.

## 검증과 실행

일반 실행은 기존 `python -m app.main`. 화면은 기본적으로 같은 origin의 Flask API를 호출한다. `KINI_USE_OPENAI`가 1이 아니면 서버의 FakeOpenAIRecommendationClient가 사용된다. 채팅·영양 API의 실제 운영 호출에는 기존 Ollama/공공 API 설정이 필요하다. 브라우저에 API 키를 넣지 않는다.

모델 평가와 외부 API 호출 없이 검증하려면:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install playwright
# Chrome이 없으면: .\.venv\Scripts\python.exe -m playwright install chromium
# 이 PC에서 LangSmith 플러그인의 xxhash DLL 로드가 Windows 정책으로 차단되어 자동 로딩을 끔
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/ui/api_client.test.cjs tests/ui/scenarios.test.cjs
```

브라우저 E2E는 임시 SQLite와 실제 로컬 HTTP 서버를 사용한다. AI client·채팅·공공 영양 응답만 테스트 대역으로 교체한다. 가족 선택→추천→재료 제거→실패 변경 보존→섭취량 .5인분 저장→DB 영양값 확인, 추천 불가·개인정보 차단·AI 오류·HTML 500·stale 조리 차단·데이터 상태 일부 실패·재고 등록을 검증한다. 모바일 390px, 데스크톱 1280px에서 넘침과 런타임 오류를 검사하고 `output/ui/`에 화면을 저장한다. Playwright가 없으면 브라우저 테스트만 skip된다.

휴대폰/Tailscale은 이번 검증에 포함되지 않았다. 실제 접속 전 호스트·네트워크 접근 설정과 동일 흐름 검증이 필요하다.
