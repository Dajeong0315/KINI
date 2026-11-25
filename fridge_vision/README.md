# Vision AI 냉장고 — YOLO 기반 식재료 인식 모듈 (KINI)

냉장고 안 카메라 영상만으로 **문 닫힘 감지 → 내부 촬영 → 이전·현재 사진에서 식재료 인식 → 추가·제거 계산 → Supabase 고내 재고 테이블 갱신**까지 처리합니다.
GPU 없는 노트북(CPU)에서 돌아가도록 만들었고, ESP32·아두이노 같은 보드는 필요 없습니다.

```text
영상 입력 (액션캠 USB 웹캠 모드 / 영상 파일)
  → 문 닫힘 감지: 움직임이 멈추고 밝기가 기준으로 돌아온 상태가 1.5초 유지 (영상 파일은 끝에서 자동 촬영)
  → before(직전 기준 화면) · after(지금) 사진
  → YOLOE-v8m: 품목 이름(글자)으로 사진별 상자 찾기 — 학습 없이 config.ITEMS 의 품목을 인식
  → 보정: 고정 카메라 규칙(같은 자리 = 같은 물건, 화면이 안 바뀐 자리는 그대로)으로 오인식·놓침 보정
  → Unknown: 목록에 없거나 잘게 쪼개진 물체 → 사용자에게 이름을 물어보고 기억
  → 코드: 품목별 개수 차이 = 추가·제거, 확인 필요 여부 판단
  → captures/ 에 사진·결과 JSON·현재 재고 저장  +  Supabase 재고 테이블 INSERT / UPDATE
```

시연 영상 2개에서 결과가 맞았습니다 (h264: 버터 −1, h265: 오렌지 −1).
모델 선택·튜닝 과정은 [최종 보고서](docs/YOLO기반_객체인식_식재료파악_최종보고서.md)에 있습니다.

---

## 1. 폴더 구조

| 파일 | 역할 |
|---|---|
| `main.py` | 실행 파일 (영상 처리 루프, 키 조작, Unknown 이름 입력, 결과 저장, Supabase 전송) |
| `detector.py` | 문 닫힘(장면 안정) 감지기 |
| `vision.py` | 인식 엔진 3종(YOLO / OpenAI / MOCK), 전후 보정, Unknown 처리, 추가·제거 계산, 위치(LOC) 계산 |
| `supabase_sync.py` | Supabase 고내 재고 테이블 갱신 (백그라운드 전송, 실패 시 보관 후 재전송) |
| `config.py` | 품목 목록, 감지·인식 기준값, Supabase 열 이름, 냉장고 칸 나누기 — **설정은 여기서만 수정** |
| `supabase/fridge_food.sql` | Supabase 에 재고 테이블을 만드는 SQL |
| `tests/test_supabase_sync.py` | 가짜 Supabase 서버로 연동 시험 |
| `make_dummy_video.py` | 실제 촬영본 없이 시험할 수 있는 더미 냉장고 영상 생성 |
| `samples/` | 실제 영상에서 뽑은 시험 사진 2장 |
| `.vscode/launch.json` | VS Code '실행 및 디버그' 메뉴용 실행 설정 0~7 |
| `.env.example` | API 키·Supabase 설정 예시 |
| `docs/` | 최종 보고서, 하이퍼파라미터·실험 기록(CSV) |
| `train_kaggle_cls.py`, `train_clip_probe.py`, `test_fridge_cls.py`, `eval_kaggle_cls.py`, `log_clip_probe.py` | (실험) Kaggle 데이터로 만든 식재료 분류 모델 학습·평가. 메인 실행에는 쓰지 않음 |

실행 중 생기는 파일 (Git 에 올리지 않음)

| 파일 | 내용 |
|---|---|
| `captures/` | 촬영 사진, 결과 JSON, 현재 재고, Supabase 전송 대기열 |
| `user_labels.json` | 사용자가 Unknown 에 붙인 이름표 (생김새 특징 + 위치) |
| `models/`, `weights/`, `*.pt`, `mobileclip_blt.ts` | 자동으로 내려받는 모델 파일 |

---

## 2. 설치 (처음 한 번)

