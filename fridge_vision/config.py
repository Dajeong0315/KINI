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
#    yolo   : PC에서 직접 인식 (YOLO-World, 무료·오프라인, 품목 이름을 글자로 지정)
#    openai : OpenAI Vision API (크레딧 필요)
#    mock   : 가짜 결과 (DEMO_SCENARIO 순서대로)
#    실행할 때 --engine 으로 바꿀 수 있습니다.
# ---------------------------------------------------------------------------
VISION_ENGINE = os.getenv("VISION_ENGINE", "yolo")

# YOLO 설정 — YOLOE-v8m: 품목 이름(글자)으로 찾는 YOLO, 중형 모델
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
# 7. Supabase 고내 재고 테이블 연동 (supabase_sync.py)
#    한 행 = 냉장고 안 식재료 1개.  넣으면 INSERT, 꺼내면 FOOD_DATE_OUT 을 오늘 날짜로 UPDATE.
#    현재 재고 = FOOD_DATE_OUT 이 비어 있는(NULL) 행.  테이블 만들기: supabase/fridge_food.sql
#    URL·키는 .env 의 SUPABASE_URL, SUPABASE_KEY. 비어 있으면 연동하지 않고 로그만 남김.
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()   # service_role 키는 이 PC(.env)에만 두고 절대 공유 금지
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "fridge_food")
# 열 이름. PostgreSQL 은 따옴표 없이 만든 이름을 소문자로 저장 → FOOD_NAME 은 food_name 으로 접근
SUPABASE_COLUMNS = {
    "pk": "food_id",               # PK (자동 증가)
    "name": "food_name",           # FOOD_NAME  VARCHAR  — 품목 한글 이름 (config.ITEMS 의 name)
    "date_in": "food_date_in",     # FOOD_DATE_IN  DATE — 넣은 날
    "date_out": "food_date_out",   # FOOD_DATE_OUT DATE — 꺼낸 날 (냉장고 안에 있으면 NULL)
    "loc": "loc",                  # LOC  VARCHAR — "X,Y"  예: "L,1"
}
SUPABASE_TIMEOUT = 5.0            # 초. 네트워크가 느려도 화면이 멈추지 않도록 별도 스레드에서 보냄
SUPABASE_QUEUE_FILE = CAPTURE_DIR / "supabase_queue.jsonl"   # 보내기 실패한 작업 → 다음 동기화 때 다시 보냄
# 프로그램 첫 촬영 때 테이블에 '안에 있는' 행이 하나도 없으면, 첫 before 사진의 품목으로 테이블을 채움
SUPABASE_SEED_FROM_FIRST_BEFORE = True

# 위치(LOC) 계산: 사진 속 물체 상자 중심이 어느 칸인지
#   X: 화면을 가로로 나눈 칸 이름 (왼쪽부터). 시연 냉장고: 왼쪽·가운데·오른쪽
#   Y: 냉장고 층. FRIDGE_FLOOR_SPLITS = 층 경계(화면 위에서부터 0~1 비율). 2층이면 경계 1개
#      카메라 각도에 맞게 부스에서 조정하세요 (captures/*/after_yolo.jpg 로 확인)
FRIDGE_X_LABELS = ["L", "C", "R"]          # L=왼쪽, C=가운데, R=오른쪽
FRIDGE_FLOOR_SPLITS = [0.5]                # 화면 위 절반 = 위층, 아래 절반 = 아래층
FRIDGE_FLOOR_NUMBER_FROM_TOP = False       # False: 맨 아래층이 1층 (건물처럼) / True: 맨 위층이 1층
