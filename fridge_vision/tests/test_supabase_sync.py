"""supabase_sync + supabase/schema.sql 시험 — 실제 Supabase 계정 없이.

1) 단위 시험: 결과 → stock_event 행 변환 (어디서나 실행)
2) 통합 시험: 진짜 PostgreSQL(pgserver)에 schema.sql 을 그대로 실행하고,
   Supabase REST(PostgREST)의 필요한 부분만 흉내 낸 작은 서버가 요청을 SQL 로 바꿔
   service_role(서버 키) / anon(화면 키) 역할로 실행 → 함수·권한·RLS·외래키를 실제로 검증

통합 시험 준비 (Python 3.12 권장, 한 번만):  pip install pgserver "psycopg[binary]" python-dotenv requests
실행:  python tests/test_supabase_sync.py
"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import supabase_sync as ss  # noqa: E402

SERVICE_KEY, ANON_KEY = "service-key", "anon-key"


# ---------------------------------------------------------------------------
# 1) 단위 시험
# ---------------------------------------------------------------------------
def test_events_from_result():
    r = {"capture_id": "c1",
         "added": [{"item_id": "orange", "quantity": 3, "locations": ["R,1", "R,1", "C,2"]}],
         "removed": [{"item_id": "butter", "quantity": 2, "locations": ["C,2"]}]}
    got = sorted((x["food_id"], x["delta_qty"], x["loc_x"] or "-", x["loc_y"] or 0) for x in ss.events_from_result(r))
    assert got == [("butter", -1, "-", 0), ("butter", -1, "C", 2),
                   ("orange", 1, "C", 2), ("orange", 2, "R", 1)], got


def test_location_of():
    try:
        from vision import location_of
    except ImportError:               # cv2 등이 없는 환경
        print("  (location_of 시험 생략: vision 모듈을 불러올 수 없음)")
        return
    shape = (540, 960, 3)
    assert location_of([0, 0, 100, 100], shape) == "L,1"        # 왼쪽 위 = 1층(위 칸)
    assert location_of([430, 400, 530, 500], shape) == "C,2"    # 가운데 아래 = 2층
    assert location_of([887, 314, 960, 424], shape) == "R,2"    # 포도 그릇 (오른쪽 아래)


# ---------------------------------------------------------------------------
# 2) 통합 시험: PostgreSQL + 미니 PostgREST
# ---------------------------------------------------------------------------
class MiniPostgrest(BaseHTTPRequestHandler):
    """GET(select·eq·is.null·order), POST(insert, ignore-duplicates, return=representation), POST rpc/<함수>."""
    conn = None
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _reply(self, code, body=None):
        data = json.dumps(body, ensure_ascii=False, default=str).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _run(self, build):
        from psycopg import errors, sql
        role = {SERVICE_KEY: "service_role", ANON_KEY: "anon"}.get(self.headers.get("apikey"))
        if role is None or self.headers.get("Authorization") != f"Bearer {self.headers.get('apikey')}":
            return self._reply(401, {"message": "Invalid API key"})
        with self.lock:
            try:
                with self.conn.transaction():
                    cur = self.conn.cursor()
                    cur.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
                    q, params = build(sql)
                    cur.execute(q, params)
                    rows = cur.fetchall() if cur.description else None
                    cols = [d.name for d in cur.description] if cur.description else []
                return self._reply(200, [dict(zip(cols, r)) for r in rows] if rows is not None else None)
            except errors.InsufficientPrivilege as e:
                return self._reply(401 if role == "anon" else 403, {"message": str(e).splitlines()[0]})
            except Exception as e:          # raise exception 등 → PostgREST 처럼 400
                return self._reply(400, {"message": str(e).splitlines()[0]})

    def do_GET(self):
        u = urlparse(self.path)
        table = u.path.split("/rest/v1/")[1]
        q = dict(parse_qsl(u.query))

        def build(sql):
            cols = q.pop("select", "*")
            order = q.pop("order", None)
            where, params = [], []
            for k, v in q.items():
                if v == "is.null":
                    where.append(sql.SQL("{} is null").format(sql.Identifier(k)))
                elif v.startswith("eq."):
                    where.append(sql.SQL("{}::text = %s").format(sql.Identifier(k)))
                    params.append(v[3:])
            s = sql.SQL("select {} from {}").format(
                sql.SQL("*") if cols == "*" else sql.SQL(",").join(map(sql.Identifier, cols.split(","))),
                sql.Identifier("public", table))
            if where:
                s += sql.SQL(" where ") + sql.SQL(" and ").join(where)
            if order:
                s += sql.SQL(" order by ") + sql.SQL(",").join(
                    sql.SQL("{} {}").format(sql.Identifier(p.split(".")[0]), sql.SQL(p.split(".")[1]))
                    for p in order.split(","))
            return s, params
        self._run(build)

    def do_POST(self):
        from psycopg.types.json import Jsonb
        u = urlparse(self.path)
        path = u.path.split("/rest/v1/")[1]
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
        prefer = self.headers.get("Prefer", "")
        if path.startswith("rpc/"):
            fn = path[4:]

            def build(sql):
                args = sql.SQL(",").join(sql.SQL("{} => %s").format(sql.Identifier(k)) for k in body)
                return sql.SQL("select {}({}) as r").format(sql.Identifier("public", fn), args), list(body.values())
            return self._run_rpc(build)

        def build(sql):
            cols = list(body[0].keys())
            vals = sql.SQL(",").join(sql.SQL("(") + sql.SQL(",").join([sql.Placeholder()] * len(cols)) + sql.SQL(")")
                                     for _ in body)
            params = [Jsonb(r[c]) if isinstance(r[c], (dict, list)) else r[c] for r in body for c in cols]
            s = sql.SQL("insert into {} ({}) values {}").format(
                sql.Identifier("public", path), sql.SQL(",").join(map(sql.Identifier, cols)), vals)
            if "resolution=ignore-duplicates" in prefer:
                s += sql.SQL(" on conflict do nothing")
            if "return=representation" in prefer:
                s += sql.SQL(" returning *")
            return s, params
        self._run(build)

    def _run_rpc(self, build):
        # 함수는 스칼라 값을 그대로 돌려줌 (PostgREST 와 같음)
        orig = self._reply

        def unwrap(code, body=None):
            if code == 200 and isinstance(body, list) and body:
                body = body[0]["r"]
            orig(code, body)
        self._reply = unwrap
        self._run(build)


def start_db_and_api():
    import pgserver
    import psycopg
    tmp = Path(tempfile.mkdtemp(prefix="fridge_pg_"))
    srv = pgserver.get_server(tmp / "pgdata", cleanup_mode="delete")
    conn = psycopg.connect(srv.get_uri(), autocommit=True)
    # Supabase 와 같은 역할 (service_role 은 RLS 우회)
    for stmt in ("create role anon nologin", "create role authenticated nologin",
                 "create role service_role nologin bypassrls"):
        conn.execute(stmt)
    schema = (ROOT / "supabase" / "schema.sql").read_text(encoding="utf-8")
    # pgserver(Windows) 의 PostgreSQL 에는 시간대 데이터가 없어 'Asia/Seoul' 을 모름 → 시험에서만 같은 뜻(+09:00,
    # 한국은 서머타임 없음)으로 바꿔 실행. Supabase 에서는 schema.sql 을 그대로 씀.
    try:
        conn.execute("select now() at time zone 'Asia/Seoul'")
    except psycopg.Error:
        schema = schema.replace("time zone 'Asia/Seoul'", "time zone interval '+09:00'")
    conn.execute(schema)
    MiniPostgrest.conn = conn
    api = ThreadingHTTPServer(("127.0.0.1", 0), MiniPostgrest)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    return srv, conn, api, f"http://127.0.0.1:{api.server_address[1]}", tmp


def stock(conn):
    return {r[0]: r[1] for r in conn.execute("select food_id, qty from current_stock where qty > 0").fetchall()}


def test_integration():
    try:
        import pgserver  # noqa: F401
        import psycopg  # noqa: F401
    except ImportError:
        print("  (통합 시험 생략: pip install pgserver \"psycopg[binary]\" 필요)")
        return
    srv, conn, api, url, tmp = start_db_and_api()
    config.SUPABASE_QUEUE_FILE = tmp / "queue.jsonl"
    logs = []
    log = lambda m: (logs.append(m), print("   ", m))
    try:
        # 0) schema.sql 초기 상태: 25개 품목, 두부·달걀·브로콜리·버섯·닭고기 1
        assert conn.execute("select count(*) from food_item").fetchone()[0] == 25
        assert stock(conn) == {"tofu": 1, "egg": 1, "broccoli": 1, "mushroom": 1, "chicken": 1}

        sync = ss.InventorySync(log=log, db=ss.SupabaseREST(url, SERVICE_KEY), baseline=True)
        before = {k: 0 for k in config.ITEMS} | {"butter": 1, "watermelon": 1, "orange": 3, "grape": 1, "juice": 2}
        r1 = {"capture_id": "cap-1", "source": "yolo:yoloe-v8m-seg.pt", "latency_sec": 0.82,
              "needs_confirmation": False, "before": before,
              "before_locations": {"orange": ["R,1", "R,1", "R,2"], "butter": ["C,2"]},
              "added": [], "removed": [{"item_id": "orange", "name": "오렌지", "quantity": 1, "locations": ["R,1"]}]}

        # 1) 결과 저장 → 시작 재고 맞추기(manual) + capture + pending 이벤트. 재고는 아직 그대로
        sync.record(r1).result()
        assert stock(conn) == {"butter": 1, "watermelon": 1, "orange": 3, "grape": 1, "juice": 2}, stock(conn)
        ev = conn.execute("select food_id, delta_qty, loc_x, loc_y, status from stock_event where capture_id='cap-1'").fetchall()
        assert ev == [("orange", -1, "R", 1, "pending")], ev
        cap = conn.execute("select engine, latency_ms, result_json->>'capture_id' from capture").fetchone()
        assert cap == ("yolo:yoloe-v8m-seg.pt", 820, "cap-1"), cap
        # 데모 5종 −1 ×5, 버터·수박·포도·주스 4건, 오렌지는 칸별(R,1 ×2 / R,2 ×1) 2건 = 11건
        assert conn.execute("select count(*) from stock_event where source='manual'").fetchone()[0] == 11
        assert conn.execute("select count(*) from fridge_stock where food_id='orange' and date_out is null"
                            " and loc_x='R' and loc_y=1").fetchone()[0] == 2

        # 2) 재고 반영(confirm) → 오렌지 3 → 2, 가장 먼저 들어온 것 consumed
        sync.decide(r1, "confirm").result()
        assert stock(conn)["orange"] == 2
        assert conn.execute("select status from stock_event where capture_id='cap-1'").fetchone()[0] == "confirmed"
        # 같은 결정이 다시 와도(재전송) 두 번 반영되지 않음
        sync.decide(r1, "confirm").result()
        assert stock(conn)["orange"] == 2

        # 3) 재고보다 많이 빼기 → 함수가 거부, 이벤트 pending 유지, 재고 그대로
        r2 = {"capture_id": "cap-2", "source": "yolo", "added": [],
              "removed": [{"item_id": "watermelon", "name": "수박", "quantity": 3, "locations": []}]}
        sync.record(r2).result()
        sync.decide(r2, "confirm").result()
        assert stock(conn)["watermelon"] == 1
        assert conn.execute("select status from stock_event where capture_id='cap-2'").fetchone()[0] == "pending"
        assert any("stock mismatch" in m for m in logs)

        # 4) 거절(reject) → 재고 변화 없음
        r3 = {"capture_id": "cap-3", "source": "yolo", "removed": [],
              "added": [{"item_id": "milk", "name": "우유", "quantity": 1, "locations": ["L,1"]}]}
        sync.record(r3).result()
        sync.decide(r3, "reject").result()
        assert "milk" not in stock(conn)
        assert conn.execute("select status from stock_event where capture_id='cap-3'").fetchone()[0] == "rejected"

        # 5) 사용자가 붙인 새 품목(user_1) → food_item 자동 등록 후 반영, 칸 기록
        config.ITEMS["user_1"] = {"name": "올리브오일", "description": "", "user": True}
        r4 = {"capture_id": "cap-4", "source": "yolo", "removed": [],
              "added": [{"item_id": "user_1", "name": "올리브오일", "quantity": 1, "locations": ["L,2"]}]}
        sync.record(r4).result()
        sync.decide(r4, "confirm").result()
        assert stock(conn)["user_1"] == 1
        assert conn.execute("select loc_x, loc_y from fridge_stock where food_id='user_1'").fetchone() == ("L", 2)

        # 6) 네트워크 장애 → 보관 → 복구 후 순서대로 재전송
        sync.worker.db.base = "http://127.0.0.1:9/rest/v1"          # 닫힌 포트
        sync.worker.db.timeout = 1
        r5 = {"capture_id": "cap-5", "source": "yolo", "removed": [],
              "added": [{"item_id": "apple", "name": "사과", "quantity": 2, "locations": ["C,1", "C,1"]}]}
        sync.record(r5).result()
        sync.decide(r5, "confirm").result()
        assert config.SUPABASE_QUEUE_FILE.exists() and len(config.SUPABASE_QUEUE_FILE.read_text(encoding="utf-8").splitlines()) == 2
        sync.worker.db.base = f"{url}/rest/v1"
        r6 = {"capture_id": "cap-6", "source": "yolo", "added": [], "removed": []}
        sync.record(r6).result()
        assert not config.SUPABASE_QUEUE_FILE.exists()
        assert stock(conn)["apple"] == 2

        # 7) 화면용 anon 키: 읽기는 되고, 쓰기·함수 실행은 거부
        anon = ss.SupabaseREST(url, ANON_KEY)
        assert len(anon.select("current_stock", {"select": "food_id,qty"})) == 26
        for call in (lambda: anon.insert("stock_event", [{"food_id": "milk", "delta_qty": 1}]),
                     lambda: anon.rpc("reset_demo", {})):
            try:
                call()
                raise AssertionError("anon 쓰기가 허용됨")
            except ss.SupabaseError as e:
                assert e.status == 401, e

        sync.close()
        print("    최종 재고:", stock(conn))
    finally:
        api.shutdown()
        conn.close()
        srv.cleanup()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for name, fn in [("결과 → 이벤트 변환", test_events_from_result), ("칸 계산", test_location_of),
                     ("통합 (PostgreSQL + REST)", test_integration)]:
        print(f"- {name}")
        fn()
    print("\n모든 시험 통과")
