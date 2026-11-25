# KINI — 온디바이스 냉장고 음성 비서 프로토타입

로컬 Ollama, 식재료 재고 JSON, faster-whisper STT, Piper TTS를 결합한 냉장고 음성 비서 프로토타입입니다. 음성 파일을 업로드하면 한국어로 전사하고, 재고 기반 답변을 생성한 뒤 한국어 WAV 음성으로 들을 수 있습니다.

기본 음성 경로는 외부 API 키 없이 동작합니다. 최초 한 번의 모델 준비에만 인터넷이 필요하며, 준비가 끝난 뒤 STT/TTS 런타임은 로컬 모델 파일을 사용합니다.

## 빠른 시작

PowerShell에서 다음을 실행합니다.

```powershell
# 저장소 폴더에서 실행
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 새 Ollama 서버가 로컬 모델만 사용하도록 설정. 기존 서버에는 재시작 필요.
$env:OLLAMA_NO_CLOUD = "1"
$env:OLLAMA_HOST = "127.0.0.1:11434"
ollama pull qwen3.5:4b-q4_K_M
ollama list
Invoke-RestMethod http://localhost:11434/api/tags
.\.venv\Scripts\python.exe .\scripts\download_voice_models.py
.\.venv\Scripts\python.exe .\scripts\local_runtime.py
```

준비된 파일을 한 번에 점검하려면 `python .\scripts\run_voice_sample_tests.py`를 실행한다. 전사·답변·생성 WAV 경로를 출력하며, WAV는 `output/audio/`에 저장된다.

브라우저에서 `http://127.0.0.1:5000`을 엽니다. 기본 모델은 `qwen3.5:4b-q4_K_M`입니다. 기존 `.env`를 사용한다면 `OLLAMA_MODEL=qwen3.5:4b-q4_K_M`으로 수정한 뒤 앱을 재시작하세요. `OLLAMA_MODEL`이 비어 있으면 같은 기본 모델을 사용합니다. 모델 가중치는 Git에 포함하지 않으며 Ollama로 별도 다운로드합니다.

Qwen3.5는 `think: false`로 호출하며 이전 Qwen3 전용 `/no_think` 문자열을 붙이지 않습니다. 컨텍스트는 4096 토큰으로 제한합니다. 자세한 준비·검증·이전 모델 전환 방법은 [Qwen3.5 실행 가이드](docs/Qwen3_5_4B_실행가이드.md)를 확인하세요.

채팅과 가족 추천 체인은 같은 `OLLAMA_MODEL`을 사용합니다. 로컬 Ollama에 모델을 준비하고, 기존 `.env`에서 다른 모델을 지정했다면 원하는 모델명으로 직접 변경하세요. 추천 체인의 호출 제한시간은 90초이며 모델 출력은 추천 규칙이나 안전 필터를 대체하지 않습니다.

기본 음성 경로는 `models/stt/small`의 faster-whisper 모델과 `models/tts/ko_KR-kss-medium.onnx`의 Piper 음성을 사용합니다. `FOODSAFETY_KOREA_API_KEY`는 COOKRCP01 레시피 검색, `DATA_GO_KR_SERVICE_KEY`는 식품영양성분 조회에만 사용합니다. ElevenLabs 모듈은 호환성을 위해 보존하지만 기본 실행 경로에서는 사용하지 않습니다.

자세한 Windows 모델 준비·오프라인 실행·문제 해결은 [로컬 음성 모델 준비 문서](docs/로컬_음성_모델_준비.md), 실제 업로드 검증은 [음성 통합 테스트셋](docs/음성_통합_테스트셋.md)을 확인하세요.

## 테스트와 벤치마크

```powershell
python -m pytest
.\.venv\Scripts\python.exe .\scripts\benchmark_models.py --models qwen3.5:4b-q4_K_M
# Qwen3 4B도 설치한 경우 동일 질의 비교
.\.venv\Scripts\python.exe .\scripts\benchmark_models.py --models qwen3:4b qwen3.5:4b-q4_K_M
```

## 폴더

- `app/`: 웹 앱과 연동 코드
- `app/data/`: 로컬 재고 JSON
- `tests/`: 재고·응답·안전 규칙 테스트
- `samples/audio/`: STT 테스트 음성 파일 자리
- `output/audio/`: 생성 WAV 출력(기존 MP3 호환 경로도 유지)
- `models/stt/`, `models/tts/`: 사전 다운로드하는 로컬 STT/TTS 모델(커밋 제외)
- `docs/`, `scripts/`: 음성 모델 준비·검증 문서와 보조 도구
# 가족 맞춤 추천 데모 API

