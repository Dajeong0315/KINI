# Vision 인식 → A 재고 이벤트 API → 로컬 SQLite 연동 검증

> 이 문서는 2026-11-26의 검증 이력입니다. 2026-11-27 main 통합에서는 최신 `kini_inventory_sink.py`를 사용하며 기존 엄격한 `kini_inventory_client.py`는 유지합니다. 아래의 무인증 `/api/inventory/items` 문제는 현재 main에서 이미 차단되어 해당 예상 실패 표시를 제거하고 회귀 검증했습니다. 최신 병합 조건과 결과는 `Vision_main_병합검증_2026-11-27.md`를 따릅니다.

- 검증일: 2026-11-26 (KST) / 브랜치: `verify/vision-inventory-integration` (기준 `origin/main` 96fc3d2, 전달 기준 56ce52d 포함 확인)
- 범위: Vision 연결부와 시험만 추가. A/B API 계약, DB migration, 인증, 추천 규칙, C 화면(`app/templates`, `app/static`)은 변경하지 않음
- 데이터: **임시 SQLite + 합성 계정·데모 재고만** 사용. 개발 DB(`app/data/kini.sqlite3`)는 생성·변경하지 않음. Supabase·Redis·외부 AI 호출 없음 (`KINI_USE_OPENAI=0`)
- 인식 성능(§4)과 서버·DB 연동(§3)은 분리해서 기록한다. DB 등록 성공률은 인식 정확도가 아니다.

## 1. 연결 구조와 실제 검증 범위

### 1-1. 작업 전 상태 (main 96fc3d2)

| 단계 | 상태 |
|---|---|
| 인식 입력 → 인식 후보 | `fridge_vision` 은 **main 에 없음** (미병합 `feature/fridge-vision-yolo` + 로컬 개발본). 후보는 `compare()` 결과의 added/removed |
| 후보 → A 이벤트 API | **연결 없음.** Vision 은 `captures/inventory.json`(Vision 자체 JSON)만 갱신하고, 설정 시 Supabase(`supabase_sync.py`)로 전송하도록 만들어져 있었음 → 개인 재고는 로컬 SQLite 라는 데이터 경계와 충돌 (기본 `.env` 가 비어 있어 실제 전송은 없었음) |
| A 이벤트 → 확인/거절 → SQLite → 조회 | A 구현·시험 완료 상태 (기존 시험 통과) |
| 별도 저장소 | `GET /api/inventory` 는 `app/data/inventory.json`(JSON) 을 읽음 — SQLite 재고와 무관. Vision 의 `captures/inventory.json` 도 SQLite 에 반영되지 않음 |

### 1-2. 이번에 만든 연결 (기존 계약 그대로)

```text
Vision compare() ─ added/removed ─▶ kini_inventory_client.events_from_result()
   물체 1개 = 이벤트 1개, client_event_id = UUIDv5(촬영ID|방향|품목|순번), source="yolo", confidence=null
 ─▶ POST /api/data/inventory/events (Bearer, 로컬 주소만)  → pending_confirmation, 재고 변화 없음
 ─▶ [사람] C 화면에서 표준 재료 ID·g·재고 행 선택 → /confirm  (Vision 은 절대 자동 확정하지 않음)
     Vision 화면 'n' → 그 촬영의 대기 이벤트 /reject,  'y' → 아무것도 하지 않음 (g 를 모르므로)
 ─▶ SQLite inventory_items / inventory_event_changes / inventory_sync_state ─▶ GET /api/data/inventory
```

