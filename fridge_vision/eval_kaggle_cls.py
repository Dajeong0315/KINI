"""Kaggle 분류 모델(models/kaggle_fridge_cls.pt) 을 실사 사진에 시험.

YOLOE 가 찾은 상자를 잘라서 → 분류 모델로 다시 판정 → YOLOE 판정과 나란히 비교.
분류 점수가 UNKNOWN_BELOW 미만이면 '모름(unknown)' 으로 표시.

사용 예
  python eval_kaggle_cls.py samples/real_before.jpg captures/xxx/after.jpg ...
  python eval_kaggle_cls.py --model models/clip_probe.pt samples/real_before.jpg ...
결과 그림: runs/kaggle_eval/<모델>_<파일이름>.jpg
"""
import sys
from pathlib import Path

import cv2

from vision import ITEM_IDS, YoloComparer

BASE_DIR = Path(__file__).resolve().parent
CLS_MODEL = BASE_DIR / "models" / "kaggle_fridge_cls.pt"
UNKNOWN_BELOW = 0.5   # 분류 1등 점수가 이보다 낮으면 '모름'
PAD = 0.1             # 상자를 사방으로 10% 넓혀서 자름 (분류 모델이 물체 전체를 보도록)


def make_classifier(path: Path):
    """crop(BGR) -> (이름, 점수) 함수를 돌려줌. clip_probe.pt 면 CLIP+선형층, 아니면 YOLO-cls."""
    if path.name.startswith("clip_probe"):
        import torch
        from PIL import Image
        from train_clip_probe import load_backbone, make_head, prep
        ck = torch.load(path)
        parts = []
        for n, w in ck["weights"].items():
            if w <= 0:
                continue
            enc, pre = load_backbone(n)
            head = make_head(ck["dims"][n], len(ck["classes"]))
            head.load_state_dict(ck["heads"][n])
            parts.append((w, enc, pre, head.eval()))

        @torch.no_grad()
        def run(crop):
            img = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            p = sum(w * head(prep(enc(pre(img).unsqueeze(0)).float())).softmax(1)[0]
                    for w, enc, pre, head in parts)
            i = int(p.argmax())
            return ck["classes"][i], float(p[i])
        return run

    from ultralytics import YOLO
    cls = YOLO(str(path))

    def run(crop):
        r = cls.predict(crop, verbose=False)[0]
        return r.names[int(r.probs.top1)], float(r.probs.top1conf)
    return run


def main(paths) -> None:
    if not CLS_MODEL.exists():
        sys.exit(f"분류 모델이 없습니다: {CLS_MODEL}  (python train_kaggle_cls.py 로 먼저 학습)")
    det = YoloComparer()
    classify = make_classifier(CLS_MODEL)
    out_dir = BASE_DIR / "runs" / "kaggle_eval"
    out_dir.mkdir(parents=True, exist_ok=True)

    for p in paths:
        frame = cv2.imread(p)
        if frame is None:
            print(f"읽기 실패: {p}")
            continue
        h, w = frame.shape[:2]
        boxes, _, _ = det._detect(frame)
        view = frame.copy()
        print(f"\n== {p}")
        for c, s, (x1, y1, x2, y2), *_ in boxes:
            bw, bh = x2 - x1, y2 - y1
            X1, Y1 = max(0, int(x1 - PAD * bw)), max(0, int(y1 - PAD * bh))
            X2, Y2 = min(w, int(x2 + PAD * bw)), min(h, int(y2 + PAD * bh))
            top_name, conf = classify(frame[Y1:Y2, X1:X2])
            name = top_name if conf >= UNKNOWN_BELOW else f"?{top_name}"
            yoloe = ITEM_IDS[c] if c >= 0 else "Unknown"
            print(f"  YOLOE {yoloe:<11} {s:.2f}  →  분류 {name:<11} {conf:.2f}")
            color = (0, 200, 0) if conf >= UNKNOWN_BELOW else (0, 0, 255)
            cv2.rectangle(view, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            cv2.putText(view, f"{yoloe}>{name} {conf:.2f}", (int(x1), max(14, int(y1) - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)
        dst = out_dir / (CLS_MODEL.stem + "_" + Path(p).parent.name + "_" + Path(p).name)
        cv2.imwrite(str(dst), view)
        print(f"  그림: {dst}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--model":   # 다른 분류 모델 파일로 시험
        CLS_MODEL = Path(args[1])
        args = args[2:]
    if not args:
        sys.exit(__doc__)
    main(args)
