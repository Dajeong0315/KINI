"""인식 결과를 Supabase 고내 재고 DB(supabase/schema.sql)에 반영.

DB 구조 (docs/DB_재고테이블_설계_설명.md)
  food_item    식재료 목록 (food_id = config.ITEMS 의 item_id)
  capture      촬영 1회 = 1행 (AI 결과 원본)
  stock_event  "오렌지 −1" 같은 변경 후보. pending → confirmed / rejected
  fridge_stock 실제 재고, 식재료 1개 = 1행 (date_out 이 NULL 이면 냉장고 안)
  confirm_stock_event() / reject_stock_event()  — 재고 반영은 반드시 이 함수로 (한 트랜잭션)

이 프로그램이 하는 일
  1) 인식 결과가 나오면      capture 1행 + stock_event(pending) 품목·칸별 1행씩 저장
  2) 재고에 반영하면(y/자동)  그 촬영의 pending 이벤트마다 confirm_stock_event()
     거절하면(n)              reject_stock_event()
     config.SUPABASE_CONFIRM_MODE = "ipad" 이면 2)는 하지 않고 iPad 확인을 기다림
  3) (영상 파일 시험 등) 첫 촬영 때 DB 재고가 카메라가 본 before 와 다르면
     source='manual' 이벤트로 맞춘 뒤 시작 (config.SUPABASE_BASELINE_ON_START)

Supabase REST(PostgREST)를 requests 로 직접 호출 — 추가 라이브러리 불필요.
모든 작업은 다시 실행해도 결과가 같도록(멱등) 만들었고, 네트워크·서버 오류면
captures/supabase_queue.jsonl 에 순서대로 보관했다가 다음 동기화 때 먼저 보냄.
사진·사용자 정보는 보내지 않음 (사진 경로 문자열과 AI 결과 JSON 만).
"""
from __future__ import annotations

import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import requests

import config


class SupabaseError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message

    @property
    def retryable(self) -> bool:
        """서버·네트워크 문제면 나중에 다시 보냄. 4xx(재고 부족·형식 오류 등)는 다시 보내도 같으므로 보관 안 함."""
        return self.status >= 500 or self.status in (408, 429)


class SupabaseREST:
    """Supabase REST(PostgREST) 최소 호출."""

    def __init__(self, url: str, key: str, timeout: float = 5.0) -> None:
        self.base = f"{url.rstrip('/')}/rest/v1"
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"apikey": key, "Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"})

    def _do(self, method: str, path: str, *, params=None, body=None, prefer: str | None = None):
        headers = {"Prefer": prefer} if prefer else {}
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        r = self.s.request(method, f"{self.base}/{path}", params=params, data=data,
                           headers=headers, timeout=self.timeout)
        if r.status_code >= 300:
            try:
                msg = r.json().get("message") or r.text
            except ValueError:
                msg = r.text
            raise SupabaseError(r.status_code, str(msg)[:300])
        return r.json() if r.content else None

    def select(self, table: str, params: dict) -> list[dict]:
        return self._do("GET", table, params=params)

    def insert(self, table: str, rows: list[dict], *, ignore_duplicates=False, returning=False):
        prefer = ["return=representation" if returning else "return=minimal"]
        if ignore_duplicates:
            prefer.append("resolution=ignore-duplicates")
        return self._do("POST", table, body=rows, prefer=",".join(prefer))

    def rpc(self, fn: str, args: dict):
        return self._do("POST", f"rpc/{fn}", body=args)


# ---------------------------------------------------------------------------
# 결과 → 이벤트
# ---------------------------------------------------------------------------
def _split_loc(loc: str | None):
    """'R,1' → ('R', 1). 모르면 (None, None)."""
    if not loc:
        return None, None
    x, y = loc.split(",")
    return x, int(y)


def events_from_result(result: dict) -> list[dict]:
    """추가·제거 → stock_event 행. 같은 품목이라도 칸이 다르면 칸마다 1행 (칸을 모르면 칸 없이 1행)."""
    rows = []
    for key, sign in (("added", 1), ("removed", -1)):
        for e in result.get(key, []):
            locs = list(e.get("locations") or [])[:e["quantity"]]
            groups = Counter(locs)
            if len(locs) < e["quantity"]:
                groups[None] += e["quantity"] - len(locs)
            for loc, n in groups.items():
                x, y = _split_loc(loc)
                rows.append({"capture_id": result["capture_id"], "food_id": e["item_id"],
                             "delta_qty": sign * n, "loc_x": x, "loc_y": y, "source": "vision"})
    return rows