- `fridge_vision/main.py`: `KINI_API_URL` 이 있으면 A API 로만 보냄. Supabase 는 `SUPABASE_URL` 이 있을 때만 (기존 동작 유지, 둘 다 없으면 전송 없음)
- 인증: `KINI_ACCESS_TOKEN` 또는 `KINI_LOGIN_NAME`·`KINI_PASSWORD`·`KINI_HOUSEHOLD_ID` (메모리에만 보관, 401/403 시 1회 재로그인, 로그·대기열·repr 에 남지 않음)
- 연결 실패 시 `captures/kini_queue.jsonl` 에 이벤트 본문(라벨만)을 보관 → 다음 전송 때 같은 ID 로 재전송
- 보내는 필드는 A-API-02 의 5개뿐 (사진·경로·위치·수량·가구 ID 없음). 원격 주소는 거부 (`127.0.0.1`/`localhost` 만)

### 1-3. 시험 종류 구분

| 구분 | 내용 | 결과 |
|---|---|---|
| A. 기존 시험 | 저장소 전체 `pytest` (임시 DB) | 210 passed, 9 skipped |
| B. 연동 통합 시험 (합성 입력) | `tests/test_vision_inventory_integration.py`: **실제 A/B 서버 코드를 로컬 HTTP 서버로** 띄우고 연결부로 합성 인식 결과 전송, HTTP 응답 + SQLite 상태 동시 확인 | 29 passed, 1 xfailed(발견 사항 P1) |
| C. 실제 인식 → API (녹화·합성 사진) | `fridge_vision/tools/kini_e2e_check.py`: 실제 YOLOE+CLIP 모델 → 연결부 → 실제 서버(임시 DB, 127.0.0.1:5055). 사용자 확인은 스크립트가 모사 | 3쌍 모두 기대대로 (§3-2) |
| D. 실제 카메라 (핸드폰, phone_live) | 인식 출력만 관찰. **A API 로 보내지 않음** (phone_live 는 재고 연동 없음) | §4-3 |
| 미실시 | C 화면 E2E(Playwright 미설치), 실제 냉장고 카메라 → API, Redis 실서버 | §6 |

## 2. 인식명 ↔ 표준 재료 매핑

A 계약대로 `detected_label` 은 자유 문자열이고 **표준 재료 ID 는 confirm 에서 사람이 고른다.** 연결부는 ID 를 추정해 보내지 않는다.
아래는 A 의 `normalize_ingredient` 가 각 라벨을 어떻게 다루는지 확인한 결과 (데모 재료 9종 DB).

| Vision 품목 → detected_label | A 정규화 | 처리 |
|---|---|---|
| tofu→두부, chicken→닭고기, carrot→당근, mushroom→버섯 | matched (4, 2, 3, 5) | 사용자 확인 후 확정 가능 |
| pepper `피망·고추` → `피망` (묶음 이름은 첫 이름) | matched (9) | 〃 |
| butter→버터, ham `햄·소시지`→햄 등 DB 미등록 | unknown | 대기 유지 (자동 등록·확정 없음) |
| OCR 상품명 `비비고 왕교자`, OCR 오탈자 `가스테라` | unknown | 대기 유지 |
| Unknown (목록에 없는 물체) | — | 이벤트를 만들지 않음 (사람이 이름을 붙인 뒤에만) |

- 낮은 신뢰도·복수 후보: 서버는 confidence 를 반영 기준으로 쓰지 않고 모든 이벤트가 pending → 임의 확정 없음 (시험 B 확인)
- 수량·단위: 이벤트에 수량이 없고, g 는 확인 단계에서 사람이 입력. 개·팩·봉지 → g 자동 변환 없음. `unit` 필드는 400 (시험 B)
- "화면에서 사라짐"은 `stock_out` **후보**일 뿐 사용·섭취가 아니다. 섭취 기록은 B 조리 완료에서만 생기며, Vision 출고 이벤트만으로 식사 기록이 생기지 않음 (시험 B §7)

## 3. 서버·DB 연동 결과

### 3-1. 시나리오별 (시험 B: 합성 인식 결과 + 실제 서버 + 임시 SQLite)

