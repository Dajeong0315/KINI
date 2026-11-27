"""인식 결과를 Supabase 고내 재고 DB(supabase/schema.sql)에 반영 — main.py 가 쓰는 창구.

이 프로그램이 하는 일 (실제 DB 작업은 db/repository.py)
  1) 인식 결과가 나오면        capture 1행 + stock_event(pending) 품목·칸별 1행씩 저장
  2) 재고에 반영하면(자동/y)    그 촬영의 pending 이벤트마다 confirm_stock_event()
     거절하면(n)               reject_stock_event()
     config.SUPABASE_CONFIRM_MODE = "ipad" 이면 2)는 하지 않고 iPad 확인을 기다림
     재고보다 많이 빼는 결과(stock mismatch)는 재고를 바꾸지 않고 pending 으로 남김 → alerts 로 '확인 필요' 알림
  3) (영상 파일 시험 등) 첫 촬영 때 DB 재고가 카메라가 본 before 와 다르면
     source='manual' 이벤트로 맞춘 뒤 시작 (config.SUPABASE_BASELINE_ON_START)

DB 가 안 될 때도 시연은 멈추지 않음: 전송은 별도 스레드 1개에서 순서대로 하고, 네트워크·서버 오류면
state/db_outbox.jsonl 에 쌓았다가 다음 촬영 때(또는 tools/flush_outbox.py) 먼저 다시 보냄.
모든 작업은 다시 보내도 결과가 같음 (capture 덮어쓰기, 같은 촬영의 이벤트는 한 번만, pending 이 아닌 이벤트는 건너뜀).
사진은 기본으로 올리지 않음 (UPLOAD_PHOTOS=false → 로컬 상대 경로만 저장).
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import config
from db.repository import DbError, Repository, events_from_changes


def events_from_result(result: dict) -> list[dict]:
    """(예전 이름 호환) 결과의 추가·제거 → stock_event 행."""
    rows, _ = events_from_changes(result.get("capture_id"), result.get("added"), result.get("removed"))
    return rows


class Worker:
    """작업 1개 실행 (모두 멱등)."""

    def __init__(self, repo: Repository, log: Callable[[str], None], alert: Callable[[dict], None]) -> None:
        self.repo = repo
        self.log = log
        self.alert = alert

    def _photo_paths(self, cid: str):
        """UPLOAD_PHOTOS 가 켜져 있으면 Storage 에 올리고 그 경로, 아니면 None (→ 로컬 상대 경로)."""
        if not config.UPLOAD_PHOTOS:
            return None, None
        paths = []
        for name in ("before.jpg", "after.jpg"):
            f = config.CAPTURE_DIR / cid / name
            if not f.exists():
                paths.append(None)
                continue
            key = f"{cid}/{name}"
            try:
                self.repo.clients.writer.storage.from_(config.SUPABASE_PHOTO_BUCKET).upload(
                    key, f.read_bytes(), {"content-type": "image/jpeg", "upsert": "true"})
            except Exception as e:  # noqa: BLE001
                from db.repository import classify
                raise classify(e) from None
            paths.append(f"storage:{config.SUPABASE_PHOTO_BUCKET}/{key}")
        return paths[0], paths[1]

    def baseline(self, before: dict, locations: dict, names: dict | None = None) -> None:
        """DB 재고를 카메라가 본 before 개수에 맞춤 (source='manual' 이벤트 → 바로 확인)."""
        rows = self.repo._exec(self.repo.clients.writer.table("current_stock").select("food_id,qty")).data
        current = {r["food_id"]: r["qty"] for r in rows}
        added, removed = [], []
        for k, n in before.items():
            if k in config.SUPABASE_EXCLUDE_LABELS:
                continue
            diff = n - current.get(k, 0)
            if diff == 0 or (k not in current and n <= 0):
                continue
            (added if diff > 0 else removed).append(
                {"item_id": k, "quantity": abs(diff), "locations": locations.get(k, []) if diff > 0 else []})
        if not added and not removed:
            self.log("DB: DB 재고가 첫 before 사진과 같음 → 맞출 것 없음")
            return
        events = self.repo.create_events(None, added, removed, source="manual", names=names)
        self.log("DB: 시작 재고를 카메라(before)에 맞춤 — " + ", ".join(f"{r['food_id']} {r['delta_qty']:+d}" for r in events))
        for ev in events:
            res = self.repo.confirm_event(ev["event_id"])
            if not res["ok"]:
                self.log(f"DB: 시작 재고 맞추기 실패 {ev['food_id']} {ev['delta_qty']:+d}: {res['reason']}")

    def run(self, job: dict) -> None:
        if job["type"] == "baseline":
            self.baseline(job["before"], job.get("locations", {}), job.get("names"))
            return
        result = job["result"]
        cid = result["capture_id"]
        before_path, after_path = self._photo_paths(cid)
        self.repo.record_capture(result, before_path, after_path)
        events = self.repo.create_events(cid, result.get("added"), result.get("removed"))
        if not events:
            self.log(f"DB: 촬영 기록 저장 ({cid}, 재고 변화 없음)")
        if job.get("decision") and events:
            for res in self.repo.decide_capture(cid, job["decision"]):
                if not res["ok"] and res.get("needs_confirmation"):
                    self.alert({"capture_id": cid, **res})


class InventorySync:
    """main.py 에서 쓰는 창구. 호출은 바로 돌아오고 실제 전송은 백그라운드 스레드 1개에서 순서대로."""

    def __init__(self, log: Callable[[str], None] = print, repo: Repository | None = None,
                 baseline: bool = False, outbox: Path | None = None) -> None:
        from db.supabase_client import Clients, configured_for_write
        self.log = log
        self.enabled = repo is not None or configured_for_write()
        self.alerts: list[dict] = []                     # stock mismatch 등 '확인 필요' (main 화면이 꺼내 감)
        self._alock = threading.Lock()
        if not self.enabled:
            log("별도 개수 재고 동기화 비활성: 명시적 허용과 전용 설정이 필요합니다. KINI g 재고는 후보 확인 API를 사용하세요.")
            return
        self.repo = repo or Repository(Clients(), log)
        self.worker = Worker(self.repo, log, self._alert)
        self.outbox = Path(outbox or config.SUPABASE_QUEUE_FILE)
        self._migrate_old_queue()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.lock = threading.Lock()
        self.need_baseline = baseline
        self.confirm_here = config.SUPABASE_CONFIRM_MODE != "ipad"

    def _alert(self, a: dict) -> None:
        with self._alock:
            self.alerts.append(a)

    # -- 대기열 (state/db_outbox.jsonl) -----------------------------------
    def _migrate_old_queue(self) -> None:
        old = Path(config.SUPABASE_OLD_QUEUE_FILE)
        if old.exists() and old.resolve() != self.outbox.resolve():
            jobs = self._load(old)
            self._save(self._load(self.outbox) + jobs)
            old.unlink()
            self.log(f"DB: 예전 대기열 {len(jobs)}건을 {self.outbox} 로 옮김")

    @staticmethod
    def _load(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _save(self, jobs: list[dict]) -> None:
        if jobs:
            self.outbox.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.outbox.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs), encoding="utf-8")
            tmp.replace(self.outbox)
        elif self.outbox.exists():
            self.outbox.unlink()

    def outbox_count(self) -> int:
        """DB 저장 대기 중인 작업 수."""
        if not self.enabled or not self.outbox.exists():
            return 0
        return sum(1 for line in self.outbox.read_text(encoding="utf-8").splitlines() if line.strip())

    def _send(self, new_jobs: list[dict]) -> int:
        """예전에 실패한 작업부터 순서대로 보냄. 돌려주는 값: 아직 남은 작업 수."""
        with self.lock:
            jobs = self._load(self.outbox) + new_jobs
            for i, job in enumerate(jobs):
                try:
                    self.worker.run(job)
                except DbError as e:
                    if not e.retryable:
                        self.log(f"DB: 작업 건너뜀 ({job['type']} {job.get('result', {}).get('capture_id', '')}): {e}")
                        continue
                    self._save(jobs[i:])
                    self.log(f"DB 저장 대기 중 {len(jobs) - i}건 ({self.outbox.name}) — 다음 촬영 때 다시 보냄: {e}")
                    return len(jobs) - i
            self._save([])
            return 0

    def _submit(self, jobs: list[dict]):
        jobs = json.loads(json.dumps(jobs, ensure_ascii=False, default=str))   # 지금 상태로 복사 (이후 변경 무시)
        fut = self.pool.submit(self._send, jobs)
        fut.add_done_callback(lambda f: f.exception() and self.log(f"DB 동기화 오류: {f.exception()!r}"))
        return fut

    # -- main.py 가 부르는 함수 ------------------------------------------
    def record(self, result: dict):
        """인식 결과가 나왔을 때: capture + pending 이벤트 저장 (iPad 가 바로 볼 수 있게)."""
        if not self.enabled:
            return None
        jobs = []
        if self.need_baseline:
            self.need_baseline = False
            before = result.get("before", {})
            jobs.append({"type": "baseline", "before": before, "locations": result.get("before_locations", {}),
                         "names": {k: config.ITEMS[k]["name"] for k, n in before.items() if n and k in config.ITEMS}})
        jobs.append({"type": "capture", "result": result, "decision": None})
        return self._submit(jobs)

    def decide(self, result: dict, decision: str):
        """재고에 반영(confirm) 또는 거절(reject)했을 때."""
        if not self.enabled or not self.confirm_here:
            return None
        return self._submit([{"type": "capture", "result": result, "decision": decision}])

    def flush(self) -> int:
        """대기열만 지금 다시 보냄 (tools/flush_outbox.py). 돌려주는 값: 남은 작업 수."""
        if not self.enabled:
            return 0
        return self._send([])

    def reset_demo(self) -> int:
        """데모 초기화 — 대기열을 먼저 비운 뒤(남은 작업이 초기화 뒤에 섞이지 않게) reset_demo() 실행."""
        if not self.enabled:
            raise RuntimeError("Supabase 미설정 (.env 의 SUPABASE_URL·SUPABASE_SECRET_KEY)")
        with self.lock:
            dropped = self.outbox_count()
            self._save([])
            if dropped:
                self.log(f"DB: 데모 초기화 전에 대기열 {dropped}건을 버림")
            return self.repo.reset_demo()

    def pop_alerts(self) -> list[dict]:
        with self._alock:
            out, self.alerts = self.alerts, []
        return out

    def close(self) -> None:
        if self.enabled:
            self.pool.shutdown(wait=True)
