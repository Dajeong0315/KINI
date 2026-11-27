# KINI main 통합 시 재고 연결 경계

KINI의 기준 재고는 `kini_private`의 g 단위 lot이며 인식 결과만으로 바뀌지 않습니다. 기본 실행에서는 별도 공개 Supabase 개수 재고에 쓰지 않습니다. 키만 설정해도 자동으로 활성화되지 않습니다.

`tools/check_supabase.py --write`의 reset 시험은 운영 Supabase에서 실행할 수 없습니다. 별도 loopback 시험 DB와 `VISION_DISPOSABLE_TEST_DB=1`에서만 허용됩니다. `--yes`도 이 제한을 우회하지 않습니다.

KINI에서는 `--engine yolo --no-sync`로 결과를 저장하고 루트의 `scripts/vision_kini_bridge.py`로 후보를 보낸 뒤 앱에서 표준 재료·실제 g·출고 lot을 확인합니다. 원격 서버는 기존 SSH loopback 터널을 사용합니다. 자세한 절차는 `docs/7단계_Vision_연결_구현검증_2026-11-27.md`를 따릅니다.

아래 기존 문서의 `supabase_sync`·`current_stock`·자동 반영은 **별도 개수 재고용 기능**입니다. 이 경로는 `VISION_ALLOW_LEGACY_SUPABASE_WRITE=1`을 명시하고 KINI API URL이 없는 경우에만 활성화됩니다. KINI 통합의 기본 경로가 아닙니다. `phone_live`의 ‘DB에 반영’도 g 확인을 대신하지 않습니다. 실제 OpenAI 호출과 사진 외부 전송은 이번 통합에서 사용하지 않았습니다.


# Vision AI 냉장고 — YOLO 기반 식재료 인식 모듈 (KINI)

냉장고 안 카메라 영상만으로 **문 닫힘 감지 → 내부 촬영 → 이전·현재 사진에서 식재료 인식 → 추가·제거 계산 → Supabase 고내 재고 테이블 갱신**까지 처리합니다.
GPU 없는 노트북(CPU)에서 돌아가도록 만들었고, ESP32·아두이노 같은 보드는 필요 없습니다.

```text
영상 입력 (액션캠 USB 웹캠 모드 / 영상 파일)
  → 문 닫힘 감지: 움직임이 멈추고 밝기가 기준으로 돌아온 상태가 1.5초 유지 (영상 파일은 끝에서 자동 촬영)
  → before(직전 기준 화면) · after(지금) 사진
  → YOLOE-v8m(원본): 영어 검색어로 사진별 후보 상자 찾기 — 학습 없이 config 의 품목을 인식
  → CLIP 재확인: 상자를 잘라 다시 보고, 확신하면 그 라벨로 확정 (비슷한 물건 헷갈림 보정)
  → 포장 식품: 포장 글자를 OCR 로 읽어 이름 만들기 (예: "비비고 왕교자")
  → 보정: 고정 카메라 규칙(같은 자리 = 같은 물건, 화면이 안 바뀐 자리는 그대로)으로 오인식·놓침 보정
  → Unknown: 목록에 없거나 잘게 쪼개진 물체 → 사용자에게 이름을 물어보고 기억
  → 코드: 품목별 개수 차이 = 추가·제거, 확인 필요 여부 판단
  → captures/ 에 사진·결과 JSON·현재 재고 저장  +  Supabase 재고 DB (capture·stock_event → confirm_stock_event)
```

Kaggle 식재료 사진 20종 500장에서 1등 상자 정답률이 **예전 방식 19.8% → 지금 방식 69.2%** 입니다 (계란 1/25 → 21/25, 4-0장).
예전 방식은 `config.DETECTOR_BACKEND = "kaggle"` 한 줄로 그대로 되돌릴 수 있습니다.
모델 선택·튜닝 과정은 [최종 보고서](docs/YOLO기반_객체인식_식재료파악_최종보고서.md)에 있습니다.

---

## 1. 폴더 구조

| 파일 | 역할 |
|---|---|
| `main.py` | 실행 파일 (영상 처리 루프, 키 조작, Unknown 이름 입력, 결과 저장, Supabase 전송) |
| `detector.py` | 문 닫힘(장면 안정) 감지기 |
| `vision.py` | 인식 엔진 3종(YOLO / OpenAI / MOCK), YOLO 의 두 방식(새 방식 / 예전 방식), CLIP 재확인, 전후 보정, Unknown 처리, 추가·제거 계산, 위치(LOC) 계산 |
| `ocr_names.py` | 포장 식품 OCR(EasyOCR) → 대표 이름 규칙. OCR 엔진은 작은 인터페이스(`OcrEngine`)로 분리 |
| `tools/build_prompt_cache.py` | 검색어 임베딩 NPZ 만들기 (인터넷 없이 실행용). 검색어를 바꾸면 다시 실행 |
| `tools/compare_detectors.py` | 예전 방식 ↔ 새 방식 비교표(`reports/compare.md`)와 상자 그림(`reports/images/`) |
| `tests/test_detector_openvocab.py` | 새 방식 단위 테스트 (모델·네트워크 없이) |
| `reid/` | 등록 물체 다시 알아보기 (4-6): `tracks.py` 위치·변화 기반 유지, `gallery.py` 증강 갤러리, `augment.py` 잘라내기·증강, `embedder.py` DINOv2/CLIP 특징 |
| `tools/eval_reid.py` | 재인식 평가 (예전 방식·원본 1장·증강 갤러리·배경 지우기 비교, 추천 기준값) |
| `tests/test_reid.py` | 재인식 단위 테스트 (모델·네트워크 없이) |
| `supabase_sync.py` | Supabase 고내 재고 DB 연동 창구 (백그라운드 전송, 실패 시 `state/db_outbox.jsonl` 보관 후 재전송, 확인 필요 알림) |
| `db/` | Supabase 클라이언트(읽기·쓰기 키 분리)와 저장 함수 (6장) |
| `tools/check_supabase.py`, `tools/flush_outbox.py` | Supabase 점검 표, 대기열 바로 재전송 |
| `tests/test_db_repository.py` | Supabase 연동 단위 시험 (가짜 클라이언트, 네트워크 없이) |
| `config.py` | 품목 목록, 감지·인식 기준값, Supabase 열 이름, 냉장고 칸 나누기 — **설정은 여기서만 수정** |
| `supabase/schema.sql` | Supabase 재고 DB (테이블 4개·뷰·함수·권한) 를 만드는 SQL |
| `tests/test_supabase_sync.py` | 실제 PostgreSQL + Supabase REST 흉내 서버로 연동 시험 |
| `reports/compare.md` | 시험 사진 19장의 예전 방식 ↔ 새 방식 비교 결과 (`tools/compare_detectors.py` 출력) |
| `make_dummy_video.py` | 실제 촬영본 없이 시험할 수 있는 더미 냉장고 영상 생성 |
| `samples/` | 실제 영상에서 뽑은 시험 사진 2장 |
| `.vscode/launch.json` | VS Code '실행 및 디버그' 메뉴용 실행 설정 0~7 |
| `.env.example` | API 키·Supabase 설정 예시 |
| `docs/` | 최종 보고서, DB 설계 설명, 하이퍼파라미터·실험 기록(CSV) |
| `train_kaggle_cls.py`, `train_clip_probe.py`, `test_fridge_cls.py`, `eval_kaggle_cls.py`, `log_clip_probe.py` | (실험) Kaggle 데이터로 만든 식재료 분류 모델 학습·평가. 메인 실행에는 쓰지 않음 |