이 기능은 기존 재고·음성 API와 별개의 SQLite 데모입니다. `pip install -r requirements.txt` 후 `python -m app.main`으로 실행합니다. 최초 요청 시 `app/data/demo_household.json`을 `app/data/kini.sqlite3`에 시드합니다. `KINI_DB_PATH`로 DB 경로를 바꿀 수 있습니다. 기존 JSON 재고는 새 DB와 자동 동기화되지 않습니다. SQLite 파일에는 개인 정보가 평문으로 저장되므로 실제 건강정보를 넣지 마세요.

모든 요청과 응답은 JSON입니다. 입력 오류는 400과 `{"error":"..."}`를 반환합니다. 데모 가구 ID는 1, 아빠·엄마·아이·할머니 ID는 각각 1~4입니다.

* `GET /api/households/demo`: `{id, name, members}`를 반환합니다. 각 구성원에는 `id`, `household_id`, `name`, `age`, `preferences`, `allergies`, `constraints`, `targets`가 있습니다.
* `GET /api/members?household_id=1`: `{members:[...]}`를 반환합니다.
* `POST /api/meal-sessions`: `{"household_id":1,"participant_ids":[1,3],"meal_type":"dinner"}` → 201 `{session_id,household_id,participant_ids,meal_type}`.
* `POST /api/recommendations`: `{"household_id":1,"participant_ids":[1,3],"meal_type":"dinner","cook_time_limit_minutes":30,"purchase_allowed":false,"desired_menu":null,"excluded_ingredients":[]}`. 마지막 네 필드는 생략 가능합니다. 자연어 입력은 `{"text":"..."}`이며 가구 및 참여자 ID가 필요합니다. 201 응답에는 `request_id`, `rules_version`, `db_version`, `as_of`, `participant_ids`, `profile_snapshot`, `inventory_snapshot`, `recommendations`, `excluded`, `explanation`이 있습니다. 후보에는 `recipe_id`, `name`, `cook_time_minutes`, `nutrition_per_serving`, `nutrition_confidence`, `inventory_usage`, `scores`, `purchase_missing_g`, `adjustments`가 있습니다. 제외 항목에는 `recipe_id`, `name`, `reasons`가 있습니다.
* `GET /api/recommendations/<request_id>`: 저장된 위 결과를 반환하며 없으면 404입니다.
* `POST /api/meal-records`: `{"session_id":1,"recipe_id":1,"eaten_at":"2026-11-25T12:00:00","items":[{"member_id":3,"servings":1}]}` → 201 `{record_id,nutrition_per_serving}`. 구성원은 해당 식사 참여자여야 합니다. 섭취량은 레시피 재료의 예시 영양값으로 계산됩니다.

현재 구현은 초기 데모입니다. 영양·나트륨 목표와 영양값은 임의 예시이며 의료 판단이나 실제 알레르기 안전 판정에 사용할 수 없습니다. 알레르기 동의어·교차 접촉·복합재료, 실제 제품 기준, 보관 기간 계산, 구입 가격, 개인별 실제 분량 조정과 전략별 점수는 아직 완성되지 않았습니다. 조정안은 동일한 기본 1인분만 제시합니다. `db_version`은 시드 버전이며 변경 이력이나 데이터 무결성 버전을 제공하지 않습니다. 날짜와 식사 기록도 결과에 영향을 줍니다. RAG는 일반 안내에 대한 SQLite 키워드 검색 인터페이스만 있고 추천 순위에는 사용하지 않습니다. Tavily는 인터페이스와 환경변수만 준비되어 실제 검색은 구현되지 않았습니다. Ollama 연결에 실패하면 한국어 템플릿을 사용하며, 안전하게 검증할 수 없는 생성 설명은 표시하지 않습니다.

# 행동 기반 개인화 메모리

기존 명시 알레르기·건강 제한과 비선호는 별도로 유지합니다. 하드 필터가 먼저 적용되며 추론 선호는 통과한 후보의 점수만 최대 ±15점 보정합니다. 추천 후보에는 `personalization_score`, `applied_preference_signals`, `ignored_preference_signals`, `explanation_facts`, `recommendation_reason_codes`가 추가되고 저장됩니다. 추론 신호는 불확실한 관찰이며 명시 프로필을 수정하지 않습니다.

