"""설정값 모음. 부스 환경에 맞게 이 파일의 숫자만 바꾸면 됩니다."""
from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# 1. 시연 식재료 목록 (Vision 모델이 이 목록 안에서만 개수를 셉니다)
#    item_id 는 영문 소문자, description 은 모델이 구분할 수 있도록 생김새를 적습니다.
# ---------------------------------------------------------------------------
ITEMS = {
    "milk":     {"name": "우유",     "description": "white milk carton or bottle (우유팩)"},
    "broccoli": {"name": "브로콜리", "description": "green broccoli head"},
    "tofu":     {"name": "두부",     "description": "tofu package, white block in a plastic tray"},
    "mushroom": {"name": "버섯",     "description": "mushroom pack or loose mushrooms"},
    "chicken":  {"name": "닭고기",   "description": "raw chicken in a tray package"},
    "egg":      {"name": "달걀",     "description": "egg carton (count cartons, not single eggs)"},
    # --- 실제 영상 테스트용 (video_preview_*.mp4) ---
    "butter":     {"name": "버터",   "description": "block of yellow butter, usually on a plate or in a wrapper"},
    "watermelon": {"name": "수박",   "description": "watermelon (whole or cut piece with red flesh and green rind; count pieces)"},
    "orange":     {"name": "오렌지", "description": "orange or mandarin, round orange citrus fruit (count single fruits)"},
    # --- 자주 쓰는 냉장고 품목 (필요 없으면 줄을 지우세요) ---
    "apple":      {"name": "사과",   "description": "apple, round red or green fruit (count single fruits)"},
    "lemon":      {"name": "레몬",   "description": "lemon, oval yellow citrus fruit (count single fruits)"},
    "tomato":     {"name": "토마토", "description": "red tomato (count single tomatoes; a pack of cherry tomatoes counts as 1)"},
    "strawberry": {"name": "딸기",   "description": "strawberry pack or container (count packs)"},
    "grape":      {"name": "포도",   "description": "bunch of grapes (count bunches)"},
    "carrot":     {"name": "당근",   "description": "orange carrot (count single carrots)"},
    "cucumber":   {"name": "오이",   "description": "long green cucumber (count single cucumbers)"},
    "cabbage":    {"name": "양배추", "description": "cabbage or lettuce head, round leafy vegetable"},
    "green_onion":{"name": "대파",   "description": "green onion / leek bundle, long white and green stalks (count bundles)"},
    "cheese":     {"name": "치즈",   "description": "cheese package or block of cheese"},
    "yogurt":     {"name": "요거트", "description": "yogurt cup or bottle"},
    "ham":        {"name": "햄·소시지", "description": "ham, sausage, or processed meat package"},
    "kimchi":     {"name": "김치",   "description": "kimchi container (red cabbage kimchi in a plastic or glass container)"},
    "juice":      {"name": "주스",   "description": "juice bottle or carton (not milk)"},
    "water":      {"name": "생수",   "description": "clear plastic water bottle"},
    "can":        {"name": "캔음료", "description": "aluminum drink can (soda, beer, coffee)"},
}

# 인식 백엔드 (자세한 설정은 아래 '4-3')
#   "yoloe_openvocab": 원본 YOLOE + 영어 검색어 목록 + 포장 식품 OCR (기본)
#   "kaggle"         : 예전 방식 그대로 (YOLO_PROMPTS 검색어, 위 25개 품목만). 되돌릴 때 이 값만 바꾸면 됨
#   .env 또는 환경변수 DETECTOR_BACKEND 로도 바꿀 수 있습니다.
DETECTOR_BACKEND = os.getenv("DETECTOR_BACKEND", "yoloe_openvocab").strip()

# 새 방식에서만 추가되는 품목: Kaggle 데이터셋 20종 중 위 목록에 없던 것 + 포장 식품
OPENVOCAB_EXTRA_ITEMS = {
    "banana": {"name": "바나나", "description": "banana (count single bananas or bunches)"},
    "bread":  {"name": "빵",     "description": "bread loaf or bag of bread"},
    "fish":   {"name": "생선",   "description": "fish or fish fillet"},
    "mango":  {"name": "망고",   "description": "mango fruit"},
    "onion":  {"name": "양파",   "description": "onion"},
    "pepper": {"name": "피망·고추", "description": "bell pepper or chili pepper"},
    # 포장 식품인데 글자를 못 읽은 것 (PACKAGE_LABEL_MODE="ocr_name")
    "unknown_package": {"name": "포장 식품(이름 모름)", "description": "packaged food whose label could not be read"},
    # 포장 식품 전체를 한 품목으로 셀 때 (PACKAGE_LABEL_MODE="generic")
    "packaged_food": {"name": "포장 식품", "description": "packaged food (OCR name only in logs)"},
}
if DETECTOR_BACKEND == "yoloe_openvocab":
    ITEMS.update(OPENVOCAB_EXTRA_ITEMS)

# ---------------------------------------------------------------------------
# 2. 문 닫힘(장면 안정) 감지 기준값
#    값은 부스 조명에서 실제로 재 보고 조정해야 합니다. README의 '튜닝' 항목 참고.
# ---------------------------------------------------------------------------
ANALYSIS_WIDTH = 320          # 감지용으로 프레임을 이 폭까지 줄여서 계산 (속도용)
PIXEL_DIFF_THRESHOLD = 25     # 0~255 밝기 차이가 이 값보다 크면 '변한 픽셀'로 봄

MOTION_START_RATIO = 0.02     # 직전 프레임 대비 변한 픽셀 비율이 이보다 크면 '활동 시작'
MOTION_STILL_RATIO = 0.004    # 이보다 작으면 '움직임 없음'
BRIGHTNESS_DELTA = 12.0       # 기준 밝기와 이만큼 차이 나면 '문이 열린 상태'로 봄 (0~255)
SETTLE_SECONDS = 1.5          # 움직임 없음 + 밝기 정상이 이 시간 동안 이어지면 촬영
COOLDOWN_SECONDS = 3.0        # 촬영 후 이 시간 동안은 다시 촬영하지 않음
NO_CHANGE_RATIO = 0.01        # 기준 장면 대비 바뀐 픽셀이 이보다 적으면 '변화 없음' 처리 (API 호출 생략)
BASELINE_ADAPT_RATE = 0.02    # 대기 중 기준 밝기를 천천히 따라가는 비율 (전시장 조명 변화 대응)

