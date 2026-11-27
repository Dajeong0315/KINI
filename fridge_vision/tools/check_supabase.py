"""Supabase 고내 재고 DB 점검. 결과는 표로 출력 (키 값은 출력하지 않음).

  python tools/check_supabase.py            읽기 점검 (Publishable 키만 사용, 언제든 실행 가능, DB 를 바꾸지 않음)
  python tools/check_supabase.py --write    + 쓰기 점검 (Secret 키, 실제 DB 데이터를 바꿈 → 실행 전에 확인을 받음)

읽기 점검
  1) food_item 행 수와 food_id ↔ 인식 라벨(config.ITEMS) 비교
  2) current_stock 을 읽을 수 있는지와 현재 개수
  3) Publishable 키로 쓰기·함수 실행을 시도하면 거부되는지 (거부돼야 정상) — 거부되지 않아도 데이터가 바뀌지 않는
     시도만 함: 존재하지 않는 food_id 로 이벤트 넣기(외래키로도 실패), 없는 이벤트 번호(-1) 거절
쓰기 점검 (--write)
  reset_demo → 가짜 capture + 이벤트(우유 +1, 브로콜리 −1) → 확인 → current_stock 확인
  → 닭고기 −3 → stock mismatch 처리 확인 → reset_demo 로 원상복구
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from db.repository import DbError, Repository, classify  # noqa: E402
from db.supabase_client import Clients, SupabaseConfigError, make_reader, make_writer  # noqa: E402

ROWS = []


def row(item, ok, detail=""):
    ROWS.append((item, "정상" if ok is True else "실패" if ok is False else str(ok), detail))


def print_table(title):
    print(f"\n## {title}\n")
    w = max(len(r[0]) for r in ROWS) if ROWS else 10
    print(f"| {'항목':<{w}} | 결과 | 내용 |")
    print(f"|{'-' * (w + 2)}|------|------|")
    for item, res, detail in ROWS:
        print(f"| {item:<{w}} | {res} | {detail} |")
    ROWS.clear()


def read_checks(reader) -> bool:
    ok_all = True
    # 1) food_item
    try:
        items = reader.table("food_item").select("food_id,food_name,demo_init_qty").order("food_id").execute().data
        ids = {r["food_id"] for r in items}
        labels = [k for k in config.ITEMS if k not in config.SUPABASE_EXCLUDE_LABELS]
        try:                    # 사용자가 Unknown 에 붙인 이름 (user_*) — vision 을 불러오지 않고 파일에서
            import json
            labels += [k for k in json.loads(config.USER_LABELS_FILE.read_text(encoding="utf-8")).get("items", {})
                       if k not in labels]
        except (OSError, ValueError):
            pass
        missing = [k for k in labels if k not in ids]
        extra = sorted(ids - set(config.ITEMS))
        row("food_item 행 수", True, f"{len(items)}행 (설계 문서 기준 6행 + 연동용 추가 품목)")
        row("인식 라벨 중 DB 에 없는 것", "참고",
            f"{len(missing)}개 — 나오면 food_item 에 자동 추가 (shelf_days {config.SUPABASE_DEFAULT_SHELF_DAYS}): "
            + (", ".join(missing[:12]) + (" …" if len(missing) > 12 else "") if missing else "없음"))
        row("DB 에만 있는 food_id", "참고", ", ".join(extra[:12]) + (" …" if len(extra) > 12 else "") if extra else "없음")
        demo = {r["food_id"]: r["demo_init_qty"] for r in items if r["demo_init_qty"]}
        row("데모 초기 재고 (demo_init_qty)", True, ", ".join(f"{k} {v}" for k, v in demo.items()) or "없음")
    except Exception as e:  # noqa: BLE001
        ok_all = False
        row("food_item 읽기", False, str(classify(e)))
    # 2) current_stock
    try:
        stock = reader.table("current_stock").select("food_id,food_name,qty,nearest_expire").order("food_id").execute().data
        have = [f"{r['food_name']}({r['food_id']}) {r['qty']}" for r in stock if r["qty"]]
        row("current_stock 읽기", True, f"{len(stock)}품목, 재고 있는 것: " + (", ".join(have) or "없음"))
    except Exception as e:  # noqa: BLE001
        ok_all = False
        row("current_stock 읽기", False, str(classify(e)))
    # 3) Publishable 키로 쓰기 → 거부돼야 정상 (거부되지 않아도 데이터가 바뀌지 않는 시도만)
    probes = [
        ("Publishable 키로 이벤트 쓰기 → 거부", lambda: reader.table("stock_event").insert(
            {"food_id": "__check_not_a_food__", "delta_qty": 1}).execute()),
        ("Publishable 키로 함수 실행 → 거부", lambda: reader.rpc("reject_stock_event", {"p_event_id": -1}).execute()),
    ]
    for name, call in probes:
        try:
            call()
            ok_all = False
            row(name, False, "거부되지 않음! 권한 설정(schema.sql 7장) 확인 필요")
        except Exception as e:  # noqa: BLE001
            err = classify(e)
            if err.kind == "permission":
                row(name, True, f"거부됨 ({err.message[:60]})")
            elif err.kind == "missing_schema":
                ok_all = False
                row(name, False, "확인 불가: 테이블·함수가 DB 에 없음 (supabase/schema.sql 미실행)")
            else:
                ok_all = False
                row(name, False, f"권한 거부가 아닌 다른 이유로 실패 → 권한이 열려 있을 수 있음: {err}")
    return ok_all


def write_checks(repo: Repository) -> bool:
    ok_all = True
    stock = lambda: {r["food_id"]: r["qty"] for r in repo._exec(repo.clients.writer.table("current_stock").select("food_id,qty")).data}
    try:
        n = repo.reset_demo()
        s0 = stock()
        row("reset_demo", True, f"초기 재고 {n}개 — " + ", ".join(f"{k} {v}" for k, v in s0.items() if v))
        cid = "check-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        result = {"capture_id": cid, "source": "check_supabase", "needs_confirmation": False, "latency_sec": 0.0,
                  "added": [{"item_id": "milk", "name": "우유", "quantity": 1, "locations": ["L,1"]}],
                  "removed": [{"item_id": "broccoli", "name": "브로콜리", "quantity": 1, "locations": []}]}
        repo.record_capture(result, before_path="(check)", after_path="(check)")
        events = repo.create_events(cid, result["added"], result["removed"])
        again = repo.create_events(cid, result["added"], result["removed"])
        row("가짜 capture + 이벤트 (우유 +1, 브로콜리 −1)", len(events) == 2, f"pending {len(events)}건")
        row("같은 capture_id 다시 저장 → 중복 없음", [e["event_id"] for e in again] == [e["event_id"] for e in events],
            f"{len(again)}건 (같은 event_id)")
        s_before = stock()
        res = repo.decide_capture(cid, "confirm")
        s1 = stock()
        good = all(r["ok"] for r in res) and s1.get("milk", 0) == s_before.get("milk", 0) + 1 \
            and s1.get("broccoli", 0) == s_before.get("broccoli", 0) - 1
        row("확인 → current_stock", good, f"우유 {s_before.get('milk', 0)}→{s1.get('milk', 0)}, "
                                          f"브로콜리 {s_before.get('broccoli', 0)}→{s1.get('broccoli', 0)}")
        cid2 = cid + "-mismatch"
        r2 = {"capture_id": cid2, "source": "check_supabase", "added": [],
              "removed": [{"item_id": "chicken", "name": "닭고기", "quantity": 3, "locations": []}]}
        repo.record_capture(r2, before_path="(check)", after_path="(check)")
        repo.create_events(cid2, r2["added"], r2["removed"])
        res2 = repo.decide_capture(cid2, "confirm")
        s2 = stock()
        pend = [e for e in repo.get_events(cid2) if e["status"] == "pending"]
        good2 = res2 and not res2[0]["ok"] and res2[0].get("needs_confirmation") and s2.get("chicken") == s1.get("chicken") and pend
        row("닭고기 −3 → stock mismatch", bool(good2),
            f"확인 필요로 넘김, 재고 {s1.get('chicken')}→{s2.get('chicken')}, 이벤트 pending {len(pend)}건")
        ok_all = bool(good and good2)
    except (DbError, SupabaseConfigError) as e:
        ok_all = False
        row("쓰기 점검 중 오류", False, str(e))
    finally:
        try:
            n = repo.reset_demo()
            row("reset_demo (원상복구)", True, f"초기 재고 {n}개")
        except Exception as e:  # noqa: BLE001
            ok_all = False
            row("reset_demo (원상복구)", False, str(classify(e)))
    return ok_all


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true", help="쓰기 점검 (실제 DB 데이터를 바꿈)")
    p.add_argument("--yes", action="store_true", help="쓰기 점검 확인 질문 건너뛰기")
    args = p.parse_args()
    print(f"Supabase 점검 — URL {'설정됨' if config.SUPABASE_URL else '없음'}, "
          f"Publishable 키 {'설정됨' if config.SUPABASE_PUBLISHABLE_KEY else '없음'}, "
          f"Secret 키 {'설정됨' if config.SUPABASE_SECRET_KEY else '없음'} (값은 출력하지 않음)")
    try:
        reader = make_reader()
    except SupabaseConfigError as e:
        print(f"[읽기 점검 불가] {e}")
        return 1
    ok = read_checks(reader)
    print_table("읽기 점검 (Publishable 키)")
    if args.write:
        if not args.yes:
            ans = input("\n쓰기 점검은 실제 DB 의 촬영·이벤트·재고 기록을 지우고(reset_demo) 시험 데이터를 넣었다가 다시 초기화합니다. "
                        "계속할까요? [y/N] ").strip().lower()
            if ans != "y":
                print("쓰기 점검을 건너뜀")
                return 0 if ok else 1
        try:
            repo = Repository(Clients(reader=reader, writer=make_writer()), log=lambda m: None)
        except SupabaseConfigError as e:
            print(f"[쓰기 점검 불가] {e}")
            return 1
        ok = write_checks(repo) and ok
        print_table("쓰기 점검 (Secret 키)")
    print("\n결과:", "모두 정상" if ok else "실패 항목 있음")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