| # | 시나리오 | 기대 | 실제 | 통과 |
|---|---|---|---|---|
| 1 | 인식 결과 전송 | pending, 재고·버전 불변, 같은 결과 재전송 시 같은 event_id | 3건 pending, items 동일, version 0, 재전송 시 행 3개 유지 | ✅ |
| 2 | 신규 재료 입고 (재고 없는 가구) | 확정 후에만 생성, 소비기한별 별도 행 | 200 g(2027-01-01)·300 g(2027-02-01) 2행 | ✅ |
| 3 | 기존 재료 추가 입고 | 새 lot, 기존 lot 유지, 이력 (250, 0→250) | 동일 | ✅ |
| 4 | 입고에 기존 재고 행 지정 | 400, 변화 없음 | 400 | ✅ |
| 5 | 입고 거절 / Vision 'n' | rejected, 이후 confirm 409, 재고 불변 | 동일 | ✅ |
| 6 | Vision 'y' | A 확정 안 함 (대기 유지) | pending 1, 재고 불변 | ✅ |
| 7 | 일부 → 전량 출고 | 1000→700→0, 0 g 행 유지 | 동일 | ✅ |
| 8 | 여러 행에서 나누어 출고 | 1000 g 행 + 200 g 행에서 1100 g | 0 / 100, 이력 4건 정확 | ✅ |
| 9 | 0·음수·문자열·bool·1e9·부족·`unit`·다른 재료 행·출고 소비기한·행 없음 (10종) | 400, 이벤트 pending·확인 해시 없음, 이력 0, 음수 없음 | 10종 모두 | ✅ |
| 10 | 다른 가구 재고 행으로 출고 | 400, 해당 가구 재고 불변 | 동일 | ✅ |
| 11 | 출고 거절 | 재고 유지 | 동일 | ✅ |
| 12 | 같은 client_event_id 재전송 / 다른 내용 | 같은 ID / 409 | 동일. applied 후 재수신은 applied | ✅ |
| 13 | 같은 확인 반복 / 다른 확인 / applied 거절 | 1회 반영 / 409 / 409 | 이력 1건 | ✅ |
| 14 | 다른 촬영에서 같은 물체 재인식 | 서버 중복 방지로는 못 막음 (기록용 시험) | 서로 다른 pending 2건, 재고 불변 → **P2** | ⚠ 기록 |
| 15 | 동시 출고 (HTTP, 별도 세션 2개, 700 g×2, 잔량 1000) | 하나 200, 하나 400, 잔량 300 | 동일 | ✅ |
| 16 | 서버 중지 중 인식 → 재시작 → 재전송 | 대기열 보관 후 같은 ID 로 1회 등록 | 동일, 대기열 비워짐 | ✅ |
| 17 | 확정 응답 유실·재시도·서버 재시작 후 재시도 | 1회만 차감 | 1000→900, version 1 | ✅ |
| 18 | 미인증 / 잘못된·만료·철회 토큰 | 401 / 403, DB 변화 없음 | 동일 (이벤트 0건) | ✅ |
| 19 | 다른 가구 이벤트 확정·거절 / 조회 | 404 / 자기 가구만 | 동일 | ✅ |
| 20 | viewer 접수·확정·거절 | 403, 조회 200, DB 불변 | 동일 | ✅ |
| 21 | 로그·대기열에 비밀값 | 없음 | 비밀번호·토큰·"Bearer" 미포함 | ✅ |
| 22 | A 출고 확정 → B 조리 완료 (1인분 / 0인분) | 완료 전후 재고 동일, 식사 기록 +1 | 동일 (두 경우 모두) | ✅ |
| 23 | 같은 출고를 다른 조리에 재사용 | 409 `inventory_confirmation_required`, 재고·기록 불변 | 동일 | ✅ |
| 24 | 미확정 출고 / 다른 가구 출고 / 사용량 미달 출고로 완료 | 409(또는 403), 재고·식사 기록 불변, 연결 없음 | 409 / 409 / 409, 기록 0 | ✅ |
| 25 | 완료 요청 미인증 | 401 | 401 | ✅ |
| 26 | 재시작 후 조회·상태 유지, 추적 | 스냅숏 동일, applied/rejected/pending 구분, 이력 = 재고 변화 합 | 동일 | ✅ |
| 27 | SQLite 무결성 | integrity ok, FK 위반 0 | 동일 | ✅ |
| 28 | 개인 재고 캐시 | `personal_data_cached=false`, 재고 모듈에 cache/redis/supabase 없음 | 동일 | ✅ |
| 29 | 기존 `POST /api/inventory/items` 인증 | 401/403 이어야 함 | **인증 없이 201, 다른 가구에 행 추가 → P1** | ❌ (xfail) |