1. VS Code에서 **이 폴더(`fridge_vision`) 자체를 엽니다.** (`파일 → 폴더 열기`) 상위 폴더를 열면 실행 설정의 경로가 맞지 않습니다.
2. VS Code 확장 **Python**(Microsoft)을 설치합니다.
3. 터미널(`Ctrl + ~`)에서 가상환경을 만들고 패키지를 설치합니다.

   **Windows**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   pip install git+https://github.com/ultralytics/CLIP.git   # YOLO-World·사용자 이름표용 CLIP
   ```
   **macOS**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   pip install git+https://github.com/ultralytics/CLIP.git
   ```
4. `Ctrl + Shift + P` → `Python: Select Interpreter` → `.venv` 를 선택합니다.
5. `.env.example` 을 복사해 이름을 `.env` 로 바꿉니다.
   - Supabase 에 재고를 올리려면 `SUPABASE_URL`, `SUPABASE_KEY` 를 넣습니다 (6장). 비워 두면 연동 없이 동작합니다.
   - OpenAI 엔진을 쓸 때만 `OPENAI_API_KEY` 가 필요합니다. 기본 엔진(YOLO)은 키가 필요 없습니다.

**처음 실행할 때 자동으로 내려받는 파일** (인터넷 필요, 한 번만)

| 파일 | 크기 | 용도 |
|---|---|---|
| `yoloe-v8m-seg.pt` | 약 66MB | 식재료 인식 모델 (`models/` 에 넣어 두면 그 파일을 씀) |
| `mobileclip_blt.ts` | 약 600MB | YOLOE 가 품목 이름(글자)을 이해하는 데 쓰는 글자 모델. 실행 폴더에 저장 |
| `weights/clip/ViT-B-32.pt` | 약 340MB | Unknown 이름표의 생김새 비교 (Unknown 이 처음 나올 때) |

GPU 는 필요 없습니다. i7-9700 CPU 기준 사진 1장 분석에 약 0.4~0.8초 걸립니다.

---

## 3. 실행

VS Code 왼쪽 **실행 및 디버그(▷ 아이콘)** 에서 목록을 골라 ▷ 를 누릅니다.

| 실행 설정 | 같은 명령 (터미널) |
|---|---|
| 0. 기본 실행 (h265 영상 + YOLO, Unknown 이름 입력) | `python main.py` |
| 1. 더미 영상 만들기 | `python make_dummy_video.py` |
| 2. 더미 영상 + MOCK 인식 | `python main.py --source dummy/fridge_demo.mp4 --mock` |
| 3. 더미 영상 + YOLO 인식 | `python main.py --source dummy/fridge_demo.mp4` |
| 4. 액션캠(카메라 0번, 없으면 실사 영상) + YOLO 인식 | `python main.py --source 0` |
| 5. 사진 2장 비교 (더미 사진) | `python main.py --compare dummy/stills/step0_closed.jpg dummy/stills/step1_closed.jpg` |
| 6. 실제 영상 파일 + YOLO 인식 | `python main.py --source C:/Users/aicam/Videos/video_preview_h265.mp4` |
| 7. 실제 영상에서 뽑은 사진 2장 비교 | `python main.py --compare samples/real_before.jpg samples/real_after.jpg` |

- 인자 없이 실행하면 `config.DEFAULT_SOURCE` 를 씁니다. 지금은 시험용 h265 영상이고, **부스에서 카메라로 돌릴 때는 `"0"` 으로 바꿉니다.**
- 카메라가 없으면 `config.FALLBACK_VIDEO`(실사 영상), 그것도 없으면 더미 영상으로 자동 전환합니다.
- 영상 앞·뒤의 검은 로고 화면은 밝기로 자동으로 찾아 분석에서 뺍니다 (화면에는 끝까지 재생).
- 영상이 끝날 때까지 문 닫힘이 없으면 마지막 화면으로 촬영합니다. 영상이 끝나도 창은 `q` 를 누를 때까지 남아 있습니다.

기타 옵션

| 옵션 | 동작 |
|---|---|
| `--engine yolo\|openai\|mock` | 인식 엔진 (기본 `yolo`). `--mock` 은 `--engine mock` 과 같음 |
| `--loop` | 영상 파일 반복 재생 |
| `--no-window` | 화면 창 없이 실행. '확인 필요' 결과도 자동 반영하고 Unknown 이름은 묻지 않음 |
| `--no-sync` | Supabase 에 보내지 않음 (MOCK 엔진은 항상 보내지 않음) |

### 창이 떠 있을 때 키