# ---------------------------------------------------------------------------
# 3. 카메라 입력
# ---------------------------------------------------------------------------
CAMERA_WIDTH = 1920
CAMERA_HEIGHT = 1080

# 카메라가 연결되어 있지 않을 때 대신 재생할 실사 영상 (없으면 더미 영상 사용)
FALLBACK_VIDEO = Path(r"C:\Users\aicam\Videos\video_preview_h265.mp4")
# --source 없이 python main.py 로 실행할 때의 입력. 지금은 시험용으로 h265 영상.
#   부스에서 카메라로 돌릴 때는 "0" 으로 바꾸세요 (카메라 0번, 없으면 FALLBACK_VIDEO)
DEFAULT_SOURCE = str(FALLBACK_VIDEO)
# 영상 앞·뒤의 검은 로고 화면은 밝기로 자동으로 찾아 분석에서 뺍니다 (화면에는 끝까지 재생)
FALLBACK_DARK_LEVEL = 40            # (모든 영상 파일에 적용) 평균 밝기(0~255)가 이보다 어두운 프레임은 로고/검은 화면으로 봄
FALLBACK_EDGE_MARGIN_SECONDS = 0.5  # 밝아지기 시작/어두워지기 직전의 페이드 구간도 이만큼 더 뺌
CAPTURE_AT_VIDEO_END = True   # 영상 파일이 끝날 때까지 촬영이 한 번도 없으면 마지막 화면으로 촬영

# ---------------------------------------------------------------------------
# 4. 인식 엔진
#    yolo   : PC에서 직접 인식 (YOLOE, 무료·오프라인, 품목 이름을 글자로 지정)
#             세부 방식은 DETECTOR_BACKEND (1번 아래): 새 방식(4-3) / 예전 방식(아래 YOLO_* 값)
#    openai : OpenAI Vision API (크레딧 필요)
#    mock   : 가짜 결과 (DEMO_SCENARIO 순서대로)
#    실행할 때 --engine 으로 바꿀 수 있습니다.
# ---------------------------------------------------------------------------
VISION_ENGINE = os.getenv("VISION_ENGINE", "yolo")

# YOLO 설정 — 예전 방식(DETECTOR_BACKEND="kaggle")의 모델·검색어. Unknown·이름표·손 설정은 두 방식 공통
#   YOLOE-v8m: 품목 이름(글자)으로 찾는 YOLO, 중형 모델
#   다른 크기: yoloe-v8s-seg.pt(빠름) / yoloe-v8l-seg.pt(정확), YOLO-World 도 가능: yolov8m-worldv2.pt
YOLO_MODEL = BASE_DIR / "models" / "yoloe-v8m-seg.pt"  # 없으면 자동 다운로드
YOLO_CONF = 0.2               # 이 점수 이상인 것만 개수로 셈 (0~1, 낮추면 더 많이 잡지만 오인식 증가)
YOLO_UNCERTAIN_BELOW = 0.35   # 이 점수보다 낮게 잡힌 품목은 '불확실'로 표시
YOLO_REGION_CHANGE_RATIO = 0.15  # 한쪽 사진에서만 잡힌 물건 자리의 픽셀이 이 비율 미만으로 바뀌었으면 '그대로 있음'으로 봄
YOLO_CHANGE_MIN_CONF = 0.35      # 새로 생기거나 사라진 물건은 이 점수 이상일 때만 추가/제거로 인정 (낮으면 잡음으로 무시)
# 품목별 YOLO 검색어 (영어, 짧게). 없으면 item_id 를 그대로 사용 (예: green_onion → "green onion")
YOLO_PROMPTS = {
    "milk": "milk carton", "watermelon": "watermelon wedge",
    "butter": "yellow butter block", "tofu": "white tofu in plastic package", "chicken": "raw chicken package",
    "egg": "egg carton", "mushroom": "mushroom", "ham": "sausage", "kimchi": "kimchi container",
    "juice": "juice bottle", "water": "water bottle", "can": "drink can", "cabbage": "cabbage",
    "strawberry": "strawberry", "grape": "grapes",
}
# 모르는 물체(Unknown) 구분용 범용 검색어 (Generic Prompt 트릭)
#   YOLOE 는 글자 뜻의 유사도로 품목을 고르므로, 위 품목 어디에도 안 맞는 물체는 'object' 같은 포괄 단어로 밀려남
#   → 이렇게 잡힌 상자는 화면·결과에 'Unknown' 으로 표시 (재고 개수에는 포함 안 함)
#   빈 리스트로 두면 기능 꺼짐. 예: ["object", "item", "obstacle"]
YOLO_GENERIC_PROMPTS = ["object", "item"]
# 배경(벽·선반)까지 object 로 잡히는 것을 막기 위해 Unknown 은 평소(YOLO_CONF)보다 높은 점수에서만 인정
YOLO_UNKNOWN_CONF = 0.5
# 한 물체가 작은 상자 여러 개로 쪼개져 잡히는 경우(예: 포도 한 송이 → 사과 여러 개) 하나로 묶어 Unknown 처리
#   점수가 이보다 낮은 상자들이 서로 겹치거나 붙어 있으면(2개 이상) 한 묶음으로 봄
YOLO_GROUP_MAX_CONF = 0.45
YOLO_GROUP_GAP = 0.15            # 상자 크기(짧은 변)의 이 비율만큼 떨어져 있어도 '붙어 있음'으로 봄
YOLO_FRAGMENT_CONF = 0.1         # 묶음을 찾을 때만 이 점수(< YOLO_CONF)까지 조각으로 씀 (혼자서는 개수에 안 넣음) → 묶음이 프레임마다 흔들리지 않게
YOLO_GROUP_MAX_AREA = 0.01      # 화면 넓이의 이 비율보다 작은 '조각' 상자만 묶음 (붙어 있는 큰 오렌지 2개 등은 묶지 않음)

