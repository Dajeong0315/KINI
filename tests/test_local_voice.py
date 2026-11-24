from io import BytesIO

import pytest

import app.main as main
from app.services.errors import ServiceError
from app.services import local_stt, local_tts


def test_voice_route_passes_local_stt_transcript_to_existing_safety_pipeline(monkeypatch):
    received_paths = []

    def fake_transcribe(path):
        received_paths.append(path)
        return "떡 먹어도 안전해?"

    monkeypatch.setattr(main, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(
        main,
        "ask_ollama",
        lambda *_: (_ for _ in ()).throw(AssertionError("안전 질문은 Ollama를 호출하면 안 됩니다.")),
    )
    client = main.app.test_client()
    response = client.post(
        "/api/voice",
        data={"audio": (BytesIO(b"fake wav bytes"), "question.wav")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert received_paths
    assert response.json["transcript"] == "떡 먹어도 안전해?"
    assert response.json["answer_source"] == "inventory"
    assert "냄새·색·점액을 확인하고 의심되면 버리세요" in response.json["answer"]


def test_tts_route_returns_local_wav_url(monkeypatch):
    monkeypatch.setattr(main, "synthesize_speech", lambda _text: "reply-local.wav")
    client = main.app.test_client()
    response = client.post("/api/tts", json={"text": "떡은 냉동실에 있습니다."})

    assert response.status_code == 200
    assert response.json["audio_url"] == "/audio/reply-local.wav"


def test_tts_route_returns_friendly_local_error(monkeypatch):
    monkeypatch.setattr(main, "synthesize_speech", lambda _text: (_ for _ in ()).throw(ServiceError("로컬 Piper 한국어 음성 모델이 준비되지 않았습니다.")))
    client = main.app.test_client()
    response = client.post("/api/tts", json={"text": "테스트"})

    assert response.status_code == 400
    assert "로컬 Piper" in response.json["error"]


def test_local_stt_missing_model_is_friendly(monkeypatch, tmp_path):
    monkeypatch.setattr(local_stt, "_settings", lambda: ("small", "cpu", "int8", tmp_path / "small"))
    local_stt._model = None
    local_stt._model_key = None
    with pytest.raises(ServiceError, match="로컬 Whisper.*준비되지 않았습니다"):
        local_stt._load_model()


def test_local_tts_missing_voice_is_friendly(monkeypatch, tmp_path):
    monkeypatch.setattr(local_tts, "_voice_model_path", lambda: tmp_path / "ko_KR-kss-medium.onnx")
    local_tts._voice = None
    local_tts._voice_path = None
    with pytest.raises(ServiceError, match="로컬 Piper.*준비되지 않았습니다"):
        local_tts._load_voice()