| 키 | 동작 |
|---|---|
| `c` | 수동 촬영 (iPad '다 넣었어요' 버튼 대신) |
| `b` | 지금 화면을 기준(before)으로 다시 지정 |
| `y` / `n` | '확인 필요' 결과를 반영 / 거절 |
| `q` 또는 `ESC` | 종료 |

창 위쪽에 감지 상태가 표시됩니다.
`IDLE`(대기) → `ACTIVE`(문 열림·손 움직임) → `SETTLING`(잠잠해짐) → 촬영 → `COOLDOWN`(중복 촬영 방지)

---

## 4. 인식 방식 (YOLO 엔진)

### 4-1. YOLOE 글자 프롬프트
- `config.ITEMS` 의 품목마다 영어 검색어(`config.YOLO_PROMPTS`, 예: 수박 → `"watermelon wedge"`)를 만들어 YOLOE 에 넣습니다. 상자 라벨 데이터나 학습 없이 품목을 찾습니다.
- 한 물건이 두 품목으로 동시에 잡히지 않도록 품목 구분 없는 NMS(`agnostic_nms`)로 가장 높은 점수 하나만 남깁니다.
- 손·팔이 after 사진에 보이면 '재촬영 권장'으로 표시합니다.

### 4-2. 고정 카메라 보정
1. before·after 에서 같은 자리(IoU ≥ 0.5)의 물건은 같은 품목으로 봅니다. 이름이 다르면 점수가 높은 쪽으로 통일합니다.
2. 한쪽 사진에서만 잡혔는데 그 자리 화면이 거의 안 바뀌었으면 "그대로 있는 물건을 놓친 것"으로 보고 반대쪽에도 채웁니다. 카메라 자동 노출로 화면 전체 밝기가 바뀌어도 속지 않도록 영역 평균 밝기를 빼고 비교합니다.
3. 그래도 한쪽에만 있는 물건은 점수가 `YOLO_CHANGE_MIN_CONF`(0.35) 이상일 때만 추가·제거로 인정합니다.

### 4-3. 목록에 없는 물체 (Unknown)
- **범용 검색어**: `"object"`, `"item"` 을 함께 넣어, 어느 품목에도 안 맞는 물체가 여기로 밀려나면 `Unknown` 으로 표시합니다 (점수 0.5 이상만). 시연 영상 2개에서는 이 방식으로 잡힌 Unknown 이 없었습니다.
- **쪼개진 물체 묶기**: 포도 한 송이처럼 한 물체가 점수 낮은 작은 상자 여러 개(사과·오렌지 등)로 잡히면 하나로 묶어 `Unknown` 으로 봅니다. 점수 0.45 미만, 화면의 1% 이하 크기, 서로 붙어 있는 상자 2개 이상이 대상입니다.
- Unknown 은 재고 개수에 넣지 않습니다. 개수가 바뀌면 '확인 필요'로 표시합니다.

### 4-4. Unknown 이름 입력 (사용자 이름표)
창 모드에서 Unknown 이 나오면 **영상이 멈추고 해당 상자를 빨갛게 표시한 뒤, 터미널에서 이름을 물어봅니다.**

```text
목록에 없는 물체(Unknown) 1개 → 이름을 입력하면 기억해서 재고에 반영합니다 (건너뛰기: Enter)
  Unknown #1 (넣은 물체, 위치 [887, 314, 960, 424]) 이름은? > 포도
  기억함: Unknown #1 → 포도 (grape)
이름표 적용 후 결과: - 오렌지 1
```

- 터미널을 클릭하고 이름 입력 + Enter. 모르면 Enter 만 누르면 건너뛰고 다음에 다시 묻습니다.
- 기존 품목 이름(한글 이름·영문 id·검색어)이면 그 품목으로 셉니다. 예: `포도`·`Grape` → grape, `소시지` → ham
- 목록에 없는 이름이면 새 품목(`user_1` …)으로 추가되어 재고·Supabase 에도 그 이름으로 들어갑니다.
- 이름표는 `user_labels.json` 에 생김새(CLIP 특징)와 위치로 저장되어 **다음 촬영부터 묻지 않고 자동 적용**됩니다.
  - 같은 자리면 생김새 유사도 0.85 이상, 다른 자리로 옮겨졌으면 0.95 이상일 때 같은 물체로 봅니다. 냉장고 배경이 비슷해서 다른 물체끼리도 0.76~0.88 이 나오기 때문입니다.