# Unknown 물체에 사용자가 붙인 이름 기억 (생김새 = CLIP 특징 + 위치)
#   창 모드에서 Unknown 이 나오면 콘솔에 이름을 물어봄 → 입력한 이름으로 다시 계산하고, 다음 촬영부터 자동 적용
USER_LABELS_FILE = BASE_DIR / "user_labels.json"
#   실측(냉장고 영상): 같은 포도 0.89~1.00, 다른 물체도 배경이 비슷해 0.76~0.88 → 다른 자리면 기준을 높게
USER_LABEL_SIM = 0.95            # 다른 자리로 옮겨진 물체: 생김새 유사도(코사인)가 이 이상이어야 같은 물체
USER_LABEL_SIM_SAME_PLACE = 0.85 # 같은 자리(IoU≥0.5): 이 이상이면 같은 물체
# 사람 손·팔이 after 사진에 보이면 '재촬영 권장'으로 표시하기 위한 보조 검색어 (개수에는 포함 안 됨)
YOLO_HAND_PROMPTS = ["hand", "arm"]

# ---------------------------------------------------------------------------
# 4-5. 등록한 물체 다시 알아보기 (재인식, reid/ 폴더)
#   gallery: (1) 위치·변화 기반 유지 — 이전 사진과 비교해 안 바뀐 자리의 물체는 이전 이름 그대로
#            (2) 증강 임베딩 갤러리 — 등록 사진 1장을 회전·원근·밝기 변형해 여러 장의 특징값으로 저장하고 비교
#   legacy : 예전 방식 그대로 (user_labels.json 의 CLIP 특징 1개 + 위치, 위 USER_LABEL_* 기준)
#   gallery 모드에서도 user_labels.json 은 계속 저장되고, 갤러리에 안 맞으면 예전 방식으로 한 번 더 찾음.
#   .env 또는 환경변수 REID_MODE 로도 바꿀 수 있습니다.
# ---------------------------------------------------------------------------
REID_MODE = os.getenv("REID_MODE", "gallery")
# 생김새 특징 모델: "dinov2" (facebook/dinov2-small, 물체 단위 비교에 강함) / "clip" (OpenAI CLIP ViT-B/32)
REID_EMBEDDER = os.getenv("REID_EMBEDDER", "dinov2")
REID_DINOV2_DIR = BASE_DIR / "weights" / "dinov2-small"   # 처음 한 번 받아 둔 모델 (인터넷 없이 실행)
# 계산 장치: "openvino" (내장 그래픽 → 안 되면 CPU, 결과가 PyTorch 와 같은지 시작할 때 확인) / "torch"
#   실측(i7-9700, 상자 9개): torch CPU 는 실시간 화면이 약 0.8초 느려짐
REID_RUNTIME = os.getenv("REID_RUNTIME", "openvino")
REID_CLIP_DIR = BASE_DIR / "weights" / "clip"
REID_STATE_FILE = BASE_DIR / "state" / "tracks.json"       # 지금 냉장고 안에 있다고 보는 물체 목록 (트랙)
REID_GALLERY_DIR = BASE_DIR / "gallery"                     # 등록 물체별 <id>.npz(특징값)·<id>.json·<id>/ 사진
REID_LOG = BASE_DIR / "logs" / "reid.jsonl"                 # 판정 근거 (후보·점수). 결과 형식에는 넣지 않음

# 잘라내기 전처리
REID_CROP_PAD = 0.10           # 상자에 더하는 여백 (변 길이 비율)
REID_INPUT_SIZE = 224          # 모델 입력 크기 (정사각형으로 패딩 후)
REID_BG_FILL = 128             # 배경을 칠하는 회색 값
# 배경(선반) 지우기: "grabcut" (OpenCV GrabCut 으로 상자 안의 물체만 대략 남김, 모델 불필요) / None (끔)
#   탐지 결과에는 분할 마스크가 없어서 GrabCut 으로 대신함. 켤지 여부는 tools/eval_reid.py 의 (c)/(d) 비교로 정함
REID_BG_MASK = None

# 1단계: 위치·변화 기반 유지 (고정 카메라의 before/after 비교에서만)
REID_CHANGE_DIFF = 25          # 회색조 밝기 차이가 이 값보다 크면 '바뀐 픽셀' (노출 변화는 사진 평균 밝기를 빼서 보정)
REID_CHANGE_KERNEL = 5         # 모폴로지(열기)로 지우는 잡음 크기 (픽셀)
UNCHANGED_RATIO = 0.05         # 상자 안 바뀐 픽셀 비율이 이 값 미만이면 '그대로'
KEEP_IOU = 0.5                 # ① 그대로: 기존 트랙과 이 IoU 이상
NEAR_IOU = 0.2                 # ② 살짝 이동: 같은 칸이고 IoU 이 이상
NEAR_DIST = 0.5                #    또는 중심 거리가 박스 대각선의 이 배수 이내
NEAR_SIM = 0.80                #    + 그 트랙과의 생김새 유사도 이 이상 (임시값 — 실제 용기 사진으로 tools/eval_reid.py 재평가 필요)
#   지시서 초깃값 0.65 → 0.80: 실제 냉장고 샘플(samples/real_*.jpg)에서 같은 물체 0.93~0.98, 옆의 다른 물체 0.78~0.84.
#   0.65 면 같은 자리에 다른 용기를 넣었을 때 이전 이름을 물려받아 입출고가 틀어짐

# 2단계: 갤러리 비교 (임시값 — tools/eval_reid.py 결과로 결정)
MATCH_SIM = 0.85               # 1등 점수가 이 이상이고 (지시서 임시값 0.75 → 0.85: 위 실측에서 0.75 면 오렌지·배경을 등록 용기로 붙임.
                               #   매장 사진 평가(tools/eval_reid.py)의 추천값 0.82. 실제 용기 사진으로 재평가 필요)