def capture_row(result: dict) -> dict:
    cid = result["capture_id"]
    clean = {k: v for k, v in result.items() if not k.startswith("_")}
    latency = result.get("latency_sec")
    return {
        "capture_id": cid,
        "engine": result.get("source", "unknown"),
        "before_path": f"captures/{cid}/before.jpg",     # 사진은 노트북 로컬에만 (Storage 업로드는 팀 결정 후)
        "after_path": f"captures/{cid}/after.jpg",
        "result_json": clean,
        "needs_confirmation": bool(result.get("needs_confirmation")),
        "latency_ms": int(latency * 1000) if isinstance(latency, (int, float)) else None,
    }


# ---------------------------------------------------------------------------
# 작업 실행 (모두 멱등)
# ---------------------------------------------------------------------------
class Worker:
    def __init__(self, db: SupabaseREST, log: Callable[[str], None]) -> None:
        self.db = db
        self.log = log
        self.known_items: set[str] | None = None

    def ensure_items(self, food_ids) -> None:
        """food_item 에 없는 품목(사용자가 이름 붙인 새 품목 등)을 추가 — stock_event 외래키 때문."""
        if self.known_items is None:
            self.known_items = {r["food_id"] for r in self.db.select("food_item", {"select": "food_id"})}
        missing = [k for k in dict.fromkeys(food_ids) if k not in self.known_items]
        if not missing:
            return
        if not config.SUPABASE_REGISTER_MISSING_ITEMS:
            raise SupabaseError(400, f"food_item 에 없는 품목: {missing} (SUPABASE_REGISTER_MISSING_ITEMS=False)")
        self.db.insert("food_item", [{
            "food_id": k, "food_name": config.ITEMS.get(k, {}).get("name", k),
            "shelf_days": config.SUPABASE_DEFAULT_SHELF_DAYS, "demo_init_qty": 0} for k in missing],
            ignore_duplicates=True)
        self.known_items.update(missing)
        self.log(f"Supabase: food_item 에 새 품목 등록 {missing} (shelf_days={config.SUPABASE_DEFAULT_SHELF_DAYS} 임시값)")

    def record(self, result: dict) -> list[dict]:
        """capture + stock_event(pending) 저장. 이미 저장된 촬영이면 기존 이벤트를 돌려줌."""
        cid = result["capture_id"]
        self.db.insert("capture", [capture_row(result)], ignore_duplicates=True)
        rows = self.db.select("stock_event", {"select": "event_id,food_id,delta_qty,status",
                                              "capture_id": f"eq.{cid}", "order": "event_id.asc"})
        if rows:
            return rows
        new = events_from_result(result)
        if not new:
            self.log(f"Supabase: 촬영 기록 저장 ({cid}, 변화 없음)")
            return []
        self.ensure_items(r["food_id"] for r in new)
        rows = self.db.insert("stock_event", new, returning=True)
        self.log(f"Supabase: 촬영 기록 + 변경 후보 {len(rows)}건 저장 (pending) — "
                 + ", ".join(f"{r['food_id']} {r['delta_qty']:+d}" for r in rows))
        return rows

    def decide(self, events: list[dict], decision: str) -> None:
        fn = "confirm_stock_event" if decision == "confirm" else "reject_stock_event"
        for ev in events:
            if ev["status"] != "pending":
                continue                       # 이미 처리됨 (재전송) → 건너뜀
            try:
                moved = self.db.rpc(fn, {"p_event_id": ev["event_id"]})
                ev["status"] = "confirmed" if decision == "confirm" else "rejected"
                self.log(f"Supabase: {ev['food_id']} {ev['delta_qty']:+d} → "
                         + (f"재고 반영 ({moved}개)" if decision == "confirm" else "거절"))
            except SupabaseError as e:
                if e.retryable:
                    raise
                # 예: 재고보다 많이 빼려 함(stock mismatch) → 이벤트는 pending 으로 남겨 화면에서 수정·거절
                self.log(f"Supabase: {ev['food_id']} {ev['delta_qty']:+d} 반영 안 됨 (pending 유지): {e.message}")

    def baseline(self, before: dict, locations: dict) -> None:
        """DB 재고를 카메라가 본 before 개수에 맞춤 (source='manual' 이벤트 → 바로 확인)."""
        current = {r["food_id"]: r["qty"] for r in self.db.select("current_stock", {"select": "food_id,qty"})}
        rows = []
        for k, n in before.items():
            diff = n - current.get(k, 0)
            if diff == 0 or (k not in current and n == 0):
                continue
            locs = locations.get(k, []) if diff > 0 else []
            res = {"capture_id": None, "added": [], "removed": []}
            res["added" if diff > 0 else "removed"].append({"item_id": k, "quantity": abs(diff), "locations": locs})
            for r in events_from_result(res):
                rows.append({**r, "source": "manual"})
        if not rows:
            self.log("Supabase: DB 재고가 첫 before 사진과 같음 → 맞출 것 없음")
            return
        self.ensure_items(r["food_id"] for r in rows)
        events = self.db.insert("stock_event", rows, returning=True)
        self.log("Supabase: 시작 재고를 카메라(before)에 맞춤 — "
                 + ", ".join(f"{r['food_id']} {r['delta_qty']:+d}" for r in events))
        self.decide(events, "confirm")

    def run(self, job: dict) -> None:
        if job["type"] == "baseline":
            self.baseline(job["before"], job.get("locations", {}))
            return
        events = self.record(job["result"])
        if job.get("decision"):
            self.decide(events, job["decision"])


