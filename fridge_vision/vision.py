"""이전·현재 사진 비교.

원칙: 모델은 '사진별 품목 개수'만 답하고, 추가·제거 계산과 확인 필요 여부는 코드가 정합니다.
"""
import base64
import contextlib
import copy
import hashlib
import json
import os
import time
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
        if config.DETECTOR_BACKEND == "yoloe_openvocab":
            names |= {p.replace(" ", "") for p in config.OPENVOCAB_PROMPTS.get(k, [])}
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


def _name_key(name: str) -> str:
    """포장 이름 비교용: 소문자, 낱말 순서 무시 ('카스테라 한입' = '한입 카스테라')."""
    return "".join(sorted(name.lower().split()))


def register_package_item(name: str) -> str:
    """OCR 로 만든 포장 식품 이름 → item_id. 처음 보는 이름이면 실행 중에 품목으로 추가.
    - 같은 이름(낱말 순서 무관)이면 같은 품목. 이미 있는 포장 이름과 거의 같으면(한 글자 잘못 읽음, 한쪽이 다른 쪽을 포함)
      그 품목으로 합침 → OCR 이 사진마다 조금씩 다르게 읽어도 한 품목으로 셈
    - id 는 이름에서 만든 고정값(pkg_<해시 8자리>)이라 다시 실행해도 같은 이름은 같은 id → DB 기록이 이어짐"""
    import difflib

    t = _name_key(name)
    for k, v in config.ITEMS.items():
        if _name_key(v["name"]) == t:
            return k
    for k, v in config.ITEMS.items():          # 실행 중 생긴 포장 이름과 비슷하면 합침
        if not v.get("package"):
            continue
        o = _name_key(v["name"])
        if (min(len(o), len(t)) >= 3 and (o in t or t in o)) or \
                difflib.SequenceMatcher(None, o, t).ratio() >= config.OCR_NAME_MERGE_RATIO:
            return k
    k = "pkg_" + hashlib.sha1(t.encode("utf-8")).hexdigest()[:8]
    config.ITEMS[k] = {"name": name.strip(), "description": f"packaged food read by OCR: {name.strip()}",
                       "user": True, "package": True}       # user: 검색어가 없는 품목 (YOLOE 에 넣지 않음)
    ITEM_IDS.append(k)
    _build_models()
    return k


def prompt_cache_path(model_path) -> Path:
    """체크포인트별 검색어 임베딩 파일 (NPZ 는 만든 체크포인트에서만 쓸 수 있음)."""
    return Path(config.OPENVOCAB_PROMPT_CACHE_DIR) / f"prompts_{Path(str(model_path)).stem}.npz"


def openvocab_prompts(item_ids):
    """새 방식의 (검색어 목록, 검색어별 (종류, 값)). 종류: item(품목 번호) / package / generic / hand."""
    names, kinds = [], []

    def add(p, kind):
        if p in names:
            raise ValueError(f"검색어 '{p}' 가 두 번 들어 있습니다 (config.OPENVOCAB_PROMPTS / PACKAGE_PROMPTS)")
        names.append(p)
        kinds.append(kind)

    missing = [k for k in config.OPENVOCAB_PROMPTS if k not in config.ITEMS]
    if missing:
        print(f"[경고] OPENVOCAB_PROMPTS 의 라벨 {missing} 이 config.ITEMS 에 없어 건너뜁니다 (품목 추가 필요)")
    for k in item_ids:
        if k in ("unknown_package", "packaged_food"):
            continue                      # 포장 식품 결과용 라벨 (검색어 없음)
        for p in config.OPENVOCAB_PROMPTS.get(k, [k.replace("_", " ")]):
            add(p, ("item", ITEM_IDS.index(k)))
    for p in config.PACKAGE_PROMPTS:
        add(p, ("package", p))
    for p in config.YOLO_GENERIC_PROMPTS:
        add(p, ("generic", None))
    for p in config.YOLO_HAND_PROMPTS:
        add(p, ("hand", None))
    return names, kinds