MATCH_MARGIN = 0.05            # (1등 − 2등) 이 이상이면 그 물체로 판정. 차이가 작으면 '확인 필요'(Unknown + 후보 이름)
REID_TOPK = 3                  # 물체별 점수 = 갤러리 벡터들과의 유사도 상위 몇 개의 평균
DUP_SIM = 0.85                 # 새로 등록할 때 기존 물체와 이 이상 비슷하면 '구분 어려움' 경고
GALLERY_MAX = 40               # 물체당 최대 벡터 수 (넘으면 오래된 증강본부터 지움. 원본·실제 사진은 남김)
REID_REAL_DUP = 0.98           # 실제 사진을 갤러리에 더할 때, 이미 있는 벡터와 이 이상 같으면 더하지 않음
# '이 물체가 아님'(네거티브): 이름을 붙일 때 같은 사진의 다른 물체들(겹침 IoU < 0.3)을 저장해 두고,
#   어떤 상자가 등록 물체보다 네거티브와 더 비슷하면 그 물체로 보지 않음 (등록 물체가 1개라 1·2등 비교가 안 될 때도 동작)
REID_NEGATIVES = True
REID_NEG_DROP = 0.90           # 등록 물체와 이 이상 비슷한 네거티브는 저장하지 않음 / 그 물체를 나중에 등록하면 지움
REID_NEG_MAX = 300             # 최대 개수 (넘으면 오래된 것부터)

# 증강: 하나씩 따로 적용한 것 + 무작위 2~3개 조합을 섞어 총 REID_AUG_COUNT 장
REID_AUG_COUNT = 24
REID_AUG_SEED = 0
REID_AUG = {
    "rotate": [-30, -20, -10, 10, 20, 30],      # 평면 회전 (도)
    "perspective": [0.10, 0.20],                # 가로 한쪽을 좁히는 비율 (좌·우 각각) — 용기를 좌우로 돌린 효과 흉내
    "flip": True,                               # 좌우 반전 1회
    "scale": [0.85, 1.15],                      # 크기 (거리 차이)
    "brightness": [-0.25, 0.25],                # 밝기
    "contrast": [-0.20, 0.20],                  # 대비
    "color_temp": [-0.08, 0.08],                # 색온도 (−: 차갑게, +: 따뜻하게)
    "blur": [1.2],                              # 가우시안 흐림 시그마
}
REID_PREVIEW_N = 8             # 눈으로 확인할 증강 사진 수 (gallery/<id>/preview/)

# ---------------------------------------------------------------------------
# 4-3. 새 인식 방식 (DETECTOR_BACKEND = "yoloe_openvocab")
#      원본 YOLOE 체크포인트 + 영어 검색어(텍스트 프롬프트). 학습 없이 검색어만 바꿔서 찾을 대상을 정함.
#      ※ 미세조정한 YOLOE 는 이 기능을 잃으므로 반드시 원본(yoloe-*-seg.pt)을 씁니다.
#      위 4번의 Unknown(범용 검색어·조각 묶음)·사용자 이름표·손 감지 설정은 그대로 함께 씁니다.
# ---------------------------------------------------------------------------
#   다른 체크포인트: "yoloe-26m-seg.pt", "yoloe-26l-seg.pt", "yoloe-v8l-seg.pt" (없으면 자동 다운로드)
OPENVOCAB_MODEL = BASE_DIR / "models" / "yoloe-v8m-seg.pt"
# 검색어 임베딩 저장 파일 (인터넷 없이 실행용). tools/build_prompt_cache.py 가 만듦. 체크포인트마다 따로.
#   있으면 이 파일을 쓰고, 없으면 글자 모델로 계산 (YOLOE-v8: mobileclip_blt.ts 약 600MB)
OPENVOCAB_PROMPT_CACHE_DIR = BASE_DIR / "models"

# 출력 라벨(item_id) → 영어 검색어 목록 (= 검색어 → 라벨 매핑). 한 라벨에 검색어 여러 개 가능.
#   라벨은 config.ITEMS 의 id 와 같아야 하며, 예전 방식과 같은 라벨 이름으로 나옵니다.
#   여기에 없는 품목은 item_id 를 그대로 검색어로 씀 (green_onion → "green onion")
OPENVOCAB_PROMPTS = {
    "milk":        ["milk carton", "milk bottle"],
    "broccoli":    ["broccoli"],
    "tofu":        ["tofu"],               # 'tofu package' 는 다른 흰 포장까지 두부로 잡아서 뺌 (포장의 '두부' 글자는 OCR 이 처리)
    "mushroom":    ["mushroom"],
    "chicken":     ["chicken", "raw chicken package"],
    "egg":         ["egg", "egg carton"],
    "butter":      ["butter", "yellow butter block"],
    "watermelon":  ["watermelon", "watermelon wedge"],
    "orange":      ["orange"],
    "apple":       ["apple"],
    "lemon":       ["lemon"],
    "tomato":      ["tomato"],
    "strawberry":  ["strawberry"],
    "grape":       ["grapes"],
    "carrot":      ["carrot"],
    "cucumber":    ["cucumber"],
    "cabbage":     ["cabbage", "lettuce"],
    "green_onion": ["green onion"],
    "cheese":      ["cheese"],
    "yogurt":      ["yogurt"],
    "ham":         ["sausage", "ham"],
    "kimchi":      ["kimchi container"],
    "juice":       ["juice bottle"],
    "water":       ["water bottle"],
    "can":         ["drink can"],
    "banana":      ["banana"],
    "bread":       ["bread"],
    "fish":        ["fish"],
    "mango":       ["mango"],
    "onion":       ["onion"],
    "pepper":      ["bell pepper", "chili pepper"],
}
# CLIP 재확인: YOLOE 가 찾은 상자를 잘라 CLIP(사진 분류 모델)으로 한 번 더 보고, CLIP 이 확신하면 그 라벨로 바꿈
#   YOLOE 글자 검색은 '물건이 어디 있는지'는 잘 찾지만 비슷한 물건(갈색 계란↔사과, 치즈↔버터)을 자주 헷갈림
#   실측 (Kaggle 사진 20종 500장, 1등 상자 정답률): YOLOE 만 26.8% → CLIP 재확인 65.0%  (docs 참고)
CLIP_VERIFY = True
CLIP_VERIFY_MODEL = "ViT-B/16"       # "ViT-B/32" 로 바꾸면 약 2배 빠름 (정답률 64.2%)
# CLIP 을 어디서 계산할지 (같은 모델, 실행 엔진만 다름 — 결과 코사인 유사도 0.9999 이상 확인)
#   "openvino": Intel 내장 그래픽(GPU)이 있으면 거기서, 없으면 OpenVINO CPU (실측: 상자당 185 → 91 ms, CPU 는 YOLOE 에 양보)
#   "openvino_cpu" / "openvino_gpu": 장치 고정,  "torch": 예전처럼 PyTorch CPU
#   openvino 패키지가 없거나 실패하면 자동으로 "torch". 처음 실행 때 weights/clip/openvino/ 에 변환 파일을 만듦 (몇 초)
CLIP_VERIFY_RUNTIME = os.getenv("CLIP_VERIFY_RUNTIME", "openvino").strip()
CLIP_VERIFY_CAND_CONF = 0.1          # YOLOE 점수가 이 이상인 상자를 후보로 CLIP 에 보여 줌
CLIP_VERIFY_MIN_PROB = 0.5           # CLIP 확률이 이 이상이면 CLIP 라벨로 확정 (YOLOE 점수가 낮아도 셈)
CLIP_VERIFY_PACKAGE_PROB = 0.5       # CLIP 이 '포장 식품'일 확률이 이 이상이면 OCR 로 이름 읽기
CLIP_VERIFY_MAX_BOXES = 20           # 한 장에서 CLIP 으로 볼 최대 상자 수 (점수 높은 순)
CLIP_VERIFY_MIN_PX = 24              # 이보다 작은 상자(짧은 변 px)는 CLIP 생략 (Kaggle 500장 실측: 12→24 로 정답률 변화 없이 CLIP 10% 감소)

