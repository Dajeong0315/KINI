"""예전 방식(kaggle) ↔ 새 방식(yoloe_openvocab) 인식 결과 비교.

  python tools/compare_detectors.py samples                       # 폴더의 사진 전체
  python tools/compare_detectors.py samples --conf 0.3            # 새 방식 기준값 바꿔서
  python tools/compare_detectors.py samples --model yoloe-26m-seg.pt

결과
  reports/compare.md                        사진별 라벨·개수·신뢰도·OCR 원문 표
  reports/images/<사진>_<방식>.jpg           YOLOE 원본 상자 그림 (ultralytics results[0].plot(), 검색어 이름으로 표시)

--conf 는 새 방식의 OPENVOCAB_CONF·PACKAGE_CONF 를 바꿉니다 (예전 방식은 config 값 그대로).
두 방식은 품목 목록이 달라서 방식마다 따로 프로세스를 띄워 실행합니다.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
BACKENDS = [("kaggle", "예전 방식 (kaggle)"), ("yoloe_openvocab", "새 방식 (yoloe_openvocab)")]


def worker(backend, images, out_json, img_dir, conf, model) -> None:
    """한 방식으로 사진 전체를 인식해서 JSON 으로 저장 (별도 프로세스에서 실행)."""
    os.environ["DETECTOR_BACKEND"] = backend
    sys.path.insert(0, str(BASE_DIR))
    import cv2
    import config

    config.DETECTION_LOG = None
    if backend == "yoloe_openvocab":
        if conf is not None:
            config.OPENVOCAB_CONF = config.PACKAGE_CONF = conf
        if model:
            config.OPENVOCAB_MODEL = BASE_DIR / "models" / model
    import vision

    t0 = time.perf_counter()
    comp = vision.YoloComparer()
    init_s = time.perf_counter() - t0
    logs = []
    comp._log = logs.append                    # 검색어 원문·OCR 원문 받기
    rows = []
    for p in images:
        frame = cv2.imread(str(p))
        if frame is None:
            rows.append({"image": str(p), "error": "사진을 읽지 못함"})
            continue
        logs.clear()
        t = time.perf_counter()
        boxes, hand, _ = comp._detect(frame)
        ms = (time.perf_counter() - t) * 1000
        dets = []
        for b in boxes:
            k = vision.ITEM_IDS[b[0]] if b[0] != vision.UNKNOWN else "Unknown"
            dets.append({"label": k, "name": config.ITEMS[k]["name"] if k in config.ITEMS else "Unknown",
                         "conf": round(b[1], 3), "tags": [str(x) for x in b[3:]]})
        ocr = [{"prompt": i["prompt"], "label": i["label"], "name": i["ocr_name"],
                "text": [f"{o['text']}({o['conf']:.2f})" for o in i["ocr"]]} for e in logs for i in e.get("packages", [])]
        # 원본 상자 그림 (같은 기준값으로 한 번 더 예측해서 plot)
        lo = min(config.OPENVOCAB_CONF, config.PACKAGE_CONF) if backend == "yoloe_openvocab" else config.YOLO_CONF
        r = comp.model.predict(frame, conf=lo, agnostic_nms=True, verbose=False)[0]
        img_path = Path(img_dir) / f"{p.stem}_{backend}.jpg"
        cv2.imwrite(str(img_path), r.plot())
        rows.append({"image": str(p), "ms": round(ms), "hand": hand, "dets": dets, "ocr": ocr, "plot": img_path.name})
    json.dump({"backend": backend, "model": comp.model_path.name, "prompt_source": comp.prompt_source,
               "init_s": round(init_s, 1), "rows": rows}, open(out_json, "w", encoding="utf-8"), ensure_ascii=False)


def summarize_dets(dets) -> str:
    if not dets:
        return "–"
    g = defaultdict(list)
    for d in dets:
        tag = " ✎" if any(t.startswith("user") for t in d["tags"]) else ""
        g[f"{d['name']} `{d['label']}`{tag}"].append(d["conf"])
    return "<br>".join(f"{k} ×{len(v)} ({', '.join(f'{c:.2f}' for c in sorted(v, reverse=True))})"
                       for k, v in sorted(g.items(), key=lambda kv: -max(kv[1])))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", help="사진 폴더")
    p.add_argument("--conf", type=float, help="새 방식의 신뢰도 기준값 (OPENVOCAB_CONF·PACKAGE_CONF)")
    p.add_argument("--model", help="새 방식 체크포인트 (models/ 아래 파일 이름)")
    p.add_argument("--out", default=str(BASE_DIR / "reports"), help="결과 폴더 (기본 reports/)")
    p.add_argument("--worker", help=argparse.SUPPRESS)
    p.add_argument("--json", help=argparse.SUPPRESS)
    args = p.parse_args()

    folder = Path(args.folder).resolve()
    images = sorted(x for x in folder.iterdir() if x.suffix.lower() in EXTS)
    out = Path(args.out).resolve()
    img_dir = out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args.worker, images, args.json, img_dir, args.conf, args.model)
        return
    if not images:
        sys.exit(f"사진이 없습니다: {folder}")

    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for backend, label in BACKENDS:
            print(f"[{label}] 사진 {len(images)}장 인식 중…", flush=True)
            js = Path(tmp) / f"{backend}.json"
            cmd = [sys.executable, str(Path(__file__).resolve()), str(folder), "--worker", backend, "--json", str(js),
                   "--out", str(out)]
            if args.conf is not None:
                cmd += ["--conf", str(args.conf)]
            if args.model:
                cmd += ["--model", args.model]
            subprocess.run(cmd, check=True, cwd=str(BASE_DIR))
            results[backend] = json.loads(js.read_text(encoding="utf-8"))

    old, new = results["kaggle"], results["yoloe_openvocab"]
    avg = lambda r: sum(x.get("ms", 0) for x in r["rows"]) / max(1, len(r["rows"]))
    lines = [
        "# 인식 방식 비교", "",
        f"- 사진 폴더: `{folder}` ({len(images)}장)",
        f"- 예전 방식: `{old['model']}` · 평균 {avg(old):.0f} ms/장",
        f"- 새 방식: `{new['model']}` (검색어 임베딩: {new['prompt_source']}) · 평균 {avg(new):.0f} ms/장 (OCR 포함)"
        + (f" · 기준값 --conf {args.conf}" if args.conf is not None else ""),
        "- 표기: 이름 `라벨` ×개수 (신뢰도). ✎ = 사용자 이름표로 바뀐 것. 그림은 `images/` (YOLOE 원본 상자, 검색어 이름)",
        "",
        "| 사진 | 예전 방식 (kaggle) | 새 방식 (yoloe_openvocab) | 포장 식품 OCR (검색어 → 라벨 / 원문) |",
        "|---|---|---|---|",
    ]
    for ro, rn in zip(old["rows"], new["rows"]):
        name = Path(ro["image"]).name
        ocr = "<br>".join(f"`{o['prompt']}` → **{o['name'] or '–'}** `{o['label']}`<br>　{' / '.join(o['text']) or '(글자 없음)'}"
                          for o in rn.get("ocr", [])) or "–"
        lines.append(f"| {name}<br>[예전](images/{ro.get('plot', '')}) · [새](images/{rn.get('plot', '')}) "
                     f"| {summarize_dets(ro.get('dets', []))} | {summarize_dets(rn.get('dets', []))} | {ocr} |")
    (out / "compare.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"저장: {out / 'compare.md'}  /  그림: {img_dir}")


if __name__ == "__main__":
    main()
