"""Vision AI 냉장고 — 영상 인식 실행 파일.

사용 예
  python main.py                                           # config.DEFAULT_SOURCE (지금은 h265 영상) + YOLO 인식
  python main.py --source 0                                # 카메라 0번 (없으면 config.FALLBACK_VIDEO)
  python main.py --source 영상.mp4                          # 영상 파일 + YOLO 인식
  python main.py --engine openai                           # OpenAI Vision API 로 인식 (크레딧 필요)
  python main.py --source dummy/fridge_demo.mp4 --mock     # 더미 영상 + 가짜 인식
  python main.py --compare before.jpg after.jpg            # 사진 2장만 비교

목록에 없는 물체(Unknown)가 나오면 영상이 잠시 멈추고 콘솔(터미널)에서 이름을 물어봅니다.
  → 터미널을 클릭하고 이름(예: 포도)을 입력 + Enter. 모르면 Enter 만. 입력한 이름은 user_labels.json 에 기억됩니다.

창이 떠 있을 때 키
  c : 수동 촬영 (iPad '다 넣었어요' 버튼 대신)
  b : 지금 화면을 기준(before)으로 다시 지정
  y / n : '확인 필요' 결과를 반영 / 거절
  q 또는 ESC : 종료
"""
import argparse
import copy
import json
import os
import platform
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import cv2

import config
from detector import DoorEventDetector, State
from vision import MockComparer, save_json, summarize


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def make_comparer(engine: str):
    if engine == "mock":
        log("MOCK 모드: 실제 인식 없이 config.DEMO_SCENARIO 순서대로 결과를 만듭니다.")
        return MockComparer()
    if engine == "yolo":
        log(f"YOLO 모드: {config.YOLO_MODEL.name} 로 PC에서 직접 인식 (모델 불러오는 중...)")
        from vision import YoloComparer
        return YoloComparer()
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY 가 없습니다. .env 파일을 만들거나, --engine yolo 로 실행하세요.")
    from vision import OpenAIComparer
    log(f"OpenAI 모드: 모델 {config.OPENAI_MODEL}")
    return OpenAIComparer()


def open_source(source: str):
    if source.isdigit():
        idx = int(source)
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if platform.system() == "Windows" else [cv2.CAP_ANY]
        for backend in backends:  # DSHOW 로 안 열리는 카메라는 MSMF 로 한 번 더 시도
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                break
            cap.release()
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
            is_file = False
        else:
            # 카메라가 없으면 멈추지 않고 실사 영상(없으면 더미 영상)으로 대신 실행
            hint = "(액션캠은 USB '웹캠 모드'인지, 장치 관리자에 카메라가 보이는지 확인)"
            if config.FALLBACK_VIDEO.exists():
                log(f"카메라 {idx}번을 열 수 없어 실사 영상으로 대신 실행합니다: {config.FALLBACK_VIDEO} {hint}")
                return open_source(str(config.FALLBACK_VIDEO))
            log(f"카메라 {idx}번을 열 수 없고 실사 영상도 없어 더미 영상으로 대신 실행합니다. {hint}")
            dummy = config.DUMMY_DIR / "fridge_demo.mp4"
            if not dummy.exists():
                log("더미 영상이 없어 새로 만듭니다...")
                import make_dummy_video
                make_dummy_video.main()
            return open_source(str(dummy))
    else:
        if not Path(source).exists():
            sys.exit(f"영상 파일을 찾을 수 없습니다: {source}\n먼저 python make_dummy_video.py 를 실행해 보세요.")
        cap = cv2.VideoCapture(source)
        is_file = True
    if not cap.isOpened():
        sys.exit(f"입력을 열 수 없습니다: {source} (카메라 번호를 0, 1, 2 로 바꿔 보세요)")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if fps <= 1 or fps > 240:
        fps = 30.0
    if is_file:
        log(f"입력: {source}  (파일, {fps:.0f}fps)")
        skip, stop = trim_for(source, cap, fps)
    else:
        log(f"입력: 카메라 {source}  ({fps:.0f}fps)")
        skip, stop = 30, None   # 카메라는 자동 노출이 안정될 때까지 30프레임 버림
    return cap, is_file, fps, skip, stop


