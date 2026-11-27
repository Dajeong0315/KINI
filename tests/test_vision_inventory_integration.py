"""Vision 연결부(fridge_vision/kini_inventory_sink.py) → A 재고 이벤트 API → 임시 SQLite 통합 검증.

- 실제 A/B 서버 코드(app.main)를 로컬 HTTP 서버(127.0.0.1, 임의 포트)로 띄우고, 임시 SQLite + 합성 계정·데모 재고만 사용한다.
- Vision 인식 결과는 **합성 입력**이다 (모델 실행 없음). 실제 인식 → API 시험은 fridge_vision/tools/kini_e2e_check.py.
- 외부 AI·Supabase·Redis 로 아무것도 보내지 않는다 (KINI_USE_OPENAI=0, 연결부는 로컬 주소만 허용).
- 재고를 직접 SQL 로 바꾸지 않는다. DB 는 결과 확인(읽기)과 시험용 계정 역할·만료 설정에만 쓴다.
- 토큰·비밀번호를 출력하지 않는다 (--tb=short 권장).
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from pathlib import Path
from uuid import uuid4

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fridge_vision"))
from kini_inventory_sink import (KiniApiError, KiniInventoryClient, KiniInventorySink,  # noqa: E402
                                   detected_label, events_from_result)

PASSWORDS = {"owner": "synthetic-owner-password", "other": "synthetic-other-password",
             "viewer": "synthetic-viewer-password"}


# ---------------------------------------------------------------------------
# 임시 DB + 실제 HTTP 서버
# ---------------------------------------------------------------------------
class Server:
    def __init__(self, app):
        from werkzeug.serving import make_server
        self.srv = make_server("127.0.0.1", 0, app, threaded=True)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.srv.shutdown()
        self.thread.join(5)


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app.services.local_identity import create_account
    from app.services.recommendation_repository import open_db

    path = tmp_path / "vision-integration.sqlite3"
    monkeypatch.setenv("KINI_DB_PATH", str(path))
    monkeypatch.setenv("KINI_USE_OPENAI", "0")
    with closing(open_db()) as db:                 # 데모 가구 1: 재료 9종 각 1000 g
        create_account(db, "owner", PASSWORDS["owner"], 1)
        with db:
            db.execute("INSERT INTO households VALUES (2,'synthetic other household')")
        create_account(db, "other", PASSWORDS["other"], 2)
        viewer = create_account(db, "viewer", PASSWORDS["viewer"], 1)
        with db:                                     # 시험용 계정 역할 설정 (재고 아님)
            db.execute("UPDATE account_households SET role='viewer' WHERE account_id=?", (viewer,))
    from app.main import app
    server = Server(app)
    ctx = {"path": path, "server": server, "app": app}
    yield ctx
    ctx["server"].stop()


def client(env, who="owner", household=None, **kw):
    hid = household or (2 if who == "other" else 1)
    return KiniInventoryClient(env["server"].url, login_name=who, password=PASSWORDS[who], household_id=hid, **kw)


def ro(env):
    """결과 확인용 읽기 연결 (재고 변경에 쓰지 않음)."""
    db = sqlite3.connect(env["path"])
    db.row_factory = sqlite3.Row
    return db


def lots(snapshot, ingredient_id):
    return [i for i in snapshot["items"] if i["ingredient_id"] == ingredient_id]


def total(snapshot, ingredient_id):
    return sum(i["quantity_g"] for i in lots(snapshot, ingredient_id))


def vision_result(capture_id, added=(), removed=()):
    """fridge_vision 의 compare 결과 형식 (합성). added/removed: (item_id, 한글 이름, 수량)."""
    mk = lambda xs: [{"item_id": i, "name": n, "quantity": q, "locations": []} for i, n, q in xs]
    return {"capture_id": capture_id, "added": mk(added), "removed": mk(removed),
            "unknown": {"before": 0, "after": 0}, "needs_confirmation": False}


def quiet_sink(c, tmp_path):
    lines = []
    return KiniInventorySink(c, tmp_path / "kini_queue.jsonl", log=lines.append), lines


TOFU, CARROT, MUSHROOM, RICE, PEPPER = 4, 3, 5, 1, 9


# ---------------------------------------------------------------------------
# 1. 연결: 인식 결과 → 대기 이벤트 (재고 변화 없음)
# ---------------------------------------------------------------------------
def test_vision_result_becomes_pending_events_only(env, tmp_path):
    c = client(env)
    before = c.snapshot()
    sink, log = quiet_sink(c, tmp_path)
    result = vision_result("cap-1", added=[("tofu", "두부", 2)], removed=[("carrot", "당근", 1)])
    sent = sink.record(result)
    assert [(r["action"], r["detected_label"], r["status"]) for r in sent] == [
        ("stock_in", "두부", "pending_confirmation")] * 2 + [("stock_out", "당근", "pending_confirmation")]
    assert all(r["source"] == "yolo" and r["requires_confirmation"] and r["inventory_item_id"] is None for r in sent)
    after = c.snapshot()
    assert after["items"] == before["items"]                         # 접수만으로 재고 변화 없음
    assert after["inventory_version"] == 0 and after["pending_event_count"] == 3
    assert after["source"] == "local_sqlite" and after["cloud_inventory_export_enabled"] is False
    # 같은 촬영 결과 재전송(재시도·재시작) → 같은 이벤트, 중복 생성 없음
    again = sink.record(result)
    assert [r["event_id"] for r in again] == [r["event_id"] for r in sent]
    with closing(ro(env)) as db:
        assert db.execute("SELECT count(*) FROM inventory_events").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM inventory_event_changes").fetchone()[0] == 0


def test_payload_contains_only_contract_fields_and_local_only(env):
    evs = events_from_result(vision_result("cap-x", added=[("pepper", "피망·고추", 1)]))
    assert set(evs[0]) == {"client_event_id", "source", "action", "detected_label", "confidence"}
    assert evs[0]["detected_label"] == "피망" and evs[0]["confidence"] is None
    with pytest.raises(ValueError):                                  # 원격(Supabase 등) 주소 거부
        KiniInventoryClient("https://example.supabase.co", token="x")
    assert "token" not in repr(KiniInventoryClient(env["server"].url, token="secret-value"))


# ---------------------------------------------------------------------------
# 2. 인식명 ↔ 표준 재료 매핑 (자동 확정 금지)
# ---------------------------------------------------------------------------
def test_label_mapping_is_suggestion_only(env, tmp_path):
    from app.services.ingredient_normalization import normalize_ingredient

    cases = {  # Vision 품목 → detected_label → A 정규화 결과
        ("tofu", "두부"): (TOFU, "matched"), ("chicken", "닭고기"): (2, "matched"),
        ("carrot", "당근"): (CARROT, "matched"), ("mushroom", "버섯"): (MUSHROOM, "matched"),
        ("pepper", "피망·고추"): (PEPPER, "matched"),
        ("butter", "버터"): (None, "unknown"),                       # DB 미등록 재료
        ("pkg_ff77e260", "비비고 왕교자"): (None, "unknown"),          # OCR 상품명
        ("pkg_x", "가스테라"): (None, "unknown"),                      # OCR 오탈자
        ("ham", "햄·소시지"): (None, "unknown"),
    }
    with closing(ro(env)) as db:
        db.execute("PRAGMA query_only=1")
        for (item_id, name), (want_id, want_status) in cases.items():
            m = normalize_ingredient(db, detected_label({"item_id": item_id, "name": name}))
            assert (m["ingredient_id"], m["match_status"]) == (want_id, want_status), name
    # 등록해도 어떤 것도 자동 확정되지 않음 (미등록·OCR 이름 포함)
    c = client(env)
    sink, _ = quiet_sink(c, tmp_path)
    sent = sink.record(vision_result("cap-map", added=[(k[0], k[1], 1) for k in cases]))
    assert {r["status"] for r in sent} == {"pending_confirmation"}
    assert c.snapshot()["inventory_version"] == 0
    # Unknown 은 added/removed 에 없으므로 이벤트가 생기지 않음
    assert events_from_result({"capture_id": "cap-u", "added": [], "removed": [],
                               "unknown": {"before": 0, "after": 2}}) == []


# ---------------------------------------------------------------------------
# 3. 입고
# ---------------------------------------------------------------------------
def test_stock_in_new_and_additional_lots(env, tmp_path):
    owner, other = client(env), client(env, "other")
    # 신규 재료 입고 (재고가 없던 가구 2)
    sink2, _ = quiet_sink(other, tmp_path)
    ev = sink2.record(vision_result("h2-in-1", added=[("tofu", "두부", 2)]))
    assert other.snapshot()["items"] == []                            # 확정 전 변화 없음
    a = other.confirm(ev[0]["event_id"], {"ingredient_id": TOFU, "quantity_g": 200, "expiry_date": "2027-01-01"})
    b = other.confirm(ev[1]["event_id"], {"ingredient_id": TOFU, "quantity_g": 300, "expiry_date": "2027-02-01"})
    assert a["status"] == b["status"] == "applied" and a["inventory_item_id"] != b["inventory_item_id"]
    s = other.snapshot()
    assert sorted((i["quantity_g"], i["expiry_date"]) for i in lots(s, TOFU)) == [(200, "2027-01-01"), (300, "2027-02-01")]
    assert s["inventory_version"] == 2 and s["pending_event_count"] == 0
    # 기존 재료 추가 입고 (가구 1 두부 1000 g) → 새 lot, 기존 lot 유지
    before = owner.snapshot()
    sink1, _ = quiet_sink(owner, tmp_path)
    ev1 = sink1.record(vision_result("h1-in-1", added=[("tofu", "두부", 1)]))
    owner.confirm(ev1[0]["event_id"], {"ingredient_id": TOFU, "quantity_g": 250})
    after = owner.snapshot()
    assert total(after, TOFU) == total(before, TOFU) + 250 and len(lots(after, TOFU)) == len(lots(before, TOFU)) + 1
    assert lots(after, TOFU)[0] == lots(before, TOFU)[0]
    with closing(ro(env)) as db:
        ch = db.execute("SELECT quantity_g,before_quantity_g,after_quantity_g FROM inventory_event_changes "
                        "WHERE event_id=?", (ev1[0]["event_id"],)).fetchall()
        assert [tuple(r) for r in ch] == [(250, 0, 250)]
    # 입고에 기존 재고 행을 지정하면 거절 (자동 합산 금지)
    ev2 = sink1.record(vision_result("h1-in-2", added=[("tofu", "두부", 1)]))
    with pytest.raises(KiniApiError) as e:
        owner.confirm(ev2[0]["event_id"], {"ingredient_id": TOFU, "quantity_g": 10,
                                            "inventory_item_id": lots(after, TOFU)[0]["id"]})
    assert e.value.status == 400 and owner.snapshot()["items"] == after["items"]


def test_rejected_stock_in_never_changes_stock(env, tmp_path):
    c = client(env)
    sink, _ = quiet_sink(c, tmp_path)
    before = c.snapshot()
    result = vision_result("cap-rej", added=[("tofu", "두부", 1)])
    ev = sink.record(result)
    sink.decide(result, "reject")                                     # Vision 화면에서 n
    with closing(ro(env)) as db:
        assert db.execute("SELECT status FROM inventory_events WHERE id=?", (ev[0]["event_id"],)).fetchone()[0] == "rejected"
    with pytest.raises(KiniApiError) as e:
        c.confirm(ev[0]["event_id"], {"ingredient_id": TOFU, "quantity_g": 100})
    assert e.value.status == 409
    after = c.snapshot()
    assert after["items"] == before["items"] and after["inventory_version"] == 0 and after["pending_event_count"] == 0
    # Vision 화면에서 y(반영)는 A 확정을 하지 않음 → 대기 유지
    r2 = vision_result("cap-yes", added=[("tofu", "두부", 1)])
    sink.record(r2)
    sink.decide(r2, "confirm")
    assert c.snapshot()["pending_event_count"] == 1 and c.snapshot()["items"] == before["items"]


# ---------------------------------------------------------------------------
# 4. 출고·차감
# ---------------------------------------------------------------------------
def test_stock_out_partial_full_and_multi_lot(env, tmp_path):
    c = client(env)
    sink, _ = quiet_sink(c, tmp_path)
    s0 = c.snapshot()
    carrot = lots(s0, CARROT)[0]
    ev = sink.record(vision_result("out-1", removed=[("carrot", "당근", 2)]))
    assert c.snapshot()["items"] == s0["items"]                       # 인식만으로 출고 확정 안 됨
    c.confirm(ev[0]["event_id"], {"ingredient_id": CARROT, "quantity_g": 300, "inventory_item_id": carrot["id"]})
    assert lots(c.snapshot(), CARROT)[0]["quantity_g"] == 700          # 일부 사용
    c.confirm(ev[1]["event_id"], {"ingredient_id": CARROT, "quantity_g": 700, "inventory_item_id": carrot["id"]})
    assert lots(c.snapshot(), CARROT)[0]["quantity_g"] == 0            # 전량 사용 (0 g 행 유지)
    # 여러 재고 행에서 나누어 사용: 두부 1000 g + 입고 200 g 에서 1100 g
    ein = sink.record(vision_result("in-for-split", added=[("tofu", "두부", 1)]))
    c.confirm(ein[0]["event_id"], {"ingredient_id": TOFU, "quantity_g": 200})
    a, b = lots(c.snapshot(), TOFU)
    eo = sink.record(vision_result("out-split", removed=[("tofu", "두부", 2)]))
    c.confirm(eo[0]["event_id"], {"ingredient_id": TOFU, "quantity_g": 1000, "inventory_item_id": a["id"]})
    c.confirm(eo[1]["event_id"], {"ingredient_id": TOFU, "quantity_g": 100, "inventory_item_id": b["id"]})
    assert [i["quantity_g"] for i in lots(c.snapshot(), TOFU)] == [0, 100]
    with closing(ro(env)) as db:
        rows = db.execute("SELECT before_quantity_g,after_quantity_g FROM inventory_event_changes c "
                          "JOIN inventory_events e ON e.id=c.event_id WHERE e.action='stock_out' ORDER BY c.rowid").fetchall()
        assert [tuple(r) for r in rows] == [(1000, 700), (700, 0), (1000, 0), (200, 100)]


@pytest.mark.parametrize("body", [
    {"quantity_g": 0}, {"quantity_g": -5}, {"quantity_g": "100"}, {"quantity_g": True}, {"quantity_g": 1e9},
    {"quantity_g": 1001},                                    # 재고 부족 (1000 g)
    {"quantity_g": 10, "unit": "개"},                          # 단위 필드 (g 외 단위 불가)
    {"quantity_g": 10, "ingredient_id": TOFU},                 # 다른 재료의 재고 행
    {"quantity_g": 10, "expiry_date": "2027-01-01"},           # 출고에 소비기한
    {"quantity_g": 10, "inventory_item_id": None},             # 재고 행 없음
])
def test_invalid_stock_out_is_rejected_without_side_effects(env, tmp_path, body):
    c = client(env)
    sink, _ = quiet_sink(c, tmp_path)
    s0 = c.snapshot()
    carrot = lots(s0, CARROT)[0]
    ev = sink.record(vision_result("bad-" + uuid4().hex, removed=[("carrot", "당근", 1)]))[0]
    payload = {"ingredient_id": CARROT, "inventory_item_id": carrot["id"], **body}
    with pytest.raises(KiniApiError) as e:
        c.confirm(ev["event_id"], payload)
    assert e.value.status == 400
    s1 = c.snapshot()
    assert s1["items"] == s0["items"] and s1["inventory_version"] == 0 and s1["pending_event_count"] == 1
    with closing(ro(env)) as db:
        assert db.execute("SELECT count(*) FROM inventory_event_changes").fetchone()[0] == 0
        assert db.execute("SELECT status,confirmation_sha256 FROM inventory_events WHERE id=?",
                          (ev["event_id"],)).fetchone()[:] == ("pending_confirmation", None)
        assert db.execute("SELECT min(quantity_g) FROM inventory_items").fetchone()[0] >= 0


def test_other_household_stock_is_never_deducted(env, tmp_path):
    owner, other = client(env), client(env, "other")
    h1_item = lots(owner.snapshot(), CARROT)[0]
    sink2, _ = quiet_sink(other, tmp_path)
    ev = sink2.record(vision_result("h2-out", removed=[("carrot", "당근", 1)]))[0]
    with pytest.raises(KiniApiError) as e:                         # 다른 가구 재고 행 지정
        other.confirm(ev["event_id"], {"ingredient_id": CARROT, "quantity_g": 10, "inventory_item_id": h1_item["id"]})
    assert e.value.status == 400
    assert lots(owner.snapshot(), CARROT)[0] == h1_item


def test_rejected_stock_out_keeps_stock(env, tmp_path):
    c = client(env)
    sink, _ = quiet_sink(c, tmp_path)
    s0 = c.snapshot()
    r = vision_result("out-rej", removed=[("tofu", "두부", 1)])
    sink.record(r)
    sink.decide(r, "reject")
    assert c.snapshot()["items"] == s0["items"] and c.snapshot()["pending_event_count"] == 0


# ---------------------------------------------------------------------------
# 5. 중복·동시·장애
# ---------------------------------------------------------------------------
def test_idempotency_and_conflicts(env, tmp_path):
    c = client(env)
    ev = events_from_result(vision_result("dup", added=[("tofu", "두부", 1)]))[0]
    first, second = c.ingest(ev), c.ingest(ev)
    assert first["event_id"] == second["event_id"]
    with pytest.raises(KiniApiError) as e:                         # 같은 ID, 다른 내용
        c.ingest({**ev, "detected_label": "당근"})
    assert e.value.status == 409
    body = {"ingredient_id": TOFU, "quantity_g": 120}
    a, b = c.confirm(first["event_id"], body), c.confirm(first["event_id"], body)   # 응답 유실 후 재시도
    assert a == b
    assert c.ingest(ev)["status"] == "applied"                        # 재수신은 현재 상태
    with pytest.raises(KiniApiError) as e:
        c.confirm(first["event_id"], {**body, "quantity_g": 121})
    assert e.value.status == 409
    with pytest.raises(KiniApiError) as e:
        c.reject(first["event_id"])
    assert e.value.status == 409
    with closing(ro(env)) as db:
        assert db.execute("SELECT count(*) FROM inventory_event_changes WHERE event_id=?", (a["event_id"],)).fetchone()[0] == 1
    assert c.snapshot()["inventory_version"] == 1


def test_same_object_in_different_captures_is_not_deduplicated_by_server(env, tmp_path):
    """연속 프레임/다른 촬영에서 같은 물체가 다시 인식되면 촬영 ID 가 달라 서로 다른 이벤트가 된다.
    서버의 client_event_id 중복 방지로는 막을 수 없음을 기록하는 시험 (사람 확인·거절이 필요)."""
    c = client(env)
    sink, _ = quiet_sink(c, tmp_path)
    a = sink.record(vision_result("cap-frame-1", removed=[("carrot", "당근", 1)]))
    b = sink.record(vision_result("cap-frame-2", removed=[("carrot", "당근", 1)]))
    assert a[0]["event_id"] != b[0]["event_id"]
    s = c.snapshot()
    assert s["pending_event_count"] == 2 and s["inventory_version"] == 0   # 확정 전이라 재고는 그대로


def test_concurrent_stock_out_over_http_cannot_overdraw(env, tmp_path):
    c = client(env)
    item = lots(c.snapshot(), CARROT)[0]
    sink, _ = quiet_sink(c, tmp_path)
    evs = sink.record(vision_result("conc", removed=[("carrot", "당근", 2)]))

    def apply(ev):
        mine = client(env)                                             # 다른 연결·세션
        try:
            mine.confirm(ev["event_id"], {"ingredient_id": CARROT, "quantity_g": 700, "inventory_item_id": item["id"]})
            return 200
        except KiniApiError as e:
            return e.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(apply, evs))
    assert codes == [200, 400]
    s = c.snapshot()
    assert lots(s, CARROT)[0]["quantity_g"] == 300 and s["inventory_version"] == 1 and s["pending_event_count"] == 1


def test_network_failure_queue_and_server_restart(env, tmp_path):
    from app.main import app
    c = client(env)
    s0 = c.snapshot()
    # 서버가 꺼진 동안 인식 결과 → 대기열에 보관
    env["server"].stop()
    sink, log = quiet_sink(client(env, timeout=1), tmp_path)
    assert sink.record(vision_result("offline-1", removed=[("carrot", "당근", 1)])) == []
    assert any("대기열" in l for l in log) and (tmp_path / "kini_queue.jsonl").exists()
    # 서버 재시작 (같은 임시 DB) → 다음 전송 때 대기열 먼저, 같은 ID 라 중복 없음
    env["server"] = Server(app)
    sink.client = client(env)
    sent = sink.record(vision_result("online-2", added=[("tofu", "두부", 1)]))
    assert [r["detected_label"] for r in sent] == ["당근", "두부"]
    assert sink.record(vision_result("online-2", added=[("tofu", "두부", 1)]))[0]["event_id"] == sent[1]["event_id"]
    assert (tmp_path / "kini_queue.jsonl").read_text(encoding="utf-8") == ""
    # 확정 → 응답을 받았다고 가정하지 않고 재시도 → 서버 재시작 → 다시 재시도
    item = lots(s0, CARROT)[0]
    body = {"ingredient_id": CARROT, "quantity_g": 100, "inventory_item_id": item["id"]}
    sink.client.confirm(sent[0]["event_id"], body)
    env["server"].stop()
    env["server"] = Server(app)
    c2 = client(env)
    assert c2.confirm(sent[0]["event_id"], body)["status"] == "applied"
    s1 = c2.snapshot()
    assert lots(s1, CARROT)[0]["quantity_g"] == 900 and s1["inventory_version"] == 1 and s1["pending_event_count"] == 1


# ---------------------------------------------------------------------------
# 6. 인증·가구 권한
# ---------------------------------------------------------------------------
def test_auth_and_household_permissions(env, tmp_path):
    from app.services.local_identity import login, revoke
    from app.services.recommendation_repository import open_db

    url = env["server"].url
    ev = events_from_result(vision_result("auth", removed=[("carrot", "당근", 1)]))[0]
    # 미인증·잘못된 토큰
    assert requests.post(url + "/api/data/inventory/events", json=ev, timeout=5).status_code == 401
    assert requests.get(url + "/api/data/inventory", headers={"Authorization": "Bearer not-a-token"}, timeout=5).status_code == 403
    # 만료·철회 토큰 (내부 함수로 짧은 세션 발급 — 시험 준비용)
    with closing(open_db()) as db:
        expired = login(db, "owner", PASSWORDS["owner"], 1, ttl_seconds=1, now=1_000_000)
        revoked = login(db, "owner", PASSWORDS["owner"], 1)
        revoke(db, revoked)
    for t in (expired, revoked):
        r = requests.post(url + "/api/data/inventory/events", json=ev, timeout=5, headers={"Authorization": "Bearer " + t})
        assert r.status_code == 403
    with closing(ro(env)) as db:
        assert db.execute("SELECT count(*) FROM inventory_events").fetchone()[0] == 0
    # 다른 가구: 이벤트 확정 404, 조회 격리
    owner, other = client(env), client(env, "other")
    mine = owner.ingest(ev)
    with pytest.raises(KiniApiError) as e:
        other.confirm(mine["event_id"], {"ingredient_id": CARROT, "quantity_g": 1,
                                          "inventory_item_id": lots(owner.snapshot(), CARROT)[0]["id"]})
    assert e.value.status == 404
    with pytest.raises(KiniApiError) as e:
        other.reject(mine["event_id"])
    assert e.value.status == 404
    assert other.snapshot()["items"] == [] and other.snapshot()["pending_event_count"] == 0
    # viewer: 조회만
    viewer = client(env, "viewer")
    s = viewer.snapshot()
    assert s["pending_event_count"] == 1
    for call in (lambda: viewer.ingest(events_from_result(vision_result("v", added=[("tofu", "두부", 1)]))[0]),
                 lambda: viewer.confirm(mine["event_id"], {"ingredient_id": CARROT, "quantity_g": 1,
                                                           "inventory_item_id": lots(s, CARROT)[0]["id"]}),
                 lambda: viewer.reject(mine["event_id"])):
        with pytest.raises(KiniApiError) as e:
            call()
        assert e.value.status == 403
    after = owner.snapshot()
    assert after["items"] == s["items"] and after["pending_event_count"] == 1
    with closing(ro(env)) as db:
        assert db.execute("SELECT status FROM inventory_events WHERE id=?", (mine["event_id"],)).fetchone()[0] == "pending_confirmation"


def test_secrets_are_not_logged(env, tmp_path):
    c = client(env)
    sink, log = quiet_sink(c, tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        sink.record(vision_result("log", added=[("tofu", "두부", 1)]))
        env["server"].stop()
        sink.record(vision_result("log2", added=[("tofu", "두부", 1)]))     # 실패 로그 포함
    text = buf.getvalue() + "\n".join(log) + (tmp_path / "kini_queue.jsonl").read_text(encoding="utf-8")
    assert PASSWORDS["owner"] not in text and c._token not in text and "Bearer" not in text


# ---------------------------------------------------------------------------
# 7. A 출고 확정 → B 조리 완료 (추가 차감 없음)
# ---------------------------------------------------------------------------
def _draft(url):
    req = {"household_id": 1, "participant_ids": [3], "meal_type": "dinner", "purchase_allowed": False}
    rec = requests.post(url + "/api/recommendations", json=req, timeout=30).json()
    meal = requests.post(url + "/api/meal-sessions", json=req, timeout=10).json()
    r = requests.post(url + "/api/cooking-sessions", json={"meal_session_id": meal["session_id"],
                      "recommendation_request_id": rec["request_id"], "recipe_id": 2}, timeout=10)
    assert r.status_code == 201
    return r.json()


def _complete(url, draft, headers, event_ids, items=({"member_id": 3, "servings": 1},)):
    return requests.post(url + f"/api/cooking-sessions/{draft['id']}/complete", headers=headers, timeout=10, json={
        "confirmed": True, "eaten_at": "2026-11-26T12:00:00", "items": list(items), "inventory_event_ids": event_ids})


@pytest.mark.parametrize("items", [({"member_id": 3, "servings": 1},), ({"member_id": 3, "servings": 0},)])
def test_a_stock_out_then_b_completion_deducts_once(env, tmp_path, items):
    url = env["server"].url
    owner = client(env)
    owner.snapshot()
    headers = {"Authorization": "Bearer " + owner._token}
    draft = _draft(url)                                             # 두부 버섯밥: 재료별 사용량
    s0 = owner.snapshot()
    sink, _ = quiet_sink(owner, tmp_path)
    event_ids = []
    for ing in draft["ingredients"]:
        lot = lots(s0, ing["ingredient_id"])[0]
        ev = owner.ingest({"client_event_id": str(uuid4()), "source": "yolo" if ing["ingredient_id"] == TOFU else "manual",
                           "action": "stock_out", "detected_label": lot["name"], "confidence": None})
        owner.confirm(ev["event_id"], {"ingredient_id": ing["ingredient_id"], "quantity_g": ing["quantity_g"],
                                       "inventory_item_id": lot["id"]})
        event_ids.append(ev["event_id"])
    after_out = owner.snapshot()
    for ing in draft["ingredients"]:
        assert total(after_out, ing["ingredient_id"]) == total(s0, ing["ingredient_id"]) - ing["quantity_g"]
    with closing(ro(env)) as db:
        meals0 = db.execute("SELECT count(*) FROM meal_records").fetchone()[0]
    done = _complete(url, draft, headers, event_ids, items)
    assert done.status_code == 201
    assert owner.snapshot() == after_out                            # B 완료 전후 재고 같음
    with closing(ro(env)) as db:
        assert db.execute("SELECT count(*) FROM meal_records").fetchone()[0] == meals0 + 1
    # 재사용된 출고 → 409, 기록·재고 변화 없음
    second = _draft(url)
    r = _complete(url, second, headers, event_ids)
    assert r.status_code == 409 and r.json().get("code") == "inventory_confirmation_required"
    assert owner.snapshot() == after_out
    with closing(ro(env)) as db:
        assert db.execute("SELECT count(*) FROM meal_records").fetchone()[0] == meals0 + 1


def test_b_completion_rejects_unconfirmed_foreign_and_short_stock_out(env, tmp_path):
    url = env["server"].url
    owner, other = client(env), client(env, "other")
    owner.snapshot(); other.snapshot()
    h_owner = {"Authorization": "Bearer " + owner._token}
    draft = _draft(url)
    s0 = owner.snapshot()
    with closing(ro(env)) as db:
        meals0 = db.execute("SELECT count(*) FROM meal_records").fetchone()[0]
    # 미확정 출고
    pending = owner.ingest({"client_event_id": str(uuid4()), "source": "yolo", "action": "stock_out",
                            "detected_label": "두부", "confidence": None})
    assert _complete(url, draft, h_owner, [pending["event_id"]]).status_code == 409
    # 다른 가구의 확정 출고 (가구 2 에 두부 입고 → 출고 확정)
    ein = other.ingest({"client_event_id": str(uuid4()), "source": "manual", "action": "stock_in",
                        "detected_label": "두부", "confidence": None})
    lot = other.confirm(ein["event_id"], {"ingredient_id": TOFU, "quantity_g": 500})["inventory_item_id"]
    eout = other.ingest({"client_event_id": str(uuid4()), "source": "manual", "action": "stock_out",
                         "detected_label": "두부", "confidence": None})
    other.confirm(eout["event_id"], {"ingredient_id": TOFU, "quantity_g": 400, "inventory_item_id": lot})
    assert _complete(url, draft, h_owner, [eout["event_id"]]).status_code in (403, 409)
    # 사용량보다 적은 출고
    short_ids = []
    for ing in draft["ingredients"]:
        l = lots(s0, ing["ingredient_id"])[0]
        ev = owner.ingest({"client_event_id": str(uuid4()), "source": "manual", "action": "stock_out",
                           "detected_label": l["name"], "confidence": None})
        owner.confirm(ev["event_id"], {"ingredient_id": ing["ingredient_id"], "quantity_g": ing["quantity_g"] / 2,
                                       "inventory_item_id": l["id"]})
        short_ids.append(ev["event_id"])
    snap = owner.snapshot()
    assert _complete(url, draft, h_owner, short_ids).status_code == 409
    assert owner.snapshot() == snap
    with closing(ro(env)) as db:
        assert db.execute("SELECT count(*) FROM meal_records").fetchone()[0] == meals0     # 일부 기록도 남지 않음
        linked = db.execute("SELECT count(*) FROM inventory_events WHERE id IN (%s) AND cooking_session_id IS NOT NULL"
                            % ",".join("?" * len(short_ids)), short_ids).fetchone()[0] if _has_col(db, "inventory_events", "cooking_session_id") else 0
        assert linked == 0
    # 미인증 완료
    assert _complete(url, draft, {}, short_ids).status_code == 401


def _has_col(db, table, col):
    return any(r[1] == col for r in db.execute(f"PRAGMA table_info({table})"))


# ---------------------------------------------------------------------------
# 8. 조회·추적·무결성
# ---------------------------------------------------------------------------
def test_traceability_persistence_and_integrity(env, tmp_path):
    from app.main import app
    c = client(env)
    s0 = c.snapshot()
    sink, _ = quiet_sink(c, tmp_path)
    ev_in = sink.record(vision_result("t-in", added=[("tofu", "두부", 1)]))[0]
    ev_out = sink.record(vision_result("t-out", removed=[("carrot", "당근", 1)]))[0]
    r_rej = vision_result("t-rej", added=[("mushroom", "버섯", 1)])
    ev_rej = sink.record(r_rej)[0]
    sink.decide(r_rej, "reject")
    sink.record(vision_result("t-pend", added=[("rice", "쌀", 1)]))
    c.confirm(ev_in["event_id"], {"ingredient_id": TOFU, "quantity_g": 150, "expiry_date": "2027-03-01"})
    carrot = lots(s0, CARROT)[0]
    c.confirm(ev_out["event_id"], {"ingredient_id": CARROT, "quantity_g": 250, "inventory_item_id": carrot["id"]})
    s1 = c.snapshot()
    # 재시작 후에도 유지
    env["server"].stop()
    env["server"] = Server(app)
    c2 = client(env)
    assert c2.snapshot() == s1 and s1["inventory_version"] == 2 and s1["pending_event_count"] == 1
    with closing(ro(env)) as db:
        statuses = dict(db.execute("SELECT detected_label,status FROM inventory_events").fetchall())
        assert statuses == {"두부": "applied", "당근": "applied", "버섯": "rejected", "쌀": "pending_confirmation"}
        # 어떤 이벤트가 어느 재고를 얼마나 바꿨는지
        trace = db.execute("""SELECT e.action,c.inventory_item_id,c.quantity_g,c.before_quantity_g,c.after_quantity_g,
                              c.confirmed_by IS NOT NULL FROM inventory_event_changes c JOIN inventory_events e ON e.id=c.event_id
                              ORDER BY c.rowid""").fetchall()
        assert [tuple(r) for r in trace] == [("stock_in", ev_in_item(db, ev_in), 150, 0, 150, 1),
                                             ("stock_out", carrot["id"], 250, 1000, 750, 1)]
        assert db.execute("SELECT count(*) FROM inventory_event_changes WHERE event_id=?", (ev_rej["event_id"],)).fetchone()[0] == 0
        # 재고 = 초기값 + 변경 이력의 합
        for item in s1["items"]:
            base = next((i["quantity_g"] for i in s0["items"] if i["id"] == item["id"]), 0)
            delta = sum((r["after_quantity_g"] - r["before_quantity_g"]) for r in db.execute(
                "SELECT before_quantity_g,after_quantity_g FROM inventory_event_changes WHERE inventory_item_id=?", (item["id"],)))
            assert item["quantity_g"] == base + delta
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    # 개인 재고 캐시 없음
    r = requests.get(env["server"].url + "/api/data/cache/status", timeout=5,
                     headers={"Authorization": "Bearer " + c2._token})
    assert r.status_code == 200 and r.json()["personal_data_cached"] is False


def ev_in_item(db, ev):
    return db.execute("SELECT inventory_item_id FROM inventory_events WHERE id=?", (ev["event_id"],)).fetchone()[0]


def test_legacy_inventory_items_endpoint_requires_auth_and_event_flow(env):
    url = env["server"].url
    owner = client(env, "other")                                   # 가구 2 소유자
    before = owner.snapshot()
    r = requests.post(url + "/api/inventory/items", timeout=5, json={       # 토큰 없이 가구 2 에 추가 시도
        "household_id": 2, "ingredient_id": TOFU, "name": "두부", "quantity": 100, "unit": "g"})
    after = owner.snapshot()
    assert r.status_code in (401, 403) and after["items"] == before["items"]


def test_inventory_modules_do_not_use_cache_or_cloud():
    src = (ROOT / "app/services/inventory_events.py").read_text(encoding="utf-8")
    assert not any(w in src for w in ("redis", "cache", "supabase", "requests", "openai"))
    client_src = (ROOT / "fridge_vision/kini_inventory_client.py").read_text(encoding="utf-8")
    assert "supabase" not in client_src.split('"""', 2)[2].lower()