- 이름표를 모두 지우려면 `user_labels.json` 을 삭제합니다.

### 4-5. 확인 필요(`needs_confirmation`)
모델의 자기 신뢰도가 아니라 코드 규칙으로 정합니다.
- after 사진에 손 또는 열린 문이 보임 → 재촬영 권장
- 변한 품목의 점수가 낮음(`YOLO_UNCERTAIN_BELOW` 0.35 미만)
- 한 품목이 한 번에 2개 이상 변함
- Unknown 개수가 바뀜

---

## 5. 결과물 (`captures/` 폴더)

```text
captures/
  baseline.jpg                    시작할 때의 기준 화면
  inventory.json                  현재 재고
  supabase_queue.jsonl            (있으면) Supabase 로 아직 못 보낸 작업
  capture-20261125-165256-397/
    before.jpg  after.jpg         촬영 사진
    before_yolo.jpg  after_yolo.jpg   최종 상자 표시 (초록: 품목, 노랑: 사용자 이름표, 빨강: Unknown)
    result.json
```

`result.json` 예시 (h265 영상, 일부 생략)

```json
{
  "capture_id": "capture-20261125-165256-397",
  "source": "yolo:yoloe-v8m-seg.pt",
  "before": {"butter": 1, "watermelon": 1, "orange": 3, "grape": 1, "juice": 2, "...": 0},
  "after":  {"butter": 1, "watermelon": 1, "orange": 2, "grape": 1, "juice": 2, "...": 0},
  "added": [],
  "removed": [{"item_id": "orange", "name": "오렌지", "quantity": 1, "locations": ["R,2"]}],
  "uncertain_items": [],
  "hand_or_open_door_visible": false,
  "needs_confirmation": false,
  "confirmation_reasons": [],
  "note": "YOLO 인식 (after): 버터 1, 수박 1, 오렌지 2, 포도 1, 주스 2",
  "unknown": {"before": 0, "after": 0},
  "unknown_regions": [],
  "latency_sec": 0.8
}
```

---

## 6. Supabase 고내 재고 테이블 연동

### 테이블 구조 (`supabase/fridge_food.sql`)

| 열 | 형식 | 내용 |
|---|---|---|
| `food_id` | bigint (PK, 자동 증가) | 행 번호 |
| `food_name` | varchar(50) | 품목 한글 이름 (예: 오렌지, 포도) |
| `food_date_in` | date | 넣은 날 |
| `food_date_out` | date | 꺼낸 날. **냉장고 안에 있으면 NULL** |
| `loc` | varchar(10) | 칸 `"X,Y"` — X: `L`(왼쪽)·`C`(가운데)·`R`(오른쪽), Y: 층 번호(1 = 맨 아래) |

- **한 행 = 냉장고 안 식재료 1개**입니다. 현재 재고 = `food_date_out IS NULL` 인 행 (뷰 `fridge_food_current` 로 품목·칸별 개수 확인).
- PostgreSQL 은 따옴표 없는 이름을 소문자로 저장하므로 `FOOD_NAME` 은 `food_name` 으로 씁니다. Oracle 의 `VARCHAR2` 는 PostgreSQL 에서 `VARCHAR` 입니다.

### 동작

| 인식 결과 | 테이블 작업 |
|---|---|
| 품목 1개 추가 | `INSERT (food_name, food_date_in = 오늘, loc)` |
| 품목 1개 제거 | 같은 이름·아직 안 꺼낸 행 중 **같은 칸(loc)** 행, 없으면 가장 오래 넣은 행 → `food_date_out = 오늘` |
| 프로그램 첫 촬영 + 테이블에 안에 있는 행이 없음 | 첫 before 사진의 품목으로 테이블을 채움 (`SUPABASE_SEED_FROM_FIRST_BEFORE`) |
| Unknown | 보내지 않음 (이름표를 붙이면 그 이름으로 보냄) |

