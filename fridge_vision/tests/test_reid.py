"""재인식(reid/) 단위 테스트 — 모델·네트워크 없이 실행 (특징값은 가짜).

  python -m pytest tests/test_reid.py -q
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DETECTOR_BACKEND", "yoloe_openvocab")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

import config  # noqa: E402
from reid import augment as aug  # noqa: E402
from reid.gallery import Gallery  # noqa: E402
from reid.tracks import Tracker, TrackStore, box_change_ratio, change_mask  # noqa: E402


def unit(*v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def thumb_embed(imgs):
    """가짜 특징값: 4×4 색 축소본 (내용이 같으면 같은 벡터, 다르면 다른 벡터)."""
    out = []
    for im in imgs:
        t = cv2.resize(im, (4, 4), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
        t = t - 128 + 1e-3 * np.arange(t.size)            # 색이 다르면 방향이 크게 다름
        out.append(t / np.linalg.norm(t))
    return np.stack(out) if out else np.zeros((0, 48), np.float32)


@pytest.fixture
def store(tmp_path):
    return TrackStore(tmp_path / "state" / "tracks.json")


def det(label, bbox, source="detector", zone="L,1", score=0.6, gid=None):
    return {"label": label, "source": source, "bbox": list(bbox), "score": score, "zone": zone, "gallery_id": gid}


H, W = 540, 960
NO_CHANGE = np.zeros((H, W), np.uint8)


def changed(*boxes):
    m = np.zeros((H, W), np.uint8)
    for x1, y1, x2, y2 in boxes:
        m[y1:y2, x1:x2] = 1
    return m


class FakeGallery:
    """decide/sim/add_real 만 흉내."""

    def __init__(self, sims=None, decision=None):
        self.sims, self.decision, self.added = sims or {}, decision, []

    def sim(self, gid, vec):
        return self.sims.get(gid, -1.0)

    def decide(self, vec, allowed=None):
        return self.decision or {"decision": "unknown", "gallery_id": None, "item_id": None, "score": None, "second": None}

    def add_real(self, gid, vec, crop=None):
        self.added.append(gid)
        return True


# ---------------------------------------------------------------------------
# 1. 트랙 갱신 규칙
# ---------------------------------------------------------------------------
def test_unchanged_place_keeps_name_without_rejudging(store):
    t = store.new(label="user_4", label_source="user", bbox=[100, 100, 300, 250], zone="L,1", gallery_id="g1")
    calls = []
    out = Tracker(store, config).update([det("unknown", (104, 102, 302, 252), source="unknown")], NO_CHANGE, "c2",
                                        embed=lambda i: calls.append(i))
    r = out["dets"][0]
    assert r["rule"] == "unchanged" and r["label"] == "user_4" and r["track_id"] == t.track_id
    assert calls == []                                   # 그대로인 물체는 특징값도 계산하지 않음
    assert store.tracks[0].bbox == [104, 102, 302, 252]  # 박스만 갱신


def test_slightly_rotated_or_moved_keeps_name(store):
    store.new(label="user_4", label_source="user", bbox=[100, 100, 300, 250], zone="L,1", gallery_id="g1",
              emb=list(unit(1, 0, 0)))
    gal = FakeGallery(sims={"g1": 0.9})
    box = (160, 110, 360, 260)                           # IoU < 0.5, 변화 있음
    out = Tracker(store, config, gal).update([det("tofu", box)], changed(box), "c2", embed=lambda i: unit(0.6, 0.8, 0))
    r = out["dets"][0]
    assert r["rule"] == "near" and r["label"] == "user_4" and r["source"] == "user"
    assert gal.added == ["g1"]                           # 확인된 실제 모습을 갤러리에 더함


def test_near_requires_similar_look(store):
    store.new(label="user_4", label_source="user", bbox=[100, 100, 300, 250], zone="L,1", emb=list(unit(1, 0, 0)))
    box = (160, 110, 360, 260)
    out = Tracker(store, config).update([det("tofu", box)], changed(box, (100, 100, 300, 250)), "c2",
                                        embed=lambda i: unit(0, 1, 0))
    assert out["dets"][0]["rule"] == "new" and out["dets"][0]["label"] == "tofu"
    assert [r["label"] for r in out["removed"]] == ["user_4"]


def test_disappeared_where_changed_is_removed(store):
    store.new(label="milk", label_source="detector", bbox=[500, 100, 600, 300], zone="C,1")
    out = Tracker(store, config).update([], changed((500, 100, 600, 300)), "c2")
    assert [r["label"] for r in out["removed"]] == ["milk"] and store.tracks == []


def test_missed_detection_without_change_is_kept(store):
    store.new(label="user_4", label_source="user", bbox=[100, 100, 300, 250], zone="L,1")
    out = Tracker(store, config).update([], NO_CHANGE, "c2")
    assert [t.label for t in out["kept"]] == ["user_4"] and out["removed"] == [] and len(store.tracks) == 1


def test_moved_to_other_zone_is_a_move_not_in_out(store):
    t = store.new(label="user_4", label_source="user", bbox=[100, 100, 300, 250], zone="L,1", gallery_id="g1")
    gal = FakeGallery(decision={"decision": "match", "gallery_id": "g1", "item_id": "user_4", "score": 0.9, "second": None})
    new_box = (700, 350, 900, 500)
    out = Tracker(store, config, gal).update([det("unknown", new_box, source="unknown", zone="R,2")],
                                             changed((100, 100, 300, 250), new_box), "c2", embed=lambda i: unit(1, 0))
    assert out["dets"][0]["rule"] == "moved" and out["dets"][0]["track_id"] == t.track_id
    assert out["moved"][0]["from_zone"] == "L,1" and out["moved"][0]["to_zone"] == "R,2"
    assert out["removed"] == [] and len(store.tracks) == 1


def test_confirm_needed_becomes_unknown_with_candidate(store):
    gal = FakeGallery(decision={"decision": "confirm", "gallery_id": "g1", "item_id": "user_4", "score": 0.8,
                                "second": ["user_6", 0.78]})
    out = Tracker(store, config, gal).update([det("tofu", (10, 10, 100, 100))], changed((10, 10, 100, 100)), "c1",
                                             embed=lambda i: unit(1, 0))
    r = out["dets"][0]
    assert r["label"] == "unknown" and r["hint"] == "user_4"


def test_lower_priority_name_never_overwrites_user_name(store):
    store.new(label="user_4", label_source="user", bbox=[100, 100, 300, 250])
    store.new(label="apple", label_source="detector", bbox=[400, 100, 500, 200])
    out = Tracker(store, config).update([det("tofu", (100, 100, 300, 250)), det("pkg_x", (400, 100, 500, 200), source="ocr")],
                                        NO_CHANGE, "c2")
    assert out["dets"][0]["label"] == "user_4"                       # 탐지 품목이 사용자 이름을 덮지 않음
    assert out["dets"][1]["label"] == "pkg_x" and out["dets"][1]["how"] == "upgraded"   # 글씨 이름은 탐지 이름보다 우선


def test_tracks_save_load_and_reset(store):
    store.frame_size = [W, H]
    store.new(label="user_4", label_source="user", bbox=[1, 2, 3, 4], zone="L,1", gallery_id="g1", emb=[0.1, 0.2])
    store.save()
    again = TrackStore(store.path)
    assert again.tracks[0].label == "user_4" and again.tracks[0].emb == [0.1, 0.2]
    assert again.next_id == 2 and again.frame_size == [W, H]
    again.reset()
    assert TrackStore(store.path).tracks == [] and not store.path.exists()


# ---------------------------------------------------------------------------
# 2. 변화 영역
# ---------------------------------------------------------------------------
def test_change_mask_ignores_exposure_and_noise_but_sees_objects():
    rng = np.random.default_rng(0)
    before = rng.integers(60, 120, (H, W, 3), dtype=np.uint8)
    brighter = cv2.convertScaleAbs(before, alpha=1.0, beta=20)       # 자동 노출로 전체가 밝아짐
    assert change_mask(before, brighter).mean() < 0.01
    after = before.copy()
    after[100:250, 100:300] = 250                                     # 물체를 넣음
    m = change_mask(before, after)
    assert box_change_ratio(m, (100, 100, 300, 250)) > 0.8
    assert box_change_ratio(m, (500, 100, 700, 250)) < config.UNCHANGED_RATIO


# ---------------------------------------------------------------------------
# 3. 증강
# ---------------------------------------------------------------------------
def sample_crop():
    img = np.full((120, 90, 3), 128, np.uint8)
    cv2.rectangle(img, (10, 20), (80, 110), (40, 40, 160), -1)
    cv2.putText(img, "K", (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
    return img


def test_augment_count_and_reproducible():
    a = aug.augment(sample_crop(), config.REID_AUG, 24, seed=0)
    b = aug.augment(sample_crop(), config.REID_AUG, 24, seed=0)
    c = aug.augment(sample_crop(), config.REID_AUG, 24, seed=1)
    assert len(a) == 24 and [d for _, d in a] == [d for _, d in b]
    assert all(np.array_equal(x, y) for (x, _), (y, _) in zip(a, b))
    assert [d for _, d in a] != [d for _, d in c]                     # 시드가 다르면 조합이 다름
    descs = [d for _, d in a]
    assert len(set(descs)) == 24 and sum("+" in d for d in descs) == 24 - len(aug.single_ops(config.REID_AUG))
    for kind in ("rotate", "perspective", "flip", "scale", "brightness", "contrast", "color_temp", "blur"):
        assert any(d.startswith(kind) for d in descs)
    assert all(aug.to_input(x).shape == (224, 224, 3) for x, _ in a)


def test_prepare_crop_pads_and_masks_background():
    frame = np.full((200, 200, 3), 30, np.uint8)
    frame[50:150, 50:150] = (0, 0, 200)
    crop = aug.prepare_crop(frame, (50, 50, 150, 150), pad=0.1)
    assert crop.shape[:2] == (120, 120)
    m = np.zeros((200, 200), bool)
    m[50:150, 50:150] = True
    masked = aug.prepare_crop(frame, (50, 50, 150, 150), pad=0.1, mask=m, fill=128)
    assert (masked[0, 0] == 128).all() and (masked[60, 60] == (0, 0, 200)).all()
    assert aug.prepare_crop(frame, (199, 199, 200, 200)) is None


# ---------------------------------------------------------------------------
# 4. 갤러리 판정
# ---------------------------------------------------------------------------
def gallery_with(tmp_path, rows):
    g = Gallery(tmp_path / "gallery", thumb_embed, "fake")
    for gid, item, vecs in rows:
        g.entries[gid] = {"meta": {"gallery_id": gid, "item_id": item, "name": item, "kinds": ["original"] * len(vecs)},
                          "vecs": np.stack([unit(*v) for v in vecs])}
    return g


def test_decide_match_confirm_unknown(tmp_path):
    g = gallery_with(tmp_path, [("ga", "a", [(1, 0, 0), (0.9, 0.1, 0), (0.95, 0, 0.05)]),
                                ("gb", "b", [(0, 1, 0), (0, 0.9, 0.1), (0.1, 0.9, 0)])])
    assert g.decide(unit(1, 0, 0))["decision"] == "match"
    assert g.decide(unit(1, 0, 0))["item_id"] == "a"
    assert g.decide(unit(0.7, 0.7, 0))["decision"] in ("confirm", "unknown")
    assert g.decide(unit(0, 0, 1))["decision"] == "unknown"
    g2 = gallery_with(tmp_path, [("ga", "a", [(1, 0, 0)]), ("gb", "b", [(0.99, 0.12, 0)])])
    d = g2.decide(unit(1, 0.05, 0))
    assert d["decision"] == "confirm" and d["second"][0] in ("a", "b")       # 1·2등 차이가 작으면 확인 필요
    g3 = gallery_with(tmp_path, [("ga", "a", [(1, 0, 0)]), ("ga2", "a", [(0.99, 0.12, 0)])])
    assert g3.decide(unit(1, 0.05, 0))["decision"] == "match"                 # 같은 품목끼리는 경쟁하지 않음
    assert g.decide(unit(1, 0, 0), allowed={"b"})["item_id"] == "b"


def test_register_builds_augmented_gallery_with_preview_and_reload(tmp_path):
    g = Gallery(tmp_path / "gallery", thumb_embed, "fake")
    gid, warn = g.register("user_4", "인절미", sample_crop(), source=[1, 2, 3, 4])
    assert warn is None
    meta = json.loads((tmp_path / "gallery" / f"{gid}.json").read_text(encoding="utf-8"))
    assert meta["name"] == "인절미" and meta["kinds"][0] == "original" and len(meta["kinds"]) == 1 + 24
    assert sum(k.startswith("aug:") for k in meta["kinds"]) == 24
    assert len(list((tmp_path / "gallery" / gid / "preview").glob("*.jpg"))) == config.REID_PREVIEW_N
    assert (tmp_path / "gallery" / gid / "original.jpg").exists()
    again = Gallery(tmp_path / "gallery", thumb_embed, "fake")
    assert again.entries[gid]["vecs"].shape == (25, 48)
    assert again.decide(thumb_embed([aug.to_input(sample_crop())])[0])["item_id"] == "user_4"


def test_register_similar_container_warns(tmp_path):
    g = Gallery(tmp_path / "gallery", thumb_embed, "fake")
    g.register("user_4", "인절미", sample_crop())
    _, warn = g.register("user_6", "떡갈비", sample_crop())              # 같은 모양 용기
    assert warn and "인절미" in warn and "라벨 스티커" in warn
    g2 = Gallery(tmp_path / "gallery2", thumb_embed, "fake")
    g2.register("user_4", "인절미", sample_crop())
    _, warn = g2.register("user_4", "인절미", sample_crop())             # 같은 품목을 다시 등록하면 경고 없음
    assert warn is None


def test_add_real_prunes_oldest_augmented_first(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GALLERY_MAX", 26)
    g = Gallery(tmp_path / "gallery", thumb_embed, "fake")
    gid, _ = g.register("user_4", "인절미", sample_crop())
    first_aug = g.entries[gid]["meta"]["kinds"][1]
    rng = np.random.default_rng(1)
    for _ in range(3):
        assert g.add_real(gid, unit(*rng.normal(size=48)))
    kinds = g.entries[gid]["meta"]["kinds"]
    assert len(kinds) == 26 and kinds.count("real") == 3 and kinds[0] == "original" and first_aug not in kinds[1:2]
    assert g.entries[gid]["vecs"].shape[0] == 26
    assert not g.add_real(gid, g.entries[gid]["vecs"][0])                 # 이미 있는 모습은 더하지 않음


def test_changed_embedder_rebuilds_from_saved_photo(tmp_path):
    g = Gallery(tmp_path / "gallery", thumb_embed, "fake")
    gid, _ = g.register("user_4", "인절미", sample_crop())
    other = Gallery(tmp_path / "gallery", lambda imgs: thumb_embed(imgs)[:, :16] / np.linalg.norm(thumb_embed(imgs)[:, :16], axis=1, keepdims=True), "fake16")
    assert other.entries[gid]["vecs"].shape == (25, 16) and other.entries[gid]["meta"]["embedder"] == "fake16"


# ---------------------------------------------------------------------------
# 5. YoloComparer 연결: 결과 형식 그대로 + 이름 유지
# ---------------------------------------------------------------------------
def make_comparer(tmp_path, monkeypatch, frames_boxes, mode="gallery"):
    import vision
    monkeypatch.setattr(config, "REID_MODE", mode)
    monkeypatch.setattr(config, "REID_STATE_FILE", tmp_path / "state" / "tracks.json")
    monkeypatch.setattr(config, "REID_GALLERY_DIR", tmp_path / "gallery")
    monkeypatch.setattr(config, "REID_LOG", tmp_path / "logs" / "reid.jsonl")
    monkeypatch.setattr(config, "USER_LABELS_FILE", tmp_path / "user_labels.json")
    monkeypatch.setattr(config, "CAPTURE_DIR", tmp_path / "captures")
    comp = vision.YoloComparer.__new__(vision.YoloComparer)
    comp.model_path = Path("yoloe-v8m-seg.pt")
    comp.memory, comp._clip = {"items": {}, "labels": []}, None
    comp._embed = lambda frame, xyxy: thumb_embed([aug.to_input(aug.prepare_crop(frame, xyxy))])[0]   # 예전 방식 (가짜)
    store = TrackStore(config.REID_STATE_FILE)
    gal = Gallery(config.REID_GALLERY_DIR, thumb_embed, "fake")
    comp._reid_parts = (thumb_embed, gal, store, Tracker(store, config, gal))

    def detect(frame):
        boxes = [list(b) for b in frames_boxes[id(frame)]]
        comp._recall_boxes(frame, boxes)
        return boxes, False, frame
    comp._detect = detect
    return comp, vision


def scene(*objs):
    f = np.full((H, W, 3), 90, np.uint8)
    for (x1, y1, x2, y2), color in objs:
        cv2.rectangle(f, (x1, y1), (x2, y2), color, -1)
        cv2.circle(f, ((x1 + x2) // 2, (y1 + y2) // 2), (x2 - x1) // 4, (250, 250, 250), -1)
    return f


BOX = (100, 100, 300, 250)
RED = (40, 40, 170)


def test_compare_output_format_unchanged_and_name_kept(tmp_path, monkeypatch):
    f0, f1, f2 = scene((BOX, RED)), scene((BOX, RED)), scene((BOX, RED), ((600, 300, 700, 450), (200, 160, 40)))
    U = -1
    boxes = {id(f0): [[U, 0.37, list(BOX)]], id(f1): [[U, 0.37, list(BOX)]],
             id(f2): [[U, 0.37, list(BOX)], [U, 0.4, [600, 300, 700, 450]]]}
    comp, vision = make_comparer(tmp_path, monkeypatch, boxes)
    r0 = comp.compare(f0, f1, "c0")
    assert r0["unknown"] == {"before": 1, "after": 1}
    k = comp.learn_label("인절미", f1, BOX)                                 # 사용자가 이름 입력 → 갤러리 등록
    assert len(comp._reid()[1].entries) == 1
    r1 = comp.compare(f1, f2, "c1")
    names = {e["name"] for e in r1["added"]}
    assert config.ITEMS[k]["name"] not in names                            # 그대로 있는 인절미는 '추가'가 아님
    assert r1["after"][k] == 1 and r1["before"][k] == 1 and r1["unknown"]["after"] == 1
    log = [json.loads(x) for x in (tmp_path / "logs" / "reid.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {e["kind"] for e in log} >= {"register", "track"}
    legacy_comp, _ = make_comparer(tmp_path / "legacy", monkeypatch, boxes, mode="legacy")
    r_legacy = legacy_comp.compare(f0, f1, "c9")
    assert set(r1) == set(r_legacy)                                        # 결과 키·자료형이 예전 방식과 같음
    for key in r_legacy:
        assert type(r1[key]) is type(r_legacy[key]), key
    for e in r1["added"] + r1["removed"]:
        assert set(e) == {"item_id", "name", "quantity", "locations"} or set(e) >= {"item_id", "quantity"}
    log = [json.loads(x) for x in (tmp_path / "logs" / "reid.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {e["kind"] for e in log} >= {"register", "track"}


def test_compare_keeps_object_when_detection_is_missed(tmp_path, monkeypatch):
    f0, f1, f2 = scene((BOX, RED)), scene((BOX, RED)), scene((BOX, RED))
    boxes = {id(f0): [[-1, 0.37, list(BOX)]], id(f1): [[-1, 0.37, list(BOX)]], id(f2): []}   # f2: 탐지가 놓침
    comp, _ = make_comparer(tmp_path, monkeypatch, boxes)
    comp.compare(f0, f1, "c0")
    k = comp.learn_label("인절미", f1, BOX)
    r = comp.compare(f1, f2, "c1")
    assert r["after"][k] == 1 and r["removed"] == []                       # 변화 없음 → 재고에서 사라지지 않음


def test_compare_registered_container_moved_to_other_shelf(tmp_path, monkeypatch):
    other = (600, 300, 800, 450)
    f0, f1, f2 = scene((BOX, RED)), scene((BOX, RED)), scene((other, RED))
    boxes = {id(f0): [[-1, 0.37, list(BOX)]], id(f1): [[-1, 0.37, list(BOX)]], id(f2): [[-1, 0.37, list(other)]]}
    comp, _ = make_comparer(tmp_path, monkeypatch, boxes)
    comp.compare(f0, f1, "c0")
    k = comp.learn_label("인절미", f1, BOX)
    r = comp.compare(f1, f2, "c1")
    assert r["after"][k] == 1 and r["added"] == [] and r["removed"] == []
    log = [json.loads(x) for x in (tmp_path / "logs" / "reid.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["kind"] == "track" and e["moved"] for e in log)


def test_legacy_mode_never_touches_gallery(tmp_path, monkeypatch):
    f0 = scene((BOX, RED))
    comp, vision = make_comparer(tmp_path, monkeypatch, {id(f0): [[-1, 0.37, list(BOX)]]}, mode="legacy")
    comp._reid = lambda: (_ for _ in ()).throw(AssertionError("legacy 에서 갤러리 사용"))
    boxes, _, _ = comp._detect(f0)
    comp.learn_label("인절미", f0, BOX)
    comp.compare(f0, f0, "c0")
    assert not (tmp_path / "gallery").exists() and not (tmp_path / "state").exists()


# ---------------------------------------------------------------------------
# 6. '이 물체가 아님'(네거티브)
# ---------------------------------------------------------------------------
def test_negatives_veto_a_single_registered_item(tmp_path):
    g = gallery_with(tmp_path, [("ga", "a", [(1, 0, 0)])])
    q = unit(0.88, 0.47, 0)                                   # 등록 물체와 0.88 (기준 0.85 이상)
    assert g.decide(q)["decision"] == "match"
    assert g.add_negatives([unit(0.8, 0.6, 0)], "ga") == 1    # 같은 사진의 다른 물체 (q 와 0.99)
    d = g.decide(q)
    assert d["decision"] == "unknown" and d["negative"] > 0.95
    assert g.decide(unit(1, 0, 0))["decision"] == "match"     # 등록 물체 자신은 그대로
    assert g.add_negatives([unit(1, 0.01, 0)], "ga") == 0     # 등록 물체와 거의 같은 것은 네거티브로 넣지 않음
    again = Gallery(tmp_path / "gallery", thumb_embed, "fake")
    assert again.negatives.shape == (1, 3) and again.neg_src == ["ga"]


def test_registering_a_former_negative_removes_it(tmp_path):
    g = Gallery(tmp_path / "gallery", thumb_embed, "fake")
    crop = sample_crop()
    g.add_negatives(thumb_embed([aug.to_input(crop)]), "older")
    gid, _ = g.register("user_7", "떡갈비", crop)               # 예전엔 '다른 물체'였던 것을 지금 등록
    assert g.negatives is None or len(g.negatives) == 0
    assert g.decide(thumb_embed([aug.to_input(crop)])[0])["item_id"] == "user_7"


def test_compare_does_not_spread_a_name_to_neighbours(tmp_path, monkeypatch):
    """이름을 붙인 용기 옆의 다른 물체가 그 이름으로 바뀌지 않음 (실제 샘플에서 발견한 문제의 재현)."""
    other = (400, 100, 600, 250)
    f0 = scene((BOX, RED), (other, (60, 150, 60)))
    f1 = scene((BOX, RED), (other, (60, 150, 60)))
    boxes = {id(f0): [[-1, 0.37, list(BOX)], [-1, 0.4, list(other)]], id(f1): [[-1, 0.37, list(BOX)], [-1, 0.4, list(other)]]}
    comp, _ = make_comparer(tmp_path, monkeypatch, boxes)
    monkeypatch.setattr(config, "MATCH_SIM", -1.0)            # 기준값만으로는 못 막는 상황 (모든 점수가 기준 이상)
    comp.compare(f0, f1, "c0")
    k = comp.learn_label("인절미", f1, BOX)
    assert len(comp._reid()[1].negatives) == 1                # 옆 물체가 네거티브로
    r = comp.compare(f1, f1, "c1")
    assert r["after"][k] == 1 and r["unknown"]["after"] == 1