def trim_for(source_path: str, cap, fps: float):
    """영상 파일 앞·뒤의 검은(로고) 구간을 밝기로 찾음.
    (건너뛸 프레임 수, 분석에 쓸 마지막 프레임 번호 또는 None)
    화면에는 끝까지 재생하되, 이 구간은 문 닫힘 감지·마지막 촬영에 쓰지 않음.
    검은 구간이 없는 영상(더미 영상 등)은 그대로 둠."""
    # 영상 전체의 프레임별 밝기를 한 번 훑어서 밝은(실사) 구간의 처음과 끝을 찾음
    bright = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
        bright.append(float(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).mean()))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    lit = [i for i, b in enumerate(bright) if b >= config.FALLBACK_DARK_LEVEL]
    if not lit:
        return 0, None
    margin = int(config.FALLBACK_EDGE_MARGIN_SECONDS * fps)
    # 검은 구간이 실제로 있는 쪽에만 페이드 여유(margin)를 더 뺌 (0부터 센 프레임 번호)
    first = lit[0] + margin if lit[0] > 0 else 0
    last = lit[-1] - margin if lit[-1] < len(bright) - 1 else len(bright) - 1
    if first >= last:
        return 0, None
    # (건너뛸 프레임 수, 분석에 쓸 마지막 프레임 번호(1부터 셈), 자를 게 없으면 None)
    return first, (last + 1 if last < len(bright) - 1 else None)


def ascii_summary(result: dict) -> str:
    parts = [f"+{x['item_id']} {x['quantity']}" for x in result["added"]]
    parts += [f"-{x['item_id']} {x['quantity']}" for x in result["removed"]]
    s = " / ".join(parts) if parts else "no change"
    return s + ("  [CONFIRM y/n]" if result.get("_pending") else "")