선택·조리 및 식사 기록은 메뉴와 기본 레시피 재료의 선호 evidence를 생성합니다. 거절은 지정된 이유에만 비선호 신호를 생성합니다. 신호는 90일 반감기로 감쇠하며, 3건 이상이고 감쇠한 순 신호의 신뢰도가 0.65 이상이어야 활성화됩니다. 개인 신호가 같은 항목의 가구 신호보다 우선하며, 명시 비선호는 추론 선호보다 우선합니다. 해제된 추론은 다시 활성화되지 않습니다. 최근 선택 반복·거절 재료·식사/조리 재료와 조리시간도 점수에 보조적으로 반영됩니다. 날짜가 바뀌면 감쇠와 최근 행동 범위가 바뀔 수 있습니다.

예시 (기존 추천 API와 함께 사용):

* `POST /api/conversations`에 `{"household_id":1,"purpose":"feedback","meal_session_id":null}` → `{"session_id":1,"household_id":1,"purpose":"feedback","meal_session_id":null}`.
* `POST /api/conversations/1/messages`에 `{"role":"user","content":"버섯은 싫어","member_id":3}` → `{"message_id":1,"evidence":[{"id":1,"feature_type":"ingredient","feature_value":"버섯","signal":"avoid"}],"ignored_reason":null}`. `member_id` 생략 시 가구 공통 신호입니다. 등록된 재료에 대한 짧고 명확한 표현만 인식하며, 그 외는 `evidence: []`와 `ignored_reason`을 반환합니다. 원문은 저장하지 않고 메시지에 삭제 표시만 남깁니다. 의료·알레르기 내용은 학습하지 않습니다.
* `POST /api/recommendations/1/feedback`에 `{"candidate_recipe_id":2,"member_id":3,"action":"rejected","reason_code":"ingredient:버섯"}` → `{"feedback_id":1,"evidence_ids":[1]}`. action은 `selected`, `rejected`, `cooked`, `skipped`, `rated`이고, `rated`에는 `rating` 1~5가 필요합니다. 이유 코드는 후보에 있는 `ingredient:재료명` 또는 `recipe:레시피ID`만 허용합니다. `reason_text`는 민감정보 보호를 위해 저장하거나 추론에 사용하지 않습니다. 표시되지 않은 후보나 참여하지 않은 구성원은 거부됩니다.
* `GET /api/preferences?household_id=1&member_id=3` → `{"preferences":[{"id":1,"feature_type":"ingredient","feature_value":"버섯","inference":"avoid","confidence":0.9,"evidence_count":3,"status":"active",...}]}`. `member_id` 생략 시 가구 공통 선호만 반환합니다.
* `POST /api/preferences/1/dismiss` → `{"preference_id":1,"status":"dismissed"}`.
* `GET /api/household-behavior-summary?household_id=1` → `{"household_id":1,"preferred_cook_time_minutes":20,"preferred_meal_types_json":["dinner"],"frequently_used_ingredients_json":["버섯"],"recently_rejected_features_json":[],"updated_at":"..."}`.
* `POST /api/meal-records/1/adjustments`에 `{"member_id":3,"ingredient":"버섯","action":"removed"}` → `{"evidence_id":1,"notice":"선호 신호만 기록했습니다. 식사 영양 수치는 재계산하지 않았습니다."}`. `added`도 지원합니다. 변경 신호는 약한 가중치를 사용하며 실제 사용량이나 영양 계산을 수정하지 않습니다.

식사 기록은 변경 전 기본 레시피 재료를 기준으로 하므로 실제 변경 재료나 섭취량을 자동 판별하지 못합니다. 레시피 데이터에 조리법·요리 종류 속성이 없어 그 항목은 자동 학습하지 않습니다. 별점·이유 없는 거절도 학습 신호로 해석하지 않습니다. 선호 추출은 로컬의 엄격한 규칙으로 처리하며 LLM 추출 호출은 없습니다. 설명에 사용하는 집계 신호는 루프백 Ollama에만 전달합니다. 사용자가 의료정보를 입력해도 원문을 저장하지 않지만, 기존 SQLite 전체에 암호화·접근 제어·삭제 및 백업 관리가 없으므로 실제 개인정보를 넣기 전 구현해야 합니다.