실행 중 생기는 파일 (Git 에 올리지 않음)

| 파일 | 내용 |
|---|---|
| `captures/` | 촬영 사진, 결과 JSON, 현재 재고, Supabase 전송 대기열 |
| `user_labels.json` | 사용자가 Unknown 에 붙인 이름표 (생김새 특징 + 위치) |
| `logs/detections.jsonl` | 새 방식의 인식 기록 (검색어 원문·점수·OCR 원문). 20MB 넘으면 `.1` 로 넘김 |
| `state/tracks.json` | 재인식: 지금 냉장고 안에 있다고 보는 물체 목록 (트랙). `python main.py --reset-reid` 로 비움 |
| `gallery/` | 재인식: 등록 물체별 특징값(`<id>.npz`)·정보(`<id>.json`)·잘라낸 사진·증강 미리보기(`<id>/preview/`) |
| `logs/reid.jsonl` | 재인식 판정 근거 (후보 1·2등 점수, 적용 규칙, 제거·이동) |
| `models/`, `weights/`, `*.pt`, `mobileclip_blt.ts` | 자동으로 내려받는 모델 파일 (단 `models/prompts_*.npz` 는 Git 에 포함) |

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
   pip install git+https://github.com/ultralytics/CLIP.git   # CLIP 재확인·사용자 이름표용
   pip install easyocr==1.7.2 --no-deps                     # 포장 식품 OCR (--no-deps 꼭 붙이기)
   ```
   **macOS**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   pip install git+https://github.com/ultralytics/CLIP.git
   pip install easyocr==1.7.2 --no-deps
   ```
   > `easyocr` 를 `--no-deps` 없이 설치하면 `opencv-python-headless` 가 함께 깔려 기존 `opencv-python` 과 충돌하고 화면 창이 뜨지 않습니다.
   > 이미 그렇게 됐다면: `pip uninstall -y opencv-python-headless` → `pip install --force-reinstall opencv-python`
4. `Ctrl + Shift + P` → `Python: Select Interpreter` → `.venv` 를 선택합니다.
5. `.env.example` 을 복사해 이름을 `.env` 로 바꿉니다.
   - Supabase 재고 DB 에 반영하려면 `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY` 를 넣습니다 (6장). 비워 두면 연동 없이 동작합니다.
   - OpenAI 엔진을 쓸 때만 `OPENAI_API_KEY` 가 필요합니다. 기본 엔진(YOLO)은 키가 필요 없습니다.

**처음 실행할 때 자동으로 내려받는 파일** (인터넷 필요, 한 번만)

| 파일 | 크기 | 용도 |
|---|---|---|
| `yoloe-v8m-seg.pt` | 약 66MB | 식재료 인식 모델 (`models/` 에 넣어 두면 그 파일을 씀) |
| `weights/clip/ViT-B-16.pt` | 약 335MB | CLIP 재확인 (새 방식). 처음 실행 때 `weights/clip/openvino/` 에 OpenVINO 변환본을 만듦 |
| `weights/easyocr/` | 약 96MB | 포장 식품 OCR (한국어+영어) |
| `weights/clip/ViT-B-32.pt` | 약 340MB | Unknown 이름표의 생김새 비교 (Unknown 이 처음 나올 때) |
| `mobileclip_blt.ts` | 약 600MB | YOLOE 의 글자 모델. **`models/prompts_yoloe-v8m-seg.npz` 가 있으면 필요 없음** (검색어를 바꿨을 때만) |

모두 받은 뒤에는 **인터넷 없이** 동작합니다 (검색어 임베딩은 NPZ, CLIP·OCR 은 `weights/`).
GPU 는 필요 없습니다. i7-9700 CPU 기준 사진 1장 분석에 약 1~2초 걸립니다 (예전 방식 약 0.5초).

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

`config.DETECTOR_BACKEND` 로 두 방식 중 하나를 씁니다. 결과 형식은 같아서 나머지 코드(재고 계산·Supabase·phone_live)는 그대로 동작합니다.

| 값 | 방식 |
|---|---|
| `"yoloe_openvocab"` (기본) | 원본 YOLOE + 영어 검색어 31개 품목 + **CLIP 재확인** + **포장 식품 OCR** |
| `"kaggle"` | 예전 방식 그대로 (25개 품목, `YOLO_PROMPTS` 검색어). 결과가 바꾸기 전과 똑같음을 확인함 |

