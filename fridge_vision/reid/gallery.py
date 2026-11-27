"""증강 임베딩 갤러리: 등록한 물체마다 특징값 여러 개를 저장하고, 새 사진과 비교해 판정.

저장 (config.REID_GALLERY_DIR)
  <gallery_id>.npz        vecs (N, D) float32
  <gallery_id>.json       이름·품목 id·등록 시각·특징 모델·각 벡터의 종류(original / aug:<변형> / real)
  <gallery_id>/original.jpg   등록에 쓴 잘라낸 사진 (특징 모델을 바꾸면 이것으로 다시 만듦)
  <gallery_id>/real/NN.jpg    같은 물체로 확인돼 더해진 실제 사진
  <gallery_id>/preview/NN_<변형>.jpg  증강 사진 몇 장 (눈으로 확인용)

한계: 증강은 2D 변형이라 실제로 돌렸을 때 보이는 옆면·뒷면은 만들 수 없음 → add_real 로 실제 사진을 계속 더함.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from . import augment as aug


def _cfg():
    import config
    return config


class Gallery:
    def __init__(self, root, embed, embedder_name, cfg=None):
        """embed: 정사각형 입력 사진 목록 → (N, D) L2 정규화 벡터. 테스트에서는 가짜 함수."""
        self.root = Path(root)
        self.embed = embed
        self.embedder_name = embedder_name
        self.cfg = cfg or _cfg()
        self.entries = {}                  # gallery_id → {"meta": dict, "vecs": ndarray}
        self.negatives = None              # '이 물체가 아님' 특징값 (등록할 때 같은 사진에 있던 다른 물체들)
        self.neg_src = []                  # 각 네거티브를 더한 gallery_id
        self.load()

    # ------------------------------------------------------------------
    NEG_FILE = "_negatives.npz"

    def load(self):
        self.entries = {}
        self.negatives, self.neg_src = None, []
        if not self.root.is_dir():
            return
        neg = self.root / self.NEG_FILE
        if neg.exists():
            try:
                with np.load(neg, allow_pickle=False) as d:
                    if str(d["embedder"]) == self.embedder_name:     # 특징 모델이 바뀌면 버림 (사진이 없어 다시 못 만듦)
                        self.negatives, self.neg_src = d["vecs"].astype(np.float32), d["src"].tolist()
            except Exception as e:
                print(f"[경고] {self.NEG_FILE} 을 읽지 못했습니다: {e}")
        for meta_path in sorted(self.root.glob("*.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                gid = meta["gallery_id"]
                with np.load(self.root / f"{gid}.npz") as d:
                    vecs = d["vecs"].astype(np.float32)
            except Exception as e:                      # 손상된 파일은 건너뜀
                print(f"[경고] 갤러리 {meta_path.name} 을 읽지 못했습니다: {e}")
                continue
            self.entries[gid] = {"meta": meta, "vecs": vecs}
            if meta.get("embedder") != self.embedder_name:
                self._rebuild(gid)                      # 특징 모델이 바뀜 → 저장된 사진으로 다시 계산

    def _rebuild(self, gid):
        e = self.entries[gid]
        folder = self.root / gid
        orig = cv2.imread(str(folder / "original.jpg"))
        if orig is None:
            print(f"[경고] 갤러리 {gid}: 특징 모델이 달라({e['meta'].get('embedder')}) 원본 사진 없이는 쓸 수 없어 건너뜁니다")
            del self.entries[gid]
            return
        reals = [cv2.imread(str(p)) for p in sorted((folder / "real").glob("*.jpg"))]
        reals = [r for r in reals if r is not None]
        vecs, kinds = self._make_vectors(orig)
        if reals:
            vecs = np.concatenate([vecs, self.embed([aug.to_input(r, self.cfg.REID_INPUT_SIZE, self.cfg.REID_BG_FILL)
                                                     for r in reals])])
            kinds += ["real"] * len(reals)
        e["vecs"] = vecs
        e["meta"].update(embedder=self.embedder_name, dim=int(vecs.shape[1]), kinds=kinds)
        self._save(gid)

    def _save(self, gid):
        self.root.mkdir(parents=True, exist_ok=True)
        e = self.entries[gid]
        np.savez_compressed(self.root / f"{gid}.npz", vecs=e["vecs"].astype(np.float32))
        (self.root / f"{gid}.json").write_text(json.dumps(e["meta"], ensure_ascii=False, indent=1), encoding="utf-8")

    # ------------------------------------------------------------------
    def _inputs(self, imgs):
        return [aug.to_input(im, self.cfg.REID_INPUT_SIZE, self.cfg.REID_BG_FILL) for im in imgs]

    def _make_vectors(self, crop, previews=None):
        """원본 1장 + 증강본 → (벡터, 종류 목록)."""
        augs = aug.augment(crop, self.cfg.REID_AUG, self.cfg.REID_AUG_COUNT, self.cfg.REID_AUG_SEED,
                           self.cfg.REID_BG_FILL)
        if previews is not None:
            previews.extend(augs[:self.cfg.REID_PREVIEW_N])
        vecs = self.embed(self._inputs([crop] + [a for a, _ in augs]))
        return vecs, ["original"] + [f"aug:{d}" for _, d in augs]

    def register(self, item_id, name, crop, source=None):
        """사용자가 이름을 붙인 물체 등록. crop = augment.prepare_crop 결과.
        돌려주는 값: (gallery_id, 경고 문장 또는 None). 경고: 이미 등록된 다른 물체와 너무 비슷함."""
        previews = []
        vecs, kinds = self._make_vectors(crop, previews)
        warning, dup = None, None
        for gid, other_item, s in self.scores(vecs[0], per_gallery=True):
            if other_item != item_id and s >= self.cfg.DUP_SIM:
                dup = (self.entries[gid]["meta"]["name"], s)
                break
        if dup:
            warning = f"이미 등록된 '{dup[0]}'과(와) 구분이 어렵습니다 (유사도 {dup[1]:.2f}). 라벨 스티커를 권장합니다."
        self._drop_negatives_like(vecs[0])
        gid = f"{re.sub(r'[^0-9A-Za-z_]', '', item_id) or 'item'}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        folder = self.root / gid
        (folder / "preview").mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(folder / "original.jpg"), crop)
        for i, (im, d) in enumerate(previews):
            cv2.imwrite(str(folder / "preview" / f"{i:02d}_{re.sub(r'[^0-9A-Za-z+_.-]', '_', d)}.jpg"),
                        aug.to_input(im, self.cfg.REID_INPUT_SIZE, self.cfg.REID_BG_FILL))
        self.entries[gid] = {"vecs": vecs, "meta": {
            "gallery_id": gid, "item_id": item_id, "name": name,
            "created_at": datetime.now().isoformat(timespec="seconds"), "source": source,
            "original": str(folder / "original.jpg"), "embedder": self.embedder_name,
            "dim": int(vecs.shape[1]), "kinds": kinds, "duplicate_warning": warning}}
        self._save(gid)
        return gid, warning

    def add_negatives(self, vecs, src) -> int:
        """'이 물체가 아님' 특징값을 더함 (등록할 때 같은 사진의 다른 물체들). 등록 물체 자신과 너무 비슷한 것은 뺌.
        돌려주는 값: 더한 개수."""
        vecs = np.atleast_2d(np.asarray(vecs, np.float32))
        if vecs.size == 0:
            return 0
        pos = np.concatenate([e["vecs"][:1] for e in self.entries.values()]) if self.entries else None
        if pos is not None and len(vecs):
            vecs = vecs[(vecs @ pos.T).max(1) < self.cfg.REID_NEG_DROP]
        if not len(vecs):
            return 0
        self.negatives = vecs if self.negatives is None else np.concatenate([self.negatives, vecs])
        self.neg_src += [src] * len(vecs)
        extra = len(self.neg_src) - self.cfg.REID_NEG_MAX
        if extra > 0:                                   # 오래된 것부터 지움
            self.negatives, self.neg_src = self.negatives[extra:], self.neg_src[extra:]
        self._save_negatives()
        return len(vecs)

    def _drop_negatives_like(self, vec):
        """새로 등록하는 물체와 비슷한 네거티브는 지움 (예전에 '다른 물체'로 저장됐던 그 물체를 지금 등록하는 경우)."""
        if self.negatives is None or vec is None:
            return
        keep = (self.negatives @ np.asarray(vec, np.float32)) < self.cfg.REID_NEG_DROP
        if not keep.all():
            self.negatives = self.negatives[keep]
            self.neg_src = [s for s, k in zip(self.neg_src, keep) if k]
            self._save_negatives()

    def _save_negatives(self):
        self.root.mkdir(parents=True, exist_ok=True)
        vecs = self.negatives if self.negatives is not None else np.zeros((0, 1), np.float32)
        np.savez_compressed(self.root / self.NEG_FILE, vecs=vecs, src=np.asarray(self.neg_src, dtype=str),
                            embedder=np.asarray(self.embedder_name))

    def add_real(self, gid, vec, crop=None) -> bool:
        """같은 물체로 확인된 실제 사진의 특징값을 더함. 이미 거의 같은 벡터가 있으면 더하지 않음.
        GALLERY_MAX 를 넘으면 가장 오래된 증강본부터 지움 (원본·실제 사진은 남김. 증강본이 없으면 더하지 않음)."""
        e = self.entries.get(gid)
        if e is None or vec is None:
            return False
        vec = np.asarray(vec, np.float32)
        if float((e["vecs"] @ vec).max()) >= self.cfg.REID_REAL_DUP:
            return False
        kinds = e["meta"]["kinds"]
        if len(kinds) >= self.cfg.GALLERY_MAX:
            old = next((i for i, k in enumerate(kinds) if k.startswith("aug:")), None)
            if old is None:
                return False
            e["vecs"] = np.delete(e["vecs"], old, axis=0)
            del kinds[old]
        e["vecs"] = np.concatenate([e["vecs"], vec[None]])
        kinds.append("real")
        if crop is not None:
            real = self.root / gid / "real"
            real.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(real / f"{kinds.count('real'):03d}.jpg"), crop)
        self._save(gid)
        return True

    # ------------------------------------------------------------------
    def name(self, gid):
        return self.entries[gid]["meta"]["name"]

    def item_id(self, gid):
        return self.entries[gid]["meta"]["item_id"]

    def sim(self, gid, vec) -> float:
        """그 물체 갤러리와의 점수 = 유사도 상위 REID_TOPK 개 평균."""
        e = self.entries.get(gid)
        if e is None or vec is None:
            return -1.0
        s = np.sort(e["vecs"] @ np.asarray(vec, np.float32))[::-1]
        return float(s[:self.cfg.REID_TOPK].mean())

    def scores(self, vec, per_gallery=False):
        """점수 높은 순. per_gallery=False: 품목(item_id)별 최고점 [(gallery_id, item_id, 점수)]
        (같은 품목을 두 번 등록한 경우 서로 경쟁하지 않게)."""
        rows = sorted(((gid, e["meta"]["item_id"], self.sim(gid, vec)) for gid, e in self.entries.items()),
                      key=lambda r: -r[2])
        if per_gallery:
            return rows
        best = {}
        for r in rows:
            best.setdefault(r[1], r)
        return list(best.values())

    def decide(self, vec, allowed=None):
        """판정. allowed: 쓸 수 있는 item_id 집합 (없으면 전부).
        '이 물체가 아님'(네거티브) 중 하나와 등록 물체보다 더 비슷하면 그 물체 후보에서 뺌.
        돌려주는 값: {"decision": "match"|"confirm"|"unknown", "gallery_id", "item_id", "score", "second": [...],
                     "negative": 네거티브 최고 유사도}"""
        neg = None
        if self.negatives is not None and len(self.negatives) and vec is not None:
            neg = round(float((self.negatives @ np.asarray(vec, np.float32)).max()), 4)
        rows = [r for r in self.scores(vec) if (allowed is None or r[1] in allowed) and (neg is None or r[2] > neg)]
        if not rows:
            return {"decision": "unknown", "gallery_id": None, "item_id": None, "score": None, "second": None,
                    "negative": neg}
        top = rows[0]
        second = rows[1] if len(rows) > 1 else None
        margin = top[2] - second[2] if second else 1.0
        if top[2] >= self.cfg.MATCH_SIM:
            decision = "match" if margin >= self.cfg.MATCH_MARGIN else "confirm"
        else:
            decision = "unknown"
        return {"decision": decision, "gallery_id": top[0], "item_id": top[1], "score": round(top[2], 4),
                "second": None if second is None else [second[1], round(second[2], 4)], "negative": neg}
