"""사전 다운로드된 Piper 한국어 음성으로 WAV를 로컬 생성한다."""
from __future__ import annotations

import os
import uuid
import wave
from pathlib import Path
from threading import Lock

from .errors import ServiceError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output" / "audio"
_voice = None
_voice_path: Path | None = None
_voice_lock = Lock()


def _voice_model_path() -> Path:
    directory_value = os.getenv("LOCAL_TTS_MODEL_DIR", "models/tts")
    directory = Path(directory_value)
    if not directory.is_absolute():
        directory = PROJECT_ROOT / directory
    voice_name = os.getenv("LOCAL_TTS_VOICE", "ko_KR-kss-medium").strip() or "ko_KR-kss-medium"
    return directory / f"{voice_name}.onnx"


def _load_voice():
    global _voice, _voice_path
    path = _voice_model_path()
    if _voice is not None and _voice_path == path:
        return _voice

    with _voice_lock:
        if _voice is not None and _voice_path == path:
            return _voice
        if not path.is_file() or not path.with_suffix(path.suffix + ".json").is_file():
            raise ServiceError("로컬 Piper 한국어 음성 모델이 준비되지 않았습니다. 모델 준비 스크립트를 실행해 주세요.")
        try:
            from piper import PiperVoice

            _voice = PiperVoice.load(str(path))
            _voice_path = path
            return _voice
        except ImportError as exc:
            raise ServiceError("로컬 Piper 의존성이 설치되지 않았습니다. requirements.txt를 설치해 주세요.") from exc
        except Exception as exc:
            raise ServiceError("로컬 Piper 음성 모델을 불러오지 못했습니다. 모델 파일을 확인해 주세요.") from exc


def synthesize_speech(text: str) -> str:
    text = text.strip()
    if not text:
        raise ServiceError("음성으로 만들 답변이 없습니다.")
    try:
        voice = _load_voice()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"reply-{uuid.uuid4().hex[:12]}.wav"
        with wave.open(str(OUTPUT_DIR / filename), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        return filename
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError("로컬 Piper 음성을 생성하지 못했습니다. 짧은 답변으로 다시 시도해 주세요.") from exc
