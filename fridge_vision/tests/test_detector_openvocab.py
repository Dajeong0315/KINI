"""새 인식 방식(yoloe_openvocab) 단위 테스트 — 모델·네트워크 없이 실행.

  python -m pytest tests/test_detector_openvocab.py -q
"""
import os
import sys
import types
from pathlib import Path

os.environ["DETECTOR_BACKEND"] = "yoloe_openvocab"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import config  # noqa: E402

if config.DETECTOR_BACKEND != "yoloe_openvocab":       # 다른 테스트가 먼저 config 를 kaggle 로 불러온 경우
    pytest.skip("DETECTOR_BACKEND=yoloe_openvocab 로 따로 실행하세요", allow_module_level=True)

import ocr_names  # noqa: E402
import vision  # noqa: E402
from ocr_names import Fragment, make_name, match_known_item  # noqa: E402

RULE = dict(min_conf=0.4, exclude_words=config.OCR_EXCLUDE_WORDS, min_chars=config.OCR_MIN_CHARS)
BIG, SMALL = 60, 15


def frag(text, h=BIG, conf=0.9, y=0, x=0):
    return Fragment(text, conf, (x, y, x + 10 * len(text), y + h))


# ---------------------------------------------------------------------------
# 1. 검색어 → 라벨 매핑
# ---------------------------------------------------------------------------
def prompt_map():
    ids = [k for k in vision.ITEM_IDS if not config.ITEMS[k].get("user")]
    names, kinds = vision.openvocab_prompts(ids)
    return {n: (k[0], vision.ITEM_IDS[k[1]] if k[0] == "item" else k[1]) for n, k in zip(names, kinds)}


def test_prompt_to_label_keeps_old_label_names():
    m = prompt_map()
    assert m["milk carton"] == ("item", "milk")
    assert m["milk bottle"] == ("item", "milk")
    assert m["egg"] == ("item", "egg") and m["egg carton"] == ("item", "egg")
    assert m["tofu"] == ("item", "tofu")
    assert m["broccoli"] == ("item", "broccoli")
    assert m["green onion"] == ("item", "green_onion")
    assert m["bell pepper"] == ("item", "pepper")


def test_every_old_label_has_a_prompt():
    """예전 25개 품목이 모두 새 방식에서도 같은 라벨로 나올 수 있어야 함."""
    old = ["milk", "broccoli", "tofu", "mushroom", "chicken", "egg", "butter", "watermelon", "orange", "apple",
           "lemon", "tomato", "strawberry", "grape", "carrot", "cucumber", "cabbage", "green_onion", "cheese",
           "yogurt", "ham", "kimchi", "juice", "water", "can"]
    labels = {v[1] for v in prompt_map().values() if v[0] == "item"}
    assert set(old) <= labels
    assert all(k in config.ITEMS for k in config.OPENVOCAB_PROMPTS)


def test_package_generic_hand_prompts():
    m = prompt_map()
    for p in config.PACKAGE_PROMPTS:
        assert m[p][0] == "package"
    assert m["object"][0] == "generic" and m["hand"][0] == "hand"
    assert "unknown_package" not in {v[1] for v in m.values()}      # 결과용 라벨은 검색어가 없음


# ---------------------------------------------------------------------------
# 2. OCR 결과 → 이름 규칙
# ---------------------------------------------------------------------------
def test_big_text_becomes_name():
    f = [frag("비비고", BIG, y=0), frag("왕교자", BIG, y=70), frag("1.05kg", SMALL, y=200)]
    assert make_name(f, **RULE) == "비비고 왕교자"


def test_order_is_reading_order_not_size():
    f = [frag("비비고", 50, y=0), frag("왕교자", 80, y=60), frag("고기만두", 20, y=150)]
    assert make_name(f, **RULE) == "비비고 왕교자"


def test_only_numbers_units_notices_is_unknown():
    f = [frag("1.05kg", BIG), frag("250kcal"), frag("15%"), frag("123456"), frag("냉동보관 -18℃이하"),
         frag("유통기한 2026.12.01"), frag("원재료명")]
    assert make_name(f, **RULE) is None


def test_low_confidence_only_is_unknown():
    assert make_name([frag("비비고", conf=0.2), frag("왕교자", conf=0.39)], **RULE) is None


def test_name_is_cut_to_max_len_and_parts():
    f = [frag("아주아주긴브랜드이름입니다", BIG, y=0), frag("정말로긴제품이름이에요요", BIG, y=70), frag("세번째", BIG, y=140)]
    name = make_name(f, max_len=20, **RULE)
    assert len(name) <= 20 and "세번째" not in name


