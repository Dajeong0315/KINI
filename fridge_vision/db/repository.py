"""Supabase 고내 재고 DB 저장 함수 (supabase/schema.sql 설계 그대로).

  record_capture(result)                 capture 1행 (같은 capture_id 면 덮어쓰기)
  create_events(capture_id, added, removed)  품목·칸별 stock_event(pending). 같은 촬영의 이벤트가 이미 있으면 다시 만들지 않음
  confirm_event(event_id)                rpc confirm_stock_event — stock mismatch 는 예외 대신 '확인 필요' 결과
  reject_event(event_id)                 rpc reject_stock_event
  reset_demo()                           rpc reset_demo
  get_current_stock()                    current_stock 뷰 (읽기 키)
  get_pending_events()                   status='pending' 이벤트 (읽기 키)

재고(fridge_stock)는 직접 INSERT·UPDATE 하지 않음. 위치 이동(재인식에서 칸만 바뀐 경우)은 스키마에 전용 함수가
없어 DB 에 쓰지 않음 (logs/reid.jsonl 에만).
"""
from __future__ import annotations

from collections import Counter
from typing import Callable

import config


# ---------------------------------------------------------------------------
# 오류 분류
# ---------------------------------------------------------------------------
class DbError(RuntimeError):
    """DB 요청 실패. retryable=True 면 나중에 다시 보내면 될 수 있음 (네트워크·서버), False 면 다시 보내도 같음."""

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind            # network / server / mismatch / not_pending / permission / missing_item / missing_schema / other
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.kind in ("network", "server")


def classify(exc: Exception) -> DbError:
    """supabase-py(httpx·postgrest) 예외 → DbError. 메시지에 키가 들어가지 않음 (서버 메시지·예외 종류만)."""
    if isinstance(exc, DbError):
        return exc
    try:
        import httpx
        if isinstance(exc, httpx.HTTPError):                 # 연결 실패·시간 초과 등
            return DbError("network", f"{type(exc).__name__}")
    except ImportError:
        pass
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return DbError("network", type(exc).__name__)
    msg = str(getattr(exc, "message", None) or exc)[:300]
    code = str(getattr(exc, "code", "") or "")
    low = msg.lower()
    if code in ("PGRST205", "PGRST202", "42P01", "42883"):     # 테이블·뷰·함수가 DB 에 없음
        return DbError("missing_schema", f"{msg} — supabase/schema.sql 을 이 프로젝트의 SQL Editor 에서 실행해야 합니다")
    if "stock mismatch" in low:
        return DbError("mismatch", msg)
    if "is already" in low or "is not pending" in low:
        return DbError("not_pending", msg)
    if code == "42501" or "permission denied" in low or "invalid api key" in low or code in ("401", "403"):
        return DbError("permission", msg)
    if code == "23503" or "foreign key" in low:
        return DbError("missing_item", msg)
    # 게이트웨이 5xx(JSON 아님 → code 가 상태 번호), PostgREST 의 DB 연결 문제(PGRST000~003), 과부하
    if code[:1] == "5" and code.isdigit() or code in ("408", "429") or code.startswith("PGRST00"):
        return DbError("server", f"{code} {msg}".strip())
    return DbError("other", f"{code} {msg}".strip())


# ---------------------------------------------------------------------------
# 결과 → 이벤트
# ---------------------------------------------------------------------------
def split_loc(loc: str | None):
    """'R,1' → ('R', 1). 모르거나 형식이 다르면 (None, None) → DB 가 food_item 기본 위치를 씀."""
    if not loc or "," not in str(loc):
        return None, None
    x, y = str(loc).split(",", 1)
    try:
        y = int(y)
    except ValueError:
        return None, None
    return (x, y) if x in ("L", "C", "R") and y in (1, 2) else (None, None)