`.env` 에 `DETECTOR_BACKEND=kaggle` 을 넣어도 됩니다.

### 4-0a. 글씨 우선 + Unknown 입력 (`TEXT_FIRST = True`, 기본 — 2026-11-26)

목표(문 열기 전·후 사진 비교 → 출입 식재료 → DB)에 맞춰, **이름은 확실할 때만 붙이고 나머지는 사람에게 묻는** 방식입니다.

```text
YOLOE 후보 상자 + CLIP ─┐
사진 전체 OCR 1회 ──────┴→ 글자 조각을 감싸는 상자에 배정 (포장류 상자 우선, 그다음 가장 작은 상자)
상자마다
  1) 글씨가 보이면 → 써 있는 글씨로 이름
       식품·브랜드 사전(config.FOOD_WORDS / BRAND_WORDS)으로 오인식 보정 (자모 단위 비교, 3글자 이상)
         '골멩이'→골뱅이, '우사골곧탕'→사골곰탕, '스위트론'→스위트콘, '고주참지'→고추참치
       이름 = 브랜드 + 식품 단어 (예: '유동 번데기', '동원 고등어', '비비고 삼계탕')
       사전에 없는 글씨는 OCR 신뢰도 0.85 이상일 때만 그대로 이름, 브랜드만 있으면 이름으로 안 씀
       한 상자 안에 같은 이름이 여러 번 보이면 그 수만큼 셈 (쌓인 캔 3개 → 3개)
  2) 글씨가 없으면 CLIP·YOLOE 신뢰도 0.75 이상일 때만 품목 라벨
       김치·주스·생수·캔·햄처럼 생김새로 구분할 수 없는 용기 품목은 글씨나 사용자 입력이 필요
  3) 물체로 보이지만(YOLOE 0.25 이상) 판단할 수 없으면 → Unknown → 사용자가 이름 입력
       → 생김새를 기억(user_labels.json)해서 다음부터 그 이름, DB 에는 입력한 이름으로 저장
버림: 가격 숫자('8,700')가 있는 상자(가격표), CLIP 이 배경(가격표·선반·벽·사람)으로 본 상자,
      글씨 상자를 감싸는 묶음 상자, 포장 안의 그림·부분 상자, 사진 끝에 잘린 물체의 글씨
```

검증 (첨부 매장 사진 3장, 1280 폭 = 핸드폰 전송 크기, 피드백 6회 반복)

| 사진 | 결과 | 틀린 이름 |
|---|---|---|
| 통조림 진열대 | 유동 번데기 ×3, 고추참치 ×2, 유동 골뱅이 ×2, 동원 고등어, 스위트콘 ×2, Unknown ×2(아래 칸 빨간 용기) | 0 (가격표 6개 모두 제외) |
| 파우치 | 비비고 삼계탕, 사골곰탕, Unknown ×2 (백설 파우치 — 입력 힌트 '백설', 위 칸 잘린 컵) | 0 |
| 컵과일 | 컵과일 ×3 (원본 해상도에서는 1개가 '조각사과'), Unknown ×4 (아래·위 칸 잘린 과일) | 0 |

개수는 일부 부족 (참치 캔 약 4 → 2, 작은 골뱅이 캔 2 → 1, 옥수수 캔 3 → 2). 처리 시간 한 장 약 9~15초 (OCR 6~8초 포함).
실시간 화면은 OCR 을 새로 하지 않고 최근 '식재료 파악하기'의 글씨 결과(같은 자리·생김새)만 다시 씁니다.

### 4-0. 새 방식 (`yoloe_openvocab`, `TEXT_FIRST = False` 일 때의 이전 규칙)

```text
YOLOE(원본 yoloe-v8m-seg.pt) ── 영어 검색어(OPENVOCAB_PROMPTS·PACKAGE_PROMPTS)로 후보 상자 (점수 0.1 이상)
  → CLIP 재확인: 상자를 잘라 CLIP(ViT-B/16)에 보여 줌
      CLIP 이 확신(50% 이상)  → 그 라벨로 확정 (YOLOE 점수가 낮아도 셈)
      확신 없음              → YOLOE 라벨, 단 점수 0.25 이상일 때만 셈 (약한 상자는 Unknown 묶음 찾기에만)
  → 포장 식품 (YOLOE 포장 검색어 또는 CLIP '포장'):
      포장 글자를 OCR → 기존 품목 단어('milk', '우유' …)가 있으면 그 품목
                      → 아니면 가장 큰 글자 2개로 이름 (예: "비비고 왕교자")
                      → 글자를 못 읽으면 CLIP 이 확신한 품목, 그것도 없으면 unknown_package
```

- **원본 체크포인트를 씁니다.** 미세조정한 YOLOE 는 학습한 품목만 찾고 검색어를 바꿀 수 없기 때문입니다. 브랜드·제품명은 어떤 모델도 모른다고 보고, 포장은 일반 이름(`food package`, `pouch`, `box` …)으로 찾은 뒤 OCR 로 이름을 만듭니다.
- **라벨 이름은 예전과 같습니다** (`milk`, `egg` …). 검색어 → 라벨 매핑은 `config.OPENVOCAB_PROMPTS` (예: `"milk carton"`, `"milk bottle"` → `milk`, `"egg"`, `"egg carton"` → `egg`).
- 추가 품목: Kaggle 데이터 20종 중 없던 `banana`, `bread`, `fish`, `mango`, `onion`, `pepper`. DB `food_item` 에 없는 품목은 Supabase 가 자동 등록합니다 (`SUPABASE_REGISTER_MISSING_ITEMS`).
- **포장 식품 이름**(`PACKAGE_LABEL_MODE`)
  - `"ocr_name"`(기본): OCR 이름을 실행 중 새 품목으로 추가해 라벨로 씀. id 는 이름에서 만든 고정값 `pkg_<해시>` 라 다시 실행해도 같은 이름이면 같은 id → 재고·DB 기록이 이어짐
  - `"generic"`: 라벨은 `packaged_food` 하나, OCR 이름은 `logs/detections.jsonl` 에만
