"""이전·현재 사진 비교.

원칙: 모델은 '사진별 품목 개수'만 답하고, 추가·제거 계산과 확인 필요 여부는 코드가 정합니다.
"""
import base64
import copy
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, create_model

import config

def _load_user_labels() -> dict:
    """사용자가 Unknown 물체에 붙인 이름 기록 {"items": {item_id: 이름}, "labels": [...]}."""
    try:
        return json.loads(config.USER_LABELS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"items": {}, "labels": []}


# 사용자가 새로 만든 품목(목록에 없던 이름)을 품목 목록 끝에 추가 → 재고에 함께 표시됨
for _k, _name in _load_user_labels().get("items", {}).items():
    config.ITEMS.setdefault(_k, {"name": _name, "description": f"user-labeled item: {_name}", "user": True})

ITEM_IDS = list(config.ITEMS.keys())
UNKNOWN = -1   # YOLO 상자 품목 번호: 목록에 없는 물체 (범용 검색어 또는 잘게 쪼개진 묶음)


def _build_models():
    """품목별 개수를 고정 필드로 갖는 스키마 → Structured Outputs 가 모든 품목을 빠짐없이 채움.
    사용자가 새 품목을 추가하면 다시 만듦."""
    global Counts, VisionAnswer
    Counts = create_model("Counts", **{k: (int, ...) for k in ITEM_IDS})
    VisionAnswer = create_model(
        "VisionAnswer",
        before=(Counts, ...),                     # 첫 번째 사진의 품목별 개수
        after=(Counts, ...),                      # 두 번째 사진의 품목별 개수
        hand_or_open_door_visible=(bool, ...),    # 두 번째 사진에 손·팔·열린 문이 보이는지
        uncertain_items=(list[str], ...),         # 개수가 확실하지 않은 item_id
        note=(str, ...),                          # 한 줄 설명 (한국어)
    )


_build_models()


def location_of(xyxy, frame_shape) -> str:
    """상자 중심 → 냉장고 칸 'X,Y' (X: config.FRIDGE_X_LABELS, Y: 층 번호)."""
    h, w = frame_shape[:2]
    cx, cy = (xyxy[0] + xyxy[2]) / 2 / w, (xyxy[1] + xyxy[3]) / 2 / h
    xs = config.FRIDGE_X_LABELS
    x = xs[min(len(xs) - 1, int(cx * len(xs)))]
    row = sum(cy >= s for s in config.FRIDGE_FLOOR_SPLITS)        # 화면 위에서부터 0, 1, ...
    floors = len(config.FRIDGE_FLOOR_SPLITS) + 1
    y = row + 1 if config.FRIDGE_FLOOR_NUMBER_FROM_TOP else floors - row
    return f"{x},{y}"


def find_or_add_item(text: str) -> str:
    """사용자가 입력한 이름 → item_id. 기존 품목(한글 이름·영문 id·검색어)과 같으면 그 품목, 없으면 새 품목 추가."""
    t = text.strip().lower().replace(" ", "")
    for k, v in config.ITEMS.items():
        names = {k, k.replace("_", ""), config.YOLO_PROMPTS.get(k, "").replace(" ", "")}
        names |= {n.strip() for n in v["name"].split("·")}
        if t in {n.lower() for n in names if n}:
            return k
    n = 1
    while f"user_{n}" in config.ITEMS:
        n += 1
    k = f"user_{n}"
    config.ITEMS[k] = {"name": text.strip(), "description": f"user-labeled item: {text.strip()}", "user": True}
    ITEM_IDS.append(k)
    _build_models()
    return k


PROMPT = """You are checking a small demo refrigerator.
Image 1 = BEFORE, image 2 = AFTER. Both are taken by the same fixed camera inside the fridge.

Count ONLY these items, separately for each image. Items not in this list must be ignored.
{item_lines}

Rules:
- Count each image independently. Do not guess the AFTER count from the BEFORE image.
- If an item is not visible, its count is 0.
- Put an item_id in uncertain_items if it is partly hidden or you are not sure of the count.
- hand_or_open_door_visible = true if a hand, arm, or an open door / outside room is visible in the AFTER image.
- note: one short Korean sentence describing what changed.
"""