def events_from_changes(capture_id, added, removed, source="vision", exclude=None) -> tuple[list[dict], list[str]]:
    """추가·제거 → stock_event 행 (같은 품목이라도 칸이 다르면 칸마다 1행). 돌려주는 값: (행, 뺀 라벨)."""
    exclude = config.SUPABASE_EXCLUDE_LABELS if exclude is None else exclude
    rows, skipped = [], []
    for entries, sign in ((added or [], 1), (removed or [], -1)):
        for e in entries:
            k, n = e["item_id"], int(e["quantity"])
            if n <= 0:
                continue
            if k in exclude:
                skipped.append(k)
                continue
            locs = list(e.get("locations") or [])[:n]
            groups = Counter(split_loc(x) for x in locs)
            if len(locs) < n:
                groups[(None, None)] += n - len(locs)
            for (x, y), cnt in groups.items():
                rows.append({"capture_id": capture_id, "food_id": k, "delta_qty": sign * cnt,
                             "loc_x": x, "loc_y": y, "source": source})
    return rows, skipped


def capture_row(result: dict, before_path: str | None = None, after_path: str | None = None) -> dict:
    cid = result["capture_id"]
    clean = {k: v for k, v in result.items() if not str(k).startswith("_")}
    latency = result.get("latency_sec")
    return {
        "capture_id": cid,
        "engine": str(result.get("source") or "unknown"),
        "before_path": before_path or f"captures/{cid}/before.jpg",     # 로컬 상대 경로 (UPLOAD_PHOTOS=false)
        "after_path": after_path or f"captures/{cid}/after.jpg",
        "result_json": clean,
        "needs_confirmation": bool(result.get("needs_confirmation")),
        "latency_ms": int(latency * 1000) if isinstance(latency, (int, float)) and latency >= 0 else None,
    }