### 3-2. 실제 인식 → API (시험 C, 임시 DB, 사용자 확인은 스크립트 모사·100 g 가정)

| 사진 쌍 | 인식 결과 | 이벤트 | 접수 후 재고 | 확인 후 |
|---|---|---|---|---|
| 녹화 영상 프레임 (`samples/real_*`) | 버터 −1 (정답) | stock_out 버터 1 | 불변 | 버터 미등록 → 대기 유지, 재고 불변 |
| 합성: 냉장고 프레임 + Kaggle 당근·피망 사진 (입고) | 당근 +4, 피망·고추 +2 | stock_in 6 | 불변 | 당근 +400 g, 피망 +200 g, version 0→6 |
| 위의 반대 (출고) | 당근 −4, 피망·고추 −2 | stock_out 6 | 불변 | 당근 −400 g, 피망 −200 g, version 6→12 |

DB 확인: 확정 이벤트 12 = 변경 이력 12, 음수 0, integrity ok, FK 위반 0, source 전부 `yolo`.
당근 사진 1장(당근 약 6개 묶음)이 4개로, 피망 사진(통 1 + 반쪽 1)이 2개로 세어져 이벤트가 4·2건 생김 → **P3** (인식 쪽 문제, 서버는 정상 동작).

### 3-3. 완료 기준

| 기준 | 결과 |
|---|---|
| 입고·부분 출고·전량 출고 수량 정확 | ✅ (B 2·3·7·8, C) |
| 확인 전·거절 시 재고 유지 | ✅ (B 1·5·6·11, C) |
| 재시도·중복 확정·동시 출고에서 중복·음수 없음 | ✅ (B 12·13·15·16·17) — 단 **다른 촬영의 같은 물체**는 서버가 막지 못함 (P2) |
| 가구 간 접근 차단 | ✅ A 이벤트 API (B 10·19·24). ❌ 기존 `/api/inventory/items` (P1) |
| A 출고 후 B 완료 추가 차감 없음 | ✅ (B 22·23) |
| 실제 인식 / mock 구분 | ✅ (§1-3) |

## 4. 인식 성능 (서버·DB 연동과 별개)

공통 조건: `yoloe-v8m-seg.pt` (ultralytics 8.4.131, 원본 체크포인트 + 영어 검색어 52개, NPZ), CLIP ViT-B/16 재확인 (OpenVINO, Intel UHD 630),
기준값 YOLOE 0.25 / CLIP 확정 0.5 / 후보 0.1, i7-9700 CPU, Python 3.14, torch 2.13 CPU. 조명·거리·가림은 자료별로 아래에 적고, 모르는 값은 미측정.

### 4-1. 식재료 인식 (Kaggle `smart-fridge-food-dataset` test, 20종 × 25장 = 500장, 웹 상품 사진, 조명·거리 미측정)

1등 상자 라벨이 정답인 비율 **69.0%** (345/500), 정답 라벨이 어느 상자에든 있는 비율 72.2%, 상자 없음(미인식) 51장, 평균 1.30 초/장.

| 클래스 | 1등 정답/25 | 클래스 | 1등 정답/25 |
|---|---|---|---|
| apple | 25 | lettuce(→cabbage) | 10 |
| banana | 23 | mango | 8 |
| bread | 18 | milk | 19 |
| butter | 9 | onions | 21 |
| carrots | 17 | orange | 21 |
| cheese | 14 | peppers | 22 |
| chicken | 23 | strawberry | 24 |
| cucumber | 15 | tomato | 22 |
| eggs | 20 | yogurt | 12 |
| fish | 5 | grapes | 17 |