def test_english_words_with_g_are_not_units():
    assert make_name([frag("Dumpling", BIG), frag("500 g", SMALL)], **RULE) == "Dumpling"


def test_known_item_keyword():
    kw = config.PACKAGE_KEYWORDS
    assert match_known_item([frag("Dairy PRIDE"), frag("milk", 40)], kw, **RULE) == "milk"
    assert match_known_item([frag("Sublime Strawberry Yogurt")], kw, **RULE) == "yogurt"
    assert match_known_item([frag("풀무원"), frag("국산콩 두부")], kw, **RULE) == "tofu"
    assert match_known_item([frag("watermelon bar")], kw, **RULE) is None          # water ≠ watermelon
    assert match_known_item([frag("비비고"), frag("왕교자")], kw, **RULE) is None


# ---------------------------------------------------------------------------
# 3. 출력 형식이 예전 방식과 같은지 (가짜 모델·가짜 OCR)
# ---------------------------------------------------------------------------
class FakeOcr:
    def __init__(self, table):
        self.table, self.calls = table, 0

    def read(self, rgb):
        self.calls += 1
        return self.table.pop(0) if self.table else []


def fake_comparer(preds, ocr_table):
    """preds: [(검색어, 점수, (x1,y1,x2,y2)), ...] 를 매번 돌려주는 가짜 YOLOE."""
    comp = vision.YoloComparer.__new__(vision.YoloComparer)
    ids = [k for k in vision.ITEM_IDS if not config.ITEMS[k].get("user")]
    comp.names, comp.class_kinds = vision.openvocab_prompts(ids)
    comp.backend, comp.model_path, comp.prompt_source = "yoloe_openvocab", Path("yoloe-v8m-seg.pt"), "test"
    comp.generic_prompts, comp.hand_prompts = list(config.YOLO_GENERIC_PROMPTS), list(config.YOLO_HAND_PROMPTS)
    comp.memory, comp._clip, comp._ocr_cache = {"items": {}, "labels": []}, None, []
    comp._ocr_engine = FakeOcr(ocr_table)
    comp._log = lambda e: None
    comp._verifier, comp._clip_cache = None, []          # CLIP 재확인 없음 (아래 테스트에서 따로 주입)

    class T(list):
        def tolist(self):
            return list(self)

    def predict(frame, **kw):
        boxes = types.SimpleNamespace(cls=T(comp.names.index(p) for p, _, _ in preds),
                                      conf=T(s for _, s, _ in preds), xyxy=T(list(b) for _, _, b in preds))
        return [types.SimpleNamespace(boxes=boxes)]

    comp.model = types.SimpleNamespace(predict=predict)
    return comp


FRAME = np.full((540, 960, 3), 128, np.uint8)


def test_detect_output_format():
    comp = fake_comparer([("egg carton", 0.8, (100, 100, 300, 200)), ("egg", 0.5, (120, 120, 160, 160)),
                          ("milk carton", 0.6, (400, 50, 480, 250)), ("food package", 0.7, (600, 300, 800, 450)),
                          ("hand", 0.6, (0, 0, 50, 50))],
                         [[frag("비비고", y=0), frag("왕교자", y=70), frag("1.05kg", SMALL, y=200)]])
    boxes, hand, view = comp._detect(FRAME)
    assert hand is True and isinstance(view, np.ndarray) and view.shape == FRAME.shape
    for b in boxes:                               # [int, float, [4], *태그] — 예전 방식과 같은 형식
        assert isinstance(b[0], int) and isinstance(b[1], float) and len(b[2]) == 4
    labels = sorted(vision.ITEM_IDS[b[0]] for b in boxes)
    pkg = [k for k in labels if k.startswith("pkg_")]
    assert len(pkg) == 1 and config.ITEMS[pkg[0]]["name"] == "비비고 왕교자"
    assert labels.count("egg") == 1               # 계란판 안의 계란 낱개는 1개로
    assert "milk" in labels


def test_unreadable_package_is_unknown_package():
    comp = fake_comparer([("pouch", 0.6, (600, 300, 800, 450))], [[frag("300g", SMALL)]])
    boxes, _, _ = comp._detect(FRAME)
    assert [vision.ITEM_IDS[b[0]] for b in boxes] == ["unknown_package"]


def test_package_with_known_word_uses_old_label():
    comp = fake_comparer([("box", 0.6, (600, 300, 800, 450))], [[frag("서울"), frag("우유", 50)]])
    boxes, _, _ = comp._detect(FRAME)
    assert [vision.ITEM_IDS[b[0]] for b in boxes] == ["milk"]


