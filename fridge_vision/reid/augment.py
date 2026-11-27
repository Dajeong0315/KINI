"""잘라내기 전처리와 증강 (모델 없이 OpenCV·numpy 만 사용 → 테스트에서 그대로 실행 가능).

한계: 여기의 변형은 모두 2D 입니다. 회전·원근 변형으로 '조금 돌린 모습'을 흉내낼 뿐,
용기를 실제로 돌렸을 때 새로 보이는 옆면·뒷면은 만들 수 없습니다.
그래서 같은 물체로 확인된 실제 사진을 갤러리에 계속 더합니다 (gallery.Gallery.add_real).
"""
import random

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# 잘라내기
# ---------------------------------------------------------------------------
def crop_box(frame, xyxy, pad=0.10):
    """상자 + 여백(변 길이 × pad) 을 잘라냄. 돌려주는 값: (잘라낸 사진, 잘라낸 사진 안의 원래 상자) 또는 None."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    if x2 - x1 < 4 or y2 - y1 < 4:                  # 너무 작은 상자는 생김새를 비교할 수 없음
        return None
    px, py = pad * (x2 - x1), pad * (y2 - y1)
    a, b = max(0, int(x1 - px)), max(0, int(y1 - py))
    c, d = min(w, int(round(x2 + px))), min(h, int(round(y2 + py)))
    if c - a < 4 or d - b < 4:
        return None
    inner = (max(0, int(x1) - a), max(0, int(y1) - b), min(c - a, int(round(x2)) - a), min(d - b, int(round(y2)) - b))
    return frame[b:d, a:c].copy(), inner


def grabcut_mask(crop, inner, iters=3):
    """GrabCut 으로 상자 안 물체(전경) 마스크를 대략 구함. 실패하면 상자 안 전체를 전경으로 봄."""
    h, w = crop.shape[:2]
    x1, y1, x2, y2 = inner
    rect = (x1, y1, max(1, x2 - x1), max(1, y2 - y1))
    fallback = np.zeros((h, w), bool)
    fallback[y1:y2, x1:x2] = True
    if w < 16 or h < 16 or rect[2] >= w and rect[3] >= h:
        return fallback
    mask = np.zeros((h, w), np.uint8)
    try:
        cv2.grabCut(crop, mask, rect, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64),
                    iters, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return fallback
    fg = (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)
    # 물체를 거의 못 남겼으면(배경과 색이 같은 용기 등) 지우지 않음
    return fg if fg.sum() >= 0.2 * rect[2] * rect[3] else fallback


def prepare_crop(frame, xyxy, pad=0.10, bg=None, mask=None, fill=128):
    """등록·비교에 쓰는 잘라낸 사진.
    mask: 프레임 크기의 분할 마스크(있으면 그것으로 배경을 칠함). bg="grabcut": GrabCut 으로 대신함."""
    got = crop_box(frame, xyxy, pad)
    if got is None:
        return None
    crop, inner = got
    fg = None
    if mask is not None:
        x1, y1, x2, y2 = (float(v) for v in xyxy)
        px, py = pad * (x2 - x1), pad * (y2 - y1)
        a, b = max(0, int(x1 - px)), max(0, int(y1 - py))
        fg = mask[b:b + crop.shape[0], a:a + crop.shape[1]].astype(bool)
    elif bg == "grabcut":
        fg = grabcut_mask(crop, inner)
    if fg is not None:
        crop[~fg] = fill
    return crop


def to_input(img, size=224, fill=128):
    """정사각형으로 패딩한 뒤 모델 입력 크기로."""
    h, w = img.shape[:2]
    s = max(h, w)
    canvas = np.full((s, s, 3), fill, np.uint8)
    y, x = (s - h) // 2, (s - w) // 2
    canvas[y:y + h, x:x + w] = img
    return cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA if s > size else cv2.INTER_LINEAR)


# ---------------------------------------------------------------------------
# 변형 하나씩
# ---------------------------------------------------------------------------
def rotate(img, deg, fill=128):
    """평면 회전. 잘리지 않게 캔버스를 넓힘."""
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, m, (nw, nh), borderValue=(fill, fill, fill))


def perspective(img, side, ratio, fill=128):
    """가로 한쪽(side='left'|'right')의 세로 길이를 ratio 만큼 줄이고 폭도 조금 줄임 → 좌우로 돌린 효과."""
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dy, dx = ratio * h / 2, ratio * w / 2
    if side == "left":
        dst = np.float32([[dx, dy], [w, 0], [w, h], [dx, h - dy]])
    else:
        dst = np.float32([[0, 0], [w - dx, dy], [w - dx, h - dy], [0, h]])
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, m, (w, h), borderValue=(fill, fill, fill))


def flip(img, _=None, fill=128):
    return cv2.flip(img, 1)


def scale(img, s, fill=128):
    """s<1: 작게 해서 가운데 둠(멀리), s>1: 키워서 가운데를 자름(가까이). 크기는 그대로."""
    h, w = img.shape[:2]
    r = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))))
    rh, rw = r.shape[:2]
    if s < 1:
        out = np.full_like(img, fill)
        y, x = (h - rh) // 2, (w - rw) // 2
        out[y:y + rh, x:x + rw] = r
        return out
    y, x = (rh - h) // 2, (rw - w) // 2
    return r[y:y + h, x:x + w].copy()


def brightness(img, b, fill=128):
    return cv2.convertScaleAbs(img, alpha=1.0, beta=b * 255)


def contrast(img, c, fill=128):
    m = float(img.mean())
    return np.clip((img.astype(np.float32) - m) * (1 + c) + m, 0, 255).astype(np.uint8)


def color_temp(img, t, fill=128):
    """t>0: 따뜻하게(빨강↑ 파랑↓), t<0: 차갑게. (BGR 순서)"""
    f = img.astype(np.float32)
    f[..., 2] *= 1 + t
    f[..., 0] *= 1 - t
    return np.clip(f, 0, 255).astype(np.uint8)


def blur(img, sigma, fill=128):
    return cv2.GaussianBlur(img, (0, 0), sigma)


OPS = {"rotate": rotate, "flip": flip, "scale": scale, "brightness": brightness,
       "contrast": contrast, "color_temp": color_temp, "blur": blur}


def single_ops(cfg):
    """설정(config.REID_AUG) → 하나씩 적용할 변형 목록 [(종류, 값), ...]."""
    ops = []
    for v in cfg.get("rotate", []):
        ops.append(("rotate", v))
    for r in cfg.get("perspective", []):
        ops += [("perspective", ("left", r)), ("perspective", ("right", r))]
    if cfg.get("flip"):
        ops.append(("flip", None))
    for k in ("scale", "brightness", "contrast", "color_temp", "blur"):
        ops += [(k, v) for v in cfg.get(k, [])]
    return ops


def apply_op(img, op, fill=128):
    kind, v = op
    if kind == "perspective":
        return perspective(img, v[0], v[1], fill)
    return OPS[kind](img, v, fill)


def describe(ops):
    return "+".join(f"{k}{'' if v is None else ':' + (f'{v[0]}{v[1]:g}' if isinstance(v, tuple) else f'{v:g}')}"
                    for k, v in ops)


def augment(img, cfg, count=24, seed=0, fill=128):
    """사진 1장 → 증강 count 장 [(사진, 설명), ...].
    하나씩 따로 적용한 것을 먼저 넣고(넘치면 고르게 추림), 나머지는 서로 다른 종류 2~3개를 무작위 조합.
    seed 가 같으면 항상 같은 결과."""
    rng = random.Random(seed)
    singles = single_ops(cfg)
    if not singles or count <= 0:
        return []
    n_single = min(len(singles), count)
    if n_single < len(singles):                      # 고르게 추림 (종류가 한쪽으로 쏠리지 않게)
        step = len(singles) / n_single
        chosen = [singles[int(i * step)] for i in range(n_single)]
    else:
        chosen = singles
    plans = [[op] for op in chosen]
    kinds = sorted({k for k, _ in singles})
    tries = 0
    while len(plans) < count and tries < count * 20:
        tries += 1
        n = rng.choice((2, 3)) if len(kinds) >= 3 else min(2, len(kinds))
        picked = rng.sample(kinds, n)
        plan = [rng.choice([op for op in singles if op[0] == k]) for k in picked]
        # 기하 변형(회전·원근·크기·반전)을 먼저, 색 변형을 나중에
        plan.sort(key=lambda op: 0 if op[0] in ("flip", "scale", "perspective", "rotate") else 1)
        if plan not in plans:
            plans.append(plan)
    out = []
    for plan in plans[:count]:
        x = img
        for op in plan:
            x = apply_op(x, op, fill)
        out.append((x, describe(plan)))
    return out