@contextlib.contextmanager
def _in_base_dir():
    """YOLOE 글자 모델(mobileclip_blt.ts)을 이 폴더에서 찾도록 (다른 폴더에서 실행해도 다시 받지 않게)."""
    cwd = os.getcwd()
    os.chdir(config.BASE_DIR)
    try:
        yield
    finally:
        os.chdir(cwd)


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

        self.backend = config.DETECTOR_BACKEND
        if self.backend not in ("yoloe_openvocab", "kaggle"):
            raise ValueError(f"DETECTOR_BACKEND 는 'yoloe_openvocab' 또는 'kaggle' 이어야 합니다: {self.backend!r}")
        # 사용자가 추가한 품목(글자 검색어 없음)은 YOLOE 에 넣지 않고 생김새 기억으로만 찾음
        prompt_ids = [k for k in ITEM_IDS if not config.ITEMS[k].get("user")]
        assert prompt_ids == ITEM_IDS[:len(prompt_ids)], "사용자 품목은 목록 끝에 있어야 함"
        self.generic_prompts = list(config.YOLO_GENERIC_PROMPTS)
        self.hand_prompts = list(config.YOLO_HAND_PROMPTS)
        self.memory = _load_user_labels()
        self._clip = None
        self._ocr_engine = None
        self._ocr_cache = []
        if self.backend == "kaggle":
            self._init_legacy(ultralytics, prompt_ids)
        else:
            self._init_openvocab(ultralytics, prompt_ids)
        self.model.predict(np.zeros((320, 320, 3), np.uint8), verbose=False)  # 첫 실행 지연 미리 해소

    def _init_legacy(self, ultralytics, prompt_ids) -> None:
        """예전 방식 그대로 (DETECTOR_BACKEND='kaggle')."""
        weights = config.YOLO_MODEL if config.YOLO_MODEL.exists() else config.YOLO_MODEL.name
        self.model_path = config.YOLO_MODEL
        self.item_prompts = [config.YOLO_PROMPTS.get(k, k.replace("_", " ")) for k in prompt_ids]
        # 클래스 번호 순서: [품목 0..N-1] [범용 N..] [손]
        names = self.item_prompts + self.generic_prompts + self.hand_prompts
        self.names = names
        self.n_items = len(self.item_prompts)
        self.n_known = self.n_items + len(self.generic_prompts)
        if config.YOLO_MODEL.name.startswith("yoloe"):
            self.model = ultralytics.YOLOE(str(weights))
            self.model.set_classes(names, self.model.get_text_pe(names))
        else:
            self.model = ultralytics.YOLOWorld(str(weights))
            self.model.set_classes(names)
        self.prompt_source = "text_encoder"

    def _init_openvocab(self, ultralytics, prompt_ids) -> None:
        """원본 YOLOE + 영어 검색어. 검색어 임베딩은 NPZ 가 있으면 그것을, 없으면 글자 모델로 계산."""
        path = Path(config.OPENVOCAB_MODEL)
        self.model_path = path
        self.names, self.class_kinds = openvocab_prompts(prompt_ids)
        self.model = ultralytics.YOLOE(str(path if path.exists() else path.name))
        cache = prompt_cache_path(path)
        self.prompt_source = None
        if cache.exists():
            try:
                with np.load(cache, allow_pickle=False) as d:
                    cached = d["names"].tolist()
                if cached == self.names:
                    self.model.load_prompt_embeddings(cache)
                    self.prompt_source = f"npz:{cache.name}"
                else:
                    print(f"[경고] 검색어 목록이 바뀌어 {cache.name} 을 쓸 수 없습니다. "
                          f"'python tools/build_prompt_cache.py' 로 다시 만드세요 (지금은 글자 모델로 계산).")
            except Exception as e:  # 다른 체크포인트용·손상된 파일
                print(f"[경고] {cache.name} 을 불러오지 못했습니다 ({e}). 글자 모델로 계산합니다.")
        if self.prompt_source is None:
            with _in_base_dir():
                self.model.set_classes(self.names, self.model.get_text_pe(self.names))
            self.prompt_source = "text_encoder"
        if config.PACKAGE_PROMPTS:
            self._ocr()                         # OCR 모델도 시작할 때 한 번 불러 둠 (첫 포장에서 멈추지 않게)
        self._verifier = self._load_verifier() if config.CLIP_VERIFY else None
        self._clip_cache = []

    # ------------------------------------------------------------------
    # CLIP 재확인 (새 방식)
    # ------------------------------------------------------------------
    PACKAGE = "__package__"                     # CLIP 라벨 중 '포장 식품'

    def _p(self, key, default):
        """지금 설정 묶음(detect_frame 의 profile)의 값. 없으면 default (= config 값)."""
        return getattr(self, "_profile", {}).get(key, default)

    def _load_verifier(self):
        """CLIP 과 라벨별 글자 특징(검색어 여러 개의 평균). 글자 모델은 CLIP 파일 안에 있어 인터넷 불필요."""
        try:
            import clip
            import torch

            model, pre = clip.load(config.CLIP_VERIFY_MODEL, device="cpu",
                                   download_root=str(config.BASE_DIR / "weights" / "clip"))
            model.eval()
            labels = list(dict.fromkeys(ITEM_IDS[r] for kd, r in self.class_kinds if kd == "item"))
            texts = {k: config.OPENVOCAB_PROMPTS.get(k, [k.replace("_", " ")]) for k in labels}
            if config.PACKAGE_PROMPTS:
                labels.append(self.PACKAGE)
                texts[self.PACKAGE] = list(config.PACKAGE_PROMPTS)
            with torch.no_grad():
                feats = []
                for k in labels:
                    e = model.encode_text(clip.tokenize([f"a photo of {p}." for p in texts[k]])).float()
                    e = (e / e.norm(dim=-1, keepdim=True)).mean(0)
                    feats.append(e / e.norm())
            encode, runtime = self._clip_image_encoder(model)
            print(f"CLIP 재확인: {config.CLIP_VERIFY_MODEL} ({runtime})")
            return {"model": model, "pre": pre, "labels": labels, "text": torch.stack(feats),
                    "encode": encode, "runtime": runtime}
        except Exception as e:
            print(f"[경고] CLIP 재확인을 불러오지 못했습니다 ({e}). YOLOE 결과만 씁니다.")
            return None

    @staticmethod
    def _clip_image_encoder(model):
        """CLIP 사진 인코더 함수 (사진 묶음 텐서 → 특징). OpenVINO 로 빠르게, 안 되면 PyTorch.
        OpenVINO 결과가 PyTorch 와 같은지(코사인 0.99 이상) 시작할 때 확인하고, 다르면 쓰지 않음."""
        import torch

        def torch_enc(x):
            with torch.no_grad():
                return model.encode_image(x).float()

        rt = config.CLIP_VERIFY_RUNTIME
        if rt == "torch":
            return torch_enc, "torch"
        try:
            import openvino as ov

            folder = config.BASE_DIR / "weights" / "clip" / "openvino"
            xml = folder / f"{config.CLIP_VERIFY_MODEL.replace('/', '-')}_visual.xml"
            core = ov.Core()
            core.set_property({"CACHE_DIR": str(folder / "cache")})     # 그래픽용 컴파일 결과 저장 → 다음 시작이 빠름
            if not xml.exists():
                folder.mkdir(parents=True, exist_ok=True)
                with torch.no_grad():
                    om = ov.convert_model(model.visual.float(), example_input=torch.zeros(2, 3, 224, 224),
                                          input=[ov.PartialShape([-1, 3, 224, 224])])
                ov.save_model(om, str(xml))
            want = {"openvino": ["GPU", "CPU"], "openvino_gpu": ["GPU"], "openvino_cpu": ["CPU"]}.get(rt, ["CPU"])
            g = torch.Generator().manual_seed(0)
            x = torch.rand(2, 3, 224, 224, generator=g) * 4 - 2                 # 정규화된 사진 범위의 시험 입력
            ref = torch_enc(x)
            for dev in want:
                if dev not in core.available_devices:
                    continue
                try:
                    cm = core.compile_model(str(xml), dev, {"PERFORMANCE_HINT": "LATENCY"})
                    buckets = (1, 2, 4, 8, 16)

                    def ov_enc(t, cm=cm):
                        """상자 수를 1·2·4·8·16 중 하나로 맞춰(빈 칸은 0) 계산 → 그래픽 장치가 상자 수가 바뀔 때마다
                        다시 컴파일하지 않음 (실측: 맞추지 않으면 한 장에 최대 3.5초)."""
                        outs = []
                        for i in range(0, len(t), buckets[-1]):
                            part = t[i:i + buckets[-1]].numpy()
                            n = next(b for b in buckets if b >= len(part))
                            if n > len(part):
                                part = np.concatenate([part, np.zeros((n - len(part), *part.shape[1:]), part.dtype)])
                            outs.append(cm(part)[0][:min(len(t) - i, buckets[-1])])
                        return torch.from_numpy(np.concatenate(outs)).float()

                    out = ov_enc(x)
                    cos = torch.nn.functional.cosine_similarity(out, ref).min().item()
                    if cos < 0.99:
                        print(f"[경고] OpenVINO {dev} 결과가 PyTorch 와 다름 (코사인 {cos:.4f}) → 쓰지 않음")
                        continue
                    for b in buckets:                  # 크기별로 미리 한 번씩 (첫 인식에서 멈추지 않게)
                        cm(np.zeros((b, 3, 224, 224), np.float32))
                    return ov_enc, f"openvino:{dev}"
                except Exception as e:
                    print(f"[경고] OpenVINO {dev} 실패: {e}")
        except Exception as e:
            print(f"[경고] OpenVINO 를 쓸 수 없어 PyTorch 로 CLIP 을 계산합니다 ({e})")
        return torch_enc, "torch"

    def _clip_verify(self, frame, xyxys):
        """상자들 → [(CLIP 1등 라벨, 확률), ...]. 같은 자리·같은 생김새면 최근 결과 재사용. 못 보면 (None, 0)."""
        import ocr_names
        import torch
        from PIL import Image

        out = [(None, 0.0)] * len(xyxys)
        v = self._verifier
        if v is None:
            return out
        h, w = frame.shape[:2]
        now = time.monotonic()
        self._clip_cache = [e for e in self._clip_cache if now - e["t"] < config.OCR_CACHE_SECONDS]
        todo, imgs = [], []
        for i, (x1, y1, x2, y2) in enumerate(xyxys):
            if min(x2 - x1, y2 - y1) < config.CLIP_VERIFY_MIN_PX:
                continue
            px, py = 0.1 * (x2 - x1), 0.1 * (y2 - y1)
            crop = frame[max(0, int(y1 - py)):min(h, int(y2 + py)), max(0, int(x1 - px)):min(w, int(x2 + px)), ::-1]
            if crop.size == 0:
                continue
            crop = np.ascontiguousarray(crop)
            th = ocr_names.thumb(crop)
            hit = None if self._p("fresh", False) else next(
                (e for e in self._clip_cache if self._iou(xyxys[i], e["box"]) >= 0.7
                 and ocr_names.same_look(th, e["thumb"], min_corr=0.9)), None)
            if hit:
                out[i] = hit["res"]
                continue
            todo.append((i, th))
            imgs.append(v["pre"](Image.fromarray(crop)))
        if imgs:
            with torch.no_grad():
                e = v["encode"](torch.stack(imgs))
                p = (100 * (e / e.norm(dim=-1, keepdim=True)) @ v["text"].T).softmax(-1)
            top_p, top_i = p.max(-1)
            for (i, th), pi, ii in zip(todo, top_p.tolist(), top_i.tolist()):
                out[i] = (v["labels"][ii], float(pi))
                self._clip_cache.append({"box": list(xyxys[i]), "thumb": th, "res": out[i], "t": now})
        return out

    # ------------------------------------------------------------------
    # 포장 식품 OCR
    # ------------------------------------------------------------------
    def _ocr(self):
        if self._ocr_engine is None:
            import ocr_names
            try:
                if config.OCR_ENGINE != "easyocr":
                    raise ValueError(f"지원하지 않는 OCR_ENGINE: {config.OCR_ENGINE}")
                self._ocr_engine = ocr_names.EasyOcrEngine(config.OCR_LANGS, config.OCR_MODEL_DIR)
            except Exception as e:
                print(f"[경고] OCR 을 불러오지 못했습니다 ({e}). 포장 식품은 이름 없이 'unknown_package' 로 나옵니다.")
                self._ocr_engine = False
        return self._ocr_engine

    def _read_package(self, frame, xyxy, allow_ocr=True):
        """상자 영역 OCR → (글자 조각, 상태). 같은 자리·같은 생김새면 최근 결과를 다시 씀 (OCR_CACHE_SECONDS).
        상태: "read" 새로 읽음 / "cached" 재사용 / "small" 너무 작음 / "skipped" 한 장 최대 개수 초과 / "no_ocr" OCR 없음"""
        import ocr_names

        engine = self._ocr()
        if not engine:
            return [], "no_ocr"
        if min(xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]) < config.OCR_MIN_BOX_PX:
            return [], "small"
        crop = ocr_names.crop_for_ocr(frame, xyxy, config.OCR_SCALE)
        if crop.size == 0:
            return [], "small"
        th, now = ocr_names.thumb(crop), time.monotonic()
        self._ocr_cache = [e for e in self._ocr_cache if now - e["t"] < config.OCR_CACHE_SECONDS]
        for e in ([] if self._p("fresh", False) else self._ocr_cache):
            if self._iou(xyxy, e["box"]) >= 0.5 and ocr_names.same_look(th, e["thumb"]):
                return e["frags"], "cached"
        if not allow_ocr:
            return [], "skipped"
        frags = engine.read(crop)
        self._ocr_cache.append({"box": list(xyxy), "thumb": th, "frags": frags, "t": now})
        return frags, "read"

    def _package_box(self, frame, s, xyxy, prompt, allow_ocr=True, clip_hint=(None, 0.0)):
        """포장 식품 상자 → [품목 번호, 점수, 상자, 'ocr <이름>'] 와 로그용 정보.
        순서: 포장 글자에 기존 품목 단어 → 그 품목 / OCR 이름 → 새 품목(또는 packaged_food)
              / 글자를 못 읽으면 CLIP 이 확신한 품목 → 그 품목 / 그래도 없으면 unknown_package"""
        import ocr_names

        frags, cached = self._read_package(frame, xyxy, allow_ocr)
        rule = dict(min_conf=config.OCR_MIN_CONF, exclude_words=config.OCR_EXCLUDE_WORDS, min_chars=config.OCR_MIN_CHARS)
        keywords = {k: v for k, v in config.PACKAGE_KEYWORDS.items() if k in config.ITEMS}
        known = ocr_names.match_known_item(frags, keywords, **rule)
        name = ocr_names.make_name(frags, max_parts=config.OCR_NAME_PARTS, max_len=config.OCR_NAME_MAX_LEN, **rule)
        top, p = clip_hint
        if known:                                   # 포장 글자에 기존 품목 이름 ('milk', '우유' …) → 그 품목
            label = known
        elif name:
            label = "packaged_food" if config.PACKAGE_LABEL_MODE == "generic" else register_package_item(name)
        elif top not in (None, self.PACKAGE) and p >= config.CLIP_VERIFY_MIN_PROB:
            label = top                             # 글자 없음 → 생김새로 확신한 품목 (예: 계란판)
        else:
            label = "packaged_food" if config.PACKAGE_LABEL_MODE == "generic" else "unknown_package"
        info = {"prompt": prompt, "conf": round(s, 3), "box": [round(v, 1) for v in xyxy], "label": label,
                "ocr_name": name, "ocr_status": cached, "clip": [top, round(p, 3)],
                "ocr": [{"text": f.text, "conf": round(f.conf, 3), "h": round(f.height, 1)} for f in frags]}
        conf = max(s, p) if label == top else s
        return [ITEM_IDS.index(label), conf, list(xyxy), f"ocr {name or ''}".strip()], info

    def _log(self, entry) -> None:
        path = config.DETECTION_LOG
        if not path:
            return
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > config.DETECTION_LOG_MAX_MB * 1e6:
                path.replace(path.with_suffix(path.suffix + ".1"))
            line = json.dumps(entry, ensure_ascii=False,      # numpy 값(정수·실수·배열)도 기록되게
                              default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:          # 기록 실패가 인식을 멈추게 하지 않음
            print(f"[경고] 인식 로그를 쓰지 못했습니다: {e}")

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

    def _group_fragments(self, boxes, frame_area, skip_same_label=False, keep_min=None):
        """낮은 점수의 작은 조각 상자들이 서로 붙어 있으면 한 묶음(Unknown)으로 합침.
        skip_same_label: 모두 같은 라벨인 묶음은 합치지 않음 (새 방식: 계란 여러 개 등)
        keep_min: 묶이지 않은 상자를 남기는 최소 점수 (기본 YOLO_CONF)"""
        keep_min = config.YOLO_CONF if keep_min is None else keep_min
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
        if skip_same_label:
            groups = {r: g for r, g in groups.items() if len({b[0] for b in g}) > 1}
        grouped = [x for g in groups.values() if len(g) > 1 for x in g]
        # 묶이지 않은 상자는 그대로, 단 YOLO_CONF 미만의 약한 조각은 혼자서는 버림
        out = [b for b in boxes if not any(b is x for x in grouped) and b[1] >= keep_min]
        for g in groups.values():
            if len(g) > 1:
                xs = [b[2] for b in g]
                out.append([UNKNOWN, max(b[1] for b in g),
                            [min(x[0] for x in xs), min(x[1] for x in xs), max(x[2] for x in xs), max(x[3] for x in xs)],
                            f"group {len(g)}"])
        return out

    def _detect(self, frame):
        """[(item_index, conf, (x1,y1,x2,y2), *태그), ...], 손 보임 여부, 표시용 그림.
        item_index 는 ITEM_IDS 의 번호, UNKNOWN(-1) 이면 목록에 없는 물체. (두 방식 모두 같은 형식)"""
        if self.backend == "kaggle":
            return self._detect_legacy(frame)
        return self._detect_openvocab(frame)

    def detect_frame(self, frame, profile: str = "default"):
        """_detect 와 같은 결과를, 설정 묶음(config.DETECT_PROFILES: default / live / full)으로 실행.
        예전 방식(kaggle)은 설정 묶음과 관계없이 그대로."""
        if profile not in config.DETECT_PROFILES:
            raise ValueError(f"알 수 없는 설정 묶음: {profile}")
        prev, self._profile = getattr(self, "_profile", {}), config.DETECT_PROFILES[profile]
        try:
            return self._detect(frame)
        finally:
            self._profile = prev

    @staticmethod
    def _drop_contained(boxes):
        """겹친 상자 정리.
        1) 같은 라벨의 더 큰 상자 안에 들어 있는 상자는 버림 (계란판 안의 계란 낱개 → 계란판 1개)
        2) 품목으로 확정된 상자와 크게 겹치는 Unknown(조각 묶음)은 버림 (CLIP 이 '포도'로 본 자리의 포도알 묶음)"""
        ratio = config.OPENVOCAB_DROP_CONTAINED
        if not ratio:
            return boxes
        area = lambda x: max(0.0, x[2] - x[0]) * max(0.0, x[3] - x[1])

        def inter(a, b):
            return (max(0.0, min(a[2], b[2]) - max(a[0], b[0]))) * (max(0.0, min(a[3], b[3]) - max(a[1], b[1])))

        out = []
        for b in boxes:
            dup = False
            if area(b[2]) > 0:
                for o in boxes:
                    if o is b:
                        continue
                    if b[0] == UNKNOWN:
                        dup = o[0] != UNKNOWN and (inter(b[2], o[2]) / area(b[2]) >= ratio or YoloComparer._iou(b[2], o[2]) >= 0.5)
                    else:
                        dup = o[0] == b[0] and area(o[2]) > area(b[2]) and inter(b[2], o[2]) / area(b[2]) >= ratio
                    if dup:
                        break
            if not dup:
                out.append(b)
        return out

    def _detect_openvocab(self, frame):
        """새 방식.
        1) 원본 YOLOE 가 영어 검색어로 후보 상자를 찾음 (agnostic_nms: 한 물건엔 가장 높은 점수 검색어 하나만)
        2) CLIP 재확인: 후보 상자를 잘라 CLIP 이 확신(CLIP_VERIFY_MIN_PROB 이상)하면 그 라벨로 확정
        3) 확신이 없으면 YOLOE 라벨을 쓰되 기준값(OPENVOCAB_CONF) 이상일 때만 셈. 약한 상자는 조각 묶음(Unknown)에만 씀
        4) 포장 식품(YOLOE 포장 검색어 또는 CLIP '포장')은 OCR 로 이름"""
        thr = lambda k: config.OPENVOCAB_CONF_PER_LABEL.get(k, config.OPENVOCAB_CONF)
        item_min = min([config.OPENVOCAB_CONF, *config.OPENVOCAB_CONF_PER_LABEL.values()])
        cand_min = config.CLIP_VERIFY_CAND_CONF if self._verifier else item_min
        low = min(item_min, config.PACKAGE_CONF, config.YOLO_FRAGMENT_CONF, cand_min)
        imgsz = self._p("imgsz", None)
        r = self.model.predict(frame, conf=low, agnostic_nms=True, verbose=False, **({"imgsz": imgsz} if imgsz else {}))[0]
        boxes, packages, hand, raw, cands = [], [], False, [], []
        for c, s, xyxy in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
            kind, ref = self.class_kinds[int(c)]
            s = float(s)
            raw.append({"prompt": self.names[int(c)], "conf": round(s, 3), "box": [round(v, 1) for v in xyxy]})
            if kind == "hand":
                hand = hand or s >= config.OPENVOCAB_CONF
            elif kind == "generic":
                if s >= config.YOLO_UNKNOWN_CONF:       # 범용 단어로 밀려난 물체 = Unknown (CLIP 이 확신하면 그 품목)
                    cands.append((kind, ref, s, xyxy, self.names[int(c)]))
            elif s >= cand_min:
                cands.append((kind, ref, s, xyxy, self.names[int(c)]))
            elif kind == "item":
                boxes.append([ref, s, xyxy])           # 약한 상자도 조각 묶음 찾기에는 씀
        # CLIP 재확인 (점수 높은 후보부터 최대 CLIP_VERIFY_MAX_BOXES 개)
        cands.sort(key=lambda x: -x[2])
        n_clip = self._p("clip_max_boxes", config.CLIP_VERIFY_MAX_BOXES)
        hints = self._clip_verify(frame, [x[3] for x in cands[:n_clip]]) if self._verifier else []
        hints += [(None, 0.0)] * (len(cands) - len(hints))
        sure = config.CLIP_VERIFY_MIN_PROB
        for (kind, ref, s, xyxy, prompt), (top, p) in zip(cands, hints):
            clip_pkg = top == self.PACKAGE and p >= config.CLIP_VERIFY_PACKAGE_PROB
            clip_item = top not in (None, self.PACKAGE) and p >= sure
            if kind == "package":
                if s >= config.PACKAGE_CONF or clip_pkg or clip_item:
                    packages.append((s, xyxy, prompt, (top, p)))
            elif clip_pkg:
                packages.append((s, xyxy, f"{prompt} → clip:package", (top, p)))
            elif clip_item:
                idx = ITEM_IDS.index(top)
                same = kind == "item" and ref == idx
                boxes.append([idx, max(s, p) if same else p, xyxy, f"clip {p:.2f}" + ("" if same else f" (yoloe {prompt})")])
            elif kind == "generic":
                boxes.append([UNKNOWN, s, xyxy])
            else:
                boxes.append([ref, s, xyxy])
        boxes = self._group_fragments(boxes, frame.shape[0] * frame.shape[1],
                                      skip_same_label=config.OPENVOCAB_GROUP_SKIP_SAME_LABEL, keep_min=item_min)
        boxes = [b for b in boxes if b[0] == UNKNOWN or b[1] >= thr(ITEM_IDS[b[0]])]
        for b in boxes:
            if b[0] == UNKNOWN:
                self._recall(frame, b)
        pkg_info = []
        packages.sort(key=lambda p: -(p[1][2] - p[1][0]) * (p[1][3] - p[1][1]))      # 큰 포장부터 OCR
        n_ocr = self._p("ocr_max_per_frame", config.OCR_MAX_PER_FRAME) if self._p("ocr", True) else 0
        for i, (s, xyxy, prompt, hint) in enumerate(packages):
            box, info = self._package_box(frame, s, xyxy, prompt, allow_ocr=i < n_ocr, clip_hint=hint)
            boxes.append(box)
            pkg_info.append(info)
        boxes = self._drop_contained(boxes)
        self._log({"t": datetime.now().isoformat(timespec="milliseconds"), "model": self.model_path.name,
                   "size": [frame.shape[1], frame.shape[0]], "hand": hand, "raw": raw, "packages": pkg_info,
                   "final": [{"label": ITEM_IDS[b[0]] if b[0] != UNKNOWN else "Unknown", "conf": round(b[1], 3),
                              "box": [round(v, 1) for v in b[2]], "tags": [str(t) for t in b[3:]]} for b in boxes]})
        return boxes, hand, self.draw(frame, boxes)

    def _detect_legacy(self, frame):
        """예전 방식 (DETECTOR_BACKEND='kaggle'). 고치지 않음.
        agnostic_nms: 한 물건이 '오렌지'와 '레몬'으로 동시에 잡히지 않도록 가장 높은 점수 하나만 남김."""
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
        result = build_result(capture_id, answer, source=f"yolo:{Path(str(self.model_path)).name}")
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
