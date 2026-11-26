"""Kaggle 'Smart Fridge Food Dataset' 으로 식재료 분류 모델(YOLOv8m-cls) 학습.

데이터셋: https://www.kaggle.com/datasets/alyamibrahim/smart-fridge-food-dataset
  - 품목별 폴더에 사진만 있는 '분류용' 데이터 (물체 위치 상자 라벨 없음) → 탐지가 아니라 분류 모델
  - 20종: apple banana bread butter carrots cheese chicken cucumber eggs fish grapes
          lettuce mango milk onions orange peppers strawberry tomato yogurt

쓰임새: YOLOE 가 찾은 물체 상자를 잘라서 이 모델로 품목을 다시 판정 (2단계 인식),
        분류 점수가 낮으면 '모름' 처리.

사용 예
  python train_kaggle_cls.py                 # 학습 (CPU i7-9700 기준 약 2시간, 앞쪽 층 고정)
  python train_kaggle_cls.py --freeze 0 --device 0 --epochs 30   # GPU 가 있으면 전체 학습
  python train_kaggle_cls.py --epochs 1 --fraction 0.05   # 속도만 확인
결과: models/kaggle_fridge_cls.pt
"""
import argparse
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = (BASE_DIR.parent / "kaggle_data" / "cache" / "datasets" / "alyamibrahim"
            / "smart-fridge-food-dataset" / "versions" / "1" / "archive (2)")
OUT_NAME = "kaggle_fridge_cls"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="yolov8m-cls.pt", help="시작 가중치 (ImageNet 사전학습)")
    p.add_argument("--epochs", type=int, default=6)
    p.add_argument("--imgsz", type=int, default=224)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--fraction", type=float, default=1.0, help="학습 데이터 사용 비율 (속도 확인용)")
    p.add_argument("--device", default="cpu")
    p.add_argument("--freeze", type=int, default=8,
                   help="앞쪽 N개 층 고정 (CPU 학습 시간 단축, 0이면 전체 학습). yolov8m-cls 는 0~8 층 + 분류층")
    args = p.parse_args()

    if not (DATA_DIR / "train").is_dir():
        sys.exit(f"데이터셋이 없습니다: {DATA_DIR}\n"
                 "kagglehub.dataset_download('alyamibrahim/smart-fridge-food-dataset') 로 먼저 받으세요.")

    from ultralytics import YOLO

    model = YOLO(str(BASE_DIR / "models" / args.model) if (BASE_DIR / "models" / args.model).exists()
                 else args.model)
    model.train(
        data=str(DATA_DIR), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
        fraction=args.fraction, device=args.device, workers=4, patience=3,
        freeze=args.freeze or None, cache="ram",
        project=str(BASE_DIR / "runs"), name=OUT_NAME, exist_ok=True, plots=True,
    )
    best = BASE_DIR / "runs" / OUT_NAME / "weights" / "best.pt"
    if best.exists() and args.fraction >= 1.0:
        dst = BASE_DIR / "models" / f"{OUT_NAME}.pt"
        shutil.copy(best, dst)
        print(f"저장: {dst}")

    # 시험(test) 세트 정확도
    if best.exists():
        m = YOLO(str(best))
        r = m.val(data=str(DATA_DIR), split="test", imgsz=args.imgsz, device=args.device, plots=False)
        print(f"TEST top1={r.top1:.3f} top5={r.top5:.3f}")


if __name__ == "__main__":
    main()
