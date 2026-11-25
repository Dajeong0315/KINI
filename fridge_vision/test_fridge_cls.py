"""저장된 분류 모델(models/clip_probe.pt)을 Kaggle test 사진 전체로 처음부터 다시 시험.

학습 때 쓴 특징 캐시를 쓰지 않고, 실제 사용과 같은 경로(사진 → 백본 → 분류층 → 앙상블)로 계산합니다.
사용 예
  python test_fridge_cls.py
"""
import sys
from collections import Counter
from pathlib import Path

import torch
from PIL import Image

from train_clip_probe import DATA_DIR, Folder, load_backbone, make_head, prep

BASE_DIR = Path(__file__).resolve().parent
MODEL = BASE_DIR / "models" / "clip_probe.pt"


@torch.no_grad()
def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ck = torch.load(MODEL)
    classes = ck["classes"]
    print("모델:", MODEL.name, " 가중치:", {k: round(v, 3) for k, v in ck["weights"].items()})
    total = None
    for n, w in ck["weights"].items():
        if w <= 0:
            continue
        enc, pre = load_backbone(n)
        head = make_head(ck["dims"][n], len(classes))
        head.load_state_dict(ck["heads"][n])
        head.eval()
        ds = Folder("test", classes, pre)
        dl = torch.utils.data.DataLoader(ds, batch_size=64, num_workers=6)
        P, Y = [], []
        for x, y in dl:
            P.append(head(prep(enc(x).float())).softmax(1))
            Y.append(y)
        P, Y = torch.cat(P), torch.cat(Y)
        print(f"  {n:<9} 단독 정확도 {(P.argmax(1) == Y).float().mean():.4f}", flush=True)
        total = w * P if total is None else total + w * P

    pred = total.argmax(1)
    ok = pred == Y
    print(f"\nTEST 정확도(top1) = {ok.float().mean():.4f}   ({int(ok.sum())}/{len(Y)})")
    print(f"top5 = {(total.topk(5, 1).indices == Y[:, None]).any(1).float().mean():.4f}")
    print("\n품목별 정확도")
    for i, c in enumerate(classes):
        m = Y == i
        print(f"  {c:<11} {ok[m].float().mean():.3f}  ({int(ok[m].sum())}/{int(m.sum())})")
    print("\n자주 틀린 조합 (정답 → 예측)")
    for (a, b), k in Counter((classes[a], classes[b]) for a, b in zip(Y[~ok].tolist(), pred[~ok].tolist())).most_common(8):
        print(f"  {a} → {b}: {k}")


if __name__ == "__main__":
    main()