def _encode(frame) -> str:
    h, w = frame.shape[:2]
    s = min(1.0, config.IMAGE_MAX_SIDE / max(h, w))
    if s < 1.0:
        frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("JPEG 인코딩 실패")
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def build_result(capture_id: str, answer: VisionAnswer, source: str) -> dict:
    """모델 답(개수)으로부터 추가·제거와 확인 필요 여부를 코드로 계산."""
    before = {k: max(0, v) for k, v in answer.before.model_dump().items()}
    after = {k: max(0, v) for k, v in answer.after.model_dump().items()}
    added, removed = [], []
    for k in ITEM_IDS:
        d = after[k] - before[k]
        if d > 0:
            added.append({"item_id": k, "name": config.ITEMS[k]["name"], "quantity": d})
        elif d < 0:
            removed.append({"item_id": k, "name": config.ITEMS[k]["name"], "quantity": -d})

    uncertain = [u for u in answer.uncertain_items if u in config.ITEMS]
    reasons = []
    if answer.hand_or_open_door_visible:
        reasons.append("손 또는 열린 문이 보임 → 재촬영 권장")
    changed_ids = {x["item_id"] for x in added + removed}
    if uncertain and changed_ids & set(uncertain):
        reasons.append("변화한 품목의 개수가 불확실함")
    if any(x["quantity"] >= config.CONFIRM_IF_DELTA_AT_LEAST for x in added + removed):
        reasons.append(f"한 품목이 {config.CONFIRM_IF_DELTA_AT_LEAST}개 이상 변함")

    return {
        "capture_id": capture_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "before": before,
        "after": after,
        "added": added,
        "removed": removed,
        "uncertain_items": uncertain,
        "hand_or_open_door_visible": answer.hand_or_open_door_visible,
        "needs_confirmation": bool(reasons),
        "confirmation_reasons": reasons,
        "note": answer.note,
    }


class OpenAIComparer:
    def __init__(self) -> None:
        from openai import OpenAI  # 키가 없을 때 mock 모드는 이 import 없이도 동작

        self.client = OpenAI()      # OPENAI_API_KEY 환경변수(.env) 사용
        self.model = config.OPENAI_MODEL
        item_lines = "\n".join(f"- {k}: {v['description']}" for k, v in config.ITEMS.items())
        self.prompt = PROMPT.format(item_lines=item_lines)

    def compare(self, before_frame, after_frame, capture_id: str) -> dict:
        kwargs = {}
        if config.OPENAI_REASONING_EFFORT:
            kwargs["reasoning"] = {"effort": config.OPENAI_REASONING_EFFORT}
        resp = self.client.responses.parse(
            model=self.model,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": self.prompt},
                    {"type": "input_image", "image_url": _encode(before_frame), "detail": "high"},
                    {"type": "input_image", "image_url": _encode(after_frame), "detail": "high"},
                ],
            }],
            text_format=VisionAnswer,
            **kwargs,
        )
        answer = resp.output_parsed
        if answer is None:
            raise RuntimeError("모델이 형식에 맞는 답을 주지 않았습니다 (거절 또는 빈 응답).")
        return build_result(capture_id, answer, source=f"openai:{self.model}")