- 대표 오인식: 생선→닭고기 5, 망고→레몬 5·바나나 4·사과 3, 오이→양배추 4, 오렌지→레몬 4, 요거트→딸기 3, 버터→OCR 포장 이름 2
- 중복 감지: 정답 라벨 상자가 2개 이상 나온 사진 127장 (여러 개가 실제로 있는 사진 포함 — 개수 정답은 미측정)
- 비교: 예전 방식(검색어만, 25종) 19.8%. Kaggle 사진은 실제 냉장고 장면과 달라 현장 정확도를 대신하지 않음

### 4-2. 텍스트(OCR) 인식 — 식재료 인식과 별도

| 자료 | 표본 | 결과 |
|---|---|---|
| 합성 포장 (냉장고 프레임에 그린 '비비고 왕교자') | 1장 | 이름 정확 ("비비고 왕교자") |
| 합성 포장 (두부·라면·무지·기울임) | 4장 | YOLOE 가 포장 상자를 못 찾음 → OCR 미실행 (미인식) |
| 실제 핸드폰 카메라, 카스테라 포장 (14:23~15:32) | 정답 미확인 | '카스테라 한입' 39 / '한입 카스테라' 8+4 / '가스데라' 2 / '큰일 스데라' 1 회 → 이름 합치기로 앞 두 개는 같은 ID |
| 실제 핸드폰 카메라, 견과류 포장 | 정답 미확인 | 알레르기 안내문 '땅콩 또는 견과류 가공품' 을 상품명으로 읽음, 2글자 잡음 'sw' 1회 → **P4** |

OCR 정확도(문자·이름 단위)는 정답 라벨이 있는 실제 포장 사진이 없어 **미측정**.

### 4-3. 실제 카메라 관찰 (phone_live, A API 미전송)

15:15~15:32 285회 인식 (화면 속 냉장고 영상 + 실제 포장). 출력 빈도만 기록하며 정답이 없어 정확도는 미측정.
'식재료 파악하기'(정밀 설정) 처리 시간 6.1~8.7 초 (포장 OCR 포함), 실시간 설정 약 0.55 초/장.

## 5. 발견한 문제와 담당 제안

| ID | 문제 | 재현 | 제안 담당 |
|---|---|---|---|
| P1 | `POST /api/inventory/items` 가 인증 없이 본문 `household_id` 로 SQLite 재고 행 추가 (A 이벤트·버전·이력 없음). 다른 가구 재고 변조 가능 | `tests/test_vision_inventory_integration.py::test_legacy_inventory_items_endpoint_requires_auth_and_event_flow` (현재 xfail) — 토큰 없이 `{"household_id":2,"ingredient_id":4,"name":"두부","quantity":100,"unit":"g"}` → 201, 가구 2 에 100 g 행이 `data_status=verified` 로 생성, A 이벤트·sync_state 0건 | A (인증 적용 또는 A 이벤트 흐름으로 대체). 계약 변경이라 이번에 수정하지 않음 |
| P2 | 같은 물체가 **다른 촬영**에서 다시 인식되면 다른 client_event_id → 대기 이벤트 중복. 서버 ID 중복 방지로 해결되지 않음 | `test_same_object_in_different_captures_is_not_deduplicated_by_server` | Vision(장면 비교·추적 개선) + C(같은 재료 대기 이벤트를 묶어 보여주고 거절 쉽게) |
| P3 | 물체 개수 오차: 당근 묶음 1장 → 4건, 피망 통+반쪽 → 2건. 이벤트 수가 실제 출입 단위와 다름 | §3-2 둘째 줄 (`tools/kini_e2e_check.py`) | Vision (묶음 단위 판단) / C (여러 건 한 번에 확정·거절) |
| P4 | OCR 이 알레르기 안내문을 상품명으로 사용, 2글자 잡음 이름 | §4-2 | Vision (`OCR_EXCLUDE_WORDS` 에 '또는·가공품·제조시설·함유' 등 추가 검토, 최소 글자 수) |
| P5 | Vision 의 `captures/inventory.json`·서버의 `app/data/inventory.json` 이 SQLite 와 별도로 존재 → 화면마다 다른 재고를 볼 위험 | 코드 확인 (`main.py apply()`, `GET /api/inventory`) | A·C (SQLite 스냅숏만 재고 기준으로 표시) |
| P6 | 장치용 인증 없음: Vision 상시 실행에 사람 계정 비밀번호·1시간 토큰 필요 | 계약 문서 | A (장치/서비스 세션 정책) |
| P7 | 기존 Vision 의 Supabase 재고 전송 경로(`supabase_sync.py`)가 데이터 경계와 충돌 | 코드 확인 | Vision — KINI 연동에서는 비활성(기본). 제거 여부 팀 결정 |

