"""핸드폰 카메라 → PC 실시간 식재료 인식 (fridge_vision 과 같은 YOLOE 모델·설정).

  핸드폰  https://<PC IP>:8443/camera   카메라 영상 + 인식 상자·이름 (전면/후면 전환)
  PC      https://localhost:8443/viewer 핸드폰 화면 + 인식 상자·이름 (+ 접속용 QR 코드)
  핸드폰  https://<PC IP>:8443/compare  전/후 사진 촬영(화면 위·아래 절반) → 추가·제거 계산 → Supabase 재고 DB 반영
  PC      https://localhost:8443/compare-view  핸드폰 전/후 비교 화면 + 결과·DB 반영 (보기 전용)

흐름: 핸드폰이 JPEG 프레임을 WebSocket 으로 보냄 → PC 가 모든 프레임을 viewer 에 바로 전달하고,
      인식기가 쉬고 있으면 그 프레임으로 YOLOE 인식 → 결과(상자·이름)를 핸드폰과 viewer 에 보냄.
      인식이 느려도 화면은 끊기지 않고, 상자는 가장 최근 인식 결과로 그려짐.

실행: python server.py            (옵션: --port 8443 --cv-window --host 0.0.0.0)
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
from aiohttp import WSMsgType, web

HERE = Path(__file__).resolve().parent
FRIDGE_DIR = HERE.parent / "fridge_vision"     # 같은 인식 코드·모델·설정을 그대로 씀
STATIC = HERE / "static"
CERT_DIR = HERE / "certs"
sys.path.insert(0, str(FRIDGE_DIR))


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 인식기 (fridge_vision.vision.YoloComparer 의 한 장 인식 _detect 를 그대로 사용)
# ---------------------------------------------------------------------------
class Detector:
    def __init__(self) -> None:
        cwd = os.getcwd()
        os.chdir(FRIDGE_DIR)        # YOLOE 의 글자 모델(mobileclip_blt.ts)을 이 폴더에서 찾음 → 600MB 재다운로드 방지
        try:
            import config
            import vision
            self.config, self.vision = config, vision
            log(f"모델 불러오는 중 (fridge_vision 설정 그대로, 방식: {config.DETECTOR_BACKEND})")
            self.comp = vision.YoloComparer()
            log(f"모델: {self.comp.model_path.name}")
        finally:
            os.chdir(cwd)
        self.pool = ThreadPoolExecutor(max_workers=1)    # 모델은 한 번에 한 장만
        self.seq = 0
        self.recent: dict[int, np.ndarray] = {}           # 최근 인식한 프레임 (이름 붙이기용, 번호 → 사진)
        self.sessions: dict[str, dict] = {}               # 전/후 비교 (capture_id → 사진·결과), 최근 10개
        self.sync = None                                  # Supabase 재고 DB 창구 (fridge_vision/supabase_sync.py)
        try:
            from supabase_sync import InventorySync
            self.sync = InventorySync(log=log)            # .env 에 키가 없으면 enabled=False (비교만 됨)
        except Exception as e:                            # DB 쪽 문제로 인식 서버가 멈추지 않게
            log(f"[경고] Supabase 연동을 준비하지 못했습니다: {e!r}")

    def _label(self, b) -> dict:
        c, s, (x1, y1, x2, y2) = b[0], b[1], b[2]
        tags = [str(t) for t in b[3:]]                 # 예: "group 4", "user 0.91", "ocr 비비고 만두", "hint 백설"
        if c == self.vision.UNKNOWN:
            item_id, name, kind = "unknown", "Unknown", "unknown"
        else:
            item_id = self.vision.ITEM_IDS[c]
            name = self.config.ITEMS[item_id]["name"]
            kind = "user" if any(t.startswith("user") for t in tags) else "text" if any(t.startswith("ocr") for t in tags) else "item"
        d = {"id": item_id, "name": name, "conf": round(s, 3), "kind": kind, "box": [round(v, 1) for v in (x1, y1, x2, y2)]}
        hint = next((t[5:] for t in tags if t.startswith("hint ")), None)
        if hint:
            d["hint"] = hint                           # Unknown 이름 입력 칸에 미리 채울 글씨 (OCR 이 읽은 것)
        return d

    def detect(self, jpeg: bytes, profile: str = "live") -> dict:
        """profile: live = 실시간 화면(빠르게, OCR 은 최근 결과 재사용만) / full = '식재료 파악하기'(정밀)."""
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("JPEG 해석 실패")
        t0 = time.perf_counter()
        boxes, hand, _ = self.comp.detect_frame(frame, profile)
        ms = (time.perf_counter() - t0) * 1000
        self.seq += 1
        self.recent[self.seq] = frame
        for old in [k for k in self.recent if k <= self.seq - 30]:
            del self.recent[old]
        h, w = frame.shape[:2]
        dets = sorted((self._label(b) for b in boxes), key=lambda d: -d["conf"])
        return {"type": "dets", "mode": profile, "seq": self.seq, "w": w, "h": h, "ms": round(ms), "hand": hand,
                "dets": dets, "annotated": self.comp.draw(frame, boxes)}

    # ------------------------------------------------------------------
    # 전/후 비교 (/compare): 사용자가 찍은 전·후 사진 → fridge_vision compare() → 추가·제거
    # ------------------------------------------------------------------
    def compare(self, before_jpeg: bytes, after_jpeg: bytes) -> dict:
        frames = []
        for name, data in (("전", before_jpeg), ("후", after_jpeg)):
            f = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if f is None:
                raise ValueError(f"{name} 사진을 읽을 수 없음")
            frames.append(f)
        before, after = frames
        if after.shape[:2] != before.shape[:2]:          # 카메라를 바꿨거나 회전 → 같은 크기로 맞춤 (변화 비교용)
            after = cv2.resize(after, (before.shape[1], before.shape[0]))
        cid = "phone-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        out = self.config.CAPTURE_DIR / cid               # main.py 와 같은 위치·이름 (DB capture 의 before_path)
        out.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out / "before.jpg"), before)
        cv2.imwrite(str(out / "after.jpg"), after)
        self.sessions[cid] = {"before": before, "after": after, "out": out}
        while len(self.sessions) > 10:
            del self.sessions[next(iter(self.sessions))]
        return self._run_compare(cid)

    def _run_compare(self, cid: str) -> dict:
        s = self.sessions.get(cid)
        if s is None:
            raise ValueError("그 비교가 너무 오래됨 — 다시 찍어 주세요")
        t0 = time.perf_counter()
        self.comp.track_enabled = False                   # 손에 든 핸드폰: 매번 다른 장면 (고정 카메라용 트랙 끔)
        result = self.comp.compare(s["before"], s["after"], cid)
        result["latency_sec"] = round(time.perf_counter() - t0, 2)
        boxes = getattr(self.comp, "last_compare_boxes", {"before": [], "after": []})
        (s["out"] / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        s["result"] = result
        sides = {}
        for side in ("before", "after"):
            h, w = s[side].shape[:2]
            sides[side] = {"w": w, "h": h, "dets": sorted((self._label(b) for b in boxes[side]), key=lambda d: -d["conf"])}
        return {"cid": cid, "ms": round(result["latency_sec"] * 1000), "before": sides["before"], "after": sides["after"],
                "added": result["added"], "removed": result["removed"], "unknown": result.get("unknown", {}),
                "needs_confirmation": result["needs_confirmation"], "reasons": result["confirmation_reasons"]}

    def compare_label(self, cid: str, side: str, name: str, box) -> dict:
        """비교 화면의 Unknown 상자에 이름 → 기억(user_labels·갤러리)하고 같은 사진으로 다시 비교."""
        s = self.sessions.get(cid)
        if s is None or side not in ("before", "after"):
            raise ValueError("그 비교가 너무 오래됨 — 다시 찍어 주세요")
        k = self.comp.learn_label(name, s[side], box)
        res = self._run_compare(cid)
        res["learned"] = f"{self.config.ITEMS[k]['name']} ({k})"
        return res

    def commit(self, cid: str, decision: str) -> dict:
        """비교 결과를 Supabase 에: capture + stock_event(pending) 저장 → confirm(재고 반영) 또는 reject(거절).
        Unknown 은 저장하지 않음. 재고보다 많이 빼는 결과는 재고를 바꾸지 않고 '확인 필요'로 돌려줌."""
        s = self.sessions.get(cid)
        if s is None or "result" not in s:
            raise ValueError("그 비교가 너무 오래됨 — 다시 찍어 주세요")
        if decision not in ("confirm", "reject"):
            raise ValueError("decision 은 confirm 또는 reject")
        if self.sync is None or not self.sync.enabled:
            raise ValueError("별도 개수 재고 반영은 비활성입니다. KINI에서는 후보를 접수하고 앱에서 표준 재료·실제 g를 확인하세요.")
        result = s["result"]
        self.sync.record(result).result()                # 기다림 (전송은 supabase_sync 의 스레드에서)
        fut = self.sync.decide(result, decision)         # SUPABASE_CONFIRM_MODE=ipad 이면 None (pending 으로만 저장)
        if fut is not None:
            fut.result()
        out = {"cid": cid, "decision": decision if fut is not None else "pending",
               "waiting": self.sync.outbox_count(), "alerts": [], "events": [], "stock": None}
        for a in self.sync.pop_alerts():
            out["alerts"].append({"food_id": a["food_id"], "name": self.config.ITEMS.get(a["food_id"], {}).get("name", a["food_id"]),
                                  "delta": a["delta_qty"], "reason": a["reason"]})
        name = lambda k: self.config.ITEMS.get(k, {}).get("name", k)
        try:
            out["events"] = [{"name": name(e["food_id"]), "delta": e["delta_qty"], "loc": f"{e['loc_x']},{e['loc_y']}"
                              if e["loc_x"] else None, "status": e["status"]} for e in self.sync.repo.get_events(cid)]
            out["stock"] = [{"name": r["food_name"], "qty": r["qty"]} for r in self.sync.repo.get_current_stock() if r["qty"]]
        except Exception as e:                           # 연결 끊김 등 → 대기열에 쌓였는지는 waiting 으로 알림
            out["read_error"] = str(e)[:200]
        return out

    def learn(self, name: str, box, seq: int) -> str:
        frame = self.recent.get(seq)
        if frame is None:
            raise ValueError("그 화면이 너무 오래됨 — 다시 눌러 주세요")
        k = self.comp.learn_label(name, frame, box)
        return f"{self.config.ITEMS[k]['name']} ({k})"


# ---------------------------------------------------------------------------
# 서버 상태
# ---------------------------------------------------------------------------
class Hub:
    def __init__(self, det: Detector, cv_window: bool) -> None:
        self.det = det
        self.cameras: set[web.WebSocketResponse] = set()
        self.viewers: set[web.WebSocketResponse] = set()
        self.busy = False
        self.last_result: dict | None = None
        self.camera_info = {"facing": "-"}
        self.cv_window = cv_window
        self.latest_annotated = None
        self.stats = {"frames": 0, "detections": 0}
        self.det_times: list[float] = []                  # 최근 인식 끝난 시각 (초당 인식 횟수 계산용)
        # 전/후 비교 (/compare 핸드폰 → /compare-view PC). 실시간 인식(cameras·viewers)과 따로
        self.cmp_phones: set[web.WebSocketResponse] = set()
        self.cmp_views: set[web.WebSocketResponse] = set()
        self.cmp_last = {"state": None, "shot_before": None, "shot_after": None, "compare": None, "commit": None}

    async def cmp_send(self, msg: dict) -> None:
        """전/후 비교 결과(JSON)를 PC 비교 화면들에 보내고, 늦게 연 화면을 위해 기억."""
        kind = msg.get("type")
        if kind == "compare":
            self.cmp_last["compare"], self.cmp_last["commit"] = msg, None
        elif kind == "commit":
            self.cmp_last["commit"] = msg
        await self.broadcast(self.cmp_views, json.dumps(msg, ensure_ascii=False, default=str))

    async def broadcast(self, targets, data) -> None:
        dead = []
        for ws in list(targets):
            try:
                if isinstance(data, (bytes, bytearray)):
                    await ws.send_bytes(data)
                else:
                    await ws.send_str(data)
            except (ConnectionResetError, RuntimeError):
                dead.append(ws)
        for ws in dead:
            targets.discard(ws)

    async def on_frame(self, jpeg: bytes) -> None:
        self.stats["frames"] += 1
        await self.broadcast(self.viewers, jpeg)              # PC 화면에는 모든 프레임을 바로
        if self.busy:
            return                                            # 인식 중이면 이 프레임은 화면 전달만
        self.busy = True
        asyncio.create_task(self._run_detect(jpeg))

    async def _run_detect(self, jpeg: bytes) -> None:
        try:
            res = await asyncio.get_running_loop().run_in_executor(self.det.pool, self.det.detect, jpeg)
            self.latest_annotated = res.pop("annotated")
            self.stats["detections"] += 1
            now = time.time()
            self.det_times = [t for t in self.det_times if now - t <= 10] + [now]   # 최근 10초
            span = now - self.det_times[0]
            res["det_fps"] = round((len(self.det_times) - 1) / span, 2) if span > 0 else 0.0
            res["viewers"] = len(self.viewers)
            res["facing"] = self.camera_info.get("facing")
            self.last_result = res
            msg = json.dumps(res, ensure_ascii=False)
            await self.broadcast(self.cameras | self.viewers, msg)
        except Exception as e:                                # 한 장 실패해도 계속
            log(f"인식 실패: {e!r}")
        finally:
            self.busy = False

    async def cv_loop(self) -> None:
        """--cv-window: PC 에 OpenCV 창으로도 표시 (영문 id 로 표시, 한글은 viewer 페이지에서)."""
        name = "phone live (q: close window)"
        while True:
            if self.latest_annotated is not None:
                cv2.imshow(name, self.latest_annotated)
            if (cv2.waitKey(30) & 0xFF) in (ord("q"), 27):
                cv2.destroyAllWindows()
                self.cv_window = False
                return
            await asyncio.sleep(0.03)


# ---------------------------------------------------------------------------
# HTTP / WebSocket
# ---------------------------------------------------------------------------
async def ws_camera(request: web.Request):
    hub: Hub = request.app["hub"]
    ws = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024, heartbeat=20)
    await ws.prepare(request)
    hub.cameras.add(ws)
    log(f"핸드폰 카메라 연결: {request.remote}")
    # PC 전/후 비교 화면(/compare-view)을 켜 둔 경우 → 실시간 PC 화면(/viewer)으로 자동 전환
    await hub.broadcast(hub.cmp_views, json.dumps({"type": "mode", "mode": "camera"}))
    if hub.last_result:
        await ws.send_str(json.dumps(hub.last_result, ensure_ascii=False))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                await hub.on_frame(msg.data)
            elif msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "hello":
                    hub.camera_info = data
                    await hub.broadcast(hub.viewers, json.dumps({**data, "type": "camera", "connected": True},
                                                                ensure_ascii=False))
                elif data.get("type") == "label":           # Unknown 상자에 이름 붙이기
                    try:
                        r = await asyncio.get_running_loop().run_in_executor(
                            hub.det.pool, hub.det.learn, data["name"], data["box"], int(data.get("seq", -1)))
                        log(f"이름표 기억: {data['name']} → {r}")
                        await ws.send_str(json.dumps({"type": "toast", "text": f"기억함: {r}"}, ensure_ascii=False))
                    except Exception as e:
                        await ws.send_str(json.dumps({"type": "toast", "text": f"실패: {e}"}, ensure_ascii=False))
    finally:
        hub.cameras.discard(ws)
        log("핸드폰 카메라 연결 끊김")
        await hub.broadcast(hub.viewers, json.dumps({"type": "camera", "connected": bool(hub.cameras),
                                                     "facing": hub.camera_info.get("facing", "-")}, ensure_ascii=False))
    return ws


async def ws_viewer(request: web.Request):
    hub: Hub = request.app["hub"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    hub.viewers.add(ws)
    log(f"PC 화면 연결: {request.remote}")
    await ws.send_str(json.dumps({"type": "camera", "facing": hub.camera_info.get("facing", "-"),
                                  "connected": bool(hub.cameras)}, ensure_ascii=False))
    if hub.cmp_phones and not hub.cameras:              # 핸드폰이 전/후 비교 화면에 있음 → PC 도 비교 화면으로
        await ws.send_str(json.dumps({"type": "mode", "mode": "compare"}))
    try:
        async for _ in ws:
            pass
    finally:
        hub.viewers.discard(ws)
    return ws


async def api_analyze(request: web.Request):
    """'식재료 파악하기': 일시정지한 화면 1장을 정밀 설정(full)으로 다시 인식.
    결과는 요청한 핸드폰에 돌려주고, PC 화면에는 그 사진 + 결과를 보냄."""
    hub: Hub = request.app["hub"]
    jpeg = await request.read()
    if not jpeg:
        return web.json_response({"error": "사진이 비어 있음"}, status=400)
    log(f"식재료 파악하기 요청 ({len(jpeg) // 1024} KB)")
    try:
        # 모델은 한 번에 한 장 → 실시간 인식이 돌고 있으면 끝난 뒤 바로 실행
        res = await asyncio.get_running_loop().run_in_executor(hub.det.pool, hub.det.detect, jpeg, "full")
    except Exception as e:
        log(f"식재료 파악 실패: {e!r}")
        return web.json_response({"error": str(e)}, status=500)
    hub.latest_annotated = res.pop("annotated")
    res["viewers"] = len(hub.viewers)
    res["facing"] = hub.camera_info.get("facing")
    log("식재료 파악 결과: " + (", ".join(f"{d['name']} {d['conf']:.2f}" for d in res["dets"]) or "없음")
        + f" ({res['ms']} ms)")
    await hub.broadcast(hub.viewers, jpeg)                     # PC 화면: 분석한 사진
    await hub.broadcast(hub.viewers, json.dumps(res, ensure_ascii=False))
    return web.json_response(res, dumps=lambda o: json.dumps(o, ensure_ascii=False))


CMP_LIVE, CMP_SHOT_BEFORE, CMP_SHOT_AFTER = 0, 1, 2      # 비교 화면 바이너리 메시지 첫 1바이트: 실시간 / 전 사진 / 후 사진


async def ws_compare(request: web.Request):
    """전/후 비교 화면 연결. ?role=phone: 핸드폰(/compare) — 실시간 화면·찍은 사진·상태를 보냄
                            ?role=view : PC(/compare-view) — 받기만 함"""
    hub: Hub = request.app["hub"]
    role = request.query.get("role", "view")
    ws = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024, heartbeat=20)
    await ws.prepare(request)
    last = hub.cmp_last
    if role == "phone":
        hub.cmp_phones.add(ws)
        log(f"전/후 비교 핸드폰 연결: {request.remote}")
        await hub.broadcast(hub.cmp_views, json.dumps({"type": "phone", "connected": True}))
        # 실시간 PC 화면(/viewer)을 켜 둔 경우 → 전/후 비교 PC 화면(/compare-view)으로 자동 전환
        await hub.broadcast(hub.viewers, json.dumps({"type": "mode", "mode": "compare"}))
    else:
        hub.cmp_views.add(ws)
        log(f"PC 전/후 비교 화면 연결: {request.remote}")
        if hub.cameras and not hub.cmp_phones:          # 핸드폰이 실시간 화면에 있음 → PC 도 실시간 화면으로
            await ws.send_str(json.dumps({"type": "mode", "mode": "camera"}))
        # 늦게 연 PC 화면: 지금까지의 사진·상태·결과를 순서대로
        await ws.send_str(json.dumps({"type": "phone", "connected": bool(hub.cmp_phones)}))
        for key, tag in (("shot_before", CMP_SHOT_BEFORE), ("shot_after", CMP_SHOT_AFTER)):
            if last[key]:
                await ws.send_bytes(bytes([tag]) + last[key])
        for key in ("state", "compare", "commit"):
            if last[key]:
                await ws.send_str(json.dumps(last[key], ensure_ascii=False, default=str))
    try:
        async for msg in ws:
            if role != "phone":
                continue
            if msg.type == WSMsgType.BINARY and msg.data:
                tag = msg.data[0]
                if tag == CMP_SHOT_BEFORE:
                    last["shot_before"], last["shot_after"], last["compare"], last["commit"] = msg.data[1:], None, None, None
                elif tag == CMP_SHOT_AFTER:
                    last["shot_after"], last["compare"], last["commit"] = msg.data[1:], None, None
                await hub.broadcast(hub.cmp_views, msg.data)
            elif msg.type == WSMsgType.TEXT:
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                if m.get("type") == "state":
                    last["state"] = m
                    shots = m.get("shots") or {}
                    if not shots.get("before"):
                        last["shot_before"] = None
                    if not shots.get("after"):
                        last["shot_after"] = None
                    if not (shots.get("before") and shots.get("after")):
                        last["compare"] = last["commit"] = None
                await hub.broadcast(hub.cmp_views, msg.data)
    finally:
        (hub.cmp_phones if role == "phone" else hub.cmp_views).discard(ws)
        if role == "phone":
            log("전/후 비교 핸드폰 연결 끊김")
            await hub.broadcast(hub.cmp_views, json.dumps({"type": "phone", "connected": bool(hub.cmp_phones)}))
    return ws


def _json(o, status=200):
    return web.json_response(o, status=status, dumps=lambda x: json.dumps(x, ensure_ascii=False, default=str))


async def api_compare(request: web.Request):
    """전/후 비교: multipart (before, after: JPEG) → 추가·제거 + 두 사진의 상자. DB 에는 아직 쓰지 않음."""
    det: Detector = request.app["hub"].det
    form = await request.post()
    try:
        before, after = form["before"].file.read(), form["after"].file.read()
    except (KeyError, AttributeError):
        return _json({"error": "전·후 사진 2장이 필요합니다"}, 400)
    log(f"전/후 비교 요청 ({len(before) // 1024} KB / {len(after) // 1024} KB)")
    try:
        res = await asyncio.get_running_loop().run_in_executor(det.pool, det.compare, before, after)
    except Exception as e:
        log(f"전/후 비교 실패: {e!r}")
        return _json({"error": str(e)}, 500)
    await request.app["hub"].cmp_send({"type": "compare", **res})          # PC 비교 화면에도
    log(f"전/후 비교 {res['cid']}: "
        + (" / ".join([f"+ {x['name']} {x['quantity']}" for x in res["added"]] + [f"- {x['name']} {x['quantity']}" for x in res["removed"]])
           or "변화 없음") + f" ({res['ms']} ms)")
    return _json(res)


async def api_compare_label(request: web.Request):
    """비교 화면의 Unknown 상자에 이름 붙이기 → 다시 비교한 결과."""
    det: Detector = request.app["hub"].det
    b = await request.json()
    try:
        res = await asyncio.get_running_loop().run_in_executor(
            det.pool, det.compare_label, b["cid"], b["side"], str(b["name"]).strip(), b["box"])
    except Exception as e:
        return _json({"error": str(e)}, 400)
    await request.app["hub"].cmp_send({"type": "compare", **res})
    log(f"전/후 비교 이름표: {res.get('learned')}")
    return _json(res)


async def api_compare_commit(request: web.Request):
    """비교 결과를 DB 에 반영(confirm) 또는 거절(reject)."""
    det: Detector = request.app["hub"].det
    b = await request.json()
    try:
        res = await asyncio.get_running_loop().run_in_executor(None, det.commit, b["cid"], b.get("decision", "confirm"))
    except Exception as e:
        log(f"DB 반영 실패: {e!r}")
        return _json({"error": str(e)}, 400)
    await request.app["hub"].cmp_send({"type": "commit", **res})
    log(f"DB {res['decision']} {res['cid']}: 이벤트 {len(res['events'])}건, 확인 필요 {len(res['alerts'])}건, 대기 {res['waiting']}건")
    return _json(res)


async def api_info(request: web.Request):
    port = request.app["port"]
    det = request.app["hub"].det
    return web.json_response({
        "camera_urls": [f"https://{ip}:{port}/camera" for ip in lan_ips()],
        "compare_urls": [f"https://{ip}:{port}/compare" for ip in lan_ips()],
        "model": det.comp.model_path.name,
        "backend": det.config.DETECTOR_BACKEND,
        "items": len(det.vision.ITEM_IDS),
        "db": bool(det.sync is not None and det.sync.enabled),     # 전/후 비교 → Supabase 재고 반영 가능 여부
    })


def page(name):
    async def handler(_):
        return web.FileResponse(STATIC / name)
    return handler


# ---------------------------------------------------------------------------
# HTTPS 인증서 (핸드폰 브라우저는 HTTPS 에서만 카메라를 열어 줌)
# ---------------------------------------------------------------------------
def lan_ips() -> list[str]:
    ips = set()
    try:                                  # 기본 경로로 나가는 IP (가장 가능성 높은 LAN 주소)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips = [ip for ip in ips if not ip.startswith("127.")]
    # 192.168.x.x 를 먼저 (WSL·Hyper-V 가상 어댑터 172.x 보다 와이파이일 가능성이 높음)
    return sorted(ips, key=lambda ip: (not ip.startswith("192.168."), ip))


def ensure_cert(ips: list[str]) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    CERT_DIR.mkdir(exist_ok=True)
    crt, key = CERT_DIR / "server.crt", CERT_DIR / "server.key"
    want = sorted(set(ips) | {"127.0.0.1"})
    if crt.exists() and key.exists():
        cert = x509.load_pem_x509_certificate(crt.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = sorted(str(i) for i in san.get_values_for_type(x509.IPAddress))
        if have == want and cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=7):
            return crt, key
    log(f"HTTPS 인증서 만드는 중 (주소: {', '.join(want)})")
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fridge phone live (local test)")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in want]),
                critical=False)
            .sign(k, hashes.SHA256()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    return crt, key


def main() -> None:
    ap = argparse.ArgumentParser(description="핸드폰 카메라 실시간 식재료 인식")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--cv-window", action="store_true", help="PC 에 OpenCV 창도 띄움 (영문 표시)")
    ap.add_argument("--http", action="store_true",
                    help="HTTPS 없이 실행 (USB 연결 + adb reverse 로 핸드폰에서 localhost 접속할 때)")
    args = ap.parse_args()

    import ssl
    ips = lan_ips()
    ssl_ctx = None
    if not args.http:
        crt, key = ensure_cert(ips)
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(crt, key)

    det = Detector()
    hub = Hub(det, args.cv_window)
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app["hub"], app["port"] = hub, args.port
    app.add_routes([
        web.get("/", page("index.html")),
        web.get("/camera", page("camera.html")),
        web.get("/viewer", page("viewer.html")),
        web.get("/compare", page("compare.html")),
        web.get("/compare-view", page("compare_view.html")),
        web.get("/ws/compare", ws_compare),
        web.post("/api/compare", api_compare),
        web.post("/api/compare/label", api_compare_label),
        web.post("/api/compare/commit", api_compare_commit),
        web.get("/ws/camera", ws_camera),
        web.get("/ws/viewer", ws_viewer),
        web.get("/api/info", api_info),
        web.post("/api/analyze", api_analyze),
        web.static("/static", STATIC),
    ])

    async def no_cache(request, response):          # 페이지·스크립트를 고치면 바로 반영되게
        response.headers["Cache-Control"] = "no-store"
    app.on_response_prepare.append(no_cache)

    async def start_bg(app):
        if args.cv_window:
            app["cv_task"] = asyncio.create_task(hub.cv_loop())
    app.on_startup.append(start_bg)

    scheme = "http" if args.http else "https"
    print("\n" + "=" * 64)
    print(" 핸드폰 (같은 와이파이)에서 열기:")
    for ip in ips:
        print(f"   {scheme}://{ip}:{args.port}/camera")
    print(f" 전/후 비교 → 재고 DB:  {scheme}://{ips[0] if ips else 'localhost'}:{args.port}/compare")
    print(f" PC 화면:  {scheme}://localhost:{args.port}/viewer")
    print(f" PC 전/후 비교 화면:  {scheme}://localhost:{args.port}/compare-view")
    if not args.http:
        print(" ※ 처음 접속 시 '안전하지 않음' 경고 → [고급] → [계속/방문] (이 PC 가 만든 시험용 인증서)")
    print(" ※ Windows 방화벽 창이 뜨면 '개인 네트워크' 허용")
    print("=" * 64 + "\n", flush=True)
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx, print=None)


if __name__ == "__main__":
    main()
