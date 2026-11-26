"""포장 식품 OCR → 대표 이름(object name).

포장에 적힌 글자를 읽어, 가장 크게 쓰인 글자 조각(보통 브랜드·제품명)으로 이름을 만듭니다.
  예: [("비비고", 큰 글자), ("왕교자", 큰 글자), ("1.05kg", 작은 글자)] → "비비고 왕교자"

구성
  OcrEngine        : 글자 읽기 인터페이스. read(RGB 사진) → [Fragment, ...]  (다른 엔진으로 바꿀 때 이것만 새로 구현)
  EasyOcrEngine    : 기본 엔진 (EasyOCR, 한국어+영어, GPU 없으면 CPU)
  make_name()      : 글자 조각 → 대표 이름 (모델 없이 단위 테스트 가능)
  match_known_item(): 글자 안에 기존 품목 단어('milk', '우유' …)가 있으면 그 품목 id
  crop_for_ocr()   : 상자 영역을 잘라 확대 (numpy 슬라이싱 + PIL, OpenCV 안 씀)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

UNKNOWN_PACKAGE = "unknown_package"


@dataclass
class Fragment:
    """OCR 이 읽은 글자 조각 하나. box 는 잘라낸 사진 안의 (x1, y1, x2, y2)."""
    text: str
    conf: float
    box: tuple[float, float, float, float]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]


class OcrEngine(Protocol):
    def read(self, rgb: np.ndarray) -> list[Fragment]: ...


class EasyOcrEngine:
    """EasyOCR (['ko', 'en']). 모델은 처음 한 번만 불러옴. 첫 실행 때 약 100MB 를 weights/easyocr 에 내려받음."""

    def __init__(self, langs=("ko", "en"), model_dir: Path | None = None, gpu: bool | None = None) -> None:
        import easyocr
        import torch

        if gpu is None:
            gpu = torch.cuda.is_available()
        kw = {}
        if model_dir is not None:
            Path(model_dir).mkdir(parents=True, exist_ok=True)
            kw = {"model_storage_directory": str(model_dir), "user_network_directory": str(model_dir)}
        import warnings
        with warnings.catch_warnings():          # CPU 양자화 관련 torch 경고 (동작에는 영향 없음)
            warnings.simplefilter("ignore")
            self.reader = easyocr.Reader(list(langs), gpu=gpu, verbose=False, **kw)

    def read(self, rgb: np.ndarray) -> list[Fragment]:
        out = []
        for pts, text, conf in self.reader.readtext(rgb):
            xs, ys = [float(p[0]) for p in pts], [float(p[1]) for p in pts]   # numpy 정수 → float (JSON 기록용)
            out.append(Fragment(str(text), float(conf), (min(xs), min(ys), max(xs), max(ys))))
        return out


# ---------------------------------------------------------------------------
# 글자 조각 → 대표 이름
# ---------------------------------------------------------------------------
_HAS_LETTER = re.compile(r"[A-Za-z가-힣]")
# 숫자 + 단위 (1.05kg, 500 ml, 250kcal, 15%) 또는 단위만 있는 조각
_UNIT = re.compile(r"\d[\d.,]*\s*(kg|mg|g|ml|l|kcal|cal|%)(?![a-z])", re.I)
_UNIT_ONLY = re.compile(r"^\s*(kg|mg|g|ml|l|kcal|cal|%)\s*$", re.I)


def clean_text(text: str) -> str:
    """숫자가 섞인 낱말(1.05kg, 120g, t3ra 같은 잘못 읽은 글자)과 앞뒤 기호를 뺀 글자."""
    words = [w.strip("()[]{}<>.,:;'\"`~!@#$^&*_-+=|\\/?") for w in text.split()]
    return " ".join(w for w in words if w and not re.search(r"\d", w))


def drop_reason(f: Fragment, min_conf: float, exclude_words, min_chars: int = 2) -> str | None:
    """이 조각을 버리는 이유 (쓰면 None)."""
    t = f.text.strip()
    if f.conf < min_conf:
        return "low_conf"
    if not _HAS_LETTER.search(t):
        return "no_letters"            # 숫자·기호만
    if _UNIT.search(t) or _UNIT_ONLY.match(t):
        return "unit"                  # 용량·영양 표기
    if any(w and w.lower() in t.lower() for w in exclude_words):
        return "excluded_word"         # 원재료, 보관, 유통기한 …
    c = clean_text(t)
    if not _HAS_LETTER.search(c):
        return "no_letters"            # 숫자 섞인 낱말만 있음 (t3ra, 1.05ka …)
    if len(re.sub(r"\s+", "", c)) < min_chars:
        return "too_short"
    return None


def make_name(fragments, min_conf: float = 0.4, exclude_words=(), max_parts: int = 2,
              max_len: int = 20, min_chars: int = 2) -> str | None:
    """대표 이름. 만들 수 없으면 None.
    1) 신뢰도 낮음·숫자만·용량/영양·안내 문구·너무 짧은 조각을 버림
    2) 남은 조각을 글자 높이가 큰 순으로 max_parts 개 고름 (포장에서 가장 크게 쓰인 글자)
    3) 고른 조각을 읽는 순서(위→아래, 왼쪽→오른쪽)로 이어 붙이고 max_len 자로 자름"""
    kept, seen = [], set()
    for f in fragments:
        if drop_reason(f, min_conf, exclude_words, min_chars):
            continue
        key = re.sub(r"\s+", "", clean_text(f.text)).lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(f)
    if not kept:
        return None
    top = sorted(kept, key=lambda f: -f.height)[:max_parts]
    # 같은 줄(세로로 겹침)이면 왼쪽부터, 아니면 위부터
    line_h = max(f.height for f in top)
    top.sort(key=lambda f: (round(f.box[1] / max(line_h, 1e-6)), f.box[0]))
    name = " ".join(clean_text(f.text) for f in top)
    return name[:max_len].strip() or None


def match_known_item(fragments, keywords: dict[str, list[str]], min_conf: float = 0.4,
                     exclude_words=(), min_chars: int = 2) -> str | None:
    """포장 글자에 기존 품목 단어가 있으면 그 item_id (예: 'Dairy PRIDE milk' → 'milk').
    큰 글자 조각부터 보고, 한 조각에 여러 품목 단어가 있으면 마지막 단어(중심 명사: 'Strawberry Yogurt' → yogurt)."""
    kept = [f for f in fragments if not drop_reason(f, min_conf, exclude_words, min_chars)]
    for f in sorted(kept, key=lambda f: -f.height):
        t = f.text.lower()
        best, best_pos = None, -1
        for item_id, words in keywords.items():
            for w in words:
                w = w.lower()
                if re.fullmatch(r"[a-z ]+", w):            # 영어: 단어 경계 (water ≠ watermelon)
                    m = None
                    for m in re.finditer(rf"\b{re.escape(w)}\b", t):
                        pass
                    pos = m.start() if m else -1
                else:                                      # 한국어: 부분 문자열
                    pos = t.rfind(w)
                if pos > best_pos:
                    best, best_pos = item_id, pos
        if best is not None:
            return best
    return None


def crop_for_ocr(frame_bgr: np.ndarray, xyxy, scale: float = 2.0, pad: float = 0.05) -> np.ndarray:
    """상자 영역을 잘라(약간 여유) scale 배 확대한 RGB 사진. numpy 슬라이싱 + PIL."""
    from PIL import Image

    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = xyxy
    px, py = pad * (x2 - x1), pad * (y2 - y1)
    x1, y1 = max(0, int(x1 - px)), max(0, int(y1 - py))
    x2, y2 = min(w, int(x2 + px)), min(h, int(y2 + py))
    rgb = np.ascontiguousarray(frame_bgr[y1:y2, x1:x2, ::-1])
    if rgb.size == 0:
        return rgb
    img = Image.fromarray(rgb)
    if scale != 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    return np.asarray(img)


def thumb(rgb: np.ndarray, size: int = 24) -> np.ndarray:
    """같은 포장인지 빠르게 비교하기 위한 작은 흑백 사진 (OCR 결과 재사용용)."""
    from PIL import Image

    if rgb.size == 0:
        return np.zeros((size, size), np.float32)
    return np.asarray(Image.fromarray(rgb).convert("L").resize((size, size), Image.BILINEAR), np.float32)


def same_look(a: np.ndarray, b: np.ndarray, min_corr: float = 0.8, flat_std: float = 4.0, flat_diff: float = 10.0) -> bool:
    """thumb() 두 장이 같은 포장으로 보이는지. 무늬가 있으면 상관계수(밝기 변화에 강함), 단색이면 평균 밝기 차이."""
    if a.std() < flat_std and b.std() < flat_std:
        return abs(float(a.mean()) - float(b.mean())) < flat_diff
    if a.std() < flat_std or b.std() < flat_std:
        return False
    za, zb = (a - a.mean()) / a.std(), (b - b.mean()) / b.std()
    return float(np.mean(za * zb)) >= min_corr
