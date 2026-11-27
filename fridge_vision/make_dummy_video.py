"""더미 냉장고 내부 영상 생성기.

실제 촬영본이 없어도 감지 → 촬영 → 비교 흐름을 시험할 수 있도록,
냉장고 내부를 단순한 그림으로 그린 영상을 만듭니다.

  - 문이 열리면 바깥 빛이 들어와 화면이 밝아지고 약간 노래짐
  - 손이 들어와 식재료를 빼거나 넣고 나감
  - 문이 닫히면 원래 밝기로 돌아옴

시나리오는 config.DEMO_SCENARIO 를 따릅니다 (--mock 모드 결과와 같은 순서).
결과물: dummy/fridge_demo.mp4, dummy/stills/*.jpg (문 닫힌 상태 사진, --compare 시험용)
"""
import copy

import cv2
import numpy as np

import config

W, H, FPS = 1280, 720, 30
SHELF_Y = [340, 660]   # 선반 윗면 y좌표 (위 칸, 아래 칸)

# 품목별 위치 (선반 번호, 중심 x)
SLOTS = {
    "milk": (0, 200), "tofu": (0, 520), "egg": (0, 900),
    "broccoli": (1, 220), "mushroom": (1, 560), "chicken": (1, 920),
}


def draw_item(img, item, cx, base_y):
    if item == "milk":
        cv2.rectangle(img, (cx - 45, base_y - 190), (cx + 45, base_y), (250, 250, 250), -1)
        cv2.rectangle(img, (cx - 45, base_y - 190), (cx + 45, base_y - 150), (200, 120, 40), -1)
        cv2.rectangle(img, (cx - 45, base_y - 190), (cx + 45, base_y), (120, 120, 120), 2)
        cv2.putText(img, "MILK", (cx - 34, base_y - 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 120, 40), 2)
    elif item == "tofu":
        cv2.rectangle(img, (cx - 90, base_y - 70), (cx + 90, base_y), (235, 235, 225), -1)
        cv2.rectangle(img, (cx - 90, base_y - 70), (cx + 90, base_y), (150, 150, 150), 2)
        cv2.putText(img, "TOFU", (cx - 34, base_y - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 60), 2)
    elif item == "egg":
        cv2.rectangle(img, (cx - 110, base_y - 60), (cx + 110, base_y), (170, 200, 220), -1)
        for i in range(5):
            cv2.ellipse(img, (cx - 88 + i * 44, base_y - 60), (18, 24), 0, 180, 360, (215, 235, 245), -1)
        cv2.putText(img, "EGG", (cx - 25, base_y - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 80, 100), 2)
    elif item == "broccoli":
        cv2.rectangle(img, (cx - 15, base_y - 80), (cx + 15, base_y), (90, 160, 110), -1)
        for dx, dy in [(-45, -110), (0, -140), (45, -110), (-25, -80), (25, -80)]:
            cv2.circle(img, (cx + dx, base_y + dy), 42, (40, 120, 40), -1)
    elif item == "mushroom":
        for dx in (-60, 0, 60):
            cv2.rectangle(img, (cx + dx - 10, base_y - 50), (cx + dx + 10, base_y), (200, 215, 225), -1)
            cv2.ellipse(img, (cx + dx, base_y - 50), (35, 30), 0, 180, 360, (60, 100, 150), -1)
    elif item == "chicken":
        cv2.rectangle(img, (cx - 110, base_y - 80), (cx + 110, base_y), (210, 210, 210), -1)
        cv2.ellipse(img, (cx, base_y - 40), (90, 32), 0, 0, 360, (150, 170, 230), -1)
        cv2.putText(img, "CHICKEN", (cx - 55, base_y - 32), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (60, 60, 120), 2)


def render(stock, door_open=0.0, hand=None, carrying=None):
    img = np.full((H, W, 3), 225, np.uint8)
    cv2.rectangle(img, (0, 0), (W, 40), (245, 245, 245), -1)          # 상단 LED
    for y in SHELF_Y:
        cv2.rectangle(img, (0, y), (W, y + 24), (200, 200, 200), -1)  # 선반
    for item, qty in stock.items():
        if qty > 0:
            shelf, cx = SLOTS[item]
            draw_item(img, item, cx, SHELF_Y[shelf])
    if hand is not None:
        hx, hy = int(hand[0]), int(hand[1])
        cv2.line(img, (-100, H + 100), (hx, hy), (120, 160, 210), 70)  # 팔
        cv2.circle(img, (hx, hy), 55, (130, 175, 225), -1)             # 손
        if carrying:
            shelf, _ = SLOTS[carrying]
            draw_item(img, carrying, hx, hy + 40)
    if door_open > 0:  # 문이 열리면 바깥 빛: 밝아지고 약간 노래짐
        warm = np.array([0, 25, 45], np.float32) * door_open
        img = np.clip(img.astype(np.float32) + 35 * door_open + warm, 0, 255).astype(np.uint8)
    noise = np.random.normal(0, 2.0, img.shape).astype(np.float32)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def main():
    config.DUMMY_DIR.mkdir(exist_ok=True)
    (config.DUMMY_DIR / "stills").mkdir(exist_ok=True)
    out_path = config.DUMMY_DIR / "fridge_demo.mp4"
    vw = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    stock = copy.deepcopy(config.INITIAL_STOCK)

    def hold(seconds, **kw):
        for _ in range(int(seconds * FPS)):
            vw.write(render(stock, **kw))

    def fade(a, b, seconds=0.4):
        n = int(seconds * FPS)
        for i in range(n):
            vw.write(render(stock, door_open=a + (b - a) * (i + 1) / n))

    def reach(item, take: bool):
        shelf, cx = SLOTS[item]
        target = np.array([cx, SHELF_Y[shelf] - 90], np.float32)
        start = np.array([-150, H + 150], np.float32)
        n = int(0.8 * FPS)
        carrying = None if take else item
        for i in range(n):
            vw.write(render(stock, 1.0, start + (target - start) * (i + 1) / n, carrying))
        if take:
            stock[item] = max(0, stock[item] - 1)
            carrying = item
        else:
            stock[item] += 1
            carrying = None
        hold(0.3, door_open=1.0, hand=target)
        for i in range(n):
            vw.write(render(stock, 1.0, target + (start - target) * (i + 1) / n, carrying))

    hold(3.0)
    cv2.imwrite(str(config.DUMMY_DIR / "stills" / "step0_closed.jpg"), render(stock))
    for i, step in enumerate(config.DEMO_SCENARIO, start=1):
        fade(0.0, 1.0)                     # 문 열림
        hold(0.5, door_open=1.0)
        for item in step["remove"]:
            reach(item, take=True)
        for item in step["add"]:
            reach(item, take=False)
        if not step["add"] and not step["remove"]:
            hold(1.5, door_open=1.0)       # 열어 보기만 함
        hold(1.0, door_open=1.0)
        fade(1.0, 0.0)                     # 문 닫힘
        hold(4.5)
        cv2.imwrite(str(config.DUMMY_DIR / "stills" / f"step{i}_closed.jpg"), render(stock))
    vw.release()
    print(f"생성 완료: {out_path}")
    print(f"문 닫힌 상태 사진: {config.DUMMY_DIR / 'stills'}")


if __name__ == "__main__":
    main()
