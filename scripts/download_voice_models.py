"""로컬 음성 모델을 미리 다운로드한다. 실행 중인 웹 앱은 네트워크를 사용하지 않는다."""
from __future__ import annotations

import shutil
from pathlib import Path

from huggingface_hub import snapshot_download


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STT_DIR = PROJECT_ROOT / "models" / "stt" / "small"
TTS_DIR = PROJECT_ROOT / "models" / "tts"
VOICE = "ko_KR-kss-medium"


def download_stt() -> None:
    print("faster-whisper small 모델을 다운로드합니다...")
    snapshot_download(
        repo_id="Systran/faster-whisper-small",
        local_dir=STT_DIR,
        allow_patterns=["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
    )


def download_tts() -> None:
    print("Piper 한국어 음성 모델을 다운로드합니다...")
    source_root = TTS_DIR / "_source"
    source = Path(
        snapshot_download(
            repo_id="rhasspy/piper-voices",
            local_dir=source_root,
            allow_patterns=[
                "ko/ko_KR/kss/medium/ko_KR-kss-medium.onnx",
                "ko/ko_KR/kss/medium/ko_KR-kss-medium.onnx.json",
            ],
        )
    ) / "ko" / "ko_KR" / "kss" / "medium"
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in (".onnx", ".onnx.json"):
        source_file = source / f"{VOICE}{suffix}"
        if not source_file.is_file():
            raise RuntimeError("Piper 음성 파일 다운로드를 완료하지 못했습니다.")
        shutil.copy2(source_file, TTS_DIR / source_file.name)


def main() -> None:
    download_stt()
    download_tts()
    print("완료: models/stt/small 및 models/tts에 로컬 음성 모델이 준비되었습니다.")
    print("이후 웹 앱 런타임은 로컬 모델 파일만 사용합니다.")


if __name__ == "__main__":
    main()