- 재고에 **반영된** 결과만 보냅니다. '확인 필요'에서 `n`(거절)한 결과는 보내지 않습니다.
- 전송은 별도 스레드에서 하므로 네트워크가 느려도 화면이 멈추지 않습니다.
- 실패하면 작업을 `captures/supabase_queue.jsonl` 에 순서대로 보관했다가 다음 동기화 때 먼저 보냅니다.
- 사진·사용자 정보는 보내지 않습니다 (KINI 아키텍처의 Supabase 비식별 원칙).
- 칸(LOC)은 사진 속 상자 중심으로 계산합니다. 경계는 `config.FRIDGE_X_LABELS`, `FRIDGE_FLOOR_SPLITS` 에서 카메라 각도에 맞게 조정합니다.

### 설정 순서
1. Supabase 대시보드 → **SQL Editor** 에 `supabase/fridge_food.sql` 을 붙여 넣고 Run
2. **Project Settings → API** 에서 Project URL 과 키를 복사해 `.env` 에 입력
   ```text
   SUPABASE_URL=https://프로젝트ID.supabase.co
   SUPABASE_KEY=...        # 부스 PC 는 service_role 키 (RLS 우회). 이 PC 밖으로 절대 유출 금지
   SUPABASE_TABLE=fridge_food
   ```
   anon 키를 쓰려면 SQL 파일 아래쪽의 RLS 정책 주석을 풀어 필요한 권한만 허용합니다.
3. `python main.py` 실행 → 로그에 `Supabase: INSERT 오렌지 (R,1)` 같은 줄이 나오면 연결된 것입니다.
4. 이미 다른 이름의 테이블·열을 쓰고 있다면 `.env` 의 `SUPABASE_TABLE` 과 `config.SUPABASE_COLUMNS` 만 바꿉니다.

### 시험 (Supabase 계정 없이)
```bash
python tests/test_supabase_sync.py
```
같은 REST 규칙으로 답하는 가짜 서버를 띄워 시작 재고 채우기, 같은 칸 우선 꺼내기, 서버 장애 시 보관 후 재전송, 없는 품목 꺼내기를 확인합니다.

---

## 7. 설정 바꾸기 (`config.py`)

### 시연 식재료 바꾸기
`ITEMS` 에 `item_id`, 한글 이름, 생김새 설명을 넣고, YOLO 검색어가 item_id 와 다르면 `YOLO_PROMPTS` 에 영어로 짧게 적습니다.
검색어는 **실제 포장의 생김새**로 적을수록 잘 잡힙니다. 예: `"butter": "yellow butter block"`
결과 확인은 `captures/*/after_yolo.jpg` 로 합니다.

### 인식 기준값

| 값 | 기본 | 뜻 |
|---|---|---|
| `YOLO_MODEL` | `models/yoloe-v8m-seg.pt` | 인식 모델 (없으면 자동 다운로드) |
| `YOLO_CONF` | 0.2 | 이 점수 이상인 상자만 개수로 셈 |
| `YOLO_UNCERTAIN_BELOW` | 0.35 | 이보다 낮으면 '불확실' |
| `YOLO_CHANGE_MIN_CONF` | 0.35 | 새로 생기거나 사라진 물건 인정 최소 점수 |
| `YOLO_REGION_CHANGE_RATIO` | 0.15 | 자리 픽셀 변화가 이보다 적으면 '그대로 있음' |
| `YOLO_GENERIC_PROMPTS` / `YOLO_UNKNOWN_CONF` | `["object","item"]` / 0.5 | 범용 검색어 Unknown |
| `YOLO_FRAGMENT_CONF`, `YOLO_GROUP_MAX_CONF`, `YOLO_GROUP_MAX_AREA`, `YOLO_GROUP_GAP` | 0.1, 0.45, 0.01, 0.15 | 쪼개진 물체 묶기 |
| `USER_LABEL_SIM` / `USER_LABEL_SIM_SAME_PLACE` | 0.95 / 0.85 | 사용자 이름표 자동 적용 기준 |

### 감지 기준값 튜닝 (부스 조명에서 꼭 한 번)
창 위쪽 숫자(`motion`, `bright`, `base`, `scene`)를 보면서 조정합니다.

| 증상 | 바꿀 값 |
|---|---|
| 문을 열어도 `ACTIVE` 로 안 바뀜 | `BRIGHTNESS_DELTA` 를 낮춤. 문 열 때와 닫을 때 `bright` 차이의 절반 정도가 적당 |
| 문을 닫기 전에 찍힘 | `SETTLE_SECONDS` 를 늘리거나 `BRIGHTNESS_DELTA` 를 낮춤 |
| 가만히 있어도 `ACTIVE` 로 바뀜 (화면 노이즈) | `MOTION_START_RATIO`, `PIXEL_DIFF_THRESHOLD` 를 올림 |
| 식재료를 바꿨는데 '변화 없음'으로 처리됨 | `NO_CHANGE_RATIO` 를 낮춤 |