def draw_overlay(frame, det: DoorEventDetector, status: str):
    view = cv2.resize(frame, (960, int(frame.shape[0] * 960 / frame.shape[1])))
    m = det.metrics
    lines = [
        f"state: {det.state.value}",
        f"motion {m.motion:.3f}  bright {m.brightness:.0f} (base {m.base_brightness:.0f})  scene {m.scene_change:.3f}",
        status,
    ]
    color = {State.IDLE: (80, 200, 80), State.ACTIVE: (0, 140, 255),
             State.SETTLING: (0, 220, 255), State.COOLDOWN: (200, 120, 255)}[det.state]
    cv2.rectangle(view, (0, 0), (960, 92), (0, 0, 0), -1)
    for i, line in enumerate(lines):
        cv2.putText(view, line, (12, 26 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                    color if i == 0 else (240, 240, 240), 2, cv2.LINE_AA)
    return view


WINDOW = "fridge vision (q: quit, c: capture, b: baseline)"


def stock_text(stock: dict) -> str:
    """0개인 품목은 빼고 '우유 1, 버터 2' 형태로."""
    s = ", ".join(f"{config.ITEMS[k]['name']} {v}" for k, v in stock.items() if v)
    return s or "(없음)"


def ask_unknown_labels(comparer, result: dict, before, after, cid: str, show: bool) -> dict:
    """Unknown 물체가 있으면 콘솔에서 이름을 입력받아 기억시키고, 다시 계산한 결과를 돌려줌.
    창 모드면 해당 상자를 굵게 표시한 사진을 보여 줌. Enter 만 누르면 건너뜀."""
    regions = result.get("unknown_regions") or []
    if not regions or not hasattr(comparer, "learn_label"):
        return result
    log(f"목록에 없는 물체(Unknown) {len(regions)}개 → 이름을 입력하면 기억해서 재고에 반영합니다 (건너뛰기: Enter)")
    learned = False
    for i, reg in enumerate(regions, 1):
        frame = after if reg["side"] == "after" else before
        if show:
            view = frame.copy()
            x1, y1, x2, y2 = (int(v) for v in reg["box"])
            cv2.rectangle(view, (x1, y1), (x2, y2), (0, 0, 255), 4)
            cv2.putText(view, f"Unknown #{i} ({reg['side']}) - type the name in the console",
                        (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)
            cv2.imshow(WINDOW, cv2.resize(view, (960, int(view.shape[0] * 960 / view.shape[1]))))
            cv2.waitKey(1)
        try:
            text = input(f"  Unknown #{i} ({'넣은' if reg['side'] == 'after' else '꺼낸'} 물체, "
                         f"위치 {[int(v) for v in reg['box']]}) 이름은? > ").strip()
        except EOFError:     # 콘솔 입력이 없는 환경
            break
        if not text:
            continue
        k = comparer.learn_label(text, frame, reg["box"])
        log(f"  기억함: Unknown #{i} → {config.ITEMS[k]['name']} ({k})")
        learned = True
    if not learned:
        return result
    new = comparer.compare(before, after, cid)          # 입력한 이름표를 적용해서 다시 계산
    new["latency_sec"] = result.get("latency_sec")
    new["user_labeled"] = True
    log(f"이름표 적용 후 결과: {summarize(new)}")
    return new


def new_capture_id() -> str:
    return "capture-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def run_compare(args) -> None:
    before, after = cv2.imread(args.compare[0]), cv2.imread(args.compare[1])
    if before is None or after is None:
        sys.exit("사진을 읽을 수 없습니다. 경로를 확인하세요.")
    comparer = make_comparer(args.engine)
    cid = new_capture_id()
    out = config.CAPTURE_DIR / cid
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "before.jpg"), before)
    cv2.imwrite(str(out / "after.jpg"), after)
    t0 = time.monotonic()
    result = comparer.compare(before, after, cid)
    result["latency_sec"] = round(time.monotonic() - t0, 2)
    log(f"결과: {summarize(result)}  ({result['latency_sec']}초)")
    result = ask_unknown_labels(comparer, result, before, after, cid, show=not args.no_window)
    cv2.destroyAllWindows()
    save_json(out / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def run_stream(args) -> None:
    comparer = make_comparer(args.engine)
    cap, is_file, fps, warmup, stop_at = open_source(args.source)
    sync = None
    if not args.no_sync and args.engine != "mock":   # mock(가짜 결과)은 실제 DB 에 보내지 않음
        # KINI 개인 재고는 로컬 SQLite(A 재고 이벤트 API)가 기준 → KINI_API_URL 이 있으면 그쪽으로만 보냄
        from kini_inventory_client import sink_from_env
        sync = sink_from_env(config.CAPTURE_DIR / "kini_queue.jsonl", log=log)
        if sync is not None:
            log("재고 연동: KINI A 재고 이벤트 API (로컬 SQLite). 확정은 앱에서 재료·중량 확인 후")
        elif config.SUPABASE_URL:
            from supabase_sync import InventorySync
            b = config.SUPABASE_BASELINE_ON_START
            sync = InventorySync(log=log, baseline=is_file if b == "auto" else bool(b))
    det = DoorEventDetector()
    pool = ThreadPoolExecutor(max_workers=1)
    pending = None          # (future, after_frame, capture_dir)
    awaiting = None         # 확인 대기 중인 (result, after_frame)
    # mock 은 시나리오의 시작 재고, 실제 인식은 첫 촬영의 before 개수로 시작
    inventory = copy.deepcopy(config.INITIAL_STOCK) if args.engine == "mock" else None
    status = "waiting for door close"
    frame_idx, pos = 0, 0
    captured_any, user_quit, last_frame, t = False, False, None, 0.0
    delay = max(1, int(1000 / fps)) if (is_file and not args.no_window) else 1
    config.CAPTURE_DIR.mkdir(exist_ok=True)
    if warmup and is_file:
        log(f"영상 앞 {warmup}프레임({warmup / fps:.1f}초)은 화면에만 재생하고 분석에는 쓰지 않음")
    if stop_at:
        log(f"영상 {stop_at}번째 프레임({stop_at / fps:.1f}초) 이후(끝부분)는 화면에만 재생하고 분석에는 쓰지 않음")

    def apply(result, after_frame):
        nonlocal inventory, status
        if inventory is None:
            log(f"시작 재고(첫 촬영 before 인식): {stock_text(result['before'])}")
        inventory = dict(result["after"])
        det.set_baseline(after_frame)
        save_json(config.CAPTURE_DIR / "inventory.json", inventory)
        status = "applied: " + ascii_summary(result)
        log(f"재고 반영 → {stock_text(inventory)}")
        if sync is not None:
            sync.decide(result, "confirm")   # Supabase: 이 촬영의 pending 이벤트 → confirm_stock_event()

    def start_vision(event):
        nonlocal pending, status, captured_any
        captured_any = True
        cid = new_capture_id()
        out = config.CAPTURE_DIR / cid
        out.mkdir(parents=True, exist_ok=True)
        before = det.baseline_frame
        cv2.imwrite(str(out / "before.jpg"), before)
        cv2.imwrite(str(out / "after.jpg"), event.frame)
        log(f"촬영 → {out.name}  Vision 분석 시작")
        status = "vision running..."
        t0 = time.monotonic()

        def job():
            r = comparer.compare(before, event.frame, cid)
            r["latency_sec"] = round(time.monotonic() - t0, 2)
            r["detector"] = vars(event.metrics)
            return r

        pending = (pool.submit(job), event.frame, out)

    def capture_at_end():
        """영상이 (분석 구간) 끝날 때까지 촬영이 한 번도 없었으면 마지막 분석 프레임으로 촬영."""
        if (is_file and config.CAPTURE_AT_VIDEO_END and not captured_any and not user_quit
                and pending is None and last_frame is not None):
            log("영상이 끝날 때까지 문 닫힘이 감지되지 않아 마지막 화면으로 촬영합니다")
            start_vision(det.force_capture(last_frame, t))

    while True:
        ok, frame = cap.read()
        if not ok:
            if is_file and args.loop:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                pos = 0
                continue
            break
        pos += 1                 # 영상 안에서의 위치 (반복 재생 시 되감김)
        frame_idx += 1           # 전체 진행 프레임 수 (시각 계산용, 되감기지 않음)

        # 앞(로고·카메라 노출 안정) / 뒤(로고) 구간: 화면에만 보여주고 분석하지 않음
        in_tail = bool(stop_at) and pos > stop_at
        if pos <= warmup or in_tail:
            if in_tail and not args.loop:
                capture_at_end()
            if not args.no_window:
                cv2.imshow(WINDOW, draw_overlay(frame, det, status))
                if (cv2.waitKey(delay) & 0xFF) in (ord("q"), 27):
                    user_quit = True
                    break
            continue
        last_frame = frame
        t = frame_idx / fps if is_file else time.monotonic()
        if det.baseline_frame is None:
            det.set_baseline(frame)
            cv2.imwrite(str(config.CAPTURE_DIR / "baseline.jpg"), frame)
            log("기준 화면 저장 (captures/baseline.jpg)")

        event = det.update(frame, t)
        busy = pending is not None or awaiting is not None
        if event and not busy:
            if event.kind == "no_change":
                log("문 닫힘 감지 → 장면 변화 거의 없음, API 호출 생략")
                status = "no change (API skipped)"
                if hasattr(comparer, "on_no_change"):
                    comparer.on_no_change()
            else:
                log("문 닫힘 감지")
                start_vision(event)
        elif event and busy:
            log("문 닫힘 감지됐지만 이전 분석이 끝나지 않아 무시")

        # Vision 결과 수신
        if pending and pending[0].done():
            fut, after_frame, out = pending
            pending = None
            try:
                result = fut.result()
            except Exception as e:  # API 실패 → 폴백은 서버 쪽에서 저장된 JSON 사용
                log(f"Vision 실패: {e}")
                status = "vision failed (see console)"
            else:
                log(f"결과: {summarize(result)}  ({result['latency_sec']}초)")
                if not args.no_window:
                    result = ask_unknown_labels(comparer, result, cv2.imread(str(out / "before.jpg")),
                                                after_frame, out.name, show=True)
                save_json(out / "result.json", result)
                if sync is not None:
                    sync.record(result)      # Supabase: capture + stock_event(pending)
                if result["needs_confirmation"] and not args.no_window:
                    result["_pending"] = True
                    awaiting = (result, after_frame)
                    status = ascii_summary(result)
                    log("확인 필요 → 창에서 y(반영) / n(거절)")
                else:
                    apply(result, after_frame)

        if not args.no_window:
            cv2.imshow(WINDOW, draw_overlay(frame, det, status))
            key = cv2.waitKey(delay) & 0xFF
            if key in (ord("q"), 27):
                user_quit = True
                break
            if key == ord("c") and not busy:
                log("수동 촬영")
                start_vision(det.force_capture(frame, t))
            if key == ord("b"):
                det.set_baseline(frame)
                log("기준 화면 다시 지정")
            if awaiting and key in (ord("y"), ord("n")):
                result, after_frame = awaiting
                awaiting = None
                if key == ord("y"):
                    apply(result, after_frame)
                else:
                    status = "rejected"
                    log("결과 거절 → 재고·기준 화면 유지")
                    if sync is not None:
                        sync.decide(result, "reject")   # Supabase: reject_stock_event()

    # 영상 파일이 끝날 때까지 문 닫힘이 한 번도 감지되지 않았으면 마지막 화면으로 촬영
    if not captured_any:
        capture_at_end()
        if captured_any and not args.no_window:
            cv2.imshow(WINDOW, draw_overlay(last_frame, det, "video ended - analyzing last frame..."))
            cv2.waitKey(1)

    # 파일이 끝났는데 분석이 남아 있으면 기다렸다가 처리
    if pending:
        fut, after_frame, out = pending
        try:
            result = fut.result()
            log(f"결과: {summarize(result)}  ({result['latency_sec']}초)")
            if not args.no_window:
                result = ask_unknown_labels(comparer, result, cv2.imread(str(out / "before.jpg")),
                                            after_frame, out.name, show=True)
            save_json(out / "result.json", result)
            if sync is not None:
                sync.record(result)
            apply(result, after_frame)
        except Exception as e:
            log(f"Vision 실패: {e}")
            status = "vision failed (see console)"

    # 영상 파일이 끝나도 창을 바로 닫지 않고 마지막 화면 + 결과를 보여줌
    if is_file and not args.no_window and not user_quit and last_frame is not None:
        log("영상 끝. 창에서 q 또는 ESC 를 누르면 종료합니다.")
        view = draw_overlay(last_frame, det, "END  " + status + "  (q: quit)")
        while True:
            cv2.imshow(WINDOW, view)
            key = cv2.waitKey(100) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    cap.release()
    cv2.destroyAllWindows()
    pool.shutdown()
    if sync is not None:
        sync.close()            # 보내는 중인 작업이 끝날 때까지 기다림
    if inventory is None:
        log("종료. 인식에 성공한 촬영이 없어 재고를 만들지 못했습니다.")
    else:
        log(f"종료. 최종 재고: {stock_text(inventory)}")


def main() -> None:
    p = argparse.ArgumentParser(description="Vision AI 냉장고 영상 인식")
    p.add_argument("--source", default=config.DEFAULT_SOURCE,
                   help=f"카메라 번호(0,1,..) 또는 영상 파일 경로 (기본: config.DEFAULT_SOURCE = {config.DEFAULT_SOURCE})")
    p.add_argument("--engine", choices=["yolo", "openai", "mock"], default=config.VISION_ENGINE,
                   help=f"인식 엔진 (기본: {config.VISION_ENGINE}, config.VISION_ENGINE)")
    p.add_argument("--mock", action="store_true", help="--engine mock 과 같음 (가짜 인식 결과)")
    p.add_argument("--no-window", action="store_true", help="화면 창 없이 실행 (확인 필요 결과도 자동 반영)")
    p.add_argument("--loop", action="store_true", help="영상 파일을 반복 재생")
    p.add_argument("--no-sync", action="store_true", help="재고 이벤트를 보내지 않음 (KINI A API / Supabase)")
    p.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="사진 2장만 비교")
    args = p.parse_args()
    if args.mock:
        args.engine = "mock"
    if args.compare:
        run_compare(args)
    else:
        run_stream(args)


if __name__ == "__main__":
    main()