## 6. 변경 파일·재현 명령

변경 (이 브랜치)

| 파일 | 내용 |
|---|---|
| `fridge_vision/` | 현재 Vision 코드 (미병합 브랜치 + 로컬 개발본). 모델·사진 캐시·개인 이름표는 제외 |
| `fridge_vision/kini_inventory_client.py` (신규) | A 재고 이벤트 API 연결부 (이벤트 생성·전송·거절·대기열) |
| `fridge_vision/main.py` | `KINI_API_URL` 이 있으면 A API 로만 전송 |
| `fridge_vision/tools/kini_e2e_check.py` (신규) | 실제 인식 → API 시험 도구 |
| `fridge_vision/conftest.py` (신규) | Vision 패키지가 없는 KINI 환경에서는 Vision 시험을 수집하지 않음 |
| `tests/test_vision_inventory_integration.py` (신규) | 연동 통합 시험 30개 |
| `docs/Vision_재고DB_연동_검증.md` (신규) | 이 문서 |

재현

```powershell
# KINI 환경 (Python 3.13, requirements.txt). 임시 DB 경로를 지정해 개발 DB 를 건드리지 않음
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:KINI_USE_OPENAI='0'; $env:KINI_DB_PATH="$env:TEMP\kini-guard.sqlite3"
python -m pytest -q --tb=short                                          # 239 passed, 9 skipped, 1 xfailed / 175.6초
python -m pytest tests/test_vision_inventory_integration.py -q --tb=short  # 29 passed, 1 xfailed / 56초

# Vision 환경 (Python 3.14, fridge_vision/requirements.txt + 모델)
cd fridge_vision; python -m pytest tests -q                              # 31 passed
# 실제 인식 → API: 임시 DB 로 KINI 서버를 127.0.0.1 에 띄운 뒤 (합성 계정)
python tools/kini_e2e_check.py --api http://127.0.0.1:5055 --pair before.jpg after.jpg --out report.json
```

## 7. 미검증 항목과 다음 작업

- **실제 냉장고 카메라 → A API** 연속 운용 (문 닫힘 감지 → 전송) 미실시. 이번 실제 인식 시험은 녹화 프레임·합성 사진 3쌍
- C 화면에서의 확인·거절 E2E (Playwright 미설치, C 화면 미변경) — 사용자 확인은 시험 코드가 API 로 모사
- 같은 물체 재인식(P2)·개수 오차(P3)의 실제 발생 빈도 미측정 → 부스 영상으로 촬영 단위 집계 필요
- OCR 정확도 미측정 (정답 있는 실제 포장 사진 필요)
- Redis 실서버 연동 미실시 (코드상 재고 모듈은 캐시 미사용, cache status 응답만 확인)
- 프로세스 강제 종료(커밋 도중 kill) 모사는 하지 않음. 서버 정상 재시작·응답 유실 재시도만 확인