# 인식 설정 묶음 (vision.YoloComparer.detect_frame(frame, profile) 에서 고름). 없는 값은 위 설정 그대로
#   default: main.py 촬영 비교 (위 설정 그대로)
#   live   : phone_live 실시간 화면 — 빠르게. OCR 은 새로 하지 않고 최근 정밀 분석 결과(같은 자리·생김새)만 다시 씀
#   full   : phone_live '식재료 파악하기' 버튼 — 정밀하게. 캐시 없이, 모든 후보를 CLIP 으로, 모든 포장을 OCR
#            (사진은 1280 폭으로 받아 CLIP·OCR 이 더 선명한 부분을 봄. YOLOE 입력 크기(imgsz)는 640 유지:
#             960 으로 올리면 우유·포장을 놓치고 레몬 오인식이 늘었음)
#   reid_detector_boxes: 재인식 갤러리(4-5)와 비교할 상자에 탐지 품목 상자까지 넣을지 (False 면 Unknown 상자만)
#            실시간은 False — 실측(상자 9개, 등록 3개): 넣으면 한 장 0.49 → 1.31초
DETECT_PROFILES = {
    "default": {},
    "live": {"ocr": False, "clip_max_boxes": 10, "reid_detector_boxes": False},
    "full": {"fresh": True, "clip_max_boxes": 60, "ocr_max_per_frame": 30},
}
# 포장 식품 검색어: 이 검색어로 잡힌 상자는 OCR 로 포장 글자를 읽어 이름을 만듦
PACKAGE_PROMPTS = ["food package", "dumpling package", "frozen food package", "snack bag", "pouch",
                   "box", "plastic bottle", "can", "jar"]

# 신뢰도 기준값 (0~1). 이 점수 이상인 상자만 셈. 품목별로 따로 정할 수 있음 (tools/compare_detectors.py --conf 로 조정)
OPENVOCAB_CONF = 0.25
OPENVOCAB_CONF_PER_LABEL = {}        # 예: {"egg": 0.2, "butter": 0.3}
PACKAGE_CONF = 0.25                  # 포장 식품 검색어 상자의 기준값
# 같은 라벨 상자 안에 들어 있는 작은 상자는 버림 (계란판 안의 계란 낱개, 포도송이 안의 알 → 1개로 셈)
OPENVOCAB_DROP_CONTAINED = 0.8       # 작은 상자 넓이의 이 비율 이상이 큰 상자 안에 있으면 '들어 있음'. 0 이면 끔
# 조각 묶음(Unknown) 규칙에서, 모두 같은 라벨인 묶음은 묶지 않고 그대로 셈 (계란 여러 개가 Unknown 이 되지 않게)
OPENVOCAB_GROUP_SKIP_SAME_LABEL = True

# 포장 식품 이름 (OCR)
#   "ocr_name": OCR 로 만든 이름을 라벨로 출력 (예: '비비고 왕교자'). 실행 중 새 품목(id: pkg_<해시>)으로 자동 추가
#               → 재고·Supabase(food_item 자동 등록) 까지 그대로 동작. 못 읽으면 'unknown_package'
#   "generic" : 라벨은 'packaged_food' 하나. OCR 이름은 로그(logs/detections.jsonl)에만 남김
PACKAGE_LABEL_MODE = os.getenv("PACKAGE_LABEL_MODE", "ocr_name").strip()
OCR_ENGINE = "easyocr"               # OCR 엔진 (ocr_names.py 의 OcrEngine 인터페이스)
OCR_LANGS = ["ko", "en"]
OCR_MODEL_DIR = BASE_DIR / "weights" / "easyocr"
OCR_SCALE = 2.0                      # 상자 영역을 이 배율로 확대해서 읽음
OCR_MIN_CONF = 0.4                   # 이보다 신뢰도가 낮은 글자 조각은 버림
OCR_NAME_PARTS = 2                   # 가장 큰 글자 조각 몇 개를 이어 붙일지
OCR_NAME_MAX_LEN = 20                # 이름 최대 글자 수
OCR_MIN_CHARS = 2                    # 이보다 짧은 조각(한 글자 등)은 잡음으로 버림
OCR_NAME_MERGE_RATIO = 0.75          # 이미 나온 포장 이름과 글자 유사도가 이 이상이면 같은 품목으로 합침 ('가스테라' → '카스테라')
# 이름에서 뺄 안내 문구 (이 단어가 들어 있는 글자 조각은 버림)
OCR_EXCLUDE_WORDS = ["원재료", "보관", "유통기한", "소비기한", "조리법", "냉동", "냉장",
                     "영양정보", "영양성분", "제조", "품목보고", "내용량", "총내용량", "열량", "바코드"]