- OCR 규칙: 신뢰도 0.4 미만 / 숫자만 / 숫자가 섞인 낱말(`1.05kg`, `t3ra`) / 용량·영양(`g` `kg` `ml` `kcal` `%`) / 안내 문구(`OCR_EXCLUDE_WORDS`: 원재료·보관·유통기한 …) / 한 글자는 버리고, **글자 높이가 큰 순**으로 2개를 골라 읽는 순서로 이어 붙임 (최대 20자).
- 속도: 상자가 작으면(48px 미만) OCR 생략, 한 장에서 OCR 은 큰 포장 6개까지, 같은 자리·같은 생김새는 30초 동안 OCR·CLIP 결과를 다시 씀.
- 같은 라벨의 큰 상자 안의 작은 상자(계란판 안의 계란 낱개)는 버리고 1개로 셉니다. 품목으로 확정된 상자와 겹치는 Unknown 묶음도 버립니다.
- **인터넷 없이 실행**: `models/prompts_<체크포인트>.npz`(검색어 임베딩)가 있으면 YOLOE 글자 모델(600MB)을 쓰지 않습니다. 검색어를 바꾸면 `python tools/build_prompt_cache.py` 로 다시 만듭니다 (안 하면 경고 후 글자 모델로 계산).

**정확도·속도 실측** (Kaggle `smart-fridge-food-dataset` test 20종 × 25장 = 500장, 1등 상자가 정답인 비율, i7-9700 CPU)

| 방식 | 정답률 | 계란 | 사진 1장 |
|---|---|---|---|
| 예전 방식 (`kaggle`) | 19.8% | 1/25 | 0.5초 |
| 새 방식, YOLOE 검색어만 (v8m / 26m / 26l) | 26.8% / 29.4% / 31.8% | 3 / 6 / 8 | 0.9 / 1.3 / 1.6초 |
| YOLOE + CLIP 재확인 (v8m / 26m / 26l, OCR 제외) | 65.0% / 65.0% / 65.2% | | |
| **최종: v8m + CLIP B/16 + OCR** | **69.2%** | **21/25** | 1.4초 |

CLIP 재확인을 붙이면 체크포인트 차이가 거의 없어서, 가장 빠른 `yoloe-v8m-seg.pt` 를 기본으로 둡니다 (`OPENVOCAB_MODEL` 로 `yoloe-26m-seg.pt`·`yoloe-26l-seg.pt` 로 바꿀 수 있고, 각 NPZ 도 만들어 둠).
남은 약점: 생선 5/25, 망고 8/25, 버터 10/25, 양상추 10/25 (Kaggle 사진은 상품 사진이라 실제 냉장고와 다름).

비교 도구: `python tools/compare_detectors.py <사진 폴더> [--conf 0.3] [--model yoloe-26m-seg.pt]` → `reports/compare.md`

**속도** — 모델은 그대로 두고 실행 방식만 바꿈 (i7-9700 + 내장 그래픽 UHD 630, 결과 동일 확인)

| 방법 | 효과 | 정확도 |
|---|---|---|
| 설정 묶음 `DETECT_PROFILES` (`live` / `full` / `default`) | phone_live 실시간: OCR 은 최근 결과 재사용만, CLIP 은 상자 10개까지 → 한 장 833 → 545 ms | Kaggle 실측 변화 없음 |
| `CLIP_VERIFY_MIN_PX` 12 → 24 | 아주 작은 상자 CLIP 생략 (CLIP 작업 약 10% 감소) | 변화 없음 |
| CLIP 을 OpenVINO 로 내장 그래픽에서 (`CLIP_VERIFY_RUNTIME="openvino"`) | CLIP 상자당 185 → 91 ms, CPU 는 YOLOE 에 양보. 식재료 파악하기 2.0 → 1.6초 | 특징 코사인 0.9999 |
| 같은 자리·같은 생김새는 30초 동안 CLIP·OCR 결과 재사용 | 고정 카메라에서 큰 효과 | 변화 없음 |

시험했지만 쓰지 않은 방법: CLIP INT8 동적 양자화(1.3배 빠르지만 특징이 크게 달라짐, 코사인 0.81), YOLOE OpenVINO(CPU 285 → 558~2319 ms 로 오히려 느림), YOLOE 입력 960(우유·포장을 놓침), 점수 높은 YOLOE 상자의 CLIP 생략(정답률 −3~−11%p).

### 4-1. 예전 방식 (`kaggle`): YOLOE 글자 프롬프트
- `config.ITEMS` 의 품목마다 영어 검색어(`config.YOLO_PROMPTS`, 예: 수박 → `"watermelon wedge"`)를 만들어 YOLOE 에 넣습니다. 상자 라벨 데이터나 학습 없이 품목을 찾습니다.
- 한 물건이 두 품목으로 동시에 잡히지 않도록 품목 구분 없는 NMS(`agnostic_nms`)로 가장 높은 점수 하나만 남깁니다 (두 방식 공통).
- 손·팔이 after 사진에 보이면 '재촬영 권장'으로 표시합니다 (두 방식 공통).
- 아래 4-2 ~ 4-5 는 두 방식 공통입니다.

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

### 4-6. 등록 물체 다시 알아보기 (재인식, `REID_MODE = "gallery"`, 기본 — 2026-11-27)
Unknown 에 이름을 붙인 불투명 용기(예: 인절미 반찬통)를 각도가 조금 바뀌거나 자리를 옮겨도 다시 알아보게 합니다.
설정은 `config.py` 의 '4-5'. `REID_MODE = "legacy"` 로 바꾸면 위 4-4 의 예전 방식 그대로 동작합니다.

