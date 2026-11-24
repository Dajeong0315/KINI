from __future__ import annotations

import os
import uuid
from pathlib import Path

import requests

from .errors import ServiceError


API_BASE = "https://api.elevenlabs.io/v1"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output" / "audio"


def _api_key() -> str:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise ServiceError("ElevenLabs API 키가 설정되지 않았습니다. 텍스트 대화는 계속 사용할 수 있습니다.")
    return key


def transcribe_audio(file_path: Path, original_name: str) -> str:
    headers = {"xi-api-key": _api_key()}
    try:
        with file_path.open("rb") as audio_file:
            response = requests.post(
                f"{API_BASE}/speech-to-text",
                headers=headers,
                data={"model_id": "scribe_v3", "language_code": "ko"},
                files={"file": (original_name, audio_file, "application/octet-stream")},
                timeout=120,
            )
    except requests.RequestException as exc:
        raise ServiceError("ElevenLabs STT에 연결할 수 없습니다.") from exc
    if not response.ok:
        raise ServiceError(f"음성을 텍스트로 변환하지 못했습니다. (HTTP {response.status_code})")
    text = response.json().get("text", "").strip()
    if not text:
        raise ServiceError("음성에서 텍스트를 찾지 못했습니다.")
    return text


def synthesize_speech(text: str) -> str:
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    if not voice_id:
        raise ServiceError("ElevenLabs 음성 ID가 설정되지 않았습니다.")
    headers = {"xi-api-key": _api_key(), "Content-Type": "application/json"}
    payload = {"text": text, "model_id": "eleven_multilingual_v2"}
    try:
        response = requests.post(
            f"{API_BASE}/text-to-speech/{voice_id}",
            params={"output_format": "mp3_44100_128"},
            headers=headers,
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise ServiceError("ElevenLabs TTS에 연결할 수 없습니다.") from exc
    if not response.ok:
        if response.status_code == 402:
            raise ServiceError("ElevenLabs 사용 가능 크레딧 또는 요금제 상태를 확인해 주세요. (HTTP 402)")
        raise ServiceError(f"음성 파일을 생성하지 못했습니다. (HTTP {response.status_code})")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"reply-{uuid.uuid4().hex[:12]}.mp3"
    (OUTPUT_DIR / filename).write_bytes(response.content)
    return filename
