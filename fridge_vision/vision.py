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
    import ocr_names

    def food_of(text):                          # 이름 안의 식품 사전 단어 ('유동 번데기' → 번데기)
        hit = ocr_names.find_word([ocr_names.Fragment(text, 1.0, (0, 0, 1, 1))], config.FOOD_WORDS, config.OCR_FUZZY_MIN)
        return hit[0] if hit else None

    mine = food_of(name)
    for k, v in config.ITEMS.items():          # 실행 중 생긴 포장 이름과 비슷하면 합침
        if not v.get("package"):
            continue
        other = food_of(v["name"])
        if mine and other and mine != other:    # 다른 식품 ('참치' ≠ '고추참치') 은 합치지 않음
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
        if self._reid_on():                     # 재인식 특징 모델·갤러리도 시작할 때 (첫 화면에서 멈추지 않게)
            emb, gal, store, _ = self._reid()
            print(f"재인식: {config.REID_MODE} ({emb.name}, {emb.runtime}) — 등록 물체 {len(gal.entries)}개, "
                  f"냉장고 안 물체 목록 {len(store.tracks)}개")

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
    BACKGROUND = "__background__"               # CLIP 라벨 중 '식재료 아님' (가격표·선반·벽·사람)

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
            if config.TEXT_FIRST and config.CLIP_BACKGROUND_PROMPTS:
                labels.append(self.BACKGROUND)
                texts[self.BACKGROUND] = list(config.CLIP_BACKGROUND_PROMPTS)
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
        """Unknown 상자 하나를 등록 물체와 비교 (REID_MODE 에 따라 갤러리 또는 예전 방식)."""
        self._recall_boxes(frame, [box])

    def _recall_legacy(self, frame, box) -> None:
        """Unknown 상자를 사용자가 붙인 이름표와 비교해서 같은 물체면 그 품목으로 바꿈 (예전 방식).
        gallery 모드에서는 갤러리가 없는 옛 등록(갤러리 도입 전 이름표)만 비교 — 갤러리가 있는 것은 이미 비교했음."""
        labels = self.memory.get("labels", [])
        if self._reid_on():
            labels = [lab for lab in labels if not lab.get("gallery_id")]
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
        if self._reid_on():
            try:
                self.memory["labels"][-1]["gallery_id"] = self._reid_register(k, frame, xyxy)
                config.USER_LABELS_FILE.write_text(json.dumps(self.memory, ensure_ascii=False), encoding="utf-8")
            except Exception as e:          # 갤러리 등록 실패가 이름 붙이기를 막지 않음 (예전 방식 기억은 저장됨)
                print(f"[경고] 갤러리 등록 실패: {e}")
        return k

    # ------------------------------------------------------------------
    # 재인식 (config '4-5', reid/ 폴더). REID_MODE="legacy" 이면 아래는 쓰지 않음
    # ------------------------------------------------------------------
    @staticmethod
    def _reid_on() -> bool:
        return config.REID_MODE == "gallery"

    def _reid(self):
        """(특징 모델, 갤러리, 트랙 저장소, 트래커). 처음 쓸 때 한 번 불러옴."""
        if getattr(self, "_reid_parts", None) is None:
            from reid.embedder import make_embedder
            from reid.gallery import Gallery
            from reid.tracks import Tracker, TrackStore
            emb = make_embedder()
            gal = Gallery(config.REID_GALLERY_DIR, emb, emb.name)
            store = TrackStore(config.REID_STATE_FILE)
            self._reid_parts = (emb, gal, store, Tracker(store, config, gal))
        return self._reid_parts

    @staticmethod
    def _reid_crop(frame, xyxy):
        from reid.augment import prepare_crop
        return prepare_crop(frame, xyxy, config.REID_CROP_PAD, config.REID_BG_MASK, fill=config.REID_BG_FILL)

    def _reid_vecs(self, frame, xyxys):
        """상자들의 특징값 (한 번에 계산). 잘라낼 수 없는 상자는 None."""
        from reid.augment import to_input
        emb = self._reid()[0]
        crops = [self._reid_crop(frame, x) for x in xyxys]
        ok = [i for i, c in enumerate(crops) if c is not None]
        vecs = emb([to_input(crops[i], config.REID_INPUT_SIZE, config.REID_BG_FILL) for i in ok])
        out = [None] * len(xyxys)
        for i, v in zip(ok, vecs):
            out[i] = v
        return out

    @staticmethod
    def _reid_log(entry) -> None:
        try:
            config.REID_LOG.parent.mkdir(parents=True, exist_ok=True)
            entry = {"t": datetime.now().isoformat(timespec="milliseconds"), **entry}
            with config.REID_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False,
                                   default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)) + "\n")
        except Exception as e:
            print(f"[경고] 재인식 로그를 쓰지 못했습니다: {e}")

    @staticmethod
    def _box_source(b) -> str:
        """상자 이름의 출처: unknown / user(사용자 이름표) / ocr(포장 글씨) / detector."""
        if b[0] == UNKNOWN:
            return "unknown"
        tags = [str(t) for t in b[3:]]
        if any(t.startswith("user") for t in tags):
            return "user"
        if any(t.startswith("ocr") for t in tags):
            return "ocr"
        return "detector"

    @staticmethod
    def _mark_user(b, tag) -> None:
        """사용자 이름표 표시 (draw·phone_live 가 노란색으로 그리도록 태그 맨 앞에)."""
        if not any(str(t).startswith("user") for t in b[3:]):
            b.insert(3, tag)

    def _recall_boxes(self, frame, boxes) -> None:
        """탐지가 끝난 상자들을 등록 물체와 비교.
        legacy : Unknown 상자만 예전 방식(_recall_legacy)
        gallery: Unknown + 탐지 품목 상자(포장 글씨·사용자 이름 제외)를 갤러리와 비교 (한 번에 계산)
                 맞음 → 등록 이름 / 애매함(Unknown 일 때) → 입력 칸에 후보 이름(hint) / 갤러리에 없으면 예전 방식"""
        if not self._reid_on():
            for b in boxes:
                if b[0] == UNKNOWN:
                    self._recall_legacy(frame, b)
            return
        self._remember_boxes(frame, boxes)
        gal = self._reid()[1]
        kinds = ("unknown", "detector") if self._p("reid_detector_boxes", True) else ("unknown",)
        targets = [b for b in boxes if self._box_source(b) in kinds] if gal.entries else []
        if targets:
            vecs = self._reid_vecs(frame, [b[2] for b in targets])
            for b, v in zip(targets, vecs):
                if v is None:
                    continue
                dec = gal.decide(v, allowed=set(ITEM_IDS))
                before = ITEM_IDS[b[0]] if b[0] != UNKNOWN else "unknown"
                if dec["decision"] == "match":
                    b[0] = ITEM_IDS.index(dec["item_id"])
                    b[1] = max(float(b[1]), float(dec["score"]))
                    self._mark_user(b, f"user {dec['score']:.2f}")
                    b.append(f"reid {dec['gallery_id']}")
                elif dec["decision"] == "confirm" and b[0] == UNKNOWN and not any(str(t).startswith("hint ") for t in b[3:]):
                    b.append(f"hint {gal.name(dec['gallery_id'])}")
                self._reid_log({"kind": "recall", "box": [round(float(x), 1) for x in b[2]], "before": before,
                                "decision": dec, "label": ITEM_IDS[b[0]] if b[0] != UNKNOWN else "unknown"})
        for b in boxes:                         # 갤러리에 없는 예전 등록(user_labels.json)도 계속 알아보게
            if b[0] == UNKNOWN:
                self._recall_legacy(frame, b)

    @staticmethod
    def _frame_key(frame):
        return (id(frame), frame.shape, hash(frame[::61, ::53].tobytes()))

    def _remember_boxes(self, frame, boxes) -> None:
        """사진별 탐지 상자 (최근 32장) — 이름을 붙일 때 같은 사진의 다른 물체를 '이 물체가 아님'으로 쓰려고."""
        recent = self.__dict__.setdefault("_recent_boxes", {})
        recent[self._frame_key(frame)] = boxes
        while len(recent) > 32:
            del recent[next(iter(recent))]

    def _reid_register(self, item_id, frame, xyxy) -> str:
        """사용자가 이름을 붙인 물체 → 증강 갤러리 등록. 같은 자리 트랙이 있으면 그 이름으로 고침. 돌려주는 값: gallery_id."""
        from reid.tracks import iou
        _, gal, store, _ = self._reid()
        crop = self._reid_crop(frame, xyxy)
        if crop is None:
            raise ValueError("상자가 너무 작아 잘라낼 수 없습니다")
        gid, warning = gal.register(item_id, config.ITEMS[item_id]["name"], crop, source=[round(float(v), 1) for v in xyxy])
        self.last_reid_warning = warning
        if warning:
            print(f"[경고] {warning}")
        n_neg = 0
        if config.REID_NEGATIVES:               # 같은 사진의 다른 물체 = '이 물체가 아님'
            others = [list(b[2]) for b in self.__dict__.get("_recent_boxes", {}).get(self._frame_key(frame), [])
                      if iou(b[2], xyxy) < 0.3]
            if others:
                n_neg = gal.add_negatives([v for v in self._reid_vecs(frame, others) if v is not None], gid)
        t = max(store.tracks, key=lambda t: iou(t.bbox, xyxy), default=None)
        if t is not None and iou(t.bbox, xyxy) >= config.KEEP_IOU:
            t.label, t.label_source, t.gallery_id = item_id, "user", gid
            store.save()
        self._reid_log({"kind": "register", "gallery_id": gid, "item_id": item_id, "name": config.ITEMS[item_id]["name"],
                        "box": [round(float(v), 1) for v in xyxy], "vectors": len(gal.entries[gid]["meta"]["kinds"]),
                        "negatives_added": n_neg,
                        "warning": warning, "track_id": None if t is None or iou(t.bbox, xyxy) < config.KEEP_IOU else t.track_id})
        return gid

    def _reid_track(self, b_boxes, a_boxes, before_frame, after_frame, capture_id) -> None:
        """위치·변화 기반 유지 (compare 안, _reconcile 다음). 결과 형식은 그대로 두고 상자의 품목만 바꿈."""
        from reid.tracks import change_mask
        _, gal, store, tracker = self._reid()
        shape = after_frame.shape
        to_det = lambda b: {"label": ITEM_IDS[b[0]] if b[0] != UNKNOWN else "unknown", "source": self._box_source(b),
                            "bbox": [float(v) for v in b[2]], "score": float(b[1]), "zone": location_of(b[2], shape),
                            "gallery_id": next((str(t)[5:] for t in b[3:] if str(t).startswith("reid ")), None)}
        size = [int(shape[1]), int(shape[0])]
        if store.frame_size and list(store.frame_size) != size:      # 다른 카메라·해상도 → 처음부터
            store.reset()

        def embedder(frame, boxes):
            cache = {}

            def f(i):
                if i not in cache:
                    cache[i] = self._reid_vecs(frame, [boxes[i][2]])[0]
                return cache[i]
            return f

        seeded = False
        if not store.tracks:
            tracker.seed([to_det(b) for b in b_boxes], capture_id, size, embedder(before_frame, b_boxes))
            seeded = True
        # before 상자에 트랙 이름 (before ≈ 지난번 after)
        for b, (tid, label, source, gid) in zip(b_boxes, tracker.relabel([to_det(b) for b in b_boxes])):
            self._apply_track(b, tid, label, source)
        mask = change_mask(before_frame, after_frame, config.REID_CHANGE_DIFF, config.REID_CHANGE_KERNEL)
        out = tracker.update([to_det(a) for a in a_boxes], mask, capture_id, embedder(after_frame, a_boxes),
                             allowed=set(ITEM_IDS))
        for a, r in zip(a_boxes, out["dets"]):
            self._apply_track(a, r["track_id"], r["label"], r["source"])
            if r.get("hint") and not any(str(t).startswith("hint ") for t in a[3:]) and r["hint"] in config.ITEMS:
                a.append(f"hint {config.ITEMS[r['hint']]['name']}")
        # 탐지가 잠깐 놓친 물체(자리 변화 없음) → after 에 다시 넣음 / 변화 있는 자리에서 사라진 물체 → before 에 있게
        for t in out["kept"]:
            for side in (a_boxes, b_boxes):
                if not any(self._iou(x[2], t.bbox) >= 0.5 for x in side):
                    side.append(self._track_box(t, "kept"))
        for t in out["removed"]:
            if not any(self._iou(x[2], t["bbox"]) >= 0.5 for x in b_boxes):
                b_boxes.append(self._track_box(t, "removed"))
        store.save()
        self._reid_log({"kind": "track", "capture_id": capture_id, "seeded": seeded,
                        "changed_ratio": round(float(mask.mean()), 4), "dets": out["dets"],
                        "removed": [{k: v for k, v in r.items() if k != "emb"} for r in out["removed"]],
                        "moved": out["moved"], "kept": [t.track_id for t in out["kept"]],
                        "tracks": len(store.tracks)})

    def _apply_track(self, b, tid, label, source) -> None:
        if tid is None:
            return
        b[0] = ITEM_IDS.index(label) if label in ITEM_IDS else UNKNOWN
        if source == "user":
            self._mark_user(b, "user track")
        b.append(f"track {tid}")

    def _track_box(self, t, why):
        """트랙(Track 또는 dict) → 상자 [품목, 점수, xyxy, 태그...]."""
        g = (lambda k: t[k]) if isinstance(t, dict) else (lambda k: getattr(t, k))
        b = [ITEM_IDS.index(g("label")) if g("label") in ITEM_IDS else UNKNOWN, float(g("score")), list(g("bbox"))]
        if g("label_source") == "user":
            b.append("user track")
        b.append(f"track {g('track_id')} {why}")
        return b

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
        if config.TEXT_FIRST:
            return self._detect_textfirst(frame)
        return self._detect_openvocab(frame)

    # ------------------------------------------------------------------
    # 글씨 우선 + Unknown (config.TEXT_FIRST)
    # ------------------------------------------------------------------
    def _ocr_whole(self, frame):
        """사진 전체를 한 번 OCR → 사진 좌표의 글자 조각 목록. (긴 변 OCR_FULL_MAX_SIDE 로 줄여서)"""
        import ocr_names

        engine = self._ocr()
        if not engine:
            return []
        h, w = frame.shape[:2]
        s = min(1.0, config.OCR_FULL_MAX_SIDE / max(h, w))
        img = frame if s >= 1.0 else cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        frags = engine.read(np.ascontiguousarray(img[:, :, ::-1]))
        return [ocr_names.Fragment(f.text, f.conf, tuple(v / s for v in f.box)) for f in frags]

    @staticmethod
    def _inside(inner, outer) -> float:
        """inner 상자 넓이 중 outer 안에 들어 있는 비율."""
        ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
        iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
        a = max(1e-6, (inner[2] - inner[0]) * (inner[3] - inner[1]))
        return ix * iy / a

    @staticmethod
    def _split_by_fragments(box, hits):
        """같은 식품 이름이 한 상자 안에서 여러 번 보이면(쌓인 캔 등) 그 위치로 상자를 나눔."""
        cs = sorted(((f.box[0] + f.box[2]) / 2, (f.box[1] + f.box[3]) / 2) for f in hits)
        xs, ys = [c[0] for c in cs], [c[1] for c in cs]
        vertical = (max(ys) - min(ys)) >= (max(xs) - min(xs))
        key = 1 if vertical else 0
        pts = sorted(c[key] for c in cs)
        lo, hi = (box[1], box[3]) if vertical else (box[0], box[2])
        cuts = [lo] + [(a + b) / 2 for a, b in zip(pts, pts[1:])] + [hi]
        out = []
        for a, b in zip(cuts, cuts[1:]):
            out.append([box[0], a, box[2], b] if vertical else [a, box[1], b, box[3]])
        return out

    def _distinct_hits(self, hits):
        """같은 위치(겹치는 조각)는 하나로 → 서로 다른 위치에 쓰인 횟수."""
        keep = []
        for f in sorted(hits, key=lambda f: -f.conf):
            if all(self._iou(f.box, k.box) < 0.3 and self._inside(f.box, k.box) < 0.5 for k in keep):
                keep.append(f)
        return keep

    def _detect_textfirst(self, frame):
        """1) YOLOE 후보 상자 + CLIP  2) 사진 전체 OCR → 글자 조각을 가장 작게 감싸는 상자에 배정
        3) 상자별 라벨: 글씨(사전 보정) → 없으면 CLIP/YOLOE 신뢰도 ≥ LABEL_MIN_CONF → 아니면 Unknown(물체로 보이면)
        4) 가격표(가격 숫자)·배경(CLIP)·그룹 상자·부분 상자 정리"""
        import ocr_names

        H, W = frame.shape[:2]
        item_min = config.OPENVOCAB_CONF
        cand_min = config.CLIP_VERIFY_CAND_CONF if self._verifier else item_min
        low = min(cand_min, config.YOLO_FRAGMENT_CONF)
        imgsz = self._p("imgsz", None)
        r = self.model.predict(frame, conf=low, agnostic_nms=True, verbose=False, **({"imgsz": imgsz} if imgsz else {}))[0]
        cands, small, hand, raw = [], [], False, []
        for c, s, xyxy in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
            kind, ref = self.class_kinds[int(c)]
            s = float(s)
            raw.append({"prompt": self.names[int(c)], "conf": round(s, 3), "box": [round(v, 1) for v in xyxy]})
            if kind == "hand":
                hand = hand or s >= config.OPENVOCAB_CONF
            elif s >= cand_min:
                cands.append({"kind": kind, "ref": ref, "s": s, "box": list(xyxy), "prompt": self.names[int(c)]})
            elif kind == "item":
                small.append([ref, s, list(xyxy)])          # 약한 조각: 묶음(Unknown) 찾기에만
        cands.sort(key=lambda d: -d["s"])
        n_clip = self._p("clip_max_boxes", config.CLIP_VERIFY_MAX_BOXES)
        hints = self._clip_verify(frame, [d["box"] for d in cands[:n_clip]]) if self._verifier else []
        for d, h in zip(cands, hints + [(None, 0.0)] * (len(cands) - len(hints))):
            d["clip"] = h

        # ---- 글씨: 사진 전체 OCR 1회 (정밀·기본) / 실시간은 최근 결과 재사용만 ----
        rule = dict(min_conf=config.OCR_MIN_CONF, exclude_words=config.OCR_EXCLUDE_WORDS, min_chars=config.OCR_MIN_CHARS)
        ocr_on = self._p("ocr", True)
        thumbs = {}
        now = time.monotonic()
        self._ocr_cache = [e for e in self._ocr_cache if now - e["t"] < config.OCR_CACHE_SECONDS]
        for i, d in enumerate(cands):
            x1, y1, x2, y2 = (int(v) for v in d["box"])
            crop = frame[max(0, y1):min(H, y2), max(0, x1):min(W, x2), ::-1]
            thumbs[i] = ocr_names.thumb(np.ascontiguousarray(crop)) if crop.size else None
            d["frags"], d["ocr"] = [], "none"
            if not self._p("fresh", False) and thumbs[i] is not None:
                hit = next((e for e in self._ocr_cache if self._iou(d["box"], e["box"]) >= 0.5
                            and ocr_names.same_look(thumbs[i], e["thumb"])), None)
                if hit:
                    d["frags"], d["ocr"] = list(hit["frags"]), "cached"
        need = [d for d in cands if d["ocr"] == "none"
                and min(d["box"][2] - d["box"][0], d["box"][3] - d["box"][1]) >= config.OCR_MIN_BOX_PX]
        whole = []
        if ocr_on and need:
            whole = self._ocr_whole(frame)
            produce = lambda d: d["kind"] == "item" and ITEM_IDS[d["ref"]] in config.PRODUCE_LABELS
            for f in whole:                                   # 조각을 감싸는 후보 상자 중 포장류 우선, 그다음 가장 작은 상자
                owners = [d for d in cands if d["ocr"] != "cached" and self._inside(f.box, d["box"]) >= 0.7]
                if owners:
                    min(owners, key=lambda d: (produce(d), (d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1])))["frags"].append(f)
            for i, d in enumerate(cands):
                if d["ocr"] != "cached":
                    d["ocr"] = "read"
                    if thumbs[i] is not None:
                        self._ocr_cache.append({"box": list(d["box"]), "thumb": thumbs[i], "frags": list(d["frags"]), "t": now})

        # ---- 상자별 글씨 이름 ----
        for d in cands:
            x1, y1, x2, y2 = d["box"]
            at_edge = x1 <= 2 or y1 <= 2 or x2 >= W - 2 or y2 >= H - 2
            d["partial"] = at_edge and ((y2 - y1) < config.OCR_EDGE_PARTIAL * H or (x2 - x1) < config.OCR_EDGE_PARTIAL * W)
            d["price"] = any(ocr_names.is_price(f.text) for f in d["frags"] if f.conf >= 0.3)
            d["name"], d["tconf"], d["hits"], d["food"] = None, 0.0, [], None
            if d["frags"] and not d["partial"]:
                d["name"], d["tconf"], d["hits"] = ocr_names.compose_text_name(
                    d["frags"], config.FOOD_WORDS, config.BRAND_WORDS, min_sim=config.OCR_FUZZY_MIN,
                    max_len=config.OCR_NAME_MAX_LEN, fallback_min=config.OCR_FALLBACK_MIN_CONF, **rule)
                d["food"] = ocr_names.find_word(d["frags"], config.FOOD_WORDS, config.OCR_FUZZY_MIN)
        # 식품 단어 없이 큰 글씨로만 지은 이름('JUST 저스트', '신선')이 같은 사진의 식품 이름 상자에도 쓰여 있으면 그 이름으로
        named = [d for d in cands if d["food"] and d["name"] and not d["price"]]
        for d in cands:
            if d["name"] and not d["food"] and not d["price"] and d["tconf"] > 0:     # 브랜드만 있는 이름은 제외
                toks = [ocr_names._squash(t) for t in d["name"].split() if ocr_names._squash(t)]
                for o in named:
                    blob = "".join(ocr_names._squash(f.text) for f in o["frags"])
                    if toks and all(t in blob for t in toks):
                        d.update(name=o["name"], tconf=min(o["tconf"], max(d["tconf"], o["tconf"])), food=o["food"],
                                 hits=d["hits"], adopted=True)
                        break

        # ---- 상자별 라벨 ----
        boxes, info = [], []
        for d in cands:
            top, p = d["clip"]
            if d["price"]:
                info.append({**self._info(d), "decision": "가격표 (가격 숫자)"})
                continue
            name, tconf, hits, food_text = d["name"], d["tconf"], d["hits"], d["food"]
            if top == self.BACKGROUND and p >= config.CLIP_BACKGROUND_PROB and not food_text:
                info.append({**self._info(d), "decision": f"배경 (CLIP {p:.2f})"})
                continue
            ocr_log = [f"{f.text}({f.conf:.2f})" for f in d["frags"]]
            if name and tconf >= config.LABEL_MIN_CONF:
                idx = ITEM_IDS.index(register_package_item(name) if config.PACKAGE_LABEL_MODE != "generic" else "packaged_food")
                parts = [d["box"]]
                if config.OCR_SPLIT_REPEATS and food_text and not d.get("adopted"):
                    # 개수는 상자 안의 모든 글자로 셈 (안쪽 캔 상자에 배정된 글자 포함) → 겹치는 캔 상자는 아래에서 하나로
                    distinct = self._distinct_hits(ocr_names.repeat_fragments(
                        [f for f in whole if self._inside(f.box, d["box"]) >= 0.7] or d["frags"],
                        food_text[0], config.FOOD_WORDS, config.OCR_FUZZY_MIN))
                    if len(distinct) > 1:
                        parts = self._split_by_fragments(d["box"], distinct)
                for pb in parts:
                    boxes.append([idx, float(tconf), pb, f"ocr {name}"])
                info.append({**self._info(d), "decision": f"글씨 '{name}' ×{len(parts)} ({tconf:.2f})"
                             + (" (같은 사진의 같은 글씨)" if d.get("adopted") else ""), "ocr": ocr_log})
                continue
            clip_item = (top not in (None, self.PACKAGE, self.BACKGROUND) and p >= config.LABEL_MIN_CONF
                         and top not in config.CLIP_TEXT_REQUIRED_LABELS)
            yoloe_item = (d["kind"] == "item" and d["s"] >= config.LABEL_MIN_CONF and top in (None, ITEM_IDS[d["ref"]])
                          and ITEM_IDS[d["ref"]] not in config.CLIP_TEXT_REQUIRED_LABELS)
            hint = name or ""
            if clip_item:
                boxes.append([ITEM_IDS.index(top), p, d["box"], f"clip {p:.2f}"])
                info.append({**self._info(d), "decision": f"CLIP {top} ({p:.2f})"})
            elif yoloe_item:
                boxes.append([d["ref"], d["s"], d["box"]])
                info.append({**self._info(d), "decision": f"YOLOE {ITEM_IDS[d['ref']]} ({d['s']:.2f})"})
            elif top == self.BACKGROUND and p >= config.CLIP_BACKGROUND_UNKNOWN_PROB and not food_text:
                info.append({**self._info(d), "decision": f"배경으로 봄 (CLIP 1등 배경 {p:.2f})", "ocr": ocr_log})
            elif d["s"] >= config.OBJECT_MIN_CONF or name:
                boxes.append([UNKNOWN, d["s"], d["box"], "unknown"] + ([f"hint {hint}"] if hint else []))
                why = f"글씨 '{name}' {tconf:.2f} 부족" if name else (f"CLIP {top} {p:.2f}" if top else "")
                info.append({**self._info(d), "decision": f"Unknown ({why})", "ocr": ocr_log})
            elif d["kind"] == "item":
                small.append([d["ref"], d["s"], d["box"]])

        # ---- 정리: 묶음 상자·포장 안의 부분 상자·겹침 ----
        is_text = lambda b: len(b) > 3 and str(b[3]).startswith("ocr")
        texted = [b for b in boxes if is_text(b)]
        labeled = [b for b in boxes if b[0] != UNKNOWN]
        # 포장 영역 = 글씨 상자 + 글씨 상자를 감싸는 큰 후보 상자 (그 안의 그림·조각은 따로 세지 않음)
        regions = [t[2] for t in texted] + [d["box"] for d in cands
                                             if any(self._inside(t[2], d["box"]) >= 0.7 for t in texted)]
        out = []
        for b in boxes:
            if not is_text(b):
                if b[0] == UNKNOWN and any(self._inside(t[2], b[2]) >= 0.7 for t in texted):
                    continue                    # 글씨 상자들을 감싸는 묶음 상자
                if any(self._inside(b[2], r) >= 0.6 and r != b[2] for r in regions):
                    continue                    # 포장 안의 그림·부분
                if b[0] == UNKNOWN and any(self._inside(b[2], o[2]) >= 0.6 or self._iou(b[2], o[2]) >= 0.5
                                           for o in labeled if o is not b):
                    continue
            out.append(b)
        # 글씨 상자 안의 다른 글씨 상자 = 같은 물체 (예: 컵 상자 '컵과일' 안의 '조각사과')
        #   → 바깥 상자 하나로, 이름은 신뢰도 높은 쪽 (같으면 안쪽의 더 구체적인 이름)
        out.sort(key=lambda x: -(x[2][2] - x[2][0]) * (x[2][3] - x[2][1]))
        merged = []
        for b in out:
            parent = next((m for m in merged if is_text(b) and is_text(m) and m[0] != b[0]
                           and self._inside(b[2], m[2]) >= 0.8), None)
            if parent is None:
                merged.append(b)
            elif b[1] >= parent[1]:
                parent[0], parent[1], parent[3:] = b[0], b[1], b[3:]
        # 같은 이름 글씨 상자가 거의 같은 자리면 하나만
        final = []
        for b in sorted(merged, key=lambda x: -x[1]):
            if any(o[0] == b[0] and self._iou(o[2], b[2]) >= 0.6 for o in final):
                continue
            final.append(b)
        # 약한 조각 묶음 → Unknown (포도알 등), 이미 잡힌 자리는 제외
        for g in self._group_fragments(small, H * W, skip_same_label=config.OPENVOCAB_GROUP_SKIP_SAME_LABEL, keep_min=1.1):
            if g[0] == UNKNOWN and not any(self._inside(g[2], o[2]) >= 0.5 for o in final):
                final.append(g)
        final = self._drop_contained(final)
        self._recall_boxes(frame, final)
        self._log({"t": datetime.now().isoformat(timespec="milliseconds"), "mode": "text_first",
                   "model": self.model_path.name, "size": [W, H], "hand": hand, "raw": raw, "decisions": info,
                   "ocr_fragments": len(whole),
                   "final": [{"label": ITEM_IDS[b[0]] if b[0] != UNKNOWN else "Unknown", "conf": round(b[1], 3),
                              "box": [round(v, 1) for v in b[2]], "tags": [str(t) for t in b[3:]]} for b in final]})
        return final, hand, self.draw(frame, final)

    @staticmethod
    def _info(d):
        return {"prompt": d["prompt"], "yoloe": round(d["s"], 3), "box": [round(v, 1) for v in d["box"]],
                "clip": [d["clip"][0], round(d["clip"][1], 3)]}

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
        self._recall_boxes(frame, boxes)
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
        # 위치·변화 기반 유지 (안 바뀐 자리 = 이전 이름). 고정 카메라용 — 손에 든 핸드폰 비교(phone_live /compare)는
        # 매번 다른 장면이라 track_enabled=False 로 끔 (이전 장면의 트랙이 '그대로 있는 물체'로 끼어들지 않게)
        if self._reid_on() and getattr(self, "track_enabled", True):
            self._reid_track(b_boxes, a_boxes, before_frame, after_frame, capture_id)
        self.last_compare_boxes = {"before": b_boxes, "after": a_boxes}   # 화면에 상자를 그리는 쪽(phone_live)용
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
