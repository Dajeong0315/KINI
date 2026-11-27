"""위치·변화 기반 유지 (고정 카메라의 before/after 비교용).

트랙 = 지금 냉장고 안에 있다고 보는 물체 하나. config.REID_STATE_FILE(state/tracks.json)에 저장해서 다시 켜도 유지.

after 사진의 탐지 결과마다 (점수 순이 아니라 규칙 순으로 모든 상자를 한 번씩)
  ① 그대로     : 상자 안 변화 비율 < UNCHANGED_RATIO 이고 기존 트랙과 IoU ≥ KEEP_IOU → 재판정 없이 트랙 이름
  ② 살짝 이동  : 같은 칸 + (IoU ≥ NEAR_IOU 또는 중심 거리 ≤ 대각선 × NEAR_DIST) + 생김새 유사도 ≥ NEAR_SIM → 트랙 이름
  ③ 새 물체    : 갤러리 전체와 비교 → 맞으면 등록 이름 / 애매하면 확인 필요(Unknown + 후보) / 아니면 탐지·OCR 이름
                 맞은 갤러리의 트랙이 같은 촬영에서 (변화가 있는 자리에서) 사라졌으면 '위치 이동'
짝 없는 트랙: 그 자리에 변화가 있으면 '제거', 변화가 없으면 탐지가 잠깐 놓친 것 → 유지 (kept)

이 모듈은 모델 없이 동작합니다: 생김새 비교는 호출하는 쪽이 넘겨 주는 함수(embed, gallery)로 합니다.
"""
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

# 이름 출처의 우선순위: 낮은 쪽이 높은 쪽 이름을 덮어쓰지 않음 (사용자 이름 > 포장 글씨 > 탐지 품목 > Unknown)
PRIORITY = {"user": 3, "ocr": 2, "detector": 1, "unknown": 0}
UNKNOWN_LABEL = "unknown"


@dataclass
class Track:
    track_id: int
    label: str                       # item_id 또는 "unknown"
    label_source: str                # detector / ocr / user / unknown
    bbox: list
    zone: str = ""
    gallery_id: str | None = None
    last_capture_id: str = ""
    score: float = 0.0
    emb: list | None = None          # 마지막으로 본 모습의 특징값 (② 비교용, 갤러리가 없는 트랙도)
    mask: list | None = None         # 분할 마스크(있으면). 지금 탐지 결과에는 없어서 비어 있음
    extra: dict = field(default_factory=dict)


class TrackStore:
    def __init__(self, path):
        self.path = Path(path)
        self.tracks: list[Track] = []
        self.next_id = 1
        self.frame_size = None
        self.load()

    def load(self):
        self.tracks, self.next_id, self.frame_size = [], 1, None
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        self.tracks = [Track(**t) for t in d.get("tracks", [])]
        self.next_id = int(d.get("next_id", 1))
        self.frame_size = d.get("frame_size")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"next_id": self.next_id, "frame_size": self.frame_size,
                                   "tracks": [asdict(t) for t in self.tracks]}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self):
        self.tracks, self.next_id, self.frame_size = [], 1, None
        self.path.unlink(missing_ok=True)

    def new(self, **kw) -> Track:
        t = Track(track_id=self.next_id, **kw)
        self.next_id += 1
        self.tracks.append(t)
        return t


# ---------------------------------------------------------------------------
# 변화 영역
# ---------------------------------------------------------------------------
def change_mask(before, after, diff=25, kernel=5):
    """before/after → 변화 마스크 (uint8 0/1).
    회색조 → (사진 평균 밝기를 빼서 자동 노출 변화 보정) → 차이 → 임계값 → 모폴로지 열기(작은 잡음 제거)."""
    a = cv2.GaussianBlur(cv2.cvtColor(before, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float32)
    b = cv2.GaussianBlur(cv2.cvtColor(after, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float32)
    if a.shape != b.shape:
        return np.ones(b.shape, np.uint8)                   # 크기가 다르면 전부 바뀐 것으로
    d = np.abs((a - a.mean()) - (b - b.mean()))
    m = (d > diff).astype(np.uint8)
    if kernel and kernel > 1:
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((kernel, kernel), np.uint8))
    return m


def box_change_ratio(mask, xyxy) -> float:
    """상자 안 변화 픽셀 비율 (0~1). 상자가 화면 밖이면 1."""
    h, w = mask.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in xyxy)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return 1.0
    return float(mask[y1:y2, x1:x2].mean())


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def center_dist_ratio(a, b) -> float:
    """두 상자 중심 거리 / b 상자 대각선."""
    ca = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
    cb = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    diag = math.hypot(b[2] - b[0], b[3] - b[1]) or 1.0
    return math.hypot(ca[0] - cb[0], ca[1] - cb[1]) / diag