**① 위치·변화 기반 유지** (고정 카메라의 before/after 비교 = `main.py`, `compare()` 에서만)
- 지금 냉장고 안에 있다고 보는 물체 목록(트랙)을 `state/tracks.json` 에 저장합니다 (다시 켜도 유지).
- before/after 를 회색조로 바꿔 차이 → 임계값 → 작은 잡음 제거로 **변화 마스크**를 만들고, 상자마다 바뀐 픽셀 비율을 셉니다.
- after 의 상자마다 순서대로:

| 규칙 | 조건 | 처리 |
|---|---|---|
| 그대로 | 상자 안 변화 < 5% 이고 기존 트랙과 IoU ≥ 0.5 | 다시 판정하지 않고 이전 이름 (특징값 계산도 안 함) |
| 살짝 돌아감·이동 | 같은 칸, IoU ≥ 0.2 또는 중심 거리 ≤ 대각선 × 0.5, 생김새 유사도 ≥ 0.80 | 같은 물체로 보고 이전 이름. 그 모습을 갤러리에 더함 |
| 새 물체·멀리 이동 | 위 둘 다 아님 | 갤러리 전체와 비교 → 맞으면 등록 이름 / 애매하면 Unknown(입력 칸에 후보 이름) / 아니면 탐지·OCR 이름 |

- 짝이 없는 트랙: 그 자리에 변화가 있으면 **제거**, 변화가 없으면 탐지가 잠깐 놓친 것으로 보고 **유지** (재고에서 사라지지 않음).
- 등록 물체가 한 칸에서 사라지고 다른 칸에 나타나면 **위치 이동** (꺼냄·넣음이 아님). 재고 개수는 그대로이고 `logs/reid.jsonl` 에만 남깁니다.
- 이름 출처 우선순위: 사용자 이름 > 포장 글씨 > 탐지 품목 > Unknown. 낮은 쪽이 높은 쪽 이름을 덮지 않습니다.
- 데모 초기화: `python main.py --reset-reid` (트랙만 비우고 등록 갤러리는 남김).

**② 증강 임베딩 갤러리** (phone_live 와 고정 카메라 모두)
- Unknown 에 이름을 붙이면 그 자리 사진 1장을 잘라(여백 10%) **24장으로 증강**합니다: 회전 ±10·20·30°, 원근(좌우 10·20% — 용기를 좌우로 돌린 효과), 좌우 반전, 크기 0.85·1.15배, 밝기 ±25%, 대비 ±20%, 색온도, 약한 흐림을 하나씩 + 2~3개 무작위 조합 (시드 고정).
- 원본 + 증강 25장을 **DINOv2-small** 특징값으로 `gallery/<id>.npz` 에 저장합니다. 증강 사진 8장은 `gallery/<id>/preview/` 에서 눈으로 확인할 수 있습니다.
- 비교 점수 = 그 물체 갤러리와의 유사도 **상위 3개 평균**. 1등 ≥ `MATCH_SIM`(0.85) 이고 2등과 0.05 이상 차이 → 그 물체 / 차이가 작음 → 확인 필요 / 아니면 Unknown.
- **'이 물체가 아님'(네거티브)**: 이름을 붙일 때 같은 사진의 다른 물체들(겹치지 않는 상자)의 특징값을 `gallery/_negatives.npz` 에 저장합니다. 어떤 상자가 등록 물체보다 네거티브와 더 비슷하면 그 이름을 붙이지 않습니다. 등록 물체가 1개뿐이라 1·2등을 비교할 수 없을 때도 이웃 물체에 이름이 번지지 않게 합니다 (실제 샘플에서 오렌지·배경이 '인절미'로 바뀌던 문제를 막음). 나중에 그 물체를 등록하면 해당 네거티브는 지웁니다.
- 같은 물체로 확인된 실제 사진은 갤러리에 계속 더합니다 (물체당 최대 40개, 넘으면 오래된 증강본부터 지움).
- 이미 등록된 다른 물체와 유사도 0.85 이상이면 등록할 때 경고를 남깁니다 ("…과 구분이 어렵습니다. 라벨 스티커를 권장합니다." — 터미널·`logs/reid.jsonl`).
- 실시간 화면(phone_live live)은 속도 때문에 Unknown 상자만 갤러리와 비교하고, '식재료 파악하기'와 고정 카메라는 탐지 품목 상자도 비교합니다.
- 갤러리 도입 전의 이름표(`user_labels.json`)는 갤러리에 안 맞을 때 예전 방식으로 계속 비교합니다.

**처음 한 번: 특징 모델 받기** (약 90MB, 이후 인터넷 없이 실행)
```bash
pip install transformers huggingface_hub
python -c "from huggingface_hub import snapshot_download as s; s('facebook/dinov2-small', local_dir='weights/dinov2-small', allow_patterns=['*.json','*.safetensors'])"
```
내장 그래픽용 변환(`weights/dinov2-small/openvino/`)은 첫 실행 때 자동으로 만듭니다 (약 20초).

**평가** — 같은 용기를 여러 각도·위치·조명에서 5~10장씩 찍어 `eval_reid/<물체이름>/` 에, 등록하지 않은 다른 용기 사진을 `eval_reid/_unknown/` 에 넣고:
```bash
python tools/eval_reid.py          # → eval_reid/report.md, eval_reid/similarity_*.png
```
용기가 사진에 꽉 차게 찍거나, 폴더에 `boxes.json` (`{"01.jpg": [x1, y1, x2, y2]}`) 을 넣습니다.

