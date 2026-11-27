"""Supabase 연동(db/, supabase_sync.py) 단위 테스트 — 네트워크 없이 가짜 Supabase 클라이언트로.

  python -m pytest tests/test_db_repository.py -q

가짜 클라이언트는 supabase/schema.sql 의 규칙을 흉내 냄: 이벤트는 pending 으로, confirm 은 추가=행 생성·제거=선입선출,
재고보다 많이 빼면 'stock mismatch' 로 전체 취소, 처리된 이벤트 재처리는 오류, anon(읽기 키)은 쓰기·함수 거부.
실제 PostgreSQL + REST 로 같은 흐름을 확인하는 통합 시험은 tests/test_supabase_sync.py.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402
from postgrest import APIError  # noqa: E402

import config  # noqa: E402
import supabase_sync as ss  # noqa: E402
from db import supabase_client as sc  # noqa: E402
from db.repository import DbError, Repository, classify, events_from_changes, split_loc  # noqa: E402

SECRET = "sb_secret_TEST_ONLY_value_1234567890"
PUBLISHABLE = "sb_publishable_TEST_ONLY_value_0987654321"


# ---------------------------------------------------------------------------
# 가짜 Supabase
# ---------------------------------------------------------------------------
class FakeDB:
    def __init__(self, items=None):
        self.food_item = {k: {"food_id": k, "food_name": k, "shelf_days": 5, "demo_init_qty": q}
                          for k, q in (items or {"milk": 0, "tofu": 1, "egg": 1, "broccoli": 1, "mushroom": 1, "chicken": 1}).items()}
        self.capture, self.stock_event, self.fridge_stock = {}, [], []
        self.next_event = 1
        self.down = False
        self.calls = []
        self.reset_demo()

    def reset_demo(self):
        self.capture.clear()
        self.stock_event.clear()
        self.fridge_stock = [{"food_id": k, "date_out": None, "order": i}
                             for i, (k, f) in enumerate(self.food_item.items()) for _ in range(f["demo_init_qty"])]
        return len(self.fridge_stock)

    def qty(self):
        out = {}
        for s in self.fridge_stock:
            if s["date_out"] is None:
                out[s["food_id"]] = out.get(s["food_id"], 0) + 1
        return out


class FakeQuery:
    def __init__(self, db, role, table):
        self.db, self.role, self.table = db, role, table
        self.op, self.payload, self.filters, self.kw = "select", None, [], {}

    def select(self, cols="*"):
        self.op = "select"
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, col, desc=False):
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def upsert(self, rows, on_conflict="", ignore_duplicates=False):
        self.op, self.payload, self.kw = "upsert", rows, {"on_conflict": on_conflict, "ignore": ignore_duplicates}
        return self

    def execute(self):
        db = self.db
        db.calls.append((self.role, self.table, self.op))
        if db.down:
            raise httpx.ConnectError("connection refused")
        if self.op != "select" and self.role == "anon":
            raise APIError({"message": f"permission denied for table {self.table}", "code": "42501"})
        rows = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.table == "food_item":
            if self.op == "select":
                return type("R", (), {"data": list(db.food_item.values())})
            for r in rows:
                if r["food_id"] in db.food_item and self.kw.get("ignore"):
                    continue
                db.food_item[r["food_id"]] = dict(r)
            return type("R", (), {"data": rows})
        if self.table == "capture":
            for r in rows:
                if r["capture_id"] in db.capture and self.op == "insert":
                    raise APIError({"message": "duplicate key", "code": "23505"})
                db.capture[r["capture_id"]] = dict(r)
            return type("R", (), {"data": rows})
        if self.table == "stock_event":
            if self.op == "select":
                data = [e for e in db.stock_event if all(str(e.get(c)) == str(v) for c, v in self.filters)]
                return type("R", (), {"data": [dict(e) for e in data]})
            out = []
            for r in rows:
                if r["food_id"] not in db.food_item:
                    raise APIError({"message": "insert or update on table violates foreign key constraint", "code": "23503"})
                e = {"event_id": db.next_event, "status": "pending", **r}
                db.next_event += 1
                db.stock_event.append(e)
                out.append(dict(e))
            return type("R", (), {"data": out})
        if self.table == "current_stock":
            q = db.qty()
            return type("R", (), {"data": [{"food_id": k, "food_name": f["food_name"], "unit": "개", "qty": q.get(k, 0),
                                             "nearest_expire": None} for k, f in db.food_item.items()]})
        raise AssertionError(self.table)


class FakeRPC:
    def __init__(self, db, role, fn, params):
        self.db, self.role, self.fn, self.params = db, role, fn, params

    def execute(self):
        db = self.db
        db.calls.append((self.role, "rpc", self.fn))
        if db.down:
            raise httpx.ConnectError("connection refused")
        if self.role == "anon":
            raise APIError({"message": f"permission denied for function {self.fn}", "code": "42501"})
        if self.fn == "reset_demo":
            return type("R", (), {"data": db.reset_demo()})
        eid = self.params["p_event_id"]
        e = next((x for x in db.stock_event if x["event_id"] == eid), None)
        if self.fn == "reject_stock_event":
            if e is None or e["status"] != "pending":
                raise APIError({"message": f"event {eid} is not pending", "code": "P0001"})
            e["status"] = "rejected"
            return type("R", (), {"data": None})
        if e is None:
            raise APIError({"message": f"event {eid} not found", "code": "P0001"})
        if e["status"] != "pending":
            raise APIError({"message": f"event {eid} is already {e['status']}", "code": "P0001"})
        if e["delta_qty"] > 0:
            for _ in range(e["delta_qty"]):
                db.fridge_stock.append({"food_id": e["food_id"], "date_out": None, "order": len(db.fridge_stock),
                                        "loc": (e.get("loc_x"), e.get("loc_y"))})
            moved = e["delta_qty"]
        else:
            have = sorted((s for s in db.fridge_stock if s["food_id"] == e["food_id"] and s["date_out"] is None),
                          key=lambda s: s["order"])
            n = -e["delta_qty"]
            if len(have) < n:
                raise APIError({"message": f"stock mismatch: {e['food_id']} has {len(have)} in fridge, tried to remove {n}",
                                "code": "P0001"})
            for s in have[:n]:
                s["date_out"] = "now"
            moved = n
        e["status"] = "confirmed"
        return type("R", (), {"data": moved})


class FakeClient:
    def __init__(self, db, role):
        self.db, self.role = db, role

    def table(self, name):
        return FakeQuery(self.db, self.role, name)

    def rpc(self, fn, params):
        return FakeRPC(self.db, self.role, fn, params)


def make_repo(db=None, logs=None):
    db = db or FakeDB()
    logs = [] if logs is None else logs
    repo = Repository(sc.Clients(reader=FakeClient(db, "anon"), writer=FakeClient(db, "service_role")), log=logs.append)
    return repo, db, logs


def result(cid, added=(), removed=(), needs=False):
    return {"capture_id": cid, "source": "yolo:yoloe-v8m-seg.pt", "latency_sec": 1.25, "needs_confirmation": needs,
            "added": [{"item_id": k, "name": _name(k), "quantity": n, "locations": locs} for k, n, locs in added],
            "removed": [{"item_id": k, "name": _name(k), "quantity": n, "locations": locs} for k, n, locs in removed]}


def _name(k):
    """결과 JSON 처럼 표시 이름 (build_result 는 config.ITEMS 의 name 을 넣음)."""
    return config.ITEMS.get(k, {}).get("name", k)


# ---------------------------------------------------------------------------
# 1. 결과 JSON → 이벤트
# ---------------------------------------------------------------------------
def test_events_split_by_location_and_skip_unknown_labels():
    rows, skipped = events_from_changes("c1",
                                        [{"item_id": "orange", "quantity": 3, "locations": ["R,1", "R,1", "C,2"]},
                                         {"item_id": "unknown_package", "quantity": 1, "locations": ["L,1"]},
                                         {"item_id": "packaged_food", "quantity": 2, "locations": []}],
                                        [{"item_id": "butter", "quantity": 2, "locations": ["C,2"]},
                                         {"item_id": "unknown", "quantity": 1, "locations": []}])
    got = sorted((r["food_id"], r["delta_qty"], r["loc_x"] or "-", r["loc_y"] or 0) for r in rows)
    assert got == [("butter", -1, "-", 0), ("butter", -1, "C", 2), ("orange", 1, "C", 2), ("orange", 2, "R", 1)]
    assert all(r["capture_id"] == "c1" and r["source"] == "vision" for r in rows)
    assert sorted(skipped) == ["packaged_food", "unknown", "unknown_package"]


def test_location_parsing_falls_back_to_default():
    assert split_loc("R,1") == ("R", 1) and split_loc("L,2") == ("L", 2)
    assert split_loc(None) == (None, None) and split_loc("X,9") == (None, None) and split_loc("R") == (None, None)


def test_capture_row_format():
    from db.repository import capture_row
    r = capture_row({**result("c9", added=[("milk", 1, ["L,1"])]), "_pending": True})
    assert r["capture_id"] == "c9" and r["engine"] == "yolo:yoloe-v8m-seg.pt" and r["latency_ms"] == 1250
    assert r["before_path"] == "captures/c9/before.jpg" and r["after_path"] == "captures/c9/after.jpg"
    assert "_pending" not in r["result_json"] and r["needs_confirmation"] is False


# ---------------------------------------------------------------------------
# 2. 저장소
# ---------------------------------------------------------------------------
def test_record_capture_upserts_and_events_are_not_duplicated():
    repo, db, _ = make_repo()
    r = result("c1", added=[("milk", 1, ["L,1"])], removed=[("broccoli", 1, [])])
    repo.record_capture(r)
    repo.record_capture({**r, "needs_confirmation": True})                 # 같은 capture_id → 덮어쓰기
    assert len(db.capture) == 1 and db.capture["c1"]["needs_confirmation"] is True
    first = repo.create_events("c1", r["added"], r["removed"])
    again = repo.create_events("c1", r["added"], r["removed"])               # 재시도
    assert len(first) == 2 and [e["event_id"] for e in again] == [e["event_id"] for e in first]
    assert len(db.stock_event) == 2 and all(e["status"] == "pending" for e in db.stock_event)
    assert db.qty() == {"tofu": 1, "egg": 1, "broccoli": 1, "mushroom": 1, "chicken": 1}   # pending → 재고 그대로


def test_confirm_changes_stock_and_reject_does_not():
    repo, db, _ = make_repo()
    r = result("c1", added=[("milk", 1, ["L,1"])], removed=[("broccoli", 1, [])])
    repo.create_events("c1", r["added"], r["removed"])
    res = repo.decide_capture("c1", "confirm")
    assert all(x["ok"] for x in res) and db.qty().get("milk") == 1 and "broccoli" not in db.qty()
    assert repo.decide_capture("c1", "confirm") == []                          # 이미 처리됨 → 다시 반영 안 함
    r2 = result("c2", added=[("tofu", 2, [])])
    repo.create_events("c2", r2["added"], r2["removed"])
    repo.decide_capture("c2", "reject")
    assert db.qty()["tofu"] == 1 and {e["status"] for e in db.stock_event if e["capture_id"] == "c2"} == {"rejected"}
    assert repo.confirm_event(1) == {"ok": True, "event_id": 1, "already": True,
                                     "reason": "event 1 is already confirmed"}


def test_stock_mismatch_becomes_needs_confirmation_not_exception():
    repo, db, logs = make_repo()
    r = result("c1", removed=[("chicken", 3, [])])
    ev = repo.create_events("c1", r["added"], r["removed"])
    res = repo.confirm_event(ev[0]["event_id"])
    assert res["ok"] is False and res["needs_confirmation"] is True and "stock mismatch" in res["reason"]
    assert db.qty()["chicken"] == 1 and db.stock_event[0]["status"] == "pending"


def test_unknown_labels_never_stored_and_new_labels_auto_added(monkeypatch):
    repo, db, logs = make_repo()
    monkeypatch.setitem(config.ITEMS, "user_99", {"name": "인절미", "user": True})
    monkeypatch.setitem(config.ITEMS, "pkg_test", {"name": "비비고 왕교자"})
    r = result("c1", added=[("user_99", 1, ["L,2"]), ("pkg_test", 1, []), ("unknown_package", 1, [])],
               removed=[("unknown", 1, [])])
    ev = repo.create_events("c1", r["added"], r["removed"])
    assert sorted(e["food_id"] for e in ev) == ["pkg_test", "user_99"]
    assert db.food_item["user_99"]["food_name"] == "인절미" and db.food_item["user_99"]["shelf_days"] == config.SUPABASE_DEFAULT_SHELF_DAYS
    assert db.food_item["pkg_test"]["food_name"] == "비비고 왕교자"
    assert "unknown" not in db.food_item and "unknown_package" not in db.food_item
    assert any("이름이 정해지지 않은" in m for m in logs)


def test_missing_label_policy_a_logs_only(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_REGISTER_MISSING_ITEMS", False)
    repo, db, _ = make_repo()
    r = result("c1", added=[("banana", 1, [])])
    with pytest.raises(DbError) as e:
        repo.create_events("c1", r["added"], r["removed"])
    assert e.value.kind == "missing_item" and not e.value.retryable and "banana" not in db.food_item


def test_reads_use_publishable_and_writes_use_secret():
    repo, db, _ = make_repo()
    repo.create_events("c1", [{"item_id": "milk", "quantity": 1, "locations": []}], [])
    assert [r["food_id"] for r in repo.get_pending_events()] == ["milk"]
    assert {r["food_id"]: r["qty"] for r in repo.get_current_stock()}["tofu"] == 1
    roles = {(role, table) for role, table, op in db.calls}
    assert ("anon", "current_stock") in roles and ("anon", "stock_event") in roles
    assert all(role == "service_role" for role, table, op in db.calls if op in ("insert", "upsert", "reset_demo",
                                                                               "confirm_stock_event"))
    with pytest.raises(Exception):
        FakeClient(db, "anon").table("stock_event").insert({"food_id": "milk", "delta_qty": 1}).execute()


def test_error_classification():
    assert classify(httpx.ConnectError("x")).kind == "network" and classify(httpx.ReadTimeout("x")).retryable
    assert classify(APIError({"message": "Service Unavailable", "code": "503"})).kind == "server"
    assert classify(APIError({"message": "Could not connect", "code": "PGRST001"})).retryable
    assert classify(APIError({"message": "permission denied for table x", "code": "42501"})).kind == "permission"
    assert classify(APIError({"message": "Invalid API key", "code": None})).kind == "permission"
    assert classify(APIError({"message": "stock mismatch: a has 1", "code": "P0001"})).kind == "mismatch"
    assert not classify(APIError({"message": "stock mismatch", "code": "P0001"})).retryable


# ---------------------------------------------------------------------------
# 3. 키 — 값이 메시지·repr·로그·대기열에 남지 않음
# ---------------------------------------------------------------------------
def test_key_errors_do_not_contain_key_values(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "https://example.supabase.co")
    for fn, wrong, attr in ((sc.make_reader, SECRET, "SUPABASE_PUBLISHABLE_KEY"), (sc.make_writer, PUBLISHABLE, "SUPABASE_SECRET_KEY")):
        monkeypatch.setattr(config, attr, wrong)
        with pytest.raises(sc.SupabaseConfigError) as e:
            fn()
        assert wrong not in str(e.value) and "자리에" in str(e.value)
        monkeypatch.setattr(config, attr, "")
        with pytest.raises(sc.SupabaseConfigError) as e:
            fn()
        assert "비어 있습니다" in str(e.value)
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    with pytest.raises(sc.SupabaseConfigError):
        sc.make_writer(key=SECRET)
    assert SECRET not in repr(sc.Clients(writer=object()))


def test_real_client_construction_keeps_keys_out_of_repr():
    w = sc.make_writer(url="https://example.supabase.co", key=SECRET)
    r = sc.make_reader(url="https://example.supabase.co", key=PUBLISHABLE)
    assert w is not None and r is not None
    assert SECRET not in repr(sc.Clients(reader=r, writer=w))


# ---------------------------------------------------------------------------
# 4. InventorySync: 연결 실패 → 대기열 → 재전송, 확인 필요 알림
# ---------------------------------------------------------------------------
@pytest.fixture
def sync(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_OLD_QUEUE_FILE", tmp_path / "old_queue.jsonl")
    monkeypatch.setattr(config, "SUPABASE_CONFIRM_MODE", "local")
    logs = []
    repo, db, _ = make_repo(logs=logs)
    s = ss.InventorySync(log=logs.append, repo=repo, outbox=tmp_path / "state" / "db_outbox.jsonl")
    s.fake_db, s.logs = db, logs
    yield s
    s.close()


def test_sync_record_then_confirm(sync):
    r = result("c1", added=[("milk", 1, ["L,1"])], removed=[("broccoli", 1, [])])
    sync.record(r).result()
    assert sync.fake_db.qty().get("milk") is None and len(sync.fake_db.stock_event) == 2
    sync.decide(r, "confirm").result()
    assert sync.fake_db.qty()["milk"] == 1 and "broccoli" not in sync.fake_db.qty()
    sync.decide(r, "confirm").result()                                        # 재전송 → 두 번 반영 안 함
    assert sync.fake_db.qty()["milk"] == 1 and sync.outbox_count() == 0


def test_sync_offline_queues_and_resends_in_order(sync):
    db = sync.fake_db
    db.down = True
    r = result("c1", added=[("apple", 2, ["C,1", "C,1"])])
    assert sync.record(r).result() == 1
    assert sync.decide(r, "confirm").result() == 2                            # 시연 루프는 멈추지 않고 대기열에 쌓임
    assert sync.outbox_count() == 2 and any("DB 저장 대기 중 2건" in m for m in sync.logs)
    lines = sync.outbox.read_text(encoding="utf-8")
    assert SECRET not in lines and PUBLISHABLE not in lines
    db.down = False
    assert sync.flush() == 0                                                  # 다시 연결 → 순서대로 전송
    assert db.qty()["apple"] == 2 and sync.outbox_count() == 0 and not sync.outbox.exists()


def test_sync_next_capture_sends_queued_first(sync):
    db = sync.fake_db
    db.down = True
    sync.record(result("c1", added=[("egg", 1, [])])).result()
    db.down = False
    sync.record(result("c2", added=[("milk", 1, [])])).result()               # 다음 촬영 때 예전 것부터
    assert [e["capture_id"] for e in db.stock_event] == ["c1", "c2"] and sync.outbox_count() == 0


def test_sync_mismatch_raises_alert_and_keeps_pending(sync):
    r = result("c1", removed=[("chicken", 3, [])])
    sync.record(r).result()
    sync.decide(r, "confirm").result()
    alerts = sync.pop_alerts()
    assert len(alerts) == 1 and alerts[0]["food_id"] == "chicken" and alerts[0]["needs_confirmation"]
    assert sync.fake_db.qty()["chicken"] == 1 and sync.fake_db.stock_event[0]["status"] == "pending"
    assert sync.outbox_count() == 0 and sync.pop_alerts() == []               # 확인 필요는 재전송 대상이 아님


def test_sync_ipad_mode_only_records(sync, monkeypatch):
    sync.confirm_here = False
    r = result("c1", added=[("milk", 1, [])])
    sync.record(r).result()
    assert sync.decide(r, "confirm") is None and sync.fake_db.stock_event[0]["status"] == "pending"


def test_sync_reset_demo_drops_queue_first(sync):
    db = sync.fake_db
    db.down = True
    sync.record(result("c1", added=[("egg", 1, [])])).result()
    db.down = False
    assert sync.reset_demo() == 5 and sync.outbox_count() == 0 and db.stock_event == []


def test_old_queue_file_is_migrated(tmp_path, monkeypatch):
    old = tmp_path / "captures" / "supabase_queue.jsonl"
    old.parent.mkdir()
    old.write_text(json.dumps({"type": "capture", "result": result("old1", added=[("egg", 1, [])]), "decision": None}) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(config, "SUPABASE_OLD_QUEUE_FILE", old)
    repo, db, _ = make_repo()
    s = ss.InventorySync(log=lambda m: None, repo=repo, outbox=tmp_path / "state" / "db_outbox.jsonl")
    assert not old.exists() and s.outbox_count() == 1
    s.flush()
    assert [e["capture_id"] for e in db.stock_event] == ["old1"]
    s.close()


def test_sync_disabled_without_keys(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_SECRET_KEY", "")
    logs = []
    s = ss.InventorySync(log=logs.append)
    assert not s.enabled and s.record(result("c1")) is None and s.outbox_count() == 0
    assert "미설정" in logs[0]


# ---------------------------------------------------------------------------
# 5. 점검 도구 (읽기 점검) — 가짜 DB 로
# ---------------------------------------------------------------------------
def test_check_tool_read_checks_pass_when_anon_writes_are_denied():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import check_supabase as cs
    db = FakeDB()
    assert cs.read_checks(FakeClient(db, "anon")) is True
    assert all(r[1] in ("정상", "참고") for r in cs.ROWS)
    cs.ROWS.clear()
    assert db.stock_event == []                                              # 읽기 점검은 DB 를 바꾸지 않음
    assert cs.read_checks(FakeClient(db, "service_role")) is False          # 쓰기가 허용되면 실패로 보고
    assert any(r[1] == "실패" for r in cs.ROWS)
    cs.ROWS.clear()


def test_check_tool_write_checks_with_fake_db():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import check_supabase as cs
    repo, db, _ = make_repo()
    assert cs.write_checks(repo) is True
    assert [r[1] for r in cs.ROWS] == ["정상"] * len(cs.ROWS)
    assert db.qty() == {"tofu": 1, "egg": 1, "broccoli": 1, "mushroom": 1, "chicken": 1}   # 원상복구
    cs.ROWS.clear()


def test_resend_from_other_process_keeps_display_names(sync):
    """대기열을 다른 프로세스(flush_outbox)가 보내도 OCR 이름(pkg_*)이 food_item 에 이름으로 들어감."""
    db = sync.fake_db
    db.down = True
    r = result("c1", added=[("pkg_only_in_result", 1, [])])
    r["added"][0]["name"] = "계란"
    sync.need_baseline = True
    r["before"] = {"pkg_only_in_result": 0}
    sync.record(r).result()
    db.down = False
    assert "pkg_only_in_result" not in config.ITEMS                      # 이 프로세스의 품목 목록에도 없음
    sync.flush()
    assert db.food_item["pkg_only_in_result"]["food_name"] == "계란"


def test_missing_schema_is_reported_clearly():
    e = classify(APIError({"message": "Could not find the table 'public.food_item' in the schema cache", "code": "PGRST205"}))
    assert e.kind == "missing_schema" and "schema.sql" in e.message and not e.retryable
    assert classify(APIError({"message": "Could not find the function", "code": "PGRST202"})).kind == "missing_schema"
