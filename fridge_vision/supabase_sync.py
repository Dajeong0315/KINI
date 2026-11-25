"""인식 결과(추가·제거)를 Supabase 고내 재고 테이블에 반영.

테이블 (supabase/fridge_food.sql)
  food_id PK | food_name VARCHAR | food_date_in DATE | food_date_out DATE | loc VARCHAR("X,Y")
  한 행 = 냉장고 안 식재료 1개.  현재 재고 = food_date_out 이 NULL 인 행.

동작
  추가 1개 → INSERT (food_name, food_date_in=오늘, loc)
  제거 1개 → 같은 이름·아직 안 꺼낸 행 중 같은 loc 우선, 없으면 가장 오래 넣은 행 → food_date_out=오늘 UPDATE
  Unknown(목록에 없는 물체)은 보내지 않음. 사진·사용자 정보도 보내지 않음 (KINI 비식별 원칙).

Supabase REST(PostgREST)를 requests 로 직접 호출 — 추가 라이브러리 불필요.
네트워크가 끊기면 작업을 captures/supabase_queue.jsonl 에 쌓아 두고 다음 동기화 때 다시 보냄.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Callable

import requests

import config

C = config.SUPABASE_COLUMNS


class SupabaseError(RuntimeError):
    pass


class FridgeTable:
    """고내 재고 테이블 REST 호출 (한 함수 = 한 요청)."""

    def __init__(self, url: str, key: str, table: str, timeout: float = 5.0) -> None:
        self.base = f"{url}/rest/v1/{table}"
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"apikey": key, "Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"})

    def _check(self, r: requests.Response):
        if r.status_code >= 300:
            raise SupabaseError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r

    def open_rows(self, name: str | None = None) -> list[dict]:
        """아직 꺼내지 않은 행 (오래 넣은 순)."""
        params = {"select": f"{C['pk']},{C['name']},{C['date_in']},{C['loc']}",
                  C["date_out"]: "is.null", "order": f"{C['date_in']}.asc,{C['pk']}.asc"}
        if name is not None:
            params[C["name"]] = f"eq.{name}"
        return self._check(self.s.get(self.base, params=params, timeout=self.timeout)).json()

    def insert(self, rows: list[dict]) -> None:
        if rows:
            self._check(self.s.post(self.base, data=json.dumps(rows, ensure_ascii=False).encode("utf-8"),
                                    headers={"Prefer": "return=minimal"}, timeout=self.timeout))

    def close_row(self, pk, day: str) -> None:
        self._check(self.s.patch(self.base, params={C["pk"]: f"eq.{pk}"},
                                 data=json.dumps({C["date_out"]: day}).encode("utf-8"),
                                 headers={"Prefer": "return=minimal"}, timeout=self.timeout))


def jobs_from_result(result: dict, day: str | None = None) -> list[dict]:
    """인식 결과 → 보낼 작업 목록. 작업 = {"op": "in"|"out", "name", "loc", "day", "capture_id"}."""
    day = day or date.today().isoformat()
    jobs = []
    for op, key in (("in", "added"), ("out", "removed")):
        for e in result.get(key, []):
            locs = list(e.get("locations") or [])
            for i in range(e["quantity"]):
                jobs.append({"op": op, "name": e["name"], "loc": locs[i] if i < len(locs) else None,
                             "day": day, "capture_id": result.get("capture_id")})
    return jobs


def seed_jobs(result: dict, day: str | None = None) -> list[dict]:
    """첫 before 사진의 품목으로 테이블 채우기 (테이블이 비어 있을 때만 사용)."""
    day = day or date.today().isoformat()
    jobs = []
    for k, n in result.get("before", {}).items():
        locs = result.get("before_locations", {}).get(k, [])
        for i in range(n):
            jobs.append({"op": "in", "name": config.ITEMS[k]["name"], "loc": locs[i] if i < len(locs) else None,
                         "day": day, "capture_id": result.get("capture_id"), "seed": True})
    return jobs


def run_job(table: FridgeTable, job: dict) -> str:
    """작업 1개 실행. 돌려주는 값: 로그용 한 줄."""
    if job["op"] == "in":
        table.insert([{C["name"]: job["name"], C["date_in"]: job["day"], C["loc"]: job["loc"]}])
        return f"INSERT {job['name']} ({job['loc']})"
    rows = table.open_rows(job["name"])
    if not rows:
        return f"꺼낼 행 없음: {job['name']} (테이블과 실제 재고가 다름 → 건너뜀)"
    same = [r for r in rows if job["loc"] and r.get(C["loc"]) == job["loc"]]
    row = (same or rows)[0]
    table.close_row(row[C["pk"]], job["day"])
    return f"OUT {job['name']} (행 {row[C['pk']]}, {row.get(C['loc'])})"


class InventorySync:
    """main.py 에서 쓰는 창구. push(result) 는 바로 돌아오고 실제 전송은 백그라운드 스레드에서."""

    def __init__(self, log: Callable[[str], None] = print, table: FridgeTable | None = None) -> None:
        self.log = log
        self.enabled = table is not None or bool(config.SUPABASE_URL and config.SUPABASE_KEY)
        self.table = table or (FridgeTable(config.SUPABASE_URL, config.SUPABASE_KEY, config.SUPABASE_TABLE,
                                           config.SUPABASE_TIMEOUT) if self.enabled else None)
        self.queue_file = config.SUPABASE_QUEUE_FILE
        self.pool = ThreadPoolExecutor(max_workers=1)   # 순서 보장 (넣고 → 꺼내기 순서가 바뀌면 안 됨)
        self.lock = threading.Lock()
        self.seeded = not config.SUPABASE_SEED_FROM_FIRST_BEFORE
        if not self.enabled:
            log("Supabase 미설정 (.env 의 SUPABASE_URL·SUPABASE_KEY) → 재고 테이블 연동 없이 실행")

    # -- 실패 작업 보관 ---------------------------------------------------
    def _load_queue(self) -> list[dict]:
        if not self.queue_file.exists():
            return []
        return [json.loads(line) for line in self.queue_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _save_queue(self, jobs: list[dict]) -> None:
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        if jobs:
            self.queue_file.write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs),
                                       encoding="utf-8")
        elif self.queue_file.exists():
            self.queue_file.unlink()

    # -- 전송 -------------------------------------------------------------
    def _send(self, new_jobs: list[dict], first_result: dict | None) -> None:
        with self.lock:
            if first_result is not None:
                try:
                    if not self.table.open_rows():
                        seed = seed_jobs(first_result)
                        new_jobs = seed + new_jobs
                        self.log(f"Supabase: 테이블이 비어 있어 첫 before 사진의 품목 {len(seed)}개로 채움")
                except (requests.RequestException, SupabaseError) as e:
                    self.log(f"Supabase 확인 실패 (시작 재고 채우기 생략): {e}")
            jobs = self._load_queue() + new_jobs     # 예전에 실패한 작업부터 순서대로
            done = 0
            for i, job in enumerate(jobs):
                try:
                    self.log("Supabase: " + run_job(self.table, job))
                    done += 1
                except (requests.RequestException, SupabaseError) as e:
                    self._save_queue(jobs[i:])
                    self.log(f"Supabase 전송 실패 → {len(jobs) - i}개 작업 보관 ({self.queue_file.name}), 다음에 다시 보냄: {e}")
                    return
            self._save_queue([])
            if done:
                self.log(f"Supabase: 재고 테이블 반영 완료 ({done}건)")

    def push(self, result: dict):
        """재고에 반영된 결과 1건을 테이블로 보냄 (백그라운드). 돌려주는 값: Future 또는 None."""
        if not self.enabled:
            return None
        first = None
        if not self.seeded:
            self.seeded = True
            first = result
        fut = self.pool.submit(self._send, jobs_from_result(result), first)
        fut.add_done_callback(lambda f: f.exception() and self.log(f"Supabase 동기화 오류: {f.exception()!r}"))
        return fut

    def close(self) -> None:
        self.pool.shutdown(wait=True)
