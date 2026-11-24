"""사전 다운로드된 faster-whisper 모델로 음성을 로컬 전사한다."""
from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

from .errors import ServiceError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_model = None
_model_key: tuple[str, str, str] | None = None
_model_lock = Lock()


def _path_from_env(name: str, default: str) -> Path:
    value = os.getenv(name, default)
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _settings() -> tuple[str, str, str, Path]:
    model_name = os.getenv("LOCAL_STT_MODEL", "small").strip() or "small"
    device = os.getenv("LOCAL_STT_DEVICE", "cpu").strip() or "cpu"
    compute_type = os.getenv("LOCAL_STT_COMPUTE_TYPE", "int8").strip() or "int8"
    model_dir = _path_from_env("LOCAL_STT_MODEL_DIR", "models/stt") / model_name
    return model_name, device, compute_type, model_dir


def _load_model():
    """모델은 프로세스당 한 번만 로드하며 절대로 런타임 다운로드하지 않는다."""
    global _model, _model_key
    model_name, device, compute_type, model_dir = _settings()
    key = (str(model_dir), device, compute_type)
    if _model is not None and _model_key == key:
        return _model

    with _model_lock:
        if _model is not None and _model_key == key:
            return _model
        if not (model_dir / "model.bin").is_file():
            raise ServiceError(
                f"로컬 Whisper '{model_name}' 모델이 준비되지 않았습니다. "
                "모델 준비 스크립트를 실행한 뒤 다시 시도해 주세요."
            )
        try:
            from faster_whisper import WhisperModel

            _model = WhisperModel(
                str(model_dir),
                device=device,
                compute_type=compute_type,
                local_files_only=True,
            )
            _model_key = key
            return _model
        except ImportError as exc:
            raise ServiceError("로컬 Whisper 의존성이 설치되지 않았습니다. requirements.txt를 설치해 주세요.") from exc
        except Exception as exc:
            raise ServiceError("로컬 Whisper 모델을 불러오지 못했습니다. 모델 파일과 CPU 설정을 확인해 주세요.") from exc


def transcribe_audio(audio_path: Path) -> str:
    if not audio_path.is_file():
        raise ServiceError("업로드한 음성 파일을 찾을 수 없습니다.")
    try:
        model = _load_model()
        segments, _info = model.transcribe(
            str(audio_path),
            language="ko",
            beam_size=5,
            vad_filter=True,
        )
        transcript = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError("음성 파일을 전사하지 못했습니다. 지원 형식과 오디오 내용을 확인해 주세요.") from exc
    if not transcript:
        raise ServiceError("음성에서 전사할 내용을 찾지 못했습니다. 더 선명한 한국어 음성 파일을 사용해 주세요.")
    return transcript