> 이 감지 방식은 **문을 열면 내부 밝기가 확실히 달라진다**는 전제에 기대고 있습니다.
> 차이가 작으면 내부 LED를 조금 어둡게 하거나 문틈을 막으세요. 그래도 부족하면 `c` 키(또는 iPad 버튼) 수동 촬영을 함께 씁니다.

---

## 8. 액션캠 연결

1. 액션캠을 **USB 웹캠 모드**로 설정하고 노트북에 연결합니다. (모델마다 메뉴 이름이 다름)
2. `config.DEFAULT_SOURCE = "0"` 으로 바꾸거나 `python main.py --source 0` 실행. 노트북 내장 카메라가 잡히면 `1`, `2` 로 바꿉니다.
3. 액션캠의 **자동 전원 꺼짐·화면 꺼짐**을 끄고, **USB 전원을 꽂은 채 몇 시간 켜 두는 테스트**를 먼저 합니다.
4. 가능하면 **노출·화이트밸런스를 고정**합니다. 자동이면 문을 열 때마다 밝기가 출렁여 감지가 흔들립니다.
5. 프로그램은 시작 후 30프레임을 버리고(자동 노출 안정 대기) 기준 화면을 저장합니다.
6. 식재료를 **쌓지 말고 한 층으로 펼쳐** 두세요. 가려진 물건은 어떤 YOLO 모델도 잘 찾지 못합니다 (보고서 10장).

---

## 9. (실험) Kaggle 데이터 분류 모델

YOLOE 상자를 잘라 다시 분류하는 2단계 인식을 시험했습니다. **메인 실행에는 연결하지 않았습니다.**

| 모델 | Kaggle 시험 정확도 | 실사(냉장고 영상) |
|---|---|---|
| YOLOv8m-cls 미세조정 (`train_kaggle_cls.py`) | 0.876 | 오렌지 → 망고 0.99 등 확신 오답 |
| CLIP-B/32 + CLIP-B/16 + EfficientNetV2-S 앙상블 (`train_clip_probe.py`) | 0.9476 | 확신 오답은 없지만 대부분 "모름" |

데이터: Kaggle `alyamibrahim/smart-fridge-food-dataset` (kagglehub 로 받아 `../kaggle_data/` 에 둠, Git 미포함).
실사 정확도를 올리려면 부스 냉장고 사진 수집이 필요합니다. 자세한 내용은 [최종 보고서](docs/YOLO기반_객체인식_식재료파악_최종보고서.md), 설정값과 실험 기록은 [docs/hyperparameters.csv](docs/hyperparameters.csv) 에 있습니다.

---

## 10. 알려진 한계

- 가려진 물건은 잡지 못합니다 (시연 영상의 4번째 오렌지). 진열 방식·카메라 각도로 해결해야 합니다.
- 범용 검색어(`object`) 방식은 시연 영상에서 Unknown 을 만들지 못했습니다. 실제로 동작한 것은 '쪼개진 물체 묶기 + 사용자 이름표'입니다.
- 사용자 이름표의 생김새 비교(CLIP)는 냉장고 배경이 비슷하면 다른 물체끼리도 유사도가 높게 나옵니다. 그래서 같은 자리일 때 주로 동작하고, 다른 칸으로 옮기면 다시 물어볼 수 있습니다.
- 칸(LOC) 경계는 화면을 같은 비율로 나눈 근사값입니다. 실제 부스 카메라 각도에서 `FRIDGE_FLOOR_SPLITS` 를 맞춰야 합니다.
- Supabase 연동은 가짜 서버로만 시험했습니다. **실제 프로젝트 URL·키로 한 번 확인해야 합니다.** 응답을 받지 못한 요청은 다시 보내므로, 드물게 같은 작업이 두 번 들어갈 수 있습니다.
- OpenAI 엔진은 요청 형식까지만 검증했습니다 (크레딧 없음).
- iPad 확인 화면·옆 모니터 연동(Socket.IO)은 아직 없습니다. 지금은 `y`/`n` 키가 iPad 확인 버튼 역할을 합니다.