class InventorySync:
    """main.py 에서 쓰는 창구. 호출은 바로 돌아오고 실제 전송은 백그라운드 스레드 1개에서 순서대로."""

    def __init__(self, log: Callable[[str], None] = print, db: SupabaseREST | None = None,
                 baseline: bool = False) -> None:
        self.log = log
        self.enabled = db is not None or bool(config.SUPABASE_URL and config.SUPABASE_KEY)
        if not self.enabled:
            log("Supabase 미설정 (.env 의 SUPABASE_URL·SUPABASE_KEY) → 재고 DB 연동 없이 실행")
            return
        self.worker = Worker(db or SupabaseREST(config.SUPABASE_URL, config.SUPABASE_KEY, config.SUPABASE_TIMEOUT), log)
        self.queue_file = config.SUPABASE_QUEUE_FILE
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.lock = threading.Lock()
        self.need_baseline = baseline
        self.confirm_here = config.SUPABASE_CONFIRM_MODE != "ipad"

    # -- 실패 작업 보관 ---------------------------------------------------
    def _load_queue(self) -> list[dict]:
        if not self.queue_file.exists():
            return []
        return [json.loads(line) for line in self.queue_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _save_queue(self, jobs: list[dict]) -> None:
        if jobs:
            self.queue_file.parent.mkdir(parents=True, exist_ok=True)
            self.queue_file.write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs),
                                       encoding="utf-8")
        elif self.queue_file.exists():
            self.queue_file.unlink()

    def _send(self, new_jobs: list[dict]) -> None:
        with self.lock:
            jobs = self._load_queue() + new_jobs          # 예전에 실패한 작업부터 순서대로
            for i, job in enumerate(jobs):
                try:
                    self.worker.run(job)
                except (requests.RequestException, SupabaseError) as e:
                    if isinstance(e, SupabaseError) and not e.retryable:
                        self.log(f"Supabase: 작업 건너뜀 ({job['type']}): {e}")
                        continue
                    self._save_queue(jobs[i:])
                    self.log(f"Supabase 전송 실패 → {len(jobs) - i}개 작업 보관 ({self.queue_file.name}), "
                             f"다음에 다시 보냄: {e}")
                    return
            self._save_queue([])

    def _submit(self, jobs: list[dict]):
        jobs = json.loads(json.dumps(jobs, ensure_ascii=False, default=str))   # 지금 상태로 복사 (이후 변경 무시)
        fut = self.pool.submit(self._send, jobs)
        fut.add_done_callback(lambda f: f.exception() and self.log(f"Supabase 동기화 오류: {f.exception()!r}"))
        return fut

    # -- main.py 가 부르는 함수 ------------------------------------------
    def record(self, result: dict):
        """인식 결과가 나왔을 때: capture + pending 이벤트 저장 (iPad 가 바로 볼 수 있게)."""
        if not self.enabled:
            return None
        jobs = []
        if self.need_baseline:
            self.need_baseline = False
            jobs.append({"type": "baseline", "before": result.get("before", {}),
                         "locations": result.get("before_locations", {})})
        jobs.append({"type": "capture", "result": result, "decision": None})
        return self._submit(jobs)

    def decide(self, result: dict, decision: str):
        """재고에 반영(confirm) 또는 거절(reject)했을 때."""
        if not self.enabled or not self.confirm_here:
            return None
        return self._submit([{"type": "capture", "result": result, "decision": decision}])

    def close(self) -> None:
        if self.enabled:
            self.pool.shutdown(wait=True)
