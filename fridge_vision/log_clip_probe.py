"""최종 앙상블 분류층 학습을 같은 설정(seed 0)으로 다시 돌려 epoch 별 학습 로그를 CSV 로 저장.

저장된 모델(models/clip_probe.pt)은 건드리지 않습니다. 특징 캐시(runs/feats_*.pt)가 있어야 합니다.
결과: docs/clip_probe_training_log.csv
  - 단계 '가중치결정': train 으로 학습, epoch 마다 val/test 정확도 기록
  - 단계 '최종(train+val)': 저장 모델과 같은 학습, epoch 마다 test 정확도 기록
"""
import csv
import sys
from pathlib import Path

import torch
import torch.nn.functional as F

from train_clip_probe import ALL_BACKBONES, DATA_DIR, features, prep, train_head

BASE_DIR = Path(__file__).resolve().parent
OUT = BASE_DIR / "docs" / "clip_probe_training_log.csv"


@torch.no_grad()
def evaluate(head, X, Y):
    out = head(X)
    return F.cross_entropy(out, Y).item(), (out.argmax(1) == Y).float().mean().item()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    classes = sorted(p.name for p in (DATA_DIR / "train").iterdir() if p.is_dir())
    rows = []
    for name in ALL_BACKBONES:
        Xtr, Ytr = features(name, "train", classes)
        Xva, Yva = features(name, "val", classes)
        Xte, Yte = features(name, "test", classes)
        Xtr, Xva, Xte = prep(Xtr), prep(Xva), prep(Xte)

        def logger(stage, use_val):
            def log(ep, loss, lr, head):
                vl, va = evaluate(head, Xva, Yva) if use_val else ("", "")
                tl, ta = evaluate(head, Xte, Yte)
                rows.append([name, stage, ep, f"{lr:.6g}", f"{loss:.4f}",
                             f"{vl:.4f}" if vl != "" else "", f"{va:.4f}" if va != "" else "",
                             f"{tl:.4f}", f"{ta:.4f}"])
                if ep % 25 == 0:
                    print(name, stage, ep, f"loss={loss:.4f}", f"test={ta:.4f}", flush=True)
            return log

        train_head(Xtr, Ytr, len(classes), log=logger("가중치결정(train)", True))
        train_head(torch.cat([Xtr, Xva]), torch.cat([Ytr, Yva]), len(classes),
                   log=logger("최종(train+val)", False))

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["백본", "단계", "epoch", "lr", "train_loss", "val_loss", "val_top1", "test_loss", "test_top1"])
        w.writerows(rows)
    print("저장:", OUT)


if __name__ == "__main__":
    main()
