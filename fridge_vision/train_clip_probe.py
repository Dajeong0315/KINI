"""여러 사전학습 모델 특징 + 작은 분류층(MLP) 앙상블로 Kaggle 식재료 분류 (CPU 에서 학습 가능).

1) 사진을 각 백본으로 특징 벡터로 바꿔 캐시(runs/feats_<백본>_<split>.pt) — 처음 한 번만 오래 걸림
   - clip_b32 : CLIP ViT-B/32   (빠름, 사진 환경이 달라도 강함)
   - clip_b16 : CLIP ViT-B/16   (느림, 더 정확)
   - effv2s   : EfficientNetV2-S (ImageNet, 모양·질감 특징 — CLIP 과 서로 보완)
2) 백본마다 MLP 분류층 학습 → val 로 앙상블 가중치 결정 → test 정확도 보고
결과: models/clip_probe.pt  (분류층들 + 가중치 + 클래스 이름)

사용 예
  python train_clip_probe.py                          # 기본 백본 전부
  python train_clip_probe.py --backbones clip_b32     # 빠르게 하나만
"""
import argparse
import itertools
import os
import sys
from pathlib import Path

import torch
import torch.nn.functional as F
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = (BASE_DIR.parent / "kaggle_data" / "cache" / "datasets" / "alyamibrahim"
            / "smart-fridge-food-dataset" / "versions" / "1" / "archive (2)")
WEIGHTS = BASE_DIR / "weights"
OUT = BASE_DIR / "models" / "clip_probe.pt"
CACHE = BASE_DIR / "runs"
ALL_BACKBONES = ["clip_b32", "clip_b16", "effv2s"]


# ---------------------------------------------------------------------------
# 백본: (특징 함수, 전처리) 를 돌려줌. 특징은 길이 1 로 정규화해서 사용.
# ---------------------------------------------------------------------------
def load_backbone(name):
    if name.startswith("clip_"):
        import clip
        arch = {"clip_b32": "ViT-B/32", "clip_b16": "ViT-B/16"}[name]
        model, pre = clip.load(arch, device="cpu", download_root=str(WEIGHTS / "clip"))
        model.eval()
        return (lambda x: model.encode_image(x).float()), pre
    if name == "effv2s":
        os.environ.setdefault("TORCH_HOME", str(WEIGHTS / "torch"))
        import torchvision.models as M
        w = M.EfficientNet_V2_S_Weights.IMAGENET1K_V1
        model = M.efficientnet_v2_s(weights=w).eval()
        model.classifier = torch.nn.Identity()
        return model, w.transforms(crop_size=288, resize_size=288)
    raise ValueError(name)


def load_clip():  # 예전 코드 호환
    enc, pre = load_backbone("clip_b32")
    return type("M", (), {"encode_image": staticmethod(enc)})(), pre


class Folder(torch.utils.data.Dataset):
    def __init__(self, split, classes, preprocess, flip=False):
        self.items = [(p, i) for i, c in enumerate(classes)
                      for p in sorted((DATA_DIR / split / c).iterdir()) if p.is_file()]
        self.pre, self.flip = preprocess, flip

    def __len__(self):
        return len(self.items)

    def __getitem__(self, k):
        p, y = self.items[k]
        img = Image.open(p).convert("RGB")
        if self.flip:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        return self.pre(img), y


@torch.no_grad()
def features(name, split, classes, enc=None, pre=None, flip=False):
    tag = "_flip" if flip else ""
    old = CACHE / f"clip_feats_{split}{tag}.pt"          # 이전 버전 캐시 재사용
    f = old if name == "clip_b32" and old.exists() else CACHE / f"feats_{name}_{split}{tag}.pt"
    if f.exists():
        return torch.load(f)
    if enc is None:
        enc, pre = load_backbone(name)
    ds = Folder(split, classes, pre, flip)
    dl = torch.utils.data.DataLoader(ds, batch_size=64, num_workers=6)
    xs, ys = [], []
    for i, (x, y) in enumerate(dl):
        xs.append(enc(x).float())
        ys.append(y)
        if i % 20 == 0:
            print(f"  {name} {split}{tag}: {i * 64}/{len(ds)}", flush=True)
    out = (torch.cat(xs), torch.cat(ys))
    torch.save(out, f)
    return out


# ---------------------------------------------------------------------------
# 분류층
# ---------------------------------------------------------------------------
SCALE = 10.0