def test_generic_mode(monkeypatch):
    monkeypatch.setattr(config, "PACKAGE_LABEL_MODE", "generic")
    comp = fake_comparer([("food package", 0.7, (600, 300, 800, 450))], [[frag("비비고"), frag("왕교자", y=70)]])
    boxes, _, _ = comp._detect(FRAME)
    assert [vision.ITEM_IDS[b[0]] for b in boxes] == ["packaged_food"]


def test_package_id_is_stable():
    a = vision.register_package_item("오뚜기 진라면")
    b = vision.register_package_item("오뚜기  진라면")
    assert a == b and a.startswith("pkg_")
    import hashlib
    assert a == "pkg_" + hashlib.sha1("오뚜기진라면".encode()).hexdigest()[:8]


def test_similar_package_names_are_one_item():
    # 실제 시험에서 같은 카스테라 포장이 이렇게 읽힘
    a = vision.register_package_item("카스테라 한입")
    assert vision.register_package_item("한입 카스테라") == a          # 낱말 순서만 다름
    assert vision.register_package_item("카스테라") == a                # 한쪽이 다른 쪽을 포함
    assert vision.register_package_item("가스테라 한입") == a          # 한 글자 잘못 읽음
    assert vision.register_package_item("서울우유 바나나") != a          # 다른 제품


def test_ocr_cache_reuses_same_package():
    comp = fake_comparer([("food package", 0.7, (600, 300, 800, 450))], [[frag("비비고"), frag("왕교자", y=70)]])
    comp._detect(FRAME)
    comp._detect(FRAME)
    assert comp._ocr_engine.calls == 1


def test_ocr_cache_not_reused_for_different_package():
    comp = fake_comparer([("food package", 0.7, (600, 300, 800, 450))], [[frag("비비고")], [frag("풀무원")]])
    a = FRAME.copy()
    a[300:450, 600:800] = np.tile(np.arange(200, dtype=np.uint8), (150, 1))[..., None]   # 가로 줄무늬
    b = FRAME.copy()
    b[300:450, 600:800] = np.tile(np.arange(150, dtype=np.uint8)[:, None], (1, 200))[..., None]  # 세로 줄무늬
    comp._detect(a)
    comp._detect(b)
    assert comp._ocr_engine.calls == 2


def test_same_look_rules():
    rng = np.random.default_rng(0)
    t = rng.uniform(0, 255, (24, 24)).astype(np.float32)
    assert ocr_names.same_look(t, t * 0.8 + 20)               # 같은 무늬, 밝기만 다름
    assert not ocr_names.same_look(t, rng.uniform(0, 255, (24, 24)).astype(np.float32))
    flat = np.full((24, 24), 128, np.float32)
    assert ocr_names.same_look(flat, flat + 3) and not ocr_names.same_look(flat, t)


# ---------------------------------------------------------------------------
# 4. CLIP 재확인 규칙 (가짜 CLIP 결과 주입)
# ---------------------------------------------------------------------------
def with_clip(comp, hints):
    """hints: 상자 순서(YOLOE 점수 높은 순)대로 (CLIP 라벨, 확률)."""
    comp._verifier = object()
    comp._clip_verify = lambda frame, xyxys: list(hints)[:len(xyxys)]
    return comp


def labels_of(boxes):
    return sorted(vision.ITEM_IDS[b[0]] if b[0] != vision.UNKNOWN else "Unknown" for b in boxes)


def test_clip_sure_relabels_and_keeps_low_score_box():
    # YOLOE: 갈색 계란을 '사과' 0.33 / 낮은 점수 '오렌지' 0.15 → CLIP 이 계란 0.8 / 0.7 로 확신
    comp = with_clip(fake_comparer([("apple", 0.33, (100, 100, 200, 200)), ("orange", 0.15, (300, 100, 400, 200))], []),
                     [("egg", 0.8), ("egg", 0.7)])
    boxes, _, _ = comp._detect(FRAME)
    assert labels_of(boxes) == ["egg", "egg"]
    assert all(b[1] >= 0.7 for b in boxes)


def test_clip_unsure_keeps_yoloe_label_only_above_threshold():
    comp = with_clip(fake_comparer([("apple", 0.6, (100, 100, 200, 200)), ("orange", 0.15, (300, 100, 400, 200))], []),
                     [("tomato", 0.3), ("lemon", 0.2)])
    boxes, _, _ = comp._detect(FRAME)
    assert labels_of(boxes) == ["apple"]            # 확신 없는 약한 상자(0.15)는 셈하지 않음