# 조리 세션과 로컬 평가

이전의 `POST /api/meal-records/<id>/adjustments`는 계속 선호 evidence만 기록합니다. 실제 재료 변경과 영양 재계산에는 아래 조리 세션을 사용하세요. 중량은 레시피 전체 기준 g이며, 등록된 데모 재료와 영양정보만 처리합니다. 확인되지 않은 제품이나 단위는 임의 변환하지 않습니다.

PowerShell 데모 (서버 실행 후):

```powershell
$base = 'http://127.0.0.1:5000'
$req = @{household_id=1; participant_ids=@(3); meal_type='dinner'} | ConvertTo-Json
$rec = Invoke-RestMethod "$base/api/recommendations" -Method Post -ContentType 'application/json' -Body $req
$meal = Invoke-RestMethod "$base/api/meal-sessions" -Method Post -ContentType 'application/json' -Body $req
$start = @{meal_session_id=$meal.session_id; recommendation_request_id=$rec.request_id; recipe_id=$rec.recommendations[0].recipe_id} | ConvertTo-Json
$cook = Invoke-RestMethod "$base/api/cooking-sessions" -Method Post -ContentType 'application/json' -Body $start
# 기존 재료의 변경은 정확한 이름과 레시피 전체 g 수량으로 입력. 0은 제거.
# Invoke-RestMethod "$base/api/cooking-sessions/$($cook.id)/ingredients" -Method Post -ContentType 'application/json' -Body '{"ingredient":"버섯","quantity_g":0}'
Invoke-RestMethod "$base/api/cooking-sessions/$($cook.id)/questions" -Method Post -ContentType 'application/json' -Body '{"question":"국물이 짜요"}'
$done = @{confirmed=$true; eaten_at=(Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'); items=@(@{member_id=3; servings=1})} | ConvertTo-Json -Depth 4
Invoke-RestMethod "$base/api/cooking-sessions/$($cook.id)/complete" -Method Post -ContentType 'application/json' -Body $done
```

조리 세션 생성은 해당 추천 후보와 동일한 가구·참여자의 식사 세션을 요구합니다. 재료 변경과 완료 시 참여자의 알레르기·식이 제한·나트륨 및 재료 영양정보를 재검증합니다. 확인 전이나 검사 실패 시 식사 기록을 만들지 않습니다. 조리 질문은 일반 문서에만 접근하며 검색 실패 시 고정 안내를 반환합니다. 해당 안내는 안전 판정이 아닙니다. 추천에 `excluded_recipe_ids` 배열을 전달하면 이미 본 후보를 제외한 추가 추천을 요청할 수 있습니다. `input_snapshot_hash`는 계산에 사용된 일부 스냅샷과 점수의 지문이며, 전체 DB 백업이나 완전한 재현 로그가 아닙니다. 끼니 목표는 남은 일일 목표의 1/3을 사용하는 데모 추정치입니다.

조리 완료 시 확정된 레시피 전체 사용량을 로컬 SQLite 재고에서 한 번 차감합니다. 재고가 부족하면 차감과 식사 기록을 모두 취소합니다. `items`에서 먹지 않은 참여자는 `servings: 0`으로 기록하거나 생략할 수 있으며 0인분은 선호 학습에 반영하지 않습니다. 기존 JSON 재고나 외부 기기 재고와는 동기화되지 않습니다.

`python scripts/evaluate_recommendations.py`는 개인 데이터 없이 임시 DB에서 41가지 합성 평가를 수행합니다. 참여자·요청 조합 외에 중량 불확실성·단위·복합재료·영양정보 누락을 검사합니다. 모델은 호출하지 않습니다. 실제 모델 지연 비교는 `scripts/benchmark_models.py`와 로컬 모델이 필요합니다. RAG 출처는 아직 검증된 외부 자료가 아닌 데모 문구입니다. Tavily는 키가 있어도 호출되지 않습니다.

음성 추천은 `POST /api/voice/recommendations`의 multipart 필드 `audio`, `household_id`, JSON 배열 문자열 `participant_ids`, `meal_type`을 사용합니다. 기존 `/api/voice`와 별개입니다. 반환된 `interpreted_request`를 확인하세요. 로컬 Ollama가 요청을 해석하지 못하면 조건을 임의로 버리지 않고 422와 전사문을 반환합니다. 해석된 값에 원래 발화의 조건이 빠졌을 가능성도 있으므로 확인이 필요합니다. 예시 (쉘에서 실행):