**한계**
- 증강은 2D 변형이라 용기를 실제로 돌렸을 때 보이는 옆면·뒷면은 만들 수 없습니다 → 확인된 실제 사진을 갤러리에 더해 보완합니다.
- 같은 모양의 불투명 용기는 사진만으로 구분할 수 없습니다. 억지로 판정하지 않고 확인 필요(후보 이름)로 넘깁니다 → 뚜껑에 이름 스티커를 붙이면 글씨 우선 방식(4-0a)으로 가장 정확합니다.
- `MATCH_SIM`(0.85)·`NEAR_SIM`(0.80) 은 아직 임시값입니다. 지시서 초깃값(0.75·0.65)에서 올린 근거는 실제 냉장고 샘플 1쌍(같은 물체 0.93~0.98, 옆의 다른 물체 0.78~0.84)과 매장 사진 평가(추천 0.82)뿐입니다. 실제 용기를 돌려 찍은 사진으로 `tools/eval_reid.py` 를 돌려 정해야 합니다.
- 탐지 결과에 분할 마스크가 없어 배경 지우기는 GrabCut 으로 대신합니다. 매장 사진 평가(시험 5장)에서 정답률이 오히려 낮고(60% → 20%) 비교 1장이 약 0.8초로 느려 기본은 끔(`REID_BG_MASK = None`).
- 속도 (i7-9700 + 내장 그래픽, OpenVINO): 등록 1회 약 1.2~1.6초 (첫 등록은 약 5초), 물체 1개 비교 약 40ms. 실시간 화면은 Unknown 상자만 비교합니다.

---

## 5. 결과물 (`captures/` 폴더)