# 포장 글자에 기존 품목 단어가 있으면 OCR 이름 대신 그 품목 라벨로 출력 (예: 'Dairy PRIDE milk' → milk)
#   한 조각에 여러 단어가 있으면 마지막 단어 ('Strawberry Yogurt' → yogurt). 빈 dict 면 끔
PACKAGE_KEYWORDS = {
    "milk":    ["milk", "우유"],
    "yogurt":  ["yogurt", "yoghurt", "요거트", "요구르트", "요플레"],
    "cheese":  ["cheese", "치즈"],
    "butter":  ["butter", "버터"],
    "tofu":    ["tofu", "두부"],
    "egg":     ["egg", "eggs", "계란", "달걀"],
    "juice":   ["juice", "주스", "쥬스"],
    "water":   ["water", "생수", "샘물"],
    "kimchi":  ["kimchi", "김치"],
    "ham":     ["ham", "sausage", "햄", "소시지"],
    "chicken": ["chicken", "닭"],
    "bread":   ["bread", "식빵"],
}
OCR_MIN_BOX_PX = 48                  # 상자의 짧은 변이 이 픽셀보다 작으면 글자를 못 읽으므로 OCR 생략 → unknown_package
OCR_MAX_PER_FRAME = 6                # 한 장에서 OCR 할 포장 최대 개수 (큰 상자부터). 나머지는 이전 결과 재사용 또는 unknown_package
# ---------------------------------------------------------------------------
# 4-4. 글씨 우선 + Unknown 확인 (2026-11-26 변경, TEXT_FIRST=True 일 때)
#   1) 글씨가 보이는 객체 → 써 있는 글씨로 이름 (식품 사전으로 OCR 오인식 보정: '골멩이' → 골뱅이)
#   2) 글씨가 없으면 CLIP·YOLOE 신뢰도가 LABEL_MIN_CONF 이상일 때만 품목 라벨
#   3) 물체로 보이지만(YOLOE ≥ OBJECT_MIN_CONF) 판단할 수 없으면 Unknown → 사용자가 이름 입력
#   OCR 은 사진 전체를 한 번 읽고(OCR_FULL_MAX_SIDE) 글자 조각을 객체 상자에 나눠 줌
# ---------------------------------------------------------------------------
TEXT_FIRST = True
LABEL_MIN_CONF = 0.75                # 라벨을 붙이는 최소 신뢰도 (글씨: 사전 일치도·OCR 신뢰도, 그 외: CLIP·YOLOE)
OBJECT_MIN_CONF = 0.25               # 이 이상이면 '물체'로 보고 잡음 (라벨 못 붙이면 Unknown)
OCR_FULL_MAX_SIDE = 1600             # 사진 전체 OCR 때 긴 변 (1280 폭 핸드폰 사진은 그대로)
OCR_FUZZY_MIN = 0.75                 # 사전 단어와 이 이상 비슷하면 같은 단어로 봄 (자모 단위, 3글자 이상만)
OCR_FALLBACK_MIN_CONF = 0.85         # 사전에 없는 글씨로 이름을 지을 때 필요한 OCR 신뢰도 (실측: '이모기도'(아보카도 오인식) 0.81)
OCR_SPLIT_REPEATS = True             # 한 상자 안에 같은 식품 이름이 여러 번 보이면 그 수만큼 셈 (쌓인 캔)
# CLIP 이 이것으로 보면 식재료가 아닌 것으로 버림 (글씨 사전 단어가 있으면 제외)
CLIP_BACKGROUND_PROMPTS = ["price tag", "shelf label with barcode and price", "empty store shelf",
                           "refrigerator shelf", "wall", "sign board", "person", "human hand"]
CLIP_BACKGROUND_PROB = 0.5           # 이 이상이면 배경으로 버림
CLIP_BACKGROUND_UNKNOWN_PROB = 0.2   # 라벨 못 붙인 상자는 CLIP 1등이 배경이고 이 이상이면 Unknown 대신 버림
# 생김새만으로는 구분할 수 없는 '용기·포장' 품목: CLIP 이 확신해도 라벨하지 않고 글씨나 사용자 입력이 필요
#   (실측: 빨간·노란 캔과 용기를 CLIP 이 '김치' 0.86~1.00 으로 봄)
CLIP_TEXT_REQUIRED_LABELS = ["kimchi", "juice", "water", "can", "ham"]
# 포장 없이 보관되는 신선 식품: 글자 조각이 여러 상자에 걸치면 이 품목 상자보다 포장류 상자에 글자를 줌
#   (실측: 포장이 수박 앞에 겹쳐 있으면 '비비고' 글자가 수박 상자로 감)
PRODUCE_LABELS = ["watermelon", "orange", "apple", "lemon", "tomato", "strawberry", "grape", "carrot", "cucumber",
                  "cabbage", "green_onion", "broccoli", "mushroom", "banana", "mango", "onion", "pepper", "fish"]