def make_head(dim, n_cls, hid=1024):
    return torch.nn.Sequential(torch.nn.Dropout(0.2), torch.nn.Linear(dim, hid), torch.nn.GELU(),
                               torch.nn.Dropout(0.5), torch.nn.Linear(hid, n_cls))


def prep(x):
    return F.normalize(x, dim=1) * SCALE


def train_head(X, Y, n_cls, epochs=150, wd=1e-2, lr=1e-3, seed=0, log=None):
    """log 가 있으면 epoch 마다 log(epoch, 평균 train loss, lr, head) 호출 (학습 결과에는 영향 없음)."""
    torch.manual_seed(seed)
    head = make_head(X.shape[1], n_cls)
    opt = torch.optim.AdamW(head.parameters(), lr, weight_decay=wd)
    sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    for ep in range(epochs):
        head.train()
        perm = torch.randperm(len(X))
        tot, cnt = 0.0, 0
        for b in range(0, len(X), 256):
            i = perm[b:b + 256]
            # 특징 공간 잡음 증강 (과적합 억제)
            loss = F.cross_entropy(head(X[i] + 0.3 * torch.randn_like(X[i])), Y[i], label_smoothing=0.1)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot, cnt = tot + loss.item() * len(i), cnt + len(i)
        cur_lr = opt.param_groups[0]["lr"]
        sch.step()
        if log:
            head.eval()
            log(ep + 1, tot / cnt, cur_lr, head)
    return head.eval()


@torch.no_grad()
def probs(head, X):
    return head(X).softmax(1)


def acc(P, Y):
    return (P.argmax(1) == Y).float().mean().item()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backbones", nargs="+", default=ALL_BACKBONES, choices=ALL_BACKBONES)
    args = ap.parse_args()
    if not (DATA_DIR / "train").is_dir():
        sys.exit(f"데이터셋이 없습니다: {DATA_DIR}")
    classes = sorted(p.name for p in (DATA_DIR / "train").iterdir() if p.is_dir())
    CACHE.mkdir(exist_ok=True)

    heads, P_va, P_te = {}, {}, {}
    for name in args.backbones:
        enc = pre = None
        if not all((CACHE / f"feats_{name}_{s}.pt").exists() for s in ("train", "val", "test")) \
                and not (name == "clip_b32" and (CACHE / "clip_feats_test.pt").exists()):
            enc, pre = load_backbone(name)
        Xtr, Ytr = features(name, "train", classes, enc, pre)
        Xva, Yva = features(name, "val", classes, enc, pre)
        Xte, Yte = features(name, "test", classes, enc, pre)
        h = train_head(prep(Xtr), Ytr, len(classes))
        heads[name] = h
        P_va[name], P_te[name] = probs(h, prep(Xva)), probs(h, prep(Xte))
        print(f"[{name}] val={acc(P_va[name], Yva):.4f} test={acc(P_te[name], Yte):.4f}", flush=True)

    # 앙상블 가중치: val 에서 0.1 단위 격자 탐색
    names = list(heads)
    best = (0, None)
    for w in itertools.product(range(11), repeat=len(names)):
        if sum(w) == 0:
            continue
        a = acc(sum(wi * P_va[n] for wi, n in zip(w, names)), Yva)
        if a > best[0]:
            best = (a, w)
    a, w = best
    wsum = sum(w)
    weights = {n: wi / wsum for n, wi in zip(names, w)}
    te = acc(sum(weights[n] * P_te[n] for n in names), Yte)
    print(f"앙상블 가중치 {weights}  val={a:.4f}  TEST top1={te:.4f}")

    # 최종: 정한 가중치 그대로, train+val 합쳐서 분류층 다시 학습 (데이터 17% 추가)
    for name in names:
        Xtr, Ytr = features(name, "train", classes)
        Xva, Yva = features(name, "val", classes)
        Xte, Yte = features(name, "test", classes)
        heads[name] = train_head(torch.cat([prep(Xtr), prep(Xva)]), torch.cat([Ytr, Yva]), len(classes))
        P_te[name] = probs(heads[name], prep(Xte))
    te = acc(sum(weights[n] * P_te[n] for n in names), Yte)
    print(f"최종(train+val 학습) TEST top1={te:.4f}")

    torch.save({"classes": classes, "scale": SCALE, "weights": weights,
                "heads": {n: heads[n].state_dict() for n in names},
                "dims": {n: heads[n][1].in_features for n in names}}, OUT)
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
