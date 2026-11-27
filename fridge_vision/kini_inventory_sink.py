"""Vision 인식 결과 → KINI A 재고 이벤트 API (로컬 Flask + 로컬 SQLite).

※ main.py(KINI_API_URL 경로)·tools/kini_e2e_check.py 가 쓰는 대기열·재전송 창구(KiniInventorySink).
   같은 폴더의 kini_inventory_client.py 는 팀원이 main 에 올린 별도 구현이라 그대로 둠 (이름 충돌을 피해 이 파일 이름을 바꿈).

계약: docs/A_API_명세서.md (A-API-02~05), docs/A_로컬_로그인_API.md
  - Vision 은 '인식 후보'만 보낸다: POST /api/data/inventory/events (status=pending_confirmation)
  - 재고 변경(확정)은 사람이 표준 재료 ID·중량(g)·재고 행을 골라 /confirm 해야 한다.
    Vision 은 g·재료 ID·재고 행을 모르므로 **절대 자동으로 confirm 하지 않는다.**
  - Vision 화면에서 사람이 결과를 거절하면 해당 촬영의 대기 이벤트만 /reject 한다 (A-API-04).

보내는 것: client_event_id, source="yolo", action, detected_label, confidence(null)
보내지 않는 것: 사진·영상·경로, 위치, 수량·g, 가구 ID, 토큰·비밀번호 로그. (A 계약상 추가 필드는 400)
Supabase·외부 서버로는 아무것도 보내지 않는다. 기본 주소는 로컬 서버만 허용.

client_event_id: '출입 이벤트' 단위로 고정 (촬영 ID + 방향 + 품목 + 순번 → UUIDv5)
  → 같은 촬영 결과를 다시 보내도(재시도·재시작) 같은 ID → 서버가 중복 생성하지 않음
  ※ 같은 물체가 **서로 다른 촬영**에서 다시 인식되면 다른 ID 가 된다. 서버 ID 중복 방지로는 막을 수 없으며,
    Vision 의 장면 비교(전/후 개수 차이)와 사람 확인이 막아야 한다.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse
try:
    from .kini_inventory_client import _local_origin
except ImportError:  # main.py runs with fridge_vision as the module search root
    from kini_inventory_client import _local_origin

# 이 네임스페이스는 바꾸지 말 것 (바꾸면 재전송 시 다른 ID 가 되어 중복 이벤트가 생김)
EVENT_NAMESPACE = uuid.UUID("6f1c7f0e-3a51-4d4e-9b1e-0b6a4b1f7c21")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class KiniApiError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.retryable = status is None or status >= 500 or status in (408, 429)


def detected_label(entry: dict) -> str:
    """인식 품목 이름 → A 의 detected_label (로컬 전용 자유 문자열, 1~100자).
    '피망·고추' 처럼 묶인 이름은 첫 이름만 ('피망'). 표준 재료 ID 로의 확정은 사람이 confirm 에서 한다."""
    name = str(entry.get("name") or entry.get("item_id") or "").split("·")[0].strip()
    return (name or str(entry.get("item_id", "unknown")))[:100]


def event_id_for(capture_id: str, action: str, item_id: str, index: int) -> str:
    return str(uuid.uuid5(EVENT_NAMESPACE, f"{capture_id}|{action}|{item_id}|{index}"))


def events_from_result(result: dict, source: str = "yolo") -> list[dict]:
    """vision.compare 결과(added/removed) → A-API-02 요청 본문 목록.
    - 물체 1개 = 이벤트 1개 (수량은 보내지 않음: A 계약상 수량은 확인 단계에서 g 로 입력)
    - Unknown(목록에 없는 물체)은 added/removed 에 없으므로 보내지 않음 → 사람이 이름을 붙인 뒤에만 전송
    - confidence 는 결과에 품목별 점수가 없으므로 null (A 는 자동 반영 기준으로 쓰지 않음)"""
    out = []
    for action, key in (("stock_in", "added"), ("stock_out", "removed")):
        for e in result.get(key, []):
            for n in range(int(e.get("quantity", 0))):
                out.append({"client_event_id": event_id_for(result["capture_id"], action, e["item_id"], n),
                            "source": source, "action": action, "detected_label": detected_label(e),
                            "confidence": None})
    return out


class KiniInventoryClient:
    """A 재고 이벤트 API 의 얇은 HTTP 클라이언트. 토큰·비밀번호는 메모리에만 두고 출력하지 않음."""

    def __init__(self, base_url: str, *, token: str | None = None, login_name: str | None = None,
                 password: str | None = None, household_id: int | None = None, timeout: float = 5.0,
                 session=None, allow_remote: bool = False) -> None:
        import requests

        if allow_remote:
            raise ValueError("원격 KINI는 SSH loopback 터널로 연결하세요.")
        self.base = _local_origin(base_url)
        self._token, self._creds = token, (login_name, password, household_id)
        self.timeout = timeout
        self.http = session or requests.Session()
        self.http.trust_env = False

    def __repr__(self) -> str:                    # 토큰이 로그에 찍히지 않도록
        return f"KiniInventoryClient({self.base!r})"

    # ---------------- 인증 ----------------
    def login(self) -> None:
        name, pw, hid = self._creds
        if not (name and pw and hid):
            raise KiniApiError(401, "로그인 정보가 없습니다 (KINI_LOGIN_NAME/KINI_PASSWORD/KINI_HOUSEHOLD_ID)")
        r = self._send("POST", "/api/auth/login", {"login_name": name, "password": pw, "household_id": int(hid)},
                       auth=False)
        self._token = r["access_token"]

    def _send(self, method: str, path: str, body=None, *, auth: bool = True, relogin: bool = True):
        import requests

        headers = {"Content-Type": "application/json"}
        if auth:
            if not self._token:
                self.login()
            headers["Authorization"] = "Bearer " + self._token
        try:
            r = self.http.request(method, self.base + path, data=None if body is None else json.dumps(body),
                                  headers=headers, timeout=self.timeout, allow_redirects=False)
        except requests.RequestException as e:      # 연결 실패·시간 초과 (응답 유실 포함)
            raise KiniApiError(None, type(e).__name__) from None
        if r.status_code in (401, 403) and auth and relogin and all(self._creds):
            self._token = None                     # 만료 등 → 한 번만 다시 로그인
            return self._send(method, path, body, auth=True, relogin=False)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 300:
            raise KiniApiError(r.status_code, str(data.get("error", "")) or f"HTTP {r.status_code}")
        return data

    # ---------------- A API ----------------
    def ingest(self, event: dict) -> dict:
        return self._send("POST", "/api/data/inventory/events", event)

    def reject(self, event_id: int) -> dict:
        return self._send("POST", f"/api/data/inventory/events/{int(event_id)}/reject")

    def confirm(self, event_id: int, body: dict) -> dict:
        """사용자 확인 화면(C)이 하는 일. 시험·관리 도구에서만 호출하고 Vision 자동 흐름에서는 쓰지 않는다."""
        return self._send("POST", f"/api/data/inventory/events/{int(event_id)}/confirm", body)

    def snapshot(self) -> dict:
        return self._send("GET", "/api/data/inventory")


class KiniInventorySink:
    """main.py 의 재고 연동 자리(record / decide / close)에 끼우는 A API 연결부.
    record : 촬영 결과 → 대기 이벤트 등록 (실패하면 대기열 파일에 남겨 다음에 같은 ID 로 재전송)
    decide : "reject" → 그 촬영의 대기 이벤트 거절 / "confirm" → 아무것도 하지 않음 (g·재료 확인은 사람이 앱에서)"""

    def __init__(self, client: KiniInventoryClient, queue_file: Path, log=print) -> None:
        self.client, self.queue_file, self.log = client, Path(queue_file), log
        self.events: dict[str, list[int]] = {}      # capture_id → event_id 목록
        self.lock = threading.Lock()

    def _load_queue(self) -> list[dict]:
        if not self.queue_file.exists():
            return []
        return [json.loads(l) for l in self.queue_file.read_text(encoding="utf-8").splitlines() if l.strip()]

    def _save_queue(self, jobs: list[dict]) -> None:
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self.queue_file.write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs), encoding="utf-8")

    def _send_events(self, capture_id: str, events: list[dict]) -> list[dict]:
        """보낸 결과 목록. 재시도 가능한 오류에서 멈추고 KiniApiError 를 올림 (남은 것은 호출자가 대기열에)."""
        done = []
        for ev in events:
            try:
                r = self.client.ingest(ev)
            except KiniApiError as e:
                if e.retryable:
                    raise
                self.log(f"KINI 재고 이벤트 거절됨 ({e.status}) — {ev['action']} {ev['detected_label']}: {e}")
                continue
            self.events.setdefault(capture_id, []).append(r["event_id"])
            done.append(r)
        return done

    def record(self, result: dict) -> list[dict]:
        with self.lock:
            jobs = self._load_queue() + [{"capture_id": result["capture_id"], "events": events_from_result(result)}]
            sent, left = [], []
            for i, job in enumerate(jobs):
                try:
                    sent += self._send_events(job["capture_id"], job["events"])
                except KiniApiError as e:
                    self.log(f"KINI 서버 연결 실패 ({e}) → {len(jobs) - i}개 촬영 결과를 대기열에 보관")
                    left = jobs[i:]
                    break
            self._save_queue(left)
            if sent:
                self.log("KINI 재고 이벤트 등록 (사용자 확인 대기): "
                         + ", ".join(f"{r['action']} {r['detected_label']}#{r['event_id']}" for r in sent))
            return sent

    def decide(self, result: dict, decision: str) -> None:
        ids = self.events.get(result["capture_id"], [])
        if decision == "reject":
            for event_id in ids:
                try:
                    self.client.reject(event_id)
                except KiniApiError as e:
                    self.log(f"KINI 이벤트 {event_id} 거절 실패: {e}")
            self.log(f"KINI 대기 이벤트 {len(ids)}건 거절")
        elif ids:
            self.log(f"KINI 대기 이벤트 {len(ids)}건: 재료·중량(g) 확인은 앱에서 해야 재고가 바뀝니다")

    def close(self) -> None:
        pass


def sink_from_env(queue_file: Path, log=print) -> KiniInventorySink | None:
    """환경변수로 연결: KINI_API_URL (+ KINI_ACCESS_TOKEN 또는 KINI_LOGIN_NAME·KINI_PASSWORD·KINI_HOUSEHOLD_ID)."""
    url = os.getenv("KINI_API_URL", "").strip()
    if not url:
        return None
    client = KiniInventoryClient(url, token=os.getenv("KINI_ACCESS_TOKEN") or None,
                                 login_name=os.getenv("KINI_LOGIN_NAME") or None,
                                 password=os.getenv("KINI_PASSWORD") or None,
                                 household_id=int(os.getenv("KINI_HOUSEHOLD_ID")) if os.getenv("KINI_HOUSEHOLD_ID") else None)
    return KiniInventorySink(client, queue_file, log)
