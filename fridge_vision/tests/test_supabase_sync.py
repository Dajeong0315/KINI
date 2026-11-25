"""supabase_sync 시험 — 실제 Supabase 없이, 같은 REST 규칙으로 답하는 가짜 서버를 띄워 확인.

실행: python tests/test_supabase_sync.py   (pytest 가 있으면 python -m pytest tests 도 가능)
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import supabase_sync as ss  # noqa: E402

TABLE = "fridge_food"


class FakePostgrest(BaseHTTPRequestHandler):
    """PostgREST 중 supabase_sync 가 쓰는 부분만: GET 필터(eq/is.null)·order, POST, PATCH(pk=eq)."""
    rows: list = []
    fail = False          # True 면 503 (네트워크/서버 장애 흉내)
    requests_log: list = []

    def log_message(self, *a):
        pass

    def _reply(self, code, body=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok_auth(self):
        return self.headers.get("apikey") == "test-key" and self.headers.get("Authorization") == "Bearer test-key"

    def _filtered(self, q):
        out = list(self.rows)
        for k, (v,) in q.items():
            if k in ("select", "order"):
                continue
            if v == "is.null":
                out = [r for r in out if r.get(k) is None]
            elif v.startswith("eq."):
                out = [r for r in out if str(r.get(k)) == v[3:]]
        if "order" in q:
            for part in reversed(q["order"][0].split(",")):
                col = part.split(".")[0]
                out.sort(key=lambda r: r.get(col))
        return out

    def _handle(self, method):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        type(self).requests_log.append((method, u.path, {k: v[0] for k, v in q.items()}))
        if type(self).fail:
            return self._reply(503, {"message": "unavailable"})
        if not self._ok_auth():
            return self._reply(401, {"message": "bad key"})
        if u.path != f"/rest/v1/{TABLE}":
            return self._reply(404, {"message": "no table"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
        if method == "GET":
            return self._reply(200, self._filtered(q))
        if method == "POST":
            for r in body:
                r = {"food_id": len(self.rows) + 1, "food_date_out": None, **r}
                self.rows.append(r)
            return self._reply(201)
        if method == "PATCH":
            for r in self._filtered(q):
                r.update(body)
            return self._reply(204)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")


def start_server():
    FakePostgrest.rows, FakePostgrest.fail, FakePostgrest.requests_log = [], False, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakePostgrest)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def make_result(cid, added=(), removed=(), before=None, before_locations=None):
    item = lambda k, n, locs: {"item_id": k, "name": config.ITEMS[k]["name"], "quantity": n, "locations": locs}
    return {"capture_id": cid, "added": [item(*a) for a in added], "removed": [item(*r) for r in removed],
            "before": before or {}, "before_locations": before_locations or {}}


def open_rows():
    return sorted((r["food_name"], r["loc"]) for r in FakePostgrest.rows if r["food_date_out"] is None)


def test_sync_flow(tmp_dir=None):
    tmp = Path(tmp_dir or Path(__file__).resolve().parent / "_tmp")
    tmp.mkdir(exist_ok=True)
    config.SUPABASE_QUEUE_FILE = tmp / "queue.jsonl"
    config.SUPABASE_QUEUE_FILE.unlink(missing_ok=True)
    config.SUPABASE_SEED_FROM_FIRST_BEFORE = True
    srv, url = start_server()
    logs = []
    sync = ss.InventorySync(log=logs.append, table=ss.FridgeTable(url, "test-key", TABLE))

    # 1) 첫 촬영: 테이블이 비어 있음 → before(버터1, 오렌지3)로 채운 뒤 오렌지 1개 꺼냄 (R,1 자리)
    r1 = make_result("c1", removed=[("orange", 1, ["R,1"])],
                     before={"butter": 1, "orange": 3},
                     before_locations={"butter": ["C,1"], "orange": ["R,2", "R,1", "C,1"]})
    sync.push(r1).result()
    assert open_rows() == [("버터", "C,1"), ("오렌지", "C,1"), ("오렌지", "R,2")], open_rows()
    out = [r for r in FakePostgrest.rows if r["food_date_out"]]
    assert len(out) == 1 and out[0]["loc"] == "R,1", "같은 칸(R,1)의 오렌지를 꺼낸 것으로 기록해야 함"

    # 2) 포도 1개 넣음 (사용자 이름표로 인식된 품목도 이름 그대로 들어감)
    sync.push(make_result("c2", added=[("grape", 1, ["R,1"])])).result()
    assert ("포도", "R,1") in open_rows()

    # 3) 서버 장애 → 큐에 보관, 복구 후 다음 동기화 때 순서대로 다시 보냄
    FakePostgrest.fail = True
    sync.push(make_result("c3", removed=[("butter", 1, ["C,1"])])).result()
    assert config.SUPABASE_QUEUE_FILE.exists() and ("버터", "C,1") in open_rows()
    FakePostgrest.fail = False
    sync.push(make_result("c4", added=[("milk", 1, ["L,2"])])).result()
    assert not config.SUPABASE_QUEUE_FILE.exists(), "복구 후 큐가 비어야 함"
    assert ("버터", "C,1") not in open_rows() and ("우유", "L,2") in open_rows(), open_rows()

    # 4) 테이블에 없는 품목을 꺼냄 → 오류 없이 건너뜀
    sync.push(make_result("c5", removed=[("kimchi", 1, [])])).result()
    assert any("꺼낼 행 없음" in m for m in logs)

    sync.close()
    srv.shutdown()
    print("\n".join(logs))
    print("최종 '안에 있는' 행:", open_rows())


def test_disabled_without_keys():
    config.SUPABASE_URL, config.SUPABASE_KEY = "", ""
    logs = []
    sync = ss.InventorySync(log=logs.append)
    assert not sync.enabled and sync.push(make_result("x", added=[("milk", 1, [])])) is None
    assert any("미설정" in m for m in logs)


def test_location_of():
    from vision import location_of
    shape = (540, 960, 3)
    assert location_of([0, 0, 100, 100], shape) == "L,2"        # 왼쪽 위 = 위층(2층)
    assert location_of([430, 400, 530, 500], shape) == "C,1"    # 가운데 아래 = 1층
    assert location_of([887, 314, 960, 424], shape) == "R,1"    # 포도 그릇 (오른쪽 아래)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_location_of()
    test_disabled_without_keys()
    test_sync_flow()
    print("\n모든 시험 통과")