# ---------------------------------------------------------------------------
# 저장소
# ---------------------------------------------------------------------------
class Repository:
    def __init__(self, clients, log: Callable[[str], None] = print) -> None:
        """clients: db.supabase_client.Clients (reader·writer). 테스트에서는 가짜."""
        self.clients = clients
        self.log = log
        self.known_items: set[str] | None = None

    # -- 내부 ----------------------------------------------------------------
    @staticmethod
    def _exec(builder):
        try:
            return builder.execute()
        except Exception as e:                       # noqa: BLE001 — 모든 예외를 분류해서 다시 던짐
            raise classify(e) from None

    def ensure_items(self, food_ids, names: dict | None = None) -> list[str]:
        """food_item 에 없는 품목을 추가 (2026-11-27 결정 (b), shelf_days 는 설정 기본값). 돌려주는 값: 추가한 id.
        names: 결과 JSON 에 들어 있던 표시 이름 (OCR 이름 pkg_* 는 실행 중에만 config.ITEMS 에 있어서,
               대기열을 다른 프로세스(tools/flush_outbox.py)가 보낼 때도 이름이 남도록)."""
        names = names or {}
        if self.known_items is None:
            rows = self._exec(self.clients.writer.table("food_item").select("food_id")).data
            self.known_items = {r["food_id"] for r in rows}
        missing = [k for k in dict.fromkeys(food_ids) if k not in self.known_items]
        if not missing:
            return []
        if not config.SUPABASE_REGISTER_MISSING_ITEMS:
            raise DbError("missing_item", f"food_item 에 없는 품목 {missing} (SUPABASE_REGISTER_MISSING_ITEMS=False)")
        self._exec(self.clients.writer.table("food_item").upsert(
            [{"food_id": k, "food_name": names.get(k) or config.ITEMS.get(k, {}).get("name", k),
              "shelf_days": config.SUPABASE_DEFAULT_SHELF_DAYS, "demo_init_qty": 0} for k in missing],
            on_conflict="food_id", ignore_duplicates=True))
        self.known_items.update(missing)
        self.log(f"DB: food_item 에 새 품목 등록 {missing} (shelf_days={config.SUPABASE_DEFAULT_SHELF_DAYS} 임시값)")
        return missing

    # -- 쓰기 ----------------------------------------------------------------
    def record_capture(self, result: dict, before_path=None, after_path=None) -> None:
        """capture 1행. 같은 capture_id 면 덮어씀 (사용자 이름 입력 후 다시 계산한 결과 등)."""
        self._exec(self.clients.writer.table("capture").upsert(
            capture_row(result, before_path, after_path), on_conflict="capture_id"))

    def get_events(self, capture_id: str) -> list[dict]:
        return self._exec(self.clients.writer.table("stock_event")
                          .select("event_id,food_id,delta_qty,loc_x,loc_y,status")
                          .eq("capture_id", capture_id).order("event_id")).data

    def create_events(self, capture_id: str, added, removed, source: str = "vision", names: dict | None = None) -> list[dict]:
        """품목·칸별 stock_event(pending). 같은 capture_id 의 이벤트가 이미 있으면 새로 만들지 않고 그것을 돌려줌.
        unknown·unknown_package 등(SUPABASE_EXCLUDE_LABELS)은 넣지 않고 로그만."""
        if capture_id is not None:
            existing = self.get_events(capture_id)
            if existing:
                return existing
        rows, skipped = events_from_changes(capture_id, added, removed, source)
        if skipped:
            self.log(f"DB: 이름이 정해지지 않은 물체는 저장하지 않음 {sorted(set(skipped))}")
        if not rows:
            return []
        names = {**(names or {}), **{e["item_id"]: e["name"] for e in list(added or []) + list(removed or []) if e.get("name")}}
        self.ensure_items((r["food_id"] for r in rows), names)
        created = self._exec(self.clients.writer.table("stock_event").insert(rows)).data
        self.log("DB: 변경 후보 저장 (pending) — " + ", ".join(f"{r['food_id']} {r['delta_qty']:+d}" for r in created))
        return created

    def confirm_event(self, event_id: int) -> dict:
        """재고 반영. 돌려주는 값: {"ok", "event_id", "moved"} 또는 확인 필요 {"ok": False, "needs_confirmation": True, "reason"}.
        이미 처리된 이벤트는 {"ok": True, "already": True} (재전송해도 두 번 반영되지 않음)."""
        try:
            moved = self._exec(self.clients.writer.rpc("confirm_stock_event", {"p_event_id": int(event_id)})).data
        except DbError as e:
            if e.kind == "mismatch":
                return {"ok": False, "event_id": event_id, "needs_confirmation": True, "reason": e.message}
            if e.kind == "not_pending":
                return {"ok": True, "event_id": event_id, "already": True, "reason": e.message}
            raise
        return {"ok": True, "event_id": event_id, "moved": moved}

    def reject_event(self, event_id: int) -> dict:
        try:
            self._exec(self.clients.writer.rpc("reject_stock_event", {"p_event_id": int(event_id)}))
        except DbError as e:
            if e.kind == "not_pending":
                return {"ok": True, "event_id": event_id, "already": True, "reason": e.message}
            raise
        return {"ok": True, "event_id": event_id}

    def reset_demo(self) -> int:
        """촬영·이벤트·재고 기록을 지우고 food_item.demo_init_qty 만큼 다시 채움. 돌려주는 값: 채운 개수."""
        n = self._exec(self.clients.writer.rpc("reset_demo", {})).data
        self.log(f"DB: 데모 초기화 (재고 {n}개로 시작)")
        return n

    # -- 읽기 (Publishable 키) -----------------------------------------------
    def get_current_stock(self) -> list[dict]:
        return self._exec(self.clients.reader.table("current_stock")
                          .select("food_id,food_name,unit,qty,nearest_expire").order("food_id")).data

    def get_pending_events(self) -> list[dict]:
        return self._exec(self.clients.reader.table("stock_event").select("*")
                          .eq("status", "pending").order("event_id")).data

    # -- 묶음 --------------------------------------------------------------
    def decide_capture(self, capture_id: str, decision: str) -> list[dict]:
        """그 촬영의 pending 이벤트를 모두 확인(confirm)·거절(reject). 이벤트별 결과 목록."""
        out = []
        for ev in self.get_events(capture_id):
            if ev["status"] != "pending":
                continue
            res = self.confirm_event(ev["event_id"]) if decision == "confirm" else self.reject_event(ev["event_id"])
            res.update(food_id=ev["food_id"], delta_qty=ev["delta_qty"])
            if decision == "confirm" and not res["ok"]:
                self.log(f"DB: {ev['food_id']} {ev['delta_qty']:+d} 반영 안 됨 → 확인 필요 (pending 유지): {res['reason']}")
            elif not res.get("already"):
                self.log(f"DB: {ev['food_id']} {ev['delta_qty']:+d} → "
                         + (f"재고 반영 ({res.get('moved')}개)" if decision == "confirm" else "거절"))
            out.append(res)
        return out
