"""supabase_sync + supabase/schema.sql 시험 — 실제 Supabase 계정 없이.

1) 단위 시험: 결과 → stock_event 행 변환 (어디서나 실행)
2) 통합 시험: 진짜 PostgreSQL(pgserver)에 schema.sql 을 그대로 실행하고,
   Supabase REST(PostgREST)의 필요한 부분만 흉내 낸 작은 서버가 요청을 SQL 로 바꿔
   service_role(서버 키) / anon(화면 키) 역할로 실행 → supabase-py 클라이언트(db/)로 함수·권한·RLS·외래키를 실제로 검증

통합 시험 준비 (Python 3.12 권장, 한 번만):  pip install pgserver "psycopg[binary]" python-dotenv supabase==2.34.1 pytest
실행:  python -m pytest tests/test_supabase_sync.py -q   (pgserver·supabase 가 없으면 통합 시험은 건너뜀)
"""
import json
import os
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


def test_destructive_cli_refuses_remote_database_before_any_request(monkeypatch):
    sys.path.insert(0, str(ROOT / 'tools'))
    import check_supabase as cs
    monkeypatch.setattr(config, 'SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('VISION_DISPOSABLE_TEST_DB', '1')
    monkeypatch.setattr(sys, 'argv', ['check_supabase.py', '--write', '--yes'])
    def forbidden(*args, **kwargs):
        raise AssertionError('No production request allowed')
    monkeypatch.setattr(cs, 'make_reader', forbidden)
    assert cs.main() == 2


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
        query = dict(parse_qsl(u.query))
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
        prefer = self.headers.get("Prefer", "")
        if not path.startswith("rpc/") and isinstance(body, dict):      # supabase-py: 1행이면 객체로 보냄
            body = [body]
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
            target = query.get("on_conflict")
            conflict = sql.SQL(" ({})").format(sql.Identifier(target)) if target else sql.SQL("")
            if "resolution=ignore-duplicates" in prefer:
                s += sql.SQL(" on conflict") + conflict + sql.SQL(" do nothing")
            elif "resolution=merge-duplicates" in prefer:           # upsert → 같은 키면 덮어쓰기
                s += sql.SQL(" on conflict") + conflict + sql.SQL(" do update set ") + sql.SQL(",").join(
                    sql.SQL("{} = excluded.{}").format(sql.Identifier(c), sql.Identifier(c)) for c in cols if c != target)
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
    import psycopg
    tmp = Path(tempfile.mkdtemp(prefix="fridge_pg_"))
    uri = os.getenv('VISION_TEST_DATABASE_URL')
    if uri:
        from psycopg.conninfo import conninfo_to_dict
        info = conninfo_to_dict(uri)
        assert info.get('host') in ('127.0.0.1', 'localhost', '::1')
        assert info.get('dbname') == 'kini_vision_test'
        class ExternalDisposable:
            def get_uri(self): return uri
            def cleanup(self): pass  # Docker owner removes the disposable server.
        srv = ExternalDisposable()
    else:
        import pgserver
        srv = pgserver.get_server(tmp / "pgdata", cleanup_mode="delete")
    conn = psycopg.connect(srv.get_uri(), autocommit=True)
    assert conn.execute("select to_regclass('public.current_stock')").fetchone()[0] is None, 'Use a NEW disposable database'
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
        if not os.getenv('VISION_TEST_DATABASE_URL'):
            import pgserver  # noqa: F401
        import psycopg  # noqa: F401
        import supabase  # noqa: F401
    except ImportError:
        try:
            import pytest
        except ImportError:
            print('  (통합 시험 생략: pip install pgserver "psycopg[binary]" supabase 필요)')
            return
        pytest.skip('통합 시험 생략: pip install pgserver "psycopg[binary]" supabase 필요 (Python 3.12 권장)')
    from db.repository import Repository
    from db.supabase_client import Clients, make_reader, make_writer
    sys.path.insert(0, str(ROOT / "tools"))
    import check_supabase as cs

    srv, conn, api, url, tmp = start_db_and_api()
    saved = (config.SUPABASE_OLD_QUEUE_FILE, config.SUPABASE_CONFIRM_MODE)
    config.SUPABASE_OLD_QUEUE_FILE, config.SUPABASE_CONFIRM_MODE = tmp / "old.jsonl", "local"
    outbox = tmp / "state" / "db_outbox.jsonl"
    logs = []
    log = lambda m: (logs.append(m), print("   ", m))
    added_user_item = "user_1" not in config.ITEMS
    try:
        # 0) schema.sql 초기 상태: 25개 품목, 두부·달걀·브로콜리·버섯·닭고기 1
        assert conn.execute("select count(*) from food_item").fetchone()[0] == 25
        assert stock(conn) == {"tofu": 1, "egg": 1, "broccoli": 1, "mushroom": 1, "chicken": 1}

        writer, reader = make_writer(url=url, key=SERVICE_KEY), make_reader(url=url, key=ANON_KEY)
        repo = Repository(Clients(reader=reader, writer=writer), log=log)
        sync = ss.InventorySync(log=log, repo=repo, baseline=True, outbox=outbox)
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
        cap = conn.execute("select engine, latency_ms, result_json->>'capture_id', before_path from capture").fetchone()
        assert cap == ("yolo:yoloe-v8m-seg.pt", 820, "cap-1", "captures/cap-1/before.jpg"), cap
        assert conn.execute("select count(*) from stock_event where source='manual'").fetchone()[0] == 11
        assert conn.execute("select count(*) from fridge_stock where food_id='orange' and date_out is null"
                            " and loc_x='R' and loc_y=1").fetchone()[0] == 2
        # 같은 결과를 다시 보내도(재시도) 이벤트·capture 가 늘지 않음
        sync.record(r1).result()
        assert conn.execute("select count(*) from stock_event where capture_id='cap-1'").fetchone()[0] == 1
        assert conn.execute("select count(*) from capture").fetchone()[0] == 1

        # 2) 확인 → 오렌지 3 → 2. 같은 확인을 다시 보내도 두 번 반영되지 않음
        sync.decide(r1, "confirm").result()
        assert stock(conn)["orange"] == 2
        assert conn.execute("select status from stock_event where capture_id='cap-1'").fetchone()[0] == "confirmed"
        sync.decide(r1, "confirm").result()
        assert stock(conn)["orange"] == 2

        # 3) 재고보다 많이 빼기 → 함수가 거부, 이벤트 pending 유지, 재고 그대로, '확인 필요' 알림
        r2 = {"capture_id": "cap-2", "source": "yolo", "added": [],
              "removed": [{"item_id": "watermelon", "name": "수박", "quantity": 3, "locations": []}]}
        sync.record(r2).result()
        sync.decide(r2, "confirm").result()
        assert stock(conn)["watermelon"] == 1
        assert conn.execute("select status from stock_event where capture_id='cap-2'").fetchone()[0] == "pending"
        alerts = sync.pop_alerts()
        assert len(alerts) == 1 and "stock mismatch" in alerts[0]["reason"], alerts

        # 4) 거절 → 재고 변화 없음
        r3 = {"capture_id": "cap-3", "source": "yolo", "removed": [],
              "added": [{"item_id": "milk", "name": "우유", "quantity": 1, "locations": ["L,1"]}]}
        sync.record(r3).result()
        sync.decide(r3, "reject").result()
        assert "milk" not in stock(conn)
        assert conn.execute("select status from stock_event where capture_id='cap-3'").fetchone()[0] == "rejected"

        # 5) DB 에 없는 라벨: 사용자 이름(user_*)·새 품목 → food_item 자동 추가 / unknown_package → 저장 안 함
        config.ITEMS.setdefault("user_1", {"name": "올리브오일", "description": "", "user": True})
        name1 = config.ITEMS["user_1"]["name"]
        r4 = {"capture_id": "cap-4", "source": "yolo", "removed": [],
              "added": [{"item_id": "user_1", "name": name1, "quantity": 1, "locations": ["L,2"]},
                        {"item_id": "banana", "name": "바나나", "quantity": 1, "locations": []},
                        {"item_id": "unknown_package", "name": "포장 식품", "quantity": 1, "locations": []}]}
        sync.record(r4).result()
        sync.decide(r4, "confirm").result()
        assert stock(conn)["user_1"] == 1 and stock(conn)["banana"] == 1
        assert conn.execute("select food_name, shelf_days from food_item where food_id='user_1'").fetchone() == \
            (name1, config.SUPABASE_DEFAULT_SHELF_DAYS)
        assert conn.execute("select count(*) from food_item where food_id like 'unknown%'").fetchone()[0] == 0
        assert conn.execute("select loc_x, loc_y from fridge_stock where food_id='user_1'").fetchone() == ("L", 2)

        # 6) 네트워크 장애 → 대기열 → 복구 후 다음 촬영 때 순서대로 재전송
        repo.clients._writer = make_writer(url="http://127.0.0.1:9", key=SERVICE_KEY, timeout=1)   # 닫힌 포트
        r5 = {"capture_id": "cap-5", "source": "yolo", "removed": [],
              "added": [{"item_id": "apple", "name": "사과", "quantity": 2, "locations": ["C,1", "C,1"]}]}
        sync.record(r5).result()
        sync.decide(r5, "confirm").result()
        assert sync.outbox_count() == 2
        assert SERVICE_KEY not in outbox.read_text(encoding="utf-8")
        repo.clients._writer = writer
        r6 = {"capture_id": "cap-6", "source": "yolo", "added": [], "removed": []}
        sync.record(r6).result()
        assert sync.outbox_count() == 0 and not outbox.exists()
        assert stock(conn)["apple"] == 2

        # 7) 읽기 함수 (Publishable 키) + 점검 도구 읽기 점검: anon 쓰기·함수 실행 거부
        assert len(repo.get_current_stock()) == 27
        assert [e["food_id"] for e in repo.get_pending_events()] == ["watermelon"]
        assert cs.read_checks(reader) is True, cs.ROWS
        cs.ROWS.clear()

        # 8) 점검 도구 쓰기 점검 (reset_demo → 우유 +1·브로콜리 −1 → 닭고기 −3 mismatch → reset_demo)
        assert cs.write_checks(repo) is True, cs.ROWS
        cs.ROWS.clear()
        assert stock(conn) == {"tofu": 1, "egg": 1, "broccoli": 1, "mushroom": 1, "chicken": 1}
        assert conn.execute("select count(*) from stock_event").fetchone()[0] == 0
        assert not any(SERVICE_KEY in m or ANON_KEY in m for m in logs)

        sync.close()
        print("    최종 재고:", stock(conn))
    finally:
        config.SUPABASE_OLD_QUEUE_FILE, config.SUPABASE_CONFIRM_MODE = saved
        if added_user_item:
            config.ITEMS.pop("user_1", None)
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
