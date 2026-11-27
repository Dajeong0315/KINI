"""재인식 평가: 등록 사진 1장으로 같은 용기를 다른 사진에서 다시 알아보는지.

입력 폴더
  eval_reid/
    <물체이름>/      같은 용기를 여러 각도·위치·조명에서 찍은 실제 사진 5~10장 (파일 이름 순서의 첫 장으로 등록)
      01.jpg 02.jpg ...
      boxes.json    (선택) {"01.jpg": [x1, y1, x2, y2], ...}  없으면 사진 전체를 용기로 봄 → 용기가 꽉 차게 찍기
    _unknown/        등록하지 않은 다른 용기·물체 사진 (잘못 붙이는 비율 측정용)

비교하는 설정
  (a) legacy      : 예전 방식 (CLIP ViT-B/32 특징 1개, 다른 자리 기준 USER_LABEL_SIM)
  (b) original    : DINOv2, 증강 없이 원본 1장
  (c) gallery     : DINOv2, 증강 갤러리
  (d) gallery+mask: DINOv2, 증강 갤러리 + 배경 지우기(GrabCut)
  (e) clip-gallery: CLIP ViT-B/32, 증강 갤러리 (특징 모델 비교용, --no-clip 으로 끔)

출력: eval_reid/report.md, eval_reid/similarity_<설정>.png
  python tools/eval_reid.py [--data eval_reid]
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import config  # noqa: E402
from reid import augment as aug  # noqa: E402
from reid.embedder import make_embedder  # noqa: E402
from reid.gallery import Gallery  # noqa: E402

TARGET_FAR = 0.05          # 잘못 붙이는 비율 목표


def load_set(data: Path):
    """→ {물체이름: [(파일 이름, 잘라낸 사진 BGR)]}, [(파일 이름, 사진)] (_unknown)"""
    objs, unknown = {}, []
    for d in sorted(p for p in data.iterdir() if p.is_dir()):
        boxes = {}
        if (d / "boxes.json").exists():
            boxes = json.loads((d / "boxes.json").read_text(encoding="utf-8"))
        rows = []
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            img = cv2.imdecode(np.fromfile(str(f), np.uint8), cv2.IMREAD_COLOR)    # 한글 경로
            if img is None:
                continue
            box = boxes.get(f.name)
            rows.append((f.name, img, box))
        if d.name == "_unknown":
            unknown = rows
        elif len(rows) >= 2:
            objs[d.name] = rows
        else:
            print(f"[건너뜀] {d.name}: 사진이 2장 미만")
    return objs, unknown


def crop_of(img, box, bg):
    """box 가 없으면 사진 전체(여백 없이), 있으면 prepare_crop 과 같은 방식."""
    if box is None:
        if bg == "grabcut":
            h, w = img.shape[:2]
            inner = (int(w * 0.05), int(h * 0.05), int(w * 0.95), int(h * 0.95))
            fg = aug.grabcut_mask(img, inner)
            img = img.copy()
            img[~fg] = config.REID_BG_FILL
        return img
    return aug.prepare_crop(img, box, config.REID_CROP_PAD, bg, fill=config.REID_BG_FILL)


def cfg_with(**kw):
    base = {k: getattr(config, k) for k in dir(config) if k.isupper()}
    base.update(kw)
    return SimpleNamespace(**base)


def run_setting(name, embed, embedder_name, objs, unknown, augment=True, bg=None, legacy=False):
    """→ 결과 dict (점수 목록·판정·시간)."""
    inp = lambda c: aug.to_input(c, config.REID_INPUT_SIZE, config.REID_BG_FILL)
    cfg = cfg_with(REID_AUG_COUNT=config.REID_AUG_COUNT if augment else 0)
    with tempfile.TemporaryDirectory() as tmp:
        gal = Gallery(Path(tmp), embed, embedder_name, cfg=cfg)
        t0 = time.perf_counter()
        for obj, rows in objs.items():
            _, img, box = rows[0]
            gal.register(obj, obj, crop_of(img, box, bg))
        reg_ms = (time.perf_counter() - t0) * 1000 / max(1, len(objs))
        queries = [(obj, f, img, box) for obj, rows in objs.items() for f, img, box in rows[1:]]
        queries += [(None, f, img, box) for f, img, box in unknown]
        t0 = time.perf_counter()
        vecs = embed([inp(crop_of(img, box, bg)) for _, _, img, box in queries]) if queries else []
        q_ms = (time.perf_counter() - t0) * 1000 / max(1, len(queries))
        rows = []
        for (obj, f, _, _), v in zip(queries, vecs):
            scores = {item: s for _, item, s in gal.scores(v)}
            if legacy:      # 예전 방식: 특징 1개, 다른 자리 기준 (USER_LABEL_SIM) 이상이면 그 물체
                best = max(scores, key=scores.get) if scores else None
                dec = "match" if best is not None and scores[best] >= config.USER_LABEL_SIM else "unknown"
                d = {"decision": dec, "item_id": best}
            else:
                d = gal.decide(v)
            rows.append({"truth": obj, "file": f, "scores": scores, "decision": d["decision"], "pred": d["item_id"]})
    return {"name": name, "rows": rows, "reg_ms": reg_ms, "query_ms": q_ms}


def summarize(res, threshold=None):
    rows = res["rows"]
    known = [r for r in rows if r["truth"] is not None]
    unk = [r for r in rows if r["truth"] is None]
    if threshold is not None:                       # 다른 기준값으로 다시 판정 (여유값 MATCH_MARGIN 유지)
        rows2 = []
        for r in rows:
            s = sorted(r["scores"].items(), key=lambda kv: -kv[1])
            top = s[0] if s else (None, -1)
            margin = top[1] - s[1][1] if len(s) > 1 else 1.0
            dec = "match" if top[1] >= threshold and margin >= config.MATCH_MARGIN else \
                  "confirm" if top[1] >= threshold else "unknown"
            rows2.append({**r, "decision": dec, "pred": top[0]})
        known = [r for r in rows2 if r["truth"] is not None]
        unk = [r for r in rows2 if r["truth"] is None]
    ok = sum(r["decision"] == "match" and r["pred"] == r["truth"] for r in known)
    wrong = sum(r["decision"] == "match" and r["pred"] != r["truth"] for r in known)
    confirm = sum(r["decision"] == "confirm" for r in known)
    nearest = sum(max(r["scores"], key=r["scores"].get) == r["truth"] for r in known if r["scores"])
    fa_unk = sum(r["decision"] == "match" for r in unk)
    n_k, n_u = len(known), len(unk)
    far_den = n_k + n_u
    return {"n_known": n_k, "n_unknown": n_u, "top1": ok / n_k if n_k else 0.0, "nearest": nearest / n_k if n_k else 0.0,
            "confirm": confirm / n_k if n_k else 0.0, "wrong_known": wrong, "fa_unknown": fa_unk,
            "far": (wrong + fa_unk) / far_den if far_den else 0.0, "far_unknown": fa_unk / n_u if n_u else None}


def recommend(res):
    """잘못 붙이는 비율 ≤ 5% 를 만족하는 가장 낮은 MATCH_SIM (0.50~0.99, 0.01 간격)."""
    for t in np.arange(0.50, 0.995, 0.01):
        s = summarize(res, float(t))
        if s["far"] <= TARGET_FAR and (s["n_unknown"] == 0 or s["far_unknown"] <= TARGET_FAR):
            return round(float(t), 2), s
    return None, None


def plot(res, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for f in ("Malgun Gothic", "AppleGothic", "NanumGothic"):             # 제목의 한글
        if any(x.name == f for x in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = f
            break
    plt.rcParams["axes.unicode_minus"] = False
    same = [r["scores"][r["truth"]] for r in res["rows"] if r["truth"] and r["truth"] in r["scores"]]
    diff = [s for r in res["rows"] for k, s in r["scores"].items() if k != r["truth"]]
    fig, ax = plt.subplots(figsize=(6, 3.2), dpi=110)
    bins = np.linspace(min(same + diff + [0.0]), 1.0, 40)
    ax.hist(diff, bins=bins, alpha=0.6, label=f"different object (n={len(diff)})", color="#d9534f")
    ax.hist(same, bins=bins, alpha=0.6, label=f"same object (n={len(same)})", color="#2b7bba")
    ax.axvline(config.MATCH_SIM, color="k", ls="--", lw=1, label=f"MATCH_SIM {config.MATCH_SIM}")
    ax.set_xlabel("score (top-3 mean cosine)")
    ax.set_title(res["name"])
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default=str(ROOT / "eval_reid"))
    p.add_argument("--no-clip", action="store_true", help="(e) CLIP 갤러리 비교를 끔")
    args = p.parse_args()
    data = Path(args.data)
    objs, unknown = load_set(data)
    if not objs:
        sys.exit(f"{data} 에 물체 폴더(사진 2장 이상)가 없습니다. 맨 위 설명을 보세요.")
    print(f"물체 {len(objs)}개, 시험 사진 {sum(len(v) - 1 for v in objs.values())}장, 미등록 사진 {len(unknown)}장")
    dino = make_embedder("dinov2")
    clip = make_embedder("clip")
    settings = [("(a) legacy (CLIP 1장, 기준 %.2f)" % config.USER_LABEL_SIM, clip, dict(augment=False, legacy=True)),
                ("(b) DINOv2 원본 1장", dino, dict(augment=False)),
                ("(c) DINOv2 증강 갤러리", dino, dict(augment=True)),
                ("(d) DINOv2 증강 갤러리 + 배경 지우기", dino, dict(augment=True, bg="grabcut"))]
    if not args.no_clip:
        settings.append(("(e) CLIP 증강 갤러리", clip, dict(augment=True)))
    results = []
    for name, emb, kw in settings:
        t = time.perf_counter()
        res = run_setting(name, emb, emb.name, objs, unknown, **kw)
        results.append(res)
        print(f"{name}: {time.perf_counter() - t:.1f}s")
    lines = ["# 재인식 평가 결과", "",
             f"- 데이터: `{data}` — 물체 {len(objs)}개 (각 첫 사진 1장으로 등록), 시험 사진 "
             f"{sum(len(v) - 1 for v in objs.values())}장, 미등록 물체 사진 {len(unknown)}장",
             f"- 현재 기준값: MATCH_SIM {config.MATCH_SIM}, MATCH_MARGIN {config.MATCH_MARGIN} (legacy 는 USER_LABEL_SIM {config.USER_LABEL_SIM})",
             f"- 처리 시간 계산 장치: DINOv2 {dino.runtime}, CLIP {clip.runtime} (i7-9700 + 내장 그래픽 UHD 630 기준)", "",
             "| 설정 | 정답률 (top-1, 판정) | 가장 가까운 것 정답 | 확인 필요 | 잘못 붙임 (등록 물체) | 미등록을 등록 물체로 | 잘못 붙인 비율 | 등록 1개 (ms) | 비교 1장 (ms) |",
             "|---|---|---|---|---|---|---|---|---|"]
    recs = []
    for res in results:
        s = summarize(res)
        fu = "-" if s["far_unknown"] is None else f"{s['fa_unknown']}/{s['n_unknown']}"
        lines.append(f"| {res['name']} | {s['top1']:.1%} | {s['nearest']:.1%} | {s['confirm']:.1%} | {s['wrong_known']} | "
                     f"{fu} | {s['far']:.1%} | {res['reg_ms']:.0f} | {res['query_ms']:.0f} |")
        if "legacy" not in res["name"]:
            t, st = recommend(res)
            recs.append((res["name"], t, st))
        slug = res["name"].split(")")[0].strip("(")
        plot(res, data / f"similarity_{slug}.png")
    lines += ["", f"## 추천 MATCH_SIM (잘못 붙인 비율 ≤ {TARGET_FAR:.0%})", "",
              "| 설정 | 추천 MATCH_SIM | 그때 정답률 | 그때 잘못 붙인 비율 |", "|---|---|---|---|"]
    for name, t, st in recs:
        acc = "-" if st is None else f"{st['top1']:.1%}"
        far = "-" if st is None else f"{st['far']:.1%}"
        lines.append(f"| {name} | {t if t is not None else '없음'} | {acc} | {far} |")
    lines += ["", "## 유사도 분포", ""]
    for res in results:
        slug = res["name"].split(")")[0].strip("(")
        lines.append(f"- {res['name']}: `similarity_{slug}.png`")
    lines += ["", "## 물체별 (설정 c)", "", "| 물체 | 시험 | 맞음 | 확인 필요 | 틀림/Unknown |", "|---|---|---|---|---|"]
    c = next(r for r in results if r["name"].startswith("(c)"))
    for obj in objs:
        rs = [r for r in c["rows"] if r["truth"] == obj]
        ok = sum(r["decision"] == "match" and r["pred"] == obj for r in rs)
        cf = sum(r["decision"] == "confirm" for r in rs)
        lines.append(f"| {obj} | {len(rs)} | {ok} | {cf} | {len(rs) - ok - cf} |")
    (data / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
