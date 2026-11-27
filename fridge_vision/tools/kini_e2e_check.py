"""실제 인식 모델 → KINI A 재고 이벤트 API → (임시) SQLite 연결 시험.

  KINI 서버를 임시 DB 로 먼저 띄운 뒤 (KINI_DB_PATH=<임시 파일> python -m app.main 등)
  KINI_LOGIN_NAME / KINI_PASSWORD / KINI_HOUSEHOLD_ID 환경변수를 넣고 실행:

  python tools/kini_e2e_check.py --api http://127.0.0.1:5055 --pair before.jpg after.jpg [--pair ...] --out report.json

각 사진 쌍마다
  1) 실제 인식(vision.YoloComparer.compare) → added/removed
  2) 연결부(KiniInventorySink.record) → A 대기 이벤트
  3) 재고 조회: 접수만으로 바뀌지 않았는지
  4) '사용자 확인' 모사: /api/inventory/normalize 가 matched 인 라벨만 --grams g 로 확정
     (출고는 같은 재료의 남은 양이 충분한 재고 행). 매칭 안 되는 라벨(미등록·OCR 이름)은 대기로 둠
  5) 재고 조회: 확정분만 바뀌었는지
사용자 확인 단계는 C 화면이 아니라 이 스크립트가 모사한 것이다 (보고서에 그렇게 기록).
토큰·비밀번호는 출력하지 않는다. 사진은 서버로 보내지 않는다 (라벨만 전송).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import cv2  # noqa: E402

import config  # noqa: E402
from kini_inventory_sink import KiniApiError, events_from_result, sink_from_env  # noqa: E402


def totals(snapshot):
    t = {}
    for i in snapshot["items"]:
        t[i["name"]] = round(t.get(i["name"], 0) + i["quantity_g"], 3)
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--pair", nargs=2, action="append", required=True, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--grams", type=float, default=100.0, help="사용자가 입력했다고 가정할 중량 (g)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.environ["KINI_API_URL"] = args.api
    config.DETECTION_LOG = None
    import vision

    queue = Path(args.out).with_suffix(".queue.jsonl")
    sink = sink_from_env(queue, log=lambda m: print("  [연결부]", m))
    api = sink.client
    t0 = time.perf_counter()
    comp = vision.YoloComparer()
    meta = {"backend": config.DETECTOR_BACKEND, "model": comp.model_path.name, "prompt_source": comp.prompt_source,
            "clip": (comp._verifier or {}).get("runtime"), "clip_model": config.CLIP_VERIFY_MODEL,
            "conf": config.OPENVOCAB_CONF, "clip_min_prob": config.CLIP_VERIFY_MIN_PROB,
            "init_s": round(time.perf_counter() - t0, 1)}
    print("모델:", meta)
    report = {"meta": meta, "pairs": []}
    for n, (b, a) in enumerate(args.pair, 1):
        before, after = cv2.imread(b), cv2.imread(a)
        cid = f"e2e-{int(time.time())}-{n}"
        t = time.perf_counter()
        result = comp.compare(before, after, cid)
        rec_s = time.perf_counter() - t
        events = events_from_result(result)
        snap0 = api.snapshot()
        sent = sink.record(result)
        snap1 = api.snapshot()
        decisions = []
        for ev in sent:
            norm = api._send("POST", "/api/inventory/normalize",
                             {"name": ev["detected_label"], "quantity": 1, "unit": "개"}, auth=False)["ingredient"]
            row = {"event_id": ev["event_id"], "action": ev["action"], "label": ev["detected_label"],
                   "match": norm["match_status"], "ingredient_id": norm["ingredient_id"]}
            if norm["match_status"] != "matched":
                row["decision"] = "대기 유지 (표준 재료 매칭 안 됨)"
            else:
                body = {"ingredient_id": norm["ingredient_id"], "quantity_g": args.grams}
                if ev["action"] == "stock_out":
                    cands = [i for i in api.snapshot()["items"]
                             if i["ingredient_id"] == norm["ingredient_id"] and i["quantity_g"] >= args.grams]
                    if not cands:
                        row["decision"] = "대기 유지 (남은 재고 부족)"
                        decisions.append(row)
                        continue
                    body["inventory_item_id"] = cands[0]["id"]
                try:
                    r = api.confirm(ev["event_id"], body)
                    row["decision"] = f"확정 {args.grams:g} g → {r['status']}"
                except KiniApiError as e:
                    row["decision"] = f"확정 실패 {e.status}"
            decisions.append(row)
        snap2 = api.snapshot()
        entry = {
            "pair": [Path(b).name, Path(a).name], "recognition_s": round(rec_s, 2),
            "added": [(x["name"], x["quantity"]) for x in result["added"]],
            "removed": [(x["name"], x["quantity"]) for x in result["removed"]],
            "unknown": result.get("unknown"), "needs_confirmation": result["needs_confirmation"],
            "events_built": len(events), "events_registered": [(r["action"], r["detected_label"], r["status"]) for r in sent],
            "stock_unchanged_after_ingest": snap0["items"] == snap1["items"],
            "pending_after_ingest": snap1["pending_event_count"],
            "decisions": decisions,
            "totals_before": totals(snap0), "totals_after": totals(snap2),
            "version": [snap0["inventory_version"], snap2["inventory_version"]],
            "pending_after": snap2["pending_event_count"],
        }
        report["pairs"].append(entry)
        print(json.dumps({k: entry[k] for k in ("pair", "added", "removed", "events_registered", "decisions",
                                                 "stock_unchanged_after_ingest", "version")}, ensure_ascii=False))
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", args.out)


if __name__ == "__main__":
    main()