```text
captures/
  baseline.jpg                    시작할 때의 기준 화면
  inventory.json                  현재 재고
  (Supabase 로 아직 못 보낸 작업은 state/db_outbox.jsonl — 6장)
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
  "removed": [{"item_id": "orange", "name": "오렌지", "quantity": 1, "locations": ["R,1"]}],
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

## 6. Supabase 고내 재고 DB 연동

DB 구조와 규칙은 [DB 설계 설명](docs/DB_재고테이블_설계_설명.md), 만드는 SQL 은 [`supabase/schema.sql`](supabase/schema.sql) 에 있습니다. 연동 코드는 [`db/`](db/) (supabase-py) 와 [`supabase_sync.py`](supabase_sync.py)(main.py 창구)입니다.

> 2026-11-27 팀 결정: 재고는 로컬 DB 를 쓰지 않고 Supabase 에 올립니다. KINI `main` 의 일부 문서(README·A 개발현황)에는 아직 "실재고는 로컬 SQLite 전용"으로 적혀 있으니 팀 문서도 함께 갱신해야 합니다.

```text
인식 결과 ─▶ capture(촬영 기록) + stock_event(변경 후보, pending)
재고에 반영(자동 또는 y) ─▶ confirm_stock_event() ─▶ fridge_stock(식재료 1개 = 1행) 갱신
거절(n) ─▶ reject_stock_event() ─▶ 재고 그대로
데모 초기화 (python main.py --reset-demo) ─▶ reset_demo()
화면(iPad·모니터) ◀── current_stock(현재 재고 뷰)
```

| 테이블·뷰 | 내용 |
|---|---|
| `food_item` | 식재료 목록 (`food_id` = `config.ITEMS` 의 item_id, 보관 일수, 기본 칸) |
| `capture` | 촬영 1회 = 1행 (엔진, 사진 경로, result.json 원본, 처리 시간) |
| `stock_event` | "오렌지 −1 (R,1)" 같은 변경 후보. `pending` → `confirmed` / `rejected` |
| `fridge_stock` | 실제 재고. 식재료 1개 = 1행, `date_out` 이 NULL 이면 냉장고 안, 소비기한·먹음/폐기 기록 |
| `current_stock` | 품목별 현재 개수와 가장 가까운 소비기한 (화면용) |

| 파일 | 역할 |
|---|---|
| `db/supabase_client.py` | 클라이언트 2개: **reader**(Publishable 키, 읽기만) / **writer**(Secret 키, 쓰기·함수). 요청 타임아웃 5초, 키가 없거나 자리가 바뀌면 한국어 오류 (키 값은 메시지에 넣지 않음) |
| `db/repository.py` | `record_capture` · `create_events` · `confirm_event` · `reject_event` · `reset_demo` · `get_current_stock` · `get_pending_events` |
| `supabase_sync.py` | main.py 창구: 백그라운드 전송, 대기열 `state/db_outbox.jsonl`, 확인 필요 알림 |
| `tools/check_supabase.py` | 읽기 점검 (`--write`: 쓰기 점검, 실제 DB 를 바꿈) |
| `tools/flush_outbox.py` | 쌓인 대기열을 지금 다시 보냄 |

### 동작
- **재고는 직접 INSERT·UPDATE 하지 않고** RPC 함수(`confirm_stock_event` · `reject_stock_event` · `reset_demo`)로만 바꿉니다.
- 같은 품목이라도 **칸이 다르면 이벤트를 칸마다** 저장합니다. 칸 `loc_x`(L·C·R) / `loc_y`(1 = 위 칸, 2 = 아래 칸)는 사진 속 상자 위치로 계산하고, 모르면 비워 둬서 `food_item` 기본 위치가 들어갑니다.
- 같은 `capture_id` 를 다시 보내도 이벤트를 다시 만들지 않습니다 (`capture` 는 덮어쓰기). 이미 처리된 이벤트는 다시 확인·거절하지 않습니다.
- **재고보다 많이 빼는 결과**(`stock mismatch`)는 재고를 바꾸지 않고 이벤트를 `pending` 으로 남깁니다. 창 상태줄에 `CHECK NEEDED: DB stock mismatch …`, 로그에 "확인 필요 (DB 재고 불일치)"가 뜹니다 → iPad·화면에서 수정하거나 거절.
- **DB 에 없는 라벨**: 새 인식 품목(바나나 등), 포장 글씨 이름(`pkg_*`), 사용자가 Unknown 에 붙인 이름(`user_*`)은 `food_item` 에 자동 추가합니다 (보관 일수 7일 임시값, 스키마 변경 아님). `unknown` · `unknown_package` · `packaged_food` 는 **어떤 경우에도 DB 에 넣지 않습니다**.
- **위치 이동**(재인식에서 칸만 바뀐 경우)은 스키마에 전용 함수가 없어 DB 에 쓰지 않고 `logs/reid.jsonl` 에만 남깁니다.
- **영상 파일로 시험할 때**는 첫 촬영에서 DB 재고를 카메라가 본 before 개수에 맞춥니다 (`source='manual'` 이벤트). 부스 카메라는 `python main.py --reset-demo` 로 맞춰 두고 시작합니다.
- iPad 에서 확인하려면 `.env` 에 `SUPABASE_CONFIRM_MODE=ipad` → 이 프로그램은 pending 이벤트만 저장합니다.
- **DB 가 안 될 때도 시연은 멈추지 않습니다.** 전송은 별도 스레드에서 순서대로 하고, 네트워크·서버 오류면 `state/db_outbox.jsonl` 에 쌓아 로그에 "DB 저장 대기 중 N건"을 남깁니다. 다음 촬영 때 먼저 다시 보내거나 `python tools/flush_outbox.py` 로 바로 보냅니다. (예전 위치 `captures/supabase_queue.jsonl` 이 있으면 자동으로 옮김)
- 사진은 기본으로 올리지 않습니다 (`UPLOAD_PHOTOS=false` → 로컬 상대 경로 `captures/<id>/before.jpg` 만 저장). 켜면 Storage 버킷(`SUPABASE_PHOTO_BUCKET`)에 올리는데, 원본 사진이 외부로 나가므로 팀 합의 후에만 켜세요.

### 설정 순서
1. Supabase 대시보드 → **SQL Editor** 에 `supabase/schema.sql` 전체를 붙여 넣고 Run (이미 만들어 두었으면 생략)
2. **Project Settings → API Keys** 에서 복사해 `.env` 에 직접 입력 (**키 값은 코드·로그·git 에 남기지 않습니다**)
   ```text
   SUPABASE_URL=https://프로젝트ID.supabase.co
   SUPABASE_PUBLISHABLE_KEY=sb_publishable_...   # 읽기 전용 (anon)
   SUPABASE_SECRET_KEY=sb_secret_...             # 쓰기·함수 실행 (service_role). 이 PC 밖으로 절대 유출 금지
   SUPABASE_CONFIRM_MODE=local
   ```
   예전 이름 `SUPABASE_KEY` 도 Secret 키로 인식합니다.
3. `python tools/check_supabase.py` → 읽기 점검 표가 모두 "정상"이면 연결된 것입니다.
4. (선택) `python tools/check_supabase.py --write` → 쓰기 점검 (reset_demo → 우유 +1·브로콜리 −1 확인 → 닭고기 −3 거부 확인 → reset_demo). **실제 DB 기록을 지우므로** 실행 전에 확인을 묻습니다.

화면(iPad·모니터)은 **Publishable 키로 읽기만** 할 수 있습니다 (쓰기·함수 실행은 RLS·권한으로 거부).

### 시험 (Supabase 계정 없이)
```bash
python -m pytest tests/test_db_repository.py -q      # 가짜 클라이언트, 네트워크 없이
pip install pgserver "psycopg[binary]" supabase==2.34.1   # 시험용 PostgreSQL (Python 3.12 권장)
python -m pytest tests/test_supabase_sync.py -q
```
두 번째는 실제 PostgreSQL 에 `schema.sql` 을 그대로 실행하고, Supabase REST 요청을 SQL 로 바꿔 service_role / anon 역할로 실행하는 시험 서버에 supabase-py 로 요청합니다. 시작 재고 맞추기, 저장·재전송 중복 없음, 확인·재확인, 재고 부족 → 확인 필요, 거절, 새 라벨 자동 추가·unknown 제외, 네트워크 장애 후 재전송, Publishable 키 쓰기 거부, 점검 도구 읽기·쓰기 점검을 검사합니다.

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

새 방식(`yoloe_openvocab`) 기준값 — `config.py` 의 '4-3' 부분

| 값 | 기본 | 뜻 |
|---|---|---|
| `DETECTOR_BACKEND` | `"yoloe_openvocab"` | `"kaggle"` 이면 예전 방식 |
| `OPENVOCAB_MODEL` | `models/yoloe-v8m-seg.pt` | 원본 YOLOE 체크포인트 |
| `OPENVOCAB_PROMPTS` / `PACKAGE_PROMPTS` | 31개 품목 / 9개 | 라벨 → 영어 검색어, 포장 식품 검색어 |
| `OPENVOCAB_CONF` / `OPENVOCAB_CONF_PER_LABEL` / `PACKAGE_CONF` | 0.25 / `{}` / 0.25 | 기준값 (품목별로 따로 가능) |
| `CLIP_VERIFY` / `CLIP_VERIFY_MODEL` | True / `"ViT-B/16"` | CLIP 재확인 (`"ViT-B/32"` 면 약 2배 빠름) |
| `CLIP_VERIFY_RUNTIME` | `"openvino"` | 내장 그래픽 → CPU 순으로 OpenVINO, 안 되면 `"torch"` |
| `TEXT_FIRST` | True | 글씨 우선 + Unknown 입력 방식 (4-0a) |
| `LABEL_MIN_CONF` / `OBJECT_MIN_CONF` | 0.75 / 0.25 | 라벨을 붙이는 최소 신뢰도 / 물체로 잡는 최소 점수 (그 사이는 Unknown) |
| `FOOD_WORDS` / `BRAND_WORDS` | 식품 약 140 / 브랜드 약 35 | OCR 오인식 보정·이름 짓기 사전. 새 식품은 여기에 추가 |
| `OCR_FUZZY_MIN` / `OCR_FALLBACK_MIN_CONF` | 0.75 / 0.85 | 사전 단어 유사도 / 사전에 없는 글씨의 최소 OCR 신뢰도 |
| `CLIP_TEXT_REQUIRED_LABELS` | 김치·주스·생수·캔·햄 | 생김새만으로 라벨하지 않는 용기 품목 |
| `CLIP_BACKGROUND_PROMPTS` | 가격표·선반·벽·사람 … | 식재료가 아닌 것으로 버릴 대상 |
| `OCR_FULL_MAX_SIDE` | 1600 | 사진 전체 OCR 때 긴 변 |
| `DETECT_PROFILES` | live / full / default | 실시간·정밀·촬영 비교용 설정 묶음 (phone_live) |
| `OCR_NAME_MERGE_RATIO` | 0.75 | 비슷하게 읽힌 포장 이름을 한 품목으로 합침 |
| `CLIP_VERIFY_CAND_CONF` / `CLIP_VERIFY_MIN_PROB` | 0.1 / 0.5 | 후보 상자 점수 / CLIP 확정 확률 |
| `PACKAGE_LABEL_MODE` | `"ocr_name"` | `"generic"` 이면 `packaged_food` 하나 |
| `OCR_MIN_CONF`, `OCR_NAME_PARTS`, `OCR_NAME_MAX_LEN`, `OCR_EXCLUDE_WORDS` | 0.4, 2, 20, 원재료·보관 … | OCR 이름 규칙 |
| `PACKAGE_KEYWORDS` | milk: milk·우유 … | 포장 글자에 이 단어가 있으면 기존 품목 라벨 |
| `OCR_MIN_BOX_PX`, `OCR_MAX_PER_FRAME`, `OCR_CACHE_SECONDS` | 48, 6, 30 | 속도 |

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

- **글씨 우선 방식은 매장 사진 3장으로만 맞췄습니다.** 실제 냉장고(조명·각도·성에·가림)에서는 다시 확인해야 합니다. 같은 사진도 해상도에 따라 OCR 결과가 조금 달라집니다 (1280 폭과 원본 4032 폭에서 컵과일 1개의 이름이 다름).
- 0.75 기준 때문에 생김새로만 알아볼 수 있는 물건(과일·채소)도 확신이 부족하면 Unknown 이 됩니다. 사용자가 한 번 이름을 붙이면 다음부터는 기억합니다.
- EasyOCR 의 한글 오인식이 잦아 사전(FOOD_WORDS)에 없는 식품은 글씨로 이름이 잘 나오지 않습니다 → Unknown + 입력 힌트로 처리됩니다.
- '식재료 파악하기'(정밀) 한 장에 약 9~15초 (사진 전체 OCR 6~8초).

- **새 방식의 정확도는 Kaggle 상품 사진 기준입니다.** 실제 부스 냉장고 사진·계란·시판 포장 식품 사진으로는 아직 재 보지 않았습니다. 부스 사진을 모아 `tools/compare_detectors.py` 로 확인하고 `OPENVOCAB_PROMPTS`·기준값을 조정하세요.
- **포장 식품 OCR 은 합성 사진(냉장고 영상 위에 그린 '비비고 왕교자')에서만 이름까지 확인했습니다.** 실제 포장 사진의 OCR 인식률은 확인하지 못했습니다. 그린 포장 4개 중 3개는 YOLOE 가 상자로 잡지 못했습니다 (평평한 그림이라 물건으로 보지 않은 것으로 추정).
- OCR 이름은 사진마다 조금씩 다르게 읽힐 수 있습니다 (실제 시험: 같은 카스테라가 '카스테라 한입' / '한입 카스테라' / '가스데라' / '큰일 스데라'). 낱말 순서 무시·비슷한 이름 합치기(`OCR_NAME_MERGE_RATIO`)로 앞의 두세 개는 한 품목이 되지만 많이 틀리게 읽은 이름은 따로 셀 수 있습니다. 고정 카메라는 같은 자리 보정(4-2)과 30초 재사용으로 줄였지만, 문제가 되면 `PACKAGE_LABEL_MODE="generic"` 으로 바꾸세요. OCR 이름은 `food_item` 에 자동 등록되므로 잘못 읽은 이름이 DB 에 남을 수 있습니다.
- 포장이 많은 화면에서 다른 흰 포장을 `두부`로 세는 경우가 있어(실제 시험 104회) `tofu package` 검색어를 뺐습니다. 두부 포장은 `tofu` 검색어나 포장의 '두부' 글자(OCR)로 찾습니다.
- 새 방식은 사진 1장에 약 1~2초로 예전보다 느립니다. 속도가 중요하면 `CLIP_VERIFY_MODEL="ViT-B/32"` 로 바꾸세요.

- 가려진 물건은 잡지 못합니다 (시연 영상의 4번째 오렌지). 진열 방식·카메라 각도로 해결해야 합니다.
- 범용 검색어(`object`) 방식은 시연 영상에서 Unknown 을 만들지 못했습니다. 실제로 동작한 것은 '쪼개진 물체 묶기 + 사용자 이름표'입니다.
- 사용자 이름표의 생김새 비교(CLIP)는 냉장고 배경이 비슷하면 다른 물체끼리도 유사도가 높게 나옵니다. 그래서 같은 자리일 때 주로 동작하고, 다른 칸으로 옮기면 다시 물어볼 수 있습니다. → 4-6 재인식(gallery)으로 보완. 단 **실제 용기를 돌려 찍은 사진으로는 아직 정확도를 재지 않았습니다.**
- 칸(LOC) 경계는 화면을 같은 비율로 나눈 근사값입니다. 실제 부스 카메라 각도에서 `FRIDGE_FLOOR_SPLITS` 를 맞춰야 합니다.
- Supabase 연동은 실제 PostgreSQL + REST 흉내 서버로 시험했습니다. **실제 Supabase 프로젝트 URL·키로 한 번 확인해야 합니다.**
- `food_item` 의 보관 일수(`shelf_days`)는 임시값입니다 (DB 설계 설명 10장).
- OpenAI 엔진은 요청 형식까지만 검증했습니다 (크레딧 없음).
- iPad 확인 화면·옆 모니터 연동(Socket.IO)은 아직 없습니다. 지금은 `y`/`n` 키가 iPad 확인 버튼 역할을 합니다.