def test_unknown_overlapping_item_is_dropped():
    # 포도 그릇: CLIP 이 큰 상자를 '포도'로 확정 + 같은 자리 작은 조각들은 Unknown 묶음 → Unknown 은 버림
    preds = [("grapes", 0.3, (590, 200, 640, 275)),
             ("apple", 0.2, (592, 230, 610, 250)), ("orange", 0.18, (608, 232, 626, 252)), ("lemon", 0.16, (612, 255, 630, 275))]
    comp = with_clip(fake_comparer(preds, []), [("grape", 0.63), ("egg", 0.2), ("egg", 0.3), ("onion", 0.2)])
    boxes, _, _ = comp._detect(FRAME)
    assert labels_of(boxes) == ["grape"]


def test_package_ocr_beats_clip():
    # 빨간 만두 포장: CLIP 은 '김치' 0.96 이라고 하지만 포장 글자가 있으면 OCR 이름이 우선
    comp = with_clip(fake_comparer([("box", 0.35, (600, 300, 800, 450))], [[frag("비비고"), frag("왕교자", y=70)]]),
                     [("kimchi", 0.96)])
    boxes, _, _ = comp._detect(FRAME)
    assert config.ITEMS[vision.ITEM_IDS[boxes[0][0]]]["name"] == "비비고 왕교자"


def test_package_without_text_uses_clip_item():
    comp = with_clip(fake_comparer([("food package", 0.34, (600, 300, 800, 450))], [[]]), [("egg", 0.7)])
    boxes, _, _ = comp._detect(FRAME)
    assert labels_of(boxes) == ["egg"]


def test_item_box_that_clip_calls_package_goes_to_ocr():
    comp = with_clip(fake_comparer([("tofu", 0.4, (600, 300, 800, 450))], [[frag("오뚜기"), frag("진라면", y=70)]]),
                     [(vision.YoloComparer.PACKAGE, 0.8)])
    boxes, _, _ = comp._detect(FRAME)
    assert config.ITEMS[vision.ITEM_IDS[boxes[0][0]]]["name"] == "오뚜기 진라면"


def test_log_accepts_numpy_values(tmp_path, monkeypatch):
    """실제 시험에서 난 오류: EasyOCR 좌표(numpy int32)가 로그 JSON 에 들어가 인식 전체가 실패함."""
    monkeypatch.setattr(config, "DETECTION_LOG", tmp_path / "d.jsonl")
    comp = vision.YoloComparer.__new__(vision.YoloComparer)
    comp._log({"h": np.int32(5), "c": np.float32(0.5), "a": np.arange(2)})
    import json
    row = json.loads((tmp_path / "d.jsonl").read_text(encoding="utf-8"))
    assert row["h"] == 5 and abs(row["c"] - 0.5) < 1e-6


def test_profiles():
    """live: OCR 새로 하지 않음 / full: 캐시 무시하고 다시 OCR."""
    comp = fake_comparer([("food package", 0.7, (600, 300, 800, 450))],
                         [[frag("비비고"), frag("왕교자", y=70)], [frag("비비고"), frag("왕교자", y=70)]])
    boxes, _, _ = comp.detect_frame(FRAME, "live")
    assert comp._ocr_engine.calls == 0 and labels_of(boxes) == ["unknown_package"]
    comp.detect_frame(FRAME, "full")
    assert comp._ocr_engine.calls == 1
    boxes, _, _ = comp.detect_frame(FRAME, "live")             # 정밀 분석 결과를 실시간 화면이 다시 씀
    assert comp._ocr_engine.calls == 1 and config.ITEMS[vision.ITEM_IDS[boxes[0][0]]]["name"] == "비비고 왕교자"
    comp.detect_frame(FRAME, "full")                           # 정밀 분석은 매번 새로
    assert comp._ocr_engine.calls == 2


def test_compare_result_keys_same_as_before():
    comp = fake_comparer([("butter", 0.8, (100, 100, 200, 200)), ("food package", 0.7, (600, 300, 800, 450))],
                         [[frag("비비고"), frag("왕교자", y=70)]])
    res = comp.compare(FRAME, FRAME.copy(), "test-cap")
    assert set(res) == {"capture_id", "created_at", "source", "before", "after", "added", "removed",
                        "uncertain_items", "hand_or_open_door_visible", "needs_confirmation",
                        "confirmation_reasons", "note", "unknown", "before_locations", "unknown_regions"}
    assert set(res["before"]) == set(vision.ITEM_IDS) and res["after"]["butter"] == 1
    assert res["source"] == "yolo:yoloe-v8m-seg.pt"
    pkg = [k for k, v in res["after"].items() if k.startswith("pkg_") and v]
    assert len(pkg) == 1