# ---------------------------------------------------------------------------
# 갱신 규칙
# ---------------------------------------------------------------------------
class Tracker:
    """dets: [{"label", "source", "bbox", "score", "zone", "gallery_id"(선택)}, ...]
    embed(i) → i 번째 det 의 특징값 (없으면 None). 호출하는 쪽이 캐시.
    gallery: reid.gallery.Gallery 와 같은 메서드(sim, decide, item_id, add_real)를 가진 객체 또는 None."""

    def __init__(self, store: TrackStore, cfg, gallery=None):
        self.store = store
        self.cfg = cfg
        self.gallery = gallery

    # -- 유사도 ---------------------------------------------------------
    def _sim(self, track, vec):
        if vec is None:
            return -1.0
        s = -1.0
        if track.emb is not None:
            s = float(np.dot(np.asarray(track.emb, np.float32), vec))
        if self.gallery is not None and track.gallery_id:
            s = max(s, self.gallery.sim(track.gallery_id, vec))
        return s

    @staticmethod
    def _inherit(track, det):
        """트랙 이름을 이어받을지, 더 믿을 만한 새 이름으로 트랙을 고칠지."""
        if PRIORITY.get(det["source"], 0) > PRIORITY.get(track.label_source, 0):
            track.label, track.label_source = det["label"], det["source"]
            track.gallery_id = det.get("gallery_id") or (track.gallery_id if det["source"] == "user" else None)
            return "upgraded"
        return "inherited"

    def seed(self, dets, capture_id, frame_size, embed=None):
        """트랙이 하나도 없을 때(처음·초기화 후) before 사진의 탐지 결과로 시작."""
        self.store.frame_size = list(frame_size)
        for i, d in enumerate(dets):
            vec = embed(i) if embed else None
            self.store.new(label=d["label"], label_source=d["source"], bbox=list(d["bbox"]), zone=d.get("zone", ""),
                           gallery_id=d.get("gallery_id"), last_capture_id=capture_id, score=float(d.get("score", 0)),
                           emb=None if vec is None else [round(float(v), 5) for v in vec])

    def relabel(self, dets):
        """before 사진의 상자에 트랙 이름을 붙임 (before ≈ 지난번 after 라 같은 자리 = 같은 물체).
        돌려주는 값: det 별 (track_id 또는 None, 결과 label, source, gallery_id)."""
        out, used = [], set()
        for d in dets:
            best = max((t for t in self.store.tracks if t.track_id not in used),
                       key=lambda t: iou(d["bbox"], t.bbox), default=None)
            if best is None or iou(d["bbox"], best.bbox) < self.cfg.KEEP_IOU:
                out.append((None, d["label"], d["source"], d.get("gallery_id")))
                continue
            used.add(best.track_id)
            if PRIORITY.get(d["source"], 0) > PRIORITY.get(best.label_source, 0):
                out.append((best.track_id, d["label"], d["source"], d.get("gallery_id")))
            else:
                out.append((best.track_id, best.label, best.label_source, best.gallery_id))
        return out

    def update(self, dets, mask, capture_id, embed=None, zone_of=None, allowed=None):
        """after 사진의 탐지 결과로 트랙 갱신. mask: change_mask 결과.
        돌려주는 값: {"dets": [det 별 결과], "removed": [...], "moved": [...], "kept": [Track...]}
          det 별 결과: {"track_id", "label", "source", "gallery_id", "rule", "hint"(확인 필요일 때), "sim"}"""
        cfg = self.cfg
        n = len(dets)
        res = [None] * n
        free = {t.track_id: t for t in self.store.tracks}        # 아직 짝이 없는 트랙
        cache = {}

        def vec(i):
            if embed is None:
                return None
            if i not in cache:
                cache[i] = embed(i)
            return cache[i]

        ratios = [box_change_ratio(mask, d["bbox"]) for d in dets]

        def take(i, t, rule, sim=None):
            d = dets[i]
            how = self._inherit(t, d)
            t.bbox, t.last_capture_id = list(d["bbox"]), capture_id
            t.score = max(t.score, float(d.get("score", 0))) if how == "inherited" else float(d.get("score", 0))
            if d.get("zone"):
                t.zone = d["zone"]
            del free[t.track_id]
            res[i] = {"track_id": t.track_id, "label": t.label, "source": t.label_source,
                      "gallery_id": t.gallery_id, "rule": rule, "how": how, "sim": sim,
                      "change": round(ratios[i], 4)}
            return t

        # ① 그대로: 변화 없는 상자 ↔ IoU 높은 트랙 (IoU 큰 짝부터)
        pairs = sorted(((iou(d["bbox"], t.bbox), i, t.track_id) for i, d in enumerate(dets)
                        if ratios[i] < cfg.UNCHANGED_RATIO for t in self.store.tracks), reverse=True)
        for ov, i, tid in pairs:
            if ov < cfg.KEEP_IOU:
                break
            if res[i] is None and tid in free:
                take(i, free[tid], "unchanged")

        # ② 살짝 돌아감·이동: 같은 칸 + 가까움 + 생김새 비슷 (유사도 높은 짝부터)
        cands = []
        for i, d in enumerate(dets):
            if res[i] is not None:
                continue
            for t in free.values():
                if d.get("zone", "") != t.zone:
                    continue
                if iou(d["bbox"], t.bbox) >= cfg.NEAR_IOU or center_dist_ratio(d["bbox"], t.bbox) <= cfg.NEAR_DIST:
                    s = self._sim(t, vec(i))
                    if s >= cfg.NEAR_SIM:
                        cands.append((s, i, t.track_id))
        for s, i, tid in sorted(cands, reverse=True):
            if res[i] is None and tid in free:
                t = take(i, free[tid], "near", round(s, 4))
                v = vec(i)
                if v is not None:
                    t.emb = [round(float(x), 5) for x in v]
                    if t.gallery_id and self.gallery is not None:
                        res[i]["added_real"] = bool(self.gallery.add_real(t.gallery_id, v))

        # ③ 새 물체 또는 멀리 이동: 갤러리 전체와 비교
        moved = []
        for i, d in enumerate(dets):
            if res[i] is not None:
                continue
            v = vec(i)
            dec = None
            label, source, gid, hint = d["label"], d["source"], d.get("gallery_id"), None
            if gid is None and self.gallery is not None and v is not None and d["source"] != "user":
                dec = self.gallery.decide(v, allowed)
                if dec["decision"] == "match":
                    label, source, gid = dec["item_id"], "user", dec["gallery_id"]
                elif dec["decision"] == "confirm" and d["source"] in ("unknown", "detector"):
                    label, source, hint = UNKNOWN_LABEL, "unknown", dec["item_id"]
            rule = "new"
            # 위치 이동: 같은 갤러리의 트랙이 아직 짝이 없고, 원래 자리에 변화가 있음(= 거기서 사라짐)
            t = None
            if gid:
                t = next((x for x in free.values() if x.gallery_id == gid
                          and box_change_ratio(mask, x.bbox) >= cfg.UNCHANGED_RATIO), None)
            if t is not None:
                old_zone, old_box = t.zone, list(t.bbox)
                t.label, t.label_source = label, source
                del free[t.track_id]
                t.bbox, t.zone, t.last_capture_id = list(d["bbox"]), d.get("zone", ""), capture_id
                t.score = float(d.get("score", 0))
                rule = "moved"
                moved.append({"track_id": t.track_id, "label": label, "from_zone": old_zone, "to_zone": t.zone,
                              "from_box": old_box, "to_box": list(d["bbox"])})
            else:
                t = self.store.new(label=label, label_source=source, bbox=list(d["bbox"]), zone=d.get("zone", ""),
                                   gallery_id=gid, last_capture_id=capture_id, score=float(d.get("score", 0)))
            t.gallery_id = gid
            if v is not None:
                t.emb = [round(float(x), 5) for x in v]
            res[i] = {"track_id": t.track_id, "label": label, "source": source, "gallery_id": gid,
                      "rule": rule, "sim": None if dec is None else dec["score"],
                      "change": round(ratios[i], 4), "decision": None if dec is None else dec}
            if hint:
                res[i]["hint"] = hint

        # 짝 없는 트랙: 변화가 있는 자리 → 제거 / 변화 없음 → 탐지가 놓친 것, 유지
        removed, kept = [], []
        for t in list(free.values()):
            r = box_change_ratio(mask, t.bbox)
            if r >= cfg.UNCHANGED_RATIO:
                removed.append({**asdict(t), "change": round(r, 4)})
                self.store.tracks.remove(t)
            else:
                kept.append(t)
        return {"dets": res, "removed": removed, "moved": moved, "kept": kept}