OCR_EDGE_PARTIAL = 0.08             # 사진 가장자리에 잘린 물체(보이는 높이·폭이 이 비율 미만)는 글씨로 이름 짓지 않음
# 식품 사전: 대표 이름 → 포장에 쓰일 수 있는 표기 (한글 3글자 이상은 오인식도 비슷하면 인정)
FOOD_WORDS = {
    # 수산·통조림
    "고추참치": ["고추참치"], "야채참치": ["야채참치"], "참치": ["참치", "tuna"], "골뱅이": ["골뱅이"],
    "번데기": ["번데기", "silkworm"], "고등어": ["고등어", "mackerel"], "꽁치": ["꽁치"], "연어": ["연어", "salmon"],
    "새우": ["새우", "shrimp"], "오징어": ["오징어"], "어묵": ["어묵"], "명란": ["명란"], "멸치": ["멸치"],
    "스팸": ["스팸", "spam"], "햄": ["햄", "ham"], "소시지": ["소시지", "sausage", "비엔나"], "베이컨": ["베이컨", "bacon"],
    "스위트콘": ["스위트콘", "sweetcorn", "sweet corn"], "옥수수": ["옥수수", "corn"], "황도": ["황도"], "꽁치통조림": ["꽁치통조림"],
    # 국·탕·간편식
    "사골곰탕": ["사골곰탕"], "곰탕": ["곰탕"], "삼계탕": ["삼계탕", "samgyetang"], "육개장": ["육개장"],
    "설렁탕": ["설렁탕"], "갈비탕": ["갈비탕"], "미역국": ["미역국"], "된장찌개": ["된장찌개"], "김치찌개": ["김치찌개"],
    "사골육수": ["사골육수"], "육수": ["육수"], "떡볶이": ["떡볶이"], "죽": ["전복죽", "호박죽", "야채죽", "단팥죽"],
    "만두": ["만두", "왕교자", "교자", "dumpling"], "햇반": ["햇반"], "즉석밥": ["즉석밥"], "라면": ["라면"],
    "카레": ["카레", "curry"], "짜장": ["짜장"], "볶음밥": ["볶음밥"], "피자": ["피자", "pizza"], "핫도그": ["핫도그"],
    # 유제품·음료
    "우유": ["우유", "milk"], "바나나우유": ["바나나우유"], "요거트": ["요거트", "요구르트", "yogurt", "yoghurt"],
    "치즈": ["치즈", "cheese"], "버터": ["버터", "butter"], "두유": ["두유"], "주스": ["주스", "쥬스", "juice"],
    "생수": ["생수", "샘물"], "콜라": ["콜라", "cola"], "사이다": ["사이다"], "맥주": ["맥주", "beer"], "커피": ["커피", "coffee"],
    # 반찬·소스
    "김치": ["김치", "kimchi"], "깍두기": ["깍두기"], "단무지": ["단무지"], "두부": ["두부", "tofu"], "계란": ["계란", "달걀", "egg"],
    "고추장": ["고추장"], "된장": ["된장"], "쌈장": ["쌈장"], "간장": ["간장"], "케첩": ["케첩", "케찹", "ketchup"],
    "마요네즈": ["마요네즈", "mayonnaise"], "잼": ["딸기잼", "사과잼"], "참기름": ["참기름"], "식용유": ["식용유"],
    # 과일
    "컵과일": ["컵과일"], "조각사과": ["조각사과"], "사과": ["사과", "apple"], "샤인머스캣": ["샤인머스캣", "샤인머스켓"],
    "포도": ["포도", "grape"], "딸기": ["딸기", "strawberry"], "바나나": ["바나나", "banana"], "오렌지": ["오렌지", "orange"],
    "귤": ["감귤", "귤"], "레몬": ["레몬", "lemon"], "키위": ["키위", "kiwi"], "망고": ["망고", "mango"], "수박": ["수박"],
    "참외": ["참외"], "복숭아": ["복숭아"], "배": ["신고배"], "블루베리": ["블루베리", "blueberry"], "아보카도": ["아보카도", "avocado"],
    "토마토": ["토마토", "tomato"], "방울토마토": ["방울토마토"],
    # 채소
    "양파": ["양파"], "대파": ["대파"], "쪽파": ["쪽파"], "마늘": ["마늘"], "생강": ["생강"], "당근": ["당근", "carrot"],
    "감자": ["감자"], "고구마": ["고구마"], "양배추": ["양배추"], "배추": ["배추"], "상추": ["상추"], "깻잎": ["깻잎"],
    "시금치": ["시금치"], "브로콜리": ["브로콜리", "broccoli"], "오이": ["오이"], "애호박": ["애호박"], "가지": ["가지"],
    "버섯": ["버섯", "새송이", "느타리", "팽이버섯", "표고"], "콩나물": ["콩나물"], "숙주": ["숙주"], "파프리카": ["파프리카"],
    "피망": ["피망"], "고추": ["청양고추", "풋고추"], "샐러드": ["샐러드", "salad"],
    # 육류
    "닭가슴살": ["닭가슴살"], "닭고기": ["닭고기", "닭다리", "chicken"], "돼지고기": ["돼지고기", "삼겹살", "목살", "pork"],
    "소고기": ["소고기", "쇠고기", "한우", "불고기", "beef"], "양념갈비": ["양념갈비"], "떡갈비": ["떡갈비"],
    # 기타
    "카스테라": ["카스테라", "castella"], "빵": ["식빵", "bread"], "떡": ["가래떡", "떡국떡"], "견과류": ["견과류", "아몬드", "땅콩"],
}
# 브랜드: 대표 이름 → 표기 (영문 로고 포함). 이름 앞에 붙임
BRAND_WORDS = {
    "동원": ["동원", "dongwon", "dong won"], "비비고": ["비비고", "bibigo"], "백설": ["백설", "beksul"], "CJ": ["cj제일제당"],
    "오뚜기": ["오뚜기", "ottogi"], "풀무원": ["풀무원", "pulmuone"], "청정원": ["청정원"], "농심": ["농심", "nongshim"],
    "유동": ["유동"], "사조": ["사조"], "샘표": ["샘표"], "대상": ["종가집"], "하림": ["하림"], "목우촌": ["목우촌"],
    "서울우유": ["서울우유"], "매일": ["매일유업"], "남양": ["남양유업"], "빙그레": ["빙그레"], "덴마크": ["덴마크"],
    "롯데": ["롯데", "lotte"], "해태": ["해태"], "오리온": ["오리온", "orion"], "삼립": ["삼립"], "파리바게뜨": ["파리바게뜨"],
    "양반": ["양반"], "고메": ["고메"], "햇반": ["햇반"], "스팸": ["스팸"], "델몬트": ["델몬트", "delmonte"],
    "신선그대로": ["신선그대로"], "피코크": ["피코크", "peacock"], "노브랜드": ["노브랜드", "nobrand"],
}
OCR_CACHE_SECONDS = 30              # 같은 자리·같은 생김새의 상자는 이 시간 동안 OCR·CLIP 결과를 다시 씀 (속도)
DETECTION_LOG = BASE_DIR / "logs" / "detections.jsonl"   # 검색어 원문·점수·OCR 원문 기록. None 이면 끔
DETECTION_LOG_MAX_MB = 20            # 넘으면 detections.jsonl.1 로 넘기고 새로 씀

# ---------------------------------------------------------------------------
# 4-2. OpenAI Vision API (VISION_ENGINE = "openai" 일 때)
# ---------------------------------------------------------------------------
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "").strip()  # 예: low (비우면 모델 기본값)
IMAGE_MAX_SIDE = 1280         # API로 보낼 때 긴 변을 이 크기로 줄임 (비용·속도)