class YoloComparer:
    """YOLOE 로 before/after 사진의 품목 개수를 각각 세고, 차이는 build_result 가 계산.

    목록에 없는 물체(Unknown)
      1) 범용 검색어('object' 등)가 1등인 상자 (점수 YOLO_UNKNOWN_CONF 이상)
      2) 낮은 점수 상자 여러 개가 붙어 있는 묶음 (예: 포도 한 송이가 사과 여러 개로 쪼개짐)
      → 사용자가 이름을 붙이면(learn_label) 생김새를 기억해서 다음부터 그 품목으로 셈."""

    def __init__(self) -> None:
        import ultralytics  # mock/openai 모드에서는 불러오지 않음

        weights = config.YOLO_MODEL if config.YOLO_MODEL.exists() else config.YOLO_MODEL.name
        # 사용자가 추가한 품목(글자 검색어 없음)은 YOLOE 에 넣지 않고 생김새 기억으로만 찾음
        prompt_ids = [k for k in ITEM_IDS if not config.ITEMS[k].get("user")]
        assert prompt_ids == ITEM_IDS[:len(prompt_ids)], "사용자 품목은 목록 끝에 있어야 함"
        self.item_prompts = [config.YOLO_PROMPTS.get(k, k.replace("_", " ")) for k in prompt_ids]
        self.generic_prompts = list(config.YOLO_GENERIC_PROMPTS)
        self.hand_prompts = list(config.YOLO_HAND_PROMPTS)
        # 클래스 번호 순서: [품목 0..N-1] [범용 N..] [손]
        names = self.item_prompts + self.generic_prompts + self.hand_prompts
        self.n_items = len(self.item_prompts)
        self.n_known = self.n_items + len(self.generic_prompts)
        if config.YOLO_MODEL.name.startswith("yoloe"):
            self.model = ultralytics.YOLOE(str(weights))
            self.model.set_classes(names, self.model.get_text_pe(names))
        else:
            self.model = ultralytics.YOLOWorld(str(weights))
            self.model.set_classes(names)
        self.model.predict(np.zeros((320, 320, 3), np.uint8), verbose=False)  # 첫 실행 지연 미리 해소
        self.memory = _load_user_labels()
        self._clip = None

    # ------------------------------------------------------------------
    # 생김새 특징 (CLIP ViT-B/32) — 사용자 이름표 기억·비교용
    # ------------------------------------------------------------------
    def _embed(self, frame, xyxy) -> np.ndarray:
        import torch
        from PIL import Image

        if self._clip is None:
            import clip
            model, pre = clip.load("ViT-B/32", device="cpu", download_root=str(config.BASE_DIR / "weights" / "clip"))
            self._clip = (model.eval(), pre)
        model, pre = self._clip
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = xyxy
        px, py = 0.1 * (x2 - x1), 0.1 * (y2 - y1)
        crop = frame[max(0, int(y1 - py)):min(h, int(y2 + py)), max(0, int(x1 - px)):min(w, int(x2 + px))]
        img = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        with torch.no_grad():
            f = model.encode_image(pre(img).unsqueeze(0)).float()[0].numpy()
        return f / np.linalg.norm(f)

    def _recall(self, frame, box) -> None:
        """Unknown 상자를 사용자가 붙인 이름표와 비교해서 같은 물체면 그 품목으로 바꿈."""
        labels = self.memory.get("labels", [])
        if not labels:
            return
        e = self._embed(frame, box[2])
        best, best_sim = None, -1.0
        for lab in labels:
            if lab["item_id"] not in ITEM_IDS:
                continue
            sim = float(np.dot(e, np.asarray(lab["emb"], np.float32)))
            same_place = self._iou(box[2], lab["box"]) >= 0.5
            if sim >= config.USER_LABEL_SIM or (same_place and sim >= config.USER_LABEL_SIM_SAME_PLACE):
                if sim > best_sim:
                    best, best_sim = lab, sim
        if best is not None:
            box[0] = ITEM_IDS.index(best["item_id"])
            box.append(f"user {best_sim:.2f}")   # 표시용: 사용자 이름표로 바뀌었음

    def learn_label(self, text: str, frame, xyxy) -> str:
        """사용자가 Unknown 물체에 붙인 이름을 기억 (user_labels.json). 돌려주는 값: item_id."""
        k = find_or_add_item(text)
        if config.ITEMS[k].get("user"):
            self.memory.setdefault("items", {})[k] = config.ITEMS[k]["name"]
        self.memory.setdefault("labels", []).append({
            "item_id": k, "name": config.ITEMS[k]["name"], "box": [round(v, 1) for v in xyxy],
            "emb": [round(float(v), 5) for v in self._embed(frame, xyxy)],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        config.USER_LABELS_FILE.write_text(json.dumps(self.memory, ensure_ascii=False), encoding="utf-8")
        return k

    # ------------------------------------------------------------------
    @staticmethod
    def _near(a, b, gap) -> bool:
        """두 상자가 겹치거나 (짧은 변 × gap) 이내로 붙어 있는지."""
        m = gap * min(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])
        return not (a[2] + m < b[0] or b[2] + m < a[0] or a[3] + m < b[1] or b[3] + m < a[1])

    def _group_fragments(self, boxes, frame_area):
        """낮은 점수의 작은 조각 상자들이 서로 붙어 있으면 한 묶음(Unknown)으로 합침."""
        max_area = config.YOLO_GROUP_MAX_AREA * frame_area
        low = [b for b in boxes if b[0] != UNKNOWN and b[1] < config.YOLO_GROUP_MAX_CONF
               and (b[2][2] - b[2][0]) * (b[2][3] - b[2][1]) <= max_area]
        parent = list(range(len(low)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(low)):
            for j in range(i + 1, len(low)):
                if self._near(low[i][2], low[j][2], config.YOLO_GROUP_GAP):
                    parent[root(i)] = root(j)
        groups = {}
        for i, b in enumerate(low):
            groups.setdefault(root(i), []).append(b)
        grouped = [x for g in groups.values() if len(g) > 1 for x in g]
        # 묶이지 않은 상자는 그대로, 단 YOLO_CONF 미만의 약한 조각은 혼자서는 버림
        out = [b for b in boxes if not any(b is x for x in grouped) and b[1] >= config.YOLO_CONF]
        for g in groups.values():
            if len(g) > 1:
                xs = [b[2] for b in g]
                out.append([UNKNOWN, max(b[1] for b in g),
                            [min(x[0] for x in xs), min(x[1] for x in xs), max(x[2] for x in xs), max(x[3] for x in xs)],
                            f"group {len(g)}"])
        return out

    def _detect(self, frame):
        """[(item_index, conf, (x1,y1,x2,y2)), ...], 손 보임 여부, 표시용 그림.
        agnostic_nms: 한 물건이 '오렌지'와 '레몬'으로 동시에 잡히지 않도록 가장 높은 점수 하나만 남김.
        item_index 가 UNKNOWN(-1) 이면 목록에 없는 물체."""
        conf = min(config.YOLO_CONF, config.YOLO_FRAGMENT_CONF)   # 약한 조각까지 받아서 묶음 찾기에만 씀
        r = self.model.predict(frame, conf=conf, agnostic_nms=True, verbose=False)[0]
        boxes, hand = [], False
        for c, s, xyxy in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
            c = int(c)
            if s < config.YOLO_CONF and c >= self.n_items:
                continue                       # 약한 범용·손 상자는 무시
            if c < self.n_items:
                boxes.append([c, float(s), xyxy])
            elif c < self.n_known:
                # 범용 단어('object' 등)로 밀려난 물체 = Unknown. 배경 과탐지를 막으려 높은 점수만 인정
                if s >= config.YOLO_UNKNOWN_CONF:
                    boxes.append([UNKNOWN, float(s), xyxy])
            else:
                hand = True
        boxes = self._group_fragments(boxes, frame.shape[0] * frame.shape[1])
        for b in boxes:
            if b[0] == UNKNOWN:
                self._recall(frame, b)
        return boxes, hand, self.draw(frame, boxes)

    @staticmethod
    def draw(frame, boxes, highlight=None):
        """상자 그림 (영문 id 로 표시: OpenCV 는 한글을 못 그림). Unknown 은 빨강, 사용자 이름표는 노랑."""
        view = frame.copy()
        for i, b in enumerate(boxes):
            c, s, (x1, y1, x2, y2) = b[0], b[1], b[2]
            label = "Unknown" if c == UNKNOWN else ITEM_IDS[c]
            color = (0, 0, 255) if c == UNKNOWN else (0, 220, 255) if len(b) > 3 and str(b[3]).startswith("user") else (0, 200, 0)
            thick = 4 if highlight is not None and i == highlight else 2
            cv2.rectangle(view, (int(x1), int(y1)), (int(x2), int(y2)), color, thick)
            cv2.putText(view, f"{label} {s:.2f}", (int(x1), max(16, int(y1) - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
        return view

    @staticmethod
    def _iou(a, b) -> float:
        ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / union if union > 0 else 0.0

    def _match(self, box, others):
        best = max(others, key=lambda o: self._iou(box[2], o[2]), default=None)
        return best if best is not None and self._iou(box[2], best[2]) >= 0.5 else None

    @staticmethod
    def _region_changed(before_frame, after_frame, xyxy) -> bool:
        """상자 영역의 픽셀이 before/after 사이에 실제로 바뀌었는지."""
        h, w = before_frame.shape[:2]
        x1, y1, x2, y2 = (max(0, int(v)) for v in xyxy)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return True
        a = cv2.cvtColor(before_frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
        b = cv2.cvtColor(after_frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
        # 사람이 움직이면 카메라 자동 노출로 화면 전체 밝기가 바뀜 → 영역 평균 밝기를 빼고 비교
        diff = np.abs((a - a.mean()) - (b - b.mean()))
        ratio = float(np.count_nonzero(diff > config.PIXEL_DIFF_THRESHOLD)) / diff.size
        return ratio >= config.YOLO_REGION_CHANGE_RATIO

    def _reconcile(self, before, after, before_frame, after_frame) -> None:
        """카메라가 고정이라는 점을 이용한 보정.
        1) 같은 자리(IoU≥0.5)의 물건은 같은 품목: 이름이 다르면 점수가 높은 쪽 이름으로 통일
           (예: before '사과' 0.30 ↔ after '오렌지' 0.36 → 둘 다 오렌지)
        2) 한쪽에서만 잡힌 물건인데 그 자리 화면이 거의 안 바뀌었으면 → 그대로 있는 것 (놓친 인식)
           반대쪽에도 같은 물건이 있는 것으로 채워 넣어 '추가/제거'로 잘못 세지 않게 함."""
        for a in after:
            b = self._match(a, before)
            if b is None or b[0] == a[0]:
                continue
            if b[1] >= a[1]:
                a[0] = b[0]
            else:
                b[0] = a[0]
        missing_in_after = [b for b in before if self._match(b, after) is None
                            and not self._region_changed(before_frame, after_frame, b[2])]
        missing_in_before = [a for a in after if self._match(a, before) is None
                             and not self._region_changed(before_frame, after_frame, a[2])]
        after.extend(list(x) for x in missing_in_after)
        before.extend(list(x) for x in missing_in_before)
        # 3) 그래도 한쪽에만 있는 물건(=추가/제거로 셀 물건)은 점수가 충분히 높을 때만 인정, 낮으면 잡음으로 버림
        for side, other in ((before, after), (after, before)):
            side[:] = [x for x in side
                       if x[1] >= config.YOLO_CHANGE_MIN_CONF or self._match(x, other) is not None]

    def _count(self, boxes):
        """(품목별 개수, 불확실 품목, Unknown 개수). Unknown 은 재고 개수에 넣지 않고 따로 셈."""
        counts = {k: 0 for k in ITEM_IDS}
        low, unknown = set(), 0
        for b in boxes:
            c, s = b[0], b[1]
            if c == UNKNOWN:
                unknown += 1
                continue
            counts[ITEM_IDS[c]] += 1
            if s < config.YOLO_UNCERTAIN_BELOW:
                low.add(ITEM_IDS[c])
        return counts, low, unknown

    def compare(self, before_frame, after_frame, capture_id: str) -> dict:
        b_boxes, _, _ = self._detect(before_frame)
        a_boxes, hand, _ = self._detect(after_frame)
        self._reconcile(b_boxes, a_boxes, before_frame, after_frame)
        b_counts, b_low, b_unk = self._count(b_boxes)
        a_counts, a_low, a_unk = self._count(a_boxes)
        out = config.CAPTURE_DIR / capture_id
        if out.is_dir():  # 어떤 상자를 무엇으로 봤는지 확인용 그림 (보정 후 최종 상자)
            cv2.imwrite(str(out / "before_yolo.jpg"), self.draw(before_frame, b_boxes))
            cv2.imwrite(str(out / "after_yolo.jpg"), self.draw(after_frame, a_boxes))
        name = lambda k: config.ITEMS[k]["name"]
        seen_after = ", ".join(f"{name(k)} {v}" for k, v in a_counts.items() if v) or "인식된 품목 없음"
        if a_unk:
            seen_after += f", Unknown {a_unk}"
        answer = VisionAnswer(
            before=Counts(**b_counts), after=Counts(**a_counts),
            hand_or_open_door_visible=hand,
            uncertain_items=sorted(b_low | a_low),
            note=f"YOLO 인식 (after): {seen_after}",
        )
        result = build_result(capture_id, answer, source=f"yolo:{Path(str(config.YOLO_MODEL)).name}")
        # 모르는 물체: 재고에는 반영하지 않고, 개수가 바뀌었으면 사람 확인 요청
        result["unknown"] = {"before": b_unk, "after": a_unk}
        if a_unk != b_unk:
            result["needs_confirmation"] = True
            result["confirmation_reasons"].append(
                f"목록에 없는 물체(Unknown) {'추가' if a_unk > b_unk else '제거'} {abs(a_unk - b_unk)}개")
        # 냉장고 안 위치(LOC "X,Y"): 추가된 물건은 after 에서 새로 생긴 상자, 꺼낸 물건은 before 에서 사라진 상자
        shape = after_frame.shape
        for entries, mine, other in ((result["added"], a_boxes, b_boxes), (result["removed"], b_boxes, a_boxes)):
            for e in entries:
                idx = ITEM_IDS.index(e["item_id"])
                cands = sorted((x for x in mine if x[0] == idx and self._match(x, other) is None),
                               key=lambda x: -x[1])
                e["locations"] = [location_of(x[2], shape) for x in cands[:e["quantity"]]]
        result["before_locations"] = {}          # 첫 동기화 때 테이블 채우기용
        for x in b_boxes:
            if x[0] != UNKNOWN:
                result["before_locations"].setdefault(ITEM_IDS[x[0]], []).append(location_of(x[2], shape))
        # 사용자에게 이름을 물어볼 Unknown 상자: after 의 것 + before 에만 있던 것(꺼낸 물체)
        regions = [{"side": "after", "box": [round(v, 1) for v in a[2]], "conf": round(a[1], 3)}
                   for a in a_boxes if a[0] == UNKNOWN]
        regions += [{"side": "before", "box": [round(v, 1) for v in b[2]], "conf": round(b[1], 3)}
                    for b in b_boxes if b[0] == UNKNOWN and self._match(b, a_boxes) is None]
        result["unknown_regions"] = regions
        return result


class MockComparer:
    """API 키 없이 흐름을 시험하는 가짜 비교기. config.DEMO_SCENARIO 순서대로 답합니다."""

    def __init__(self) -> None:
        self.stock = copy.deepcopy(config.INITIAL_STOCK)
        self.step = 0

    def compare(self, before_frame, after_frame, capture_id: str) -> dict:
        before = dict(self.stock)
        scenario = config.DEMO_SCENARIO[self.step % len(config.DEMO_SCENARIO)]
        self.step += 1
        for k in scenario["add"]:
            self.stock[k] += 1
        for k in scenario["remove"]:
            self.stock[k] = max(0, self.stock[k] - 1)
        answer = VisionAnswer(
            before=Counts(**before), after=Counts(**self.stock),
            hand_or_open_door_visible=False, uncertain_items=[],
            note="MOCK 결과입니다 (실제 인식 아님).",
        )
        return build_result(capture_id, answer, source="mock")

    def on_no_change(self) -> None:
        """API를 생략한 '변화 없음' 회차도 시나리오 한 단계로 셈 (더미 영상과 순서 맞춤)."""
        self.step += 1


def summarize(result: dict) -> str:
    parts = [f"+ {x['name']} {x['quantity']}" for x in result["added"]]
    parts += [f"- {x['name']} {x['quantity']}" for x in result["removed"]]
    unk = result.get("unknown")
    if unk and unk["after"] != unk["before"]:
        d = unk["after"] - unk["before"]
        parts.append(f"{'+' if d > 0 else '-'} Unknown {abs(d)}")
    head = " / ".join(parts) if parts else "변화 없음"
    tail = "  [확인 필요: " + ", ".join(result["confirmation_reasons"]) + "]" if result["needs_confirmation"] else ""
    return head + tail


def save_json(path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