```sh
curl -X POST http://127.0.0.1:5000/api/voice/recommendations \
  -F 'audio=@question.wav' -F 'household_id=1' \
  -F 'participant_ids=[3,4]' -F 'meal_type=dinner'
curl -X POST http://127.0.0.1:5000/api/recommendations \
  -H 'Content-Type: application/json' \
  -d '{"household_id":1,"participant_ids":[3,4],"meal_type":"dinner"}'
```

보안상 현재 앱은 인증과 SQLite 암호화가 없고 기본 호스트가 `0.0.0.0`입니다. 공유 네트워크에서는 사용하지 말고 로컬 데모에 한해 `APP_HOST=127.0.0.1`로 제한하세요. `/api/privacy/capabilities`는 삭제·내보내기가 비활성화된 상태만 알려줍니다. 실제 사용자 데이터를 저장하기 전에 인증, 구성원별 권한·동의, 암호화 키 관리, 안전한 삭제·암호화 백업, 로그와 네트워크 검토가 필요합니다.

## 재료 정규화와 데이터 품질

`POST /api/inventory/normalize` 요청 예: `{"name":"당근 1개","quantity":1,"unit":"개"}`. 응답은 정규 재료와 변환 결과를 포함하며, 실제 무게의 근거가 없으면 `conversion.status`가 `needs_confirmation`, `quantity_g`가 null입니다. 예를 들어 사용자가 실제 중량을 확인하면 `{"household_id":1,"ingredient_id":3,"name":"당근","quantity":200,"unit":"g"}`을 `POST /api/inventory/items`에 보내 저장할 수 있습니다. `GET /api/ingredients/search?q=당근`은 정규 재료를 조회합니다. ml/L은 부피로만 변환하고 밀도 없이 g으로 바꾸지 않습니다. 사용자 제품명이나 복합 재료는 라벨 확인 대상으로 남습니다.

추천 및 조리 응답의 `data_quality`는 `verified`, `partially_verified`, `confirmation_required`, `demo_data`로 구분합니다. 현재 엔진은 교차접촉 확인 안내를 항상 표시하므로 실제 안전 보증 의미의 `verified`는 반환하지 않습니다. `demo_data`도 `requires_confirmation: true`이며 실제 음식·영양 판단에 사용할 수 없습니다. `data_status=verified`인 재고는 입력 이름과 중량이 정규화됐다는 뜻일 뿐 제품 안전이나 라벨 검증을 뜻하지 않습니다. 알레르기 충돌·라벨 미확인·영양 누락 등은 `excluded_candidates[].reason_codes`에 표시하고, 후보가 없으면 `status: no_safe_recommendation`입니다. 자세한 출처 정책은 [데이터 안전 설계](docs/데이터_안전_정규화_설계.md)를 참고하세요.
## Official food-data import MVP

Recommendation requests never query external sources; they read only the explicitly published local SQLite release. Set only the variable name `DATA_GO_KR_SERVICE_KEY` in local configuration; do not commit a key, raw payload, or SQLite database.

```powershell
python scripts/import_mfds_nutrition.py --input tests/fixtures/mfds_nutrition_sample.json --dry-run
python scripts/import_mfds_nutrition.py --input tests/fixtures/mfds_nutrition_sample.json --validate
python scripts/import_mfds_nutrition.py --input tests/fixtures/mfds_nutrition_sample.json --publish --confirm-publish
python scripts/import_mfds_nutrition.py --from-api --dry-run
python scripts/import_allergen_reference.py --input tests/fixtures/allergen_reference_sample.json --publish --confirm-publish
```

`--from-api` reads `DATA_GO_KR_SERVICE_KEY` only when explicitly invoked. Tests use fixtures, never a real API key. `GET /api/data-releases`, `GET /api/data-releases/<id>`, and `GET /api/data-quality/summary` expose release metadata only, never raw payloads or raw-file paths.

Official nutrition applies only through an approved ingredient mapping. Nutrition imports do not infer product allergens, cross-contact, or label safety. Production still requires authentication, authorization, encryption, auditing, and recovery controls. See [the import design](docs/공식_식품데이터_import_설계.md).