# 확인 화면을 띄워야 하는 조건 (코드 규칙)
CONFIRM_IF_DELTA_AT_LEAST = 2  # 한 품목이 한 번에 2개 이상 변하면 사람 확인

# ---------------------------------------------------------------------------
# 5. 저장 위치
# ---------------------------------------------------------------------------
CAPTURE_DIR = BASE_DIR / "captures"
DUMMY_DIR = BASE_DIR / "dummy"

# ---------------------------------------------------------------------------
# 6. 더미 시나리오 (make_dummy_video.py 와 --mock 모드가 함께 사용)
#    각 단계: 문을 열고 add/remove 한 뒤 닫음
# ---------------------------------------------------------------------------
# 여기에 없는 품목은 0개로 시작합니다.
INITIAL_STOCK = {k: 0 for k in ITEMS} | {"milk": 0, "broccoli": 1, "tofu": 1, "mushroom": 1, "chicken": 1, "egg": 1}
DEMO_SCENARIO = [
    {"add": ["milk"], "remove": ["broccoli"]},   # 우유 추가 + 브로콜리 제거
    {"add": [], "remove": []},                    # 문만 열었다 닫음 (변화 없음)
    {"add": ["broccoli"], "remove": ["tofu"]},    # 브로콜리 추가 + 두부 제거
]

# ---------------------------------------------------------------------------
# 7. Supabase 고내 재고 DB 연동 (db/ 폴더 + supabase_sync.py, 구조: supabase/schema.sql)
#    인식 결과 → capture + stock_event(pending) 저장 → 재고 반영 시 confirm_stock_event() / 거절 시 reject_stock_event()
#    키는 .env 에만 (코드·로그·git 에 값을 남기지 않음). 비어 있으면 연동하지 않고 로그만 남김.
#      SUPABASE_URL             프로젝트 주소
#      SUPABASE_PUBLISHABLE_KEY sb_publishable_... (anon 역할, 읽기 전용: 현재 재고·대기 이벤트 조회, 점검 도구)
#      SUPABASE_SECRET_KEY      sb_secret_...      (service_role 역할, 쓰기·함수 실행) — 이 PC 밖으로 절대 유출 금지
#      SUPABASE_KEY             (예전 이름) SUPABASE_SECRET_KEY 가 비어 있으면 이 값을 Secret 으로 씀
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
SUPABASE_SECRET_KEY = (os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_KEY", "")).strip()
SUPABASE_KEY = SUPABASE_SECRET_KEY            # 예전 코드 호환 (쓰기 키)
SUPABASE_TIMEOUT = 5.0            # 초 (요청 1건). 네트워크가 느려도 화면이 멈추지 않도록 별도 스레드에서 보냄
# 보내기 실패한 작업 → 다음 촬영 때 또는 tools/flush_outbox.py 로 다시 보냄
SUPABASE_QUEUE_FILE = BASE_DIR / "state" / "db_outbox.jsonl"
SUPABASE_OLD_QUEUE_FILE = CAPTURE_DIR / "supabase_queue.jsonl"   # 예전 위치 (있으면 새 위치로 옮김)
# 사진 파일을 Supabase Storage 에 올릴지. 기본 끔 → before_path/after_path 에 로컬 상대 경로만 저장
#   켜려면 .env 에 UPLOAD_PHOTOS=true 와 Storage 버킷(SUPABASE_PHOTO_BUCKET)이 필요. 원본 사진이 외부로 나가므로 팀 합의 후에만
UPLOAD_PHOTOS = os.getenv("UPLOAD_PHOTOS", "false").strip().lower() in ("1", "true", "yes")
SUPABASE_PHOTO_BUCKET = os.getenv("SUPABASE_PHOTO_BUCKET", "captures").strip()
# 어떤 경우에도 DB 에 넣지 않는 라벨 (사용자가 이름을 붙이기 전의 모르는 물체)
SUPABASE_EXCLUDE_LABELS = {"unknown", "unknown_package", "packaged_food"}
# 재고 확인을 어디서 하나
#   "local": 이 프로그램이 확인 (자동 반영 또는 창에서 y/n) → confirm/reject 함수 호출
#   "ipad" : 이벤트를 pending 으로만 저장하고 iPad 화면에서 확인을 기다림
SUPABASE_CONFIRM_MODE = os.getenv("SUPABASE_CONFIRM_MODE", "local")
# 첫 촬영 때 DB 재고를 카메라가 본 before 개수에 맞출지 (source='manual' 이벤트로 기록)
#   "auto": 영상 파일로 시험할 때만 맞춤 (부스 카메라는 reset_demo() 로 실제 냉장고와 맞춰 두고 시작)
SUPABASE_BASELINE_ON_START = "auto"          # "auto" | True | False
# food_item 에 없는 품목이 나오면 자동 등록할지 (외래키 때문) — 2026-11-27 결정: (b) 자동 추가
#   대상: 새 인식 품목(바나나 등), 포장 글씨 이름(pkg_*), 사용자가 Unknown 에 붙인 이름(user_*)
#   스키마 변경이 아니라 food_item 행 추가. False 면 DB 에 기록하지 않고 로그에만 남김 ((a) 방식)
SUPABASE_REGISTER_MISSING_ITEMS = True
SUPABASE_DEFAULT_SHELF_DAYS = 7              # 자동 등록 품목의 보관 일수 (임시값)

# 위치(loc_x, loc_y) 계산: 사진 속 물체 상자 중심이 어느 칸인지 (DB 규칙: 3×2 격자)
#   X: 화면을 가로로 나눈 칸 이름 (왼쪽부터) L=왼쪽, C=가운데, R=오른쪽
#   Y: 냉장고 층. FRIDGE_FLOOR_SPLITS = 층 경계(화면 위에서부터 0~1 비율). 2층이면 경계 1개
#      카메라 각도에 맞게 부스에서 조정하세요 (captures/*/after_yolo.jpg 로 확인)
FRIDGE_X_LABELS = ["L", "C", "R"]
FRIDGE_FLOOR_SPLITS = [0.5]                # 화면 위 절반 = 1층(위 칸), 아래 절반 = 2층(아래 칸)
FRIDGE_FLOOR_NUMBER_FROM_TOP = True        # DB 규칙: 1 = 위 칸, 2 = 아래 칸
