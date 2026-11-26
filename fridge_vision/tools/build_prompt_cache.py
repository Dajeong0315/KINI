"""검색어 임베딩 NPZ 만들기 (인터넷 없이 실행하기 위한 준비).

config 의 검색어(OPENVOCAB_PROMPTS + PACKAGE_PROMPTS + 범용 + 손)로 YOLOE 임베딩을 계산해
models/prompts_<체크포인트>.npz 로 저장합니다. 실행할 때 이 파일이 있으면 글자 모델(약 600MB)을 쓰지 않습니다.

  python tools/build_prompt_cache.py                       # config.OPENVOCAB_MODEL
  python tools/build_prompt_cache.py --model yoloe-26m-seg.pt

검색어 목록을 바꾸면 다시 실행하세요 (안 하면 실행할 때 경고 후 글자 모델로 계산).
NPZ 는 만든 체크포인트에서만 쓸 수 있습니다.
"""
import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DETECTOR_BACKEND", "yoloe_openvocab")

import config  # noqa: E402
import vision  # noqa: E402


def build(model_path: Path) -> Path:
    import ultralytics

    prompt_ids = [k for k in vision.ITEM_IDS if not config.ITEMS[k].get("user")]
    names, _ = vision.openvocab_prompts(prompt_ids)
    model = ultralytics.YOLOE(str(model_path if model_path.exists() else model_path.name))
    with vision._in_base_dir():
        model.set_classes(names, model.get_text_pe(names))
    out = vision.prompt_cache_path(model_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save_prompt_embeddings(out)
    return out


def main() -> None:
    if config.DETECTOR_BACKEND != "yoloe_openvocab":
        sys.exit("DETECTOR_BACKEND 가 'yoloe_openvocab' 일 때만 씁니다.")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", help="체크포인트 파일 이름 (models/ 아래). 기본: config.OPENVOCAB_MODEL")
    args = p.parse_args()
    path = BASE_DIR / "models" / args.model if args.model else Path(config.OPENVOCAB_MODEL)
    out = build(path)
    n = len(vision.openvocab_prompts([k for k in vision.ITEM_IDS if not config.ITEMS[k].get("user")])[0])
    print(f"저장: {out}  (검색어 {n}개, 체크포인트 {path.name})")


if __name__ == "__main__":
    main()
